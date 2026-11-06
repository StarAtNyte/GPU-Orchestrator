package main

import (
	"fmt"
	"log"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

// GPU states reported to clients.
const (
	GPUStateFree     = "free"     // nothing using the GPU, safe to submit
	GPUStateWarm     = "warm"     // one of our models is loaded but idle; submit is fine
	GPUStateLoading  = "loading"  // a worker is starting / loading its model
	GPUStateBusy     = "busy"     // one of our jobs is queued or running
	GPUStateExternal = "external" // a process outside the orchestrator is using the GPU
	GPUStateUnknown  = "unknown"  // nvidia-smi unavailable
)

// vramSafetyMarginGB is kept free on top of an app's configured VRAM.
const vramSafetyMarginGB = 1.0

// externalUtilThreshold: utilisation (%) above which, with no orchestrator job
// running, we attribute the load to an external process.
const externalUtilThreshold = 10.0

// activeJobWindow bounds how old an unfinished job may be and still count as
// "using the GPU". Older rows are stale and handled by the cleanup routines.
const activeJobWindow = "15 minutes"

// submitMu serialises the "is the GPU free?" check with the job insert so two
// simultaneous submissions cannot both pass the check.
var submitMu sync.Mutex

// GPUProc is one compute process reported by nvidia-smi.
type GPUProc struct {
	PID    int     `json:"pid"`
	VRAMGB float64 `json:"vram_gb"`
	Ours   bool    `json:"ours"`
}

// GPUState is the single source of truth for "is the GPU usable right now".
type GPUState struct {
	Status         string    `json:"status"` // "ok" | "error" (kept for older UIs)
	State          string    `json:"state"`
	Message        string    `json:"message"`
	Timestamp      string    `json:"timestamp"`
	IsAvailable    bool      `json:"is_available"` // can a new request be accepted now?
	IsBusy         bool      `json:"is_busy"`
	ActiveJobs     int       `json:"active_jobs"`
	ActiveApp      string    `json:"active_app,omitempty"`
	ActiveJobID    string    `json:"active_job_id,omitempty"`
	FreeVRAMGB     float64   `json:"free_vram_gb"`
	UsedVRAMGB     float64   `json:"used_vram_gb"`
	TotalVRAMGB    float64   `json:"total_vram_gb"`
	OrchVRAMGB     float64   `json:"orchestrator_vram_gb"`
	ExternalVRAMGB float64   `json:"external_vram_gb"`
	UtilizationPct float64   `json:"utilization_pct"`
	Processes      []GPUProc `json:"processes,omitempty"`
	RetryAfterSec  int       `json:"retry_after_seconds,omitempty"`
	Error          string    `json:"error,omitempty"`
}

type smiReading struct {
	freeGB, usedGB, totalGB, util float64
	procs                         map[int]float64 // pid -> GB
}

func readNvidiaSMI() (*smiReading, error) {
	out, err := exec.Command("nvidia-smi",
		"--query-gpu=memory.free,memory.used,memory.total,utilization.gpu",
		"--format=csv,noheader,nounits").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("nvidia-smi: %v: %s", err, strings.TrimSpace(string(out)))
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	parts := strings.Split(lines[0], ",")
	if len(parts) < 4 {
		return nil, fmt.Errorf("unexpected nvidia-smi output %q", lines[0])
	}
	f := func(s string) float64 {
		v, _ := strconv.ParseFloat(strings.TrimSpace(s), 64)
		return v
	}
	r := &smiReading{
		freeGB:  f(parts[0]) / 1024,
		usedGB:  f(parts[1]) / 1024,
		totalGB: f(parts[2]) / 1024,
		util:    f(parts[3]),
		procs:   map[int]float64{},
	}

	// Per-process memory is best-effort.
	if pout, perr := exec.Command("nvidia-smi",
		"--query-compute-apps=pid,used_memory",
		"--format=csv,noheader,nounits").CombinedOutput(); perr == nil {
		for _, ln := range strings.Split(strings.TrimSpace(string(pout)), "\n") {
			p := strings.Split(ln, ",")
			if len(p) < 2 {
				continue
			}
			pid, err := strconv.Atoi(strings.TrimSpace(p[0]))
			if err != nil {
				continue
			}
			r.procs[pid] += f(p[1]) / 1024
		}
	}
	return r, nil
}

// orchestratorPIDs returns host PIDs of every process inside our worker containers.
func orchestratorPIDs() map[int]bool {
	pids := map[int]bool{}
	if dockerCli == nil {
		return pids
	}
	for _, w := range GetAllWorkers() {
		if w.ContainerID == "" {
			continue
		}
		top, err := dockerCli.ContainerTop(ctx, w.ContainerID, nil)
		if err != nil {
			continue
		}
		idx := -1
		for i, t := range top.Titles {
			if strings.EqualFold(t, "PID") {
				idx = i
				break
			}
		}
		if idx < 0 {
			continue
		}
		for _, row := range top.Processes {
			if idx < len(row) {
				if pid, err := strconv.Atoi(row[idx]); err == nil {
					pids[pid] = true
				}
			}
		}
	}
	return pids
}

// computeGPUState builds the authoritative GPU state.
func computeGPUState() GPUState {
	st := GPUState{Status: "ok", Timestamp: time.Now().Format(time.RFC3339)}

	// --- Orchestrator-side activity (workers + unfinished jobs) ---
	loading, processing, warm := false, false, false
	for _, w := range GetAllWorkers() {
		switch w.State {
		case WorkerStateStarting:
			loading = true
			st.ActiveApp = w.AppID
		case WorkerStateProcessing:
			processing = true
			st.ActiveApp, st.ActiveJobID = w.AppID, w.CurrentJobID
		case WorkerStateWarm:
			warm = true
		}
	}
	var jobApp, jobID string
	var jobStatus string
	err := db.QueryRow(`SELECT id, app_id, status FROM jobs
		WHERE status IN ('PENDING','QUEUED','PROCESSING')
		  AND created_at > NOW() - INTERVAL '`+activeJobWindow+`'
		ORDER BY created_at LIMIT 1`).Scan(&jobID, &jobApp, &jobStatus)
	hasJob := err == nil
	if hasJob {
		_ = db.QueryRow(`SELECT COUNT(*) FROM jobs
			WHERE status IN ('PENDING','QUEUED','PROCESSING')
			  AND created_at > NOW() - INTERVAL '` + activeJobWindow + `'`).Scan(&st.ActiveJobs)
		if st.ActiveApp == "" {
			st.ActiveApp, st.ActiveJobID = jobApp, jobID
		}
	}

	// --- Hardware reading ---
	smi, serr := readNvidiaSMI()
	if serr != nil {
		log.Printf("[GPU] %v", serr)
		st.Status, st.State, st.Error = "error", GPUStateUnknown, serr.Error()
		st.Message = "GPU status unavailable"
		// Fail closed only for what we can prove: our own activity.
		st.IsBusy = processing || loading || hasJob
		st.IsAvailable = !st.IsBusy
		return st
	}
	st.FreeVRAMGB, st.UsedVRAMGB, st.TotalVRAMGB, st.UtilizationPct = smi.freeGB, smi.usedGB, smi.totalGB, smi.util

	ours := orchestratorPIDs()
	for pid, gb := range smi.procs {
		isOurs := ours[pid]
		st.Processes = append(st.Processes, GPUProc{PID: pid, VRAMGB: round2(gb), Ours: isOurs})
		if isOurs {
			st.OrchVRAMGB += gb
		} else {
			st.ExternalVRAMGB += gb
		}
	}
	// VRAM not attributed to any listed process (driver overhead, containers whose
	// PIDs we can't see) is treated as external too.
	if unattributed := st.UsedVRAMGB - st.OrchVRAMGB - st.ExternalVRAMGB; unattributed > 0.5 {
		st.ExternalVRAMGB += unattributed
	}
	st.OrchVRAMGB, st.ExternalVRAMGB = round2(st.OrchVRAMGB), round2(st.ExternalVRAMGB)

	// External compute load: hardware is busy but none of our jobs are running.
	externalActive := !processing && !loading && !hasJob && smi.util > externalUtilThreshold

	switch {
	case processing || (hasJob && jobStatus == "PROCESSING"):
		st.State, st.IsBusy = GPUStateBusy, true
		st.Message = busyMessage(st)
		st.RetryAfterSec = 15
	case loading:
		st.State, st.IsBusy = GPUStateLoading, true
		st.Message = fmt.Sprintf("Loading model for %s", nameOf(st.ActiveApp))
		st.RetryAfterSec = 20
	case hasJob:
		st.State, st.IsBusy = GPUStateBusy, true
		st.Message = fmt.Sprintf("A %s job is queued", nameOf(st.ActiveApp))
		st.RetryAfterSec = 15
	case externalActive:
		st.State, st.IsBusy = GPUStateExternal, true
		st.Message = fmt.Sprintf("GPU in use by another process (%.0f%% load, %.1f GB)", smi.util, st.ExternalVRAMGB)
		st.RetryAfterSec = 30
	case warm:
		st.State = GPUStateWarm
		st.Message = fmt.Sprintf("Ready • model loaded • %.1f GB free", st.FreeVRAMGB)
	default:
		st.State = GPUStateFree
		st.Message = fmt.Sprintf("Free • %.1f GB available", st.FreeVRAMGB)
		if st.ExternalVRAMGB > 0.5 {
			st.Message = fmt.Sprintf("Idle • %.1f GB free (%.1f GB held by other processes)", st.FreeVRAMGB, st.ExternalVRAMGB)
		}
	}
	st.IsAvailable = !st.IsBusy
	return st
}

func busyMessage(st GPUState) string {
	if st.ActiveApp != "" {
		return fmt.Sprintf("Busy • running %s", nameOf(st.ActiveApp))
	}
	return "Busy • job running"
}

func nameOf(appID string) string {
	if a, ok := appRegistry[appID]; ok && a.Name != "" {
		return a.Name
	}
	if appID == "" {
		return "a job"
	}
	return appID
}

func round2(v float64) float64 { return float64(int(v*100+0.5)) / 100 }

// checkGPUForApp decides whether a submission for appID may proceed. It returns
// the state and, when rejecting, a human-readable reason.
func checkGPUForApp(appID string) (GPUState, bool, string) {
	st := computeGPUState()
	if !st.IsAvailable {
		return st, false, st.Message
	}
	app, ok := appRegistry[appID]
	if !ok || app.Type != "local" || st.State == GPUStateUnknown {
		return st, true, ""
	}
	// Enough VRAM for this model? A warm worker of any app can be offloaded, so
	// count its VRAM as reclaimable.
	reclaimable := 0.0
	if st.State == GPUStateWarm {
		reclaimable = st.OrchVRAMGB
	}
	need := float64(app.GPUVramGB) + vramSafetyMarginGB
	if app.GPUVramGB > 0 && st.FreeVRAMGB+reclaimable < need {
		st.RetryAfterSec = 30
		return st, false, fmt.Sprintf(
			"Not enough GPU memory for %s: needs ~%d GB, %.1f GB available (%.1f GB held by other processes)",
			app.Name, app.GPUVramGB, st.FreeVRAMGB+reclaimable, st.ExternalVRAMGB)
	}
	return st, true, ""
}
