package main

import (
	"fmt"
	"strings"
	"testing"
)

// fakeGPU simulates nvidia-smi for a 24 GB card.
// usedMB/utilPct describe the whole GPU; procs are "pid,mb" lines.
func fakeGPU(t *testing.T, usedMB, utilPct int, procs ...string) {
	t.Helper()
	const totalMB = 24576
	prev := runSMI
	t.Cleanup(func() { runSMI = prev })
	runSMI = func(args ...string) ([]byte, error) {
		q := strings.Join(args, " ")
		if strings.Contains(q, "query-compute-apps") {
			return []byte(strings.Join(procs, "\n")), nil
		}
		return []byte(fmt.Sprintf("%d, %d, %d, %d\n", totalMB-usedMB, usedMB, totalMB, utilPct)), nil
	}
}

func noJobs(t *testing.T) {
	t.Helper()
	prev := activeJobLookup
	t.Cleanup(func() { activeJobLookup = prev })
	activeJobLookup = func() (string, string, string, int, bool) { return "", "", "", 0, false }
}

func withApps(t *testing.T, apps map[string]AppConfig) {
	t.Helper()
	prev := appRegistry
	t.Cleanup(func() { appRegistry = prev })
	appRegistry = apps
}

func withWorkers(t *testing.T, ws map[string]*WorkerInfo) {
	t.Helper()
	workerMutex.Lock()
	prev := workers
	workers = ws
	workerMutex.Unlock()
	t.Cleanup(func() { workerMutex.Lock(); workers = prev; workerMutex.Unlock() })
}

var testApps = map[string]AppConfig{
	"small": {ID: "small", Name: "Small", Type: "local", GPUVramGB: 6},
	"big":   {ID: "big", Name: "Big", Type: "local", GPUVramGB: 20},
}

// The GPU-free path: nothing running, plenty of VRAM -> accepted, workers may start.
func TestFreeGPUAcceptsAndStarts(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{})
	noJobs(t)
	fakeGPU(t, 600, 0) // idle desktop usage only

	st, ok, reason := checkGPUForApp("big")
	if !ok || st.State != GPUStateFree || !st.IsAvailable {
		t.Fatalf("free GPU must accept: state=%s ok=%v reason=%q", st.State, ok, reason)
	}
	if err := gpuStartPreflight("big"); err != nil {
		t.Fatalf("preflight must pass on a free GPU: %v", err)
	}
}

// A different process holds memory but is idle: GPU usable if the model still fits.
func TestIdleForeignVRAMStillFitsSmallModel(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{})
	noJobs(t)
	fakeGPU(t, 12000, 0, "4242, 12000") // 12 GB held, 0% load

	if _, ok, reason := checkGPUForApp("small"); !ok {
		t.Fatalf("6 GB model fits in ~12 GB free: %s", reason)
	}
	st, ok, reason := checkGPUForApp("big")
	if ok {
		t.Fatalf("20 GB model must be refused with 12 GB held elsewhere")
	}
	if !strings.Contains(reason, "Not enough GPU memory") || st.ExternalVRAMGB < 11 {
		t.Fatalf("expected VRAM refusal naming external usage, got %q (external=%.1f)", reason, st.ExternalVRAMGB)
	}
	if err := gpuStartPreflight("big"); err == nil {
		t.Fatal("preflight must refuse to start a model that does not fit")
	}
}

// The case that happens today: a foreign process computing at 100%.
func TestForeignComputeBlocksSubmitAndStart(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{})
	noJobs(t)
	fakeGPU(t, 11700, 100, "1361557, 8656", "3859, 1626", "10276, 652")

	st, ok, _ := checkGPUForApp("small")
	if ok || st.State != GPUStateExternal || st.IsAvailable {
		t.Fatalf("foreign load must block: state=%s ok=%v", st.State, ok)
	}
	if err := gpuStartPreflight("small"); err == nil {
		t.Fatal("preflight must refuse while a foreign process computes")
	} else if _, isRes := err.(*ErrGPUReserved); !isRes {
		t.Fatalf("want ErrGPUReserved, got %T", err)
	}
}

// Our own warm worker: accepted, and its VRAM is reclaimable for a bigger model.
func TestWarmWorkerAcceptsAndIsReclaimable(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{"w": {WorkerID: "w", AppID: "small", State: WorkerStateWarm}})
	noJobs(t)
	fakeGPU(t, 7000, 0) // 7 GB used, no per-process info available

	st, ok, reason := checkGPUForApp("small")
	if !ok || st.State != GPUStateWarm {
		t.Fatalf("warm GPU must accept: state=%s ok=%v %s", st.State, ok, reason)
	}
}

// Our own job in flight blocks new submissions but not the worker that serves it.
func TestQueuedOwnJobBlocksSubmitButNotItsWorkerStart(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{})
	prev := activeJobLookup
	t.Cleanup(func() { activeJobLookup = prev })
	activeJobLookup = func() (string, string, string, int, bool) { return "j1", "small", "QUEUED", 1, true }
	fakeGPU(t, 600, 0)

	if _, ok, _ := checkGPUForApp("small"); ok {
		t.Fatal("a queued job must block further submissions")
	}
	if err := gpuStartPreflight("small"); err != nil {
		t.Fatalf("the worker waiting for that very job must be allowed to start: %v", err)
	}
}

// A worker mid-job must stop any second model from starting.
func TestProcessingWorkerBlocksOtherStarts(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{"w": {WorkerID: "w", AppID: "big", State: WorkerStateProcessing}})
	noJobs(t)
	fakeGPU(t, 18000, 95)
	if err := gpuStartPreflight("small"); err == nil {
		t.Fatal("must not start a second model while one is processing")
	}
}

// If the GPU cannot be read, starting a worker fails closed.
func TestUnreadableGPUFailsClosedForStarts(t *testing.T) {
	withApps(t, testApps)
	withWorkers(t, map[string]*WorkerInfo{})
	noJobs(t)
	prev := runSMI
	t.Cleanup(func() { runSMI = prev })
	runSMI = func(args ...string) ([]byte, error) { return nil, fmt.Errorf("NVML unavailable") }
	if err := gpuStartPreflight("small"); err == nil {
		t.Fatal("unreadable GPU must refuse to start workers")
	}
}
