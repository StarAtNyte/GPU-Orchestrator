package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"sync"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/client"
)

// WorkerState represents the current state of a worker
type WorkerState string

const (
	WorkerStateAbsent   WorkerState = "ABSENT"
	WorkerStateStarting WorkerState = "STARTING"

	// WorkerStateReady: container is running and registered in etcd, but model is NOT
	// yet loaded into GPU VRAM. GPU memory is free.
	WorkerStateReady WorkerState = "READY"

	// WorkerStateWarm: model IS loaded into GPU VRAM, worker is idle (not processing).
	// This is the intermediate state between a finished job and the idle-timeout
	// offload. GPU VRAM is occupied but compute is free. Can be preempted cheaply
	// by calling the worker's /cleanup HTTP endpoint (no container stop needed).
	WorkerStateWarm WorkerState = "WARM"

	// WorkerStateProcessing: worker is actively running inference. Both GPU VRAM
	// and compute are occupied. Cannot be preempted.
	WorkerStateProcessing WorkerState = "PROCESSING"

	// WorkerStateIdle: model has been offloaded from GPU VRAM (either by the
	// idle-timeout timer or by a preemption request). Container is still running
	// and listening on its queue. GPU memory is free.
	WorkerStateIdle WorkerState = "IDLE"

	WorkerStateStopping WorkerState = "STOPPING"
)

// WorkerInfo holds information about a worker instance
type WorkerInfo struct {
	WorkerID           string
	AppID              string
	ContainerID        string
	State              WorkerState
	LastHeartbeat      time.Time
	LastActivityTime   time.Time
	CurrentJobID       string
	StartedAt          time.Time
	IdleTimeoutSeconds int
	CleanupPort        int           // HTTP port for /cleanup and /status endpoints (default 8000)
	StartupFailures    int           // Track consecutive startup failures
	LastFailureTime    time.Time     // Last time worker failed to start
	Backoff            time.Duration // Exponential backoff delay
}

var (
	workers                = make(map[string]*WorkerInfo)
	workerMutex            sync.RWMutex
	gpuAllocationMutex     sync.Mutex
	dockerCli              *client.Client
	workerFailureThreshold = 5               // Max consecutive failures before circuit breaker
	workerMaxBackoff       = 5 * time.Minute // Max backoff delay
)

// InitWorkerManager initializes the worker manager with Docker SDK client
func InitWorkerManager() error {
	var err error
	dockerCli, err = client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		return fmt.Errorf("failed to create Docker client: %w", err)
	}

	workers = make(map[string]*WorkerInfo)
	log.Println("[WORKER_MANAGER] Initialized successfully")
	return nil
}

// StartWorkerForApp starts a worker container for the specified app
func StartWorkerForApp(appID string) (string, error) {
	gpuAllocationMutex.Lock()
	defer gpuAllocationMutex.Unlock()

	// Get app configuration
	appConfig, exists := appRegistry[appID]
	if !exists {
		return "", fmt.Errorf("app %s not found in registry", appID)
	}

	// Check if app is local (only local workers can be started)
	if appConfig.Type != "local" {
		return "", fmt.Errorf("app %s is not a local worker", appID)
	}

	// Generate worker ID
	workerID := appConfig.ContainerName
	if workerID == "" {
		workerID = fmt.Sprintf("%s-worker", appID)
	}

	// Resolve cleanup port (default 8000)
	cleanupPort := appConfig.CleanupPort
	if cleanupPort == 0 {
		cleanupPort = 8000
	}

	// Check if worker already exists
	workerMutex.Lock()
	if existing := workers[workerID]; existing != nil {
		// Check circuit breaker - if worker has failed too many times, don't restart
		if existing.StartupFailures >= workerFailureThreshold {
			timeSinceLastFailure := time.Since(existing.LastFailureTime)
			if timeSinceLastFailure < existing.Backoff {
				workerMutex.Unlock()
				return "", fmt.Errorf("worker %s in circuit breaker (failures: %d, backoff: %v remaining)",
					workerID, existing.StartupFailures, existing.Backoff-timeSinceLastFailure)
			}
			// Reset backoff if enough time has passed
			log.Printf("[WORKER_MANAGER] Resetting circuit breaker for %s after %v", workerID, timeSinceLastFailure)
			existing.StartupFailures = 0
			existing.Backoff = 0
		}

		if existing.State != WorkerStateAbsent {
			workerMutex.Unlock()
			return "", fmt.Errorf("worker %s already exists in state %s", workerID, existing.State)
		}
	}

	// Create worker info entry
	idleTimeout := appConfig.IdleTimeoutSeconds
	if idleTimeout == 0 {
		idleTimeout = 300 // Default 5 minutes
	}

	workers[workerID] = &WorkerInfo{
		WorkerID:           workerID,
		AppID:              appID,
		State:              WorkerStateStarting,
		StartedAt:          time.Now(),
		LastActivityTime:   time.Now(),
		IdleTimeoutSeconds: idleTimeout,
		CleanupPort:        cleanupPort,
		StartupFailures:    0,
		Backoff:            0,
	}
	workerMutex.Unlock()

	log.Printf("[WORKER_MANAGER] Starting worker %s for app %s", workerID, appID)

	// Remove any existing container with the same name
	if err := removeExistingContainer(workerID); err != nil {
		log.Printf("[WORKER_MANAGER] Warning: failed to remove existing container: %v", err)
	}

	// Create container configuration
	containerConfig := &container.Config{
		Image: appConfig.DockerImage,
		Env: []string{
			fmt.Sprintf("REDIS_HOST=%s", "redis"),
			fmt.Sprintf("REDIS_PORT=%s", "6379"),
			fmt.Sprintf("POSTGRES_HOST=%s", getEnv("POSTGRES_HOST", "postgres")),
			fmt.Sprintf("POSTGRES_USER=%s", getEnv("POSTGRES_USER", "postgres")),
			fmt.Sprintf("POSTGRES_PASSWORD=%s", getEnv("POSTGRES_PASSWORD", "postgres")),
			fmt.Sprintf("POSTGRES_DB=%s", getEnv("POSTGRES_DB", "gpu_orchestrator")),
			fmt.Sprintf("ETCD_HOST=%s", "etcd"),
			fmt.Sprintf("ETCD_PORT=%s", "2379"),
			fmt.Sprintf("WORKER_ID=%s", workerID),
			fmt.Sprintf("APP_ID=%s", appID),
			fmt.Sprintf("QUEUE_NAME=%s", appConfig.Queue),
			fmt.Sprintf("HF_TOKEN=%s", getEnv("HF_TOKEN", "")),
			fmt.Sprintf("CLEANUP_PORT=%d", cleanupPort),
		},
		Volumes: map[string]struct{}{
			"/models": {},
		},
	}

	// Append app-specific environment variables from apps.yaml
	for k, v := range appConfig.Environment {
		containerConfig.Env = append(containerConfig.Env, fmt.Sprintf("%s=%s", k, v))
	}

	// Host configuration with GPU support
	hostConfig := &container.HostConfig{
		NetworkMode: "backend_backend-network",
		Binds: append([]string{
			fmt.Sprintf("%s_models:/models", workerID),
		}, appConfig.Volumes...),
		Resources: container.Resources{
			DeviceRequests: []container.DeviceRequest{
				{
					Count:        -1, // All GPUs
					Capabilities: [][]string{{"gpu"}},
				},
			},
		},
		RestartPolicy: container.RestartPolicy{
			Name: "no",
		},
	}

	// Create the container
	ctx := context.Background()
	resp, err := dockerCli.ContainerCreate(
		ctx,
		containerConfig,
		hostConfig,
		nil,
		nil,
		workerID,
	)
	if err != nil {
		workerMutex.Lock()
		delete(workers, workerID)
		workerMutex.Unlock()
		return "", fmt.Errorf("failed to create container: %w", err)
	}

	// Update worker info with container ID
	workerMutex.Lock()
	workers[workerID].ContainerID = resp.ID
	workerMutex.Unlock()

	// Start the container
	if err := dockerCli.ContainerStart(ctx, resp.ID, container.StartOptions{}); err != nil {
		workerMutex.Lock()
		delete(workers, workerID)
		workerMutex.Unlock()
		return "", fmt.Errorf("failed to start container: %w", err)
	}

	log.Printf("[WORKER_MANAGER] Container %s started for worker %s", resp.ID[:12], workerID)

	// Wait for worker to register in etcd (async)
	startupTimeout := time.Duration(appConfig.StartupTimeoutSeconds) * time.Second
	if startupTimeout == 0 {
		startupTimeout = 120 * time.Second
	}

	go func() {
		if err := waitForWorkerReady(workerID, startupTimeout); err != nil {
			log.Printf("[WORKER_MANAGER] Worker %s failed to start: %v", workerID, err)

			// Increment failure counter and calculate backoff
			workerMutex.Lock()
			if worker := workers[workerID]; worker != nil {
				worker.StartupFailures++
				worker.LastFailureTime = time.Now()
				// Exponential backoff: 30s, 1m, 2m, 4m, 5m (max)
				worker.Backoff = time.Duration(30*worker.StartupFailures) * time.Second
				if worker.Backoff > workerMaxBackoff {
					worker.Backoff = workerMaxBackoff
				}
				log.Printf("[WORKER_MANAGER] Worker %s failure count: %d, backoff: %v",
					workerID, worker.StartupFailures, worker.Backoff)

				if worker.StartupFailures >= workerFailureThreshold {
					log.Printf("[WORKER_MANAGER] ⚠️  Worker %s hit circuit breaker threshold! Will not auto-restart for %v",
						workerID, worker.Backoff)
				}
			}
			workerMutex.Unlock()

			StopWorker(workerID)
		} else {
			// Success - reset failure counter
			workerMutex.Lock()
			if worker := workers[workerID]; worker != nil {
				worker.StartupFailures = 0
				worker.Backoff = 0
			}
			workerMutex.Unlock()
		}
	}()

	return workerID, nil
}

// StopWorker gracefully stops and removes a worker container
func StopWorker(workerID string) error {
	workerMutex.Lock()
	workerInfo, exists := workers[workerID]
	if !exists {
		workerMutex.Unlock()
		return fmt.Errorf("worker %s not found", workerID)
	}

	// Check if worker is processing a job — never interrupt active inference
	if workerInfo.State == WorkerStateProcessing {
		workerMutex.Unlock()
		return fmt.Errorf("worker %s is processing job %s, cannot stop", workerID, workerInfo.CurrentJobID)
	}

	workerInfo.State = WorkerStateStopping
	containerID := workerInfo.ContainerID
	workerMutex.Unlock()

	// Use container name as fallback for externally-started workers with no ContainerID
	stopTarget := containerID
	if stopTarget == "" {
		stopTarget = workerID
	}
	log.Printf("[WORKER_MANAGER] Stopping worker %s (target: %s)", workerID, stopTarget)

	ctx := context.Background()
	timeout := 30
	stopOptions := container.StopOptions{
		Timeout: &timeout,
	}

	// Stop the container
	if err := dockerCli.ContainerStop(ctx, stopTarget, stopOptions); err != nil {
		log.Printf("[WORKER_MANAGER] Error stopping container %s: %v", stopTarget, err)
	}

	// Remove the container
	removeOptions := container.RemoveOptions{
		Force:         true,
		RemoveVolumes: true,
	}
	if err := dockerCli.ContainerRemove(ctx, stopTarget, removeOptions); err != nil {
		log.Printf("[WORKER_MANAGER] Error removing container %s: %v", stopTarget, err)
	}

	// Remove from workers map
	workerMutex.Lock()
	delete(workers, workerID)
	workerMutex.Unlock()

	log.Printf("[WORKER_MANAGER] Worker %s stopped and removed", workerID)
	return nil
}

// PreemptWorkerGPU tells a WARM worker to immediately offload its model from GPU VRAM
// by calling its /cleanup HTTP endpoint. The container keeps running and continues to
// listen on its queue — it transitions WARM → IDLE without being killed.
//
// Use this instead of StopWorker when you only need to free GPU VRAM; it is faster,
// non-destructive, and allows the preempted worker to reload its model later.
func PreemptWorkerGPU(workerID string) error {
	workerMutex.RLock()
	workerInfo, exists := workers[workerID]
	if !exists {
		workerMutex.RUnlock()
		return fmt.Errorf("worker %s not found", workerID)
	}

	if workerInfo.State != WorkerStateWarm {
		state := workerInfo.State
		workerMutex.RUnlock()
		return fmt.Errorf("worker %s is not in WARM state (current: %s) — cannot preempt", workerID, state)
	}

	cleanupPort := workerInfo.CleanupPort
	if cleanupPort == 0 {
		cleanupPort = 8000
	}
	workerMutex.RUnlock()

	// POST to the worker's cleanup endpoint — blocks until the model is fully unloaded
	url := fmt.Sprintf("http://%s:%d/cleanup", workerID, cleanupPort)
	log.Printf("[PREEMPT] Requesting model offload from WARM worker %s → %s", workerID, url)

	httpClient := &http.Client{Timeout: 60 * time.Second}
	resp, err := httpClient.Post(url, "application/json", nil)
	if err != nil {
		return fmt.Errorf("HTTP call to %s failed: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("cleanup endpoint on %s returned status %d", workerID, resp.StatusCode)
	}

	// Model is now unloaded — GPU VRAM is free. Transition worker WARM → IDLE.
	workerMutex.Lock()
	if worker := workers[workerID]; worker != nil {
		worker.State = WorkerStateIdle
		worker.LastActivityTime = time.Now()
		log.Printf("[PREEMPT] Worker %s: WARM → IDLE (GPU VRAM freed without container restart)", workerID)
	}
	workerMutex.Unlock()

	return nil
}

// GetWorkerForApp returns the worker info for a specific app, or nil if not found
func GetWorkerForApp(appID string) *WorkerInfo {
	workerMutex.RLock()
	defer workerMutex.RUnlock()

	for _, worker := range workers {
		if worker.AppID == appID {
			return worker
		}
	}
	return nil
}

// IsGPUAvailable checks if the GPU is free for a new worker to load its model.
//
// Only WARM and PROCESSING workers actually occupy GPU VRAM.
// READY and IDLE workers have no model in VRAM so the GPU is considered available.
func IsGPUAvailable() (bool, error) {
	workerMutex.RLock()
	defer workerMutex.RUnlock()

	for _, worker := range workers {
		if worker.State == WorkerStateWarm || worker.State == WorkerStateProcessing {
			return false, nil
		}
	}
	return true, nil
}

// getWarmWorkerForOtherApp returns the worker ID of a WARM worker that belongs to a
// different app. WARM workers are the primary preemption target: we can free their
// GPU VRAM cheaply by calling /cleanup without stopping the container.
func getWarmWorkerForOtherApp(appID string) string {
	workerMutex.RLock()
	defer workerMutex.RUnlock()

	for workerID, info := range workers {
		if info.AppID != appID && info.State == WorkerStateWarm {
			return workerID
		}
	}
	return ""
}

// getIdleWorkerForOtherApp returns the worker ID of an IDLE or READY worker that
// belongs to a different app. These workers have no model in VRAM; stopping them
// frees container resources but not GPU VRAM (already free). Used as a fallback
// when there is no WARM worker to preempt.
func getIdleWorkerForOtherApp(appID string) string {
	workerMutex.RLock()
	defer workerMutex.RUnlock()

	for workerID, info := range workers {
		if info.AppID != appID &&
			(info.State == WorkerStateIdle || info.State == WorkerStateReady) {
			return workerID
		}
	}
	return ""
}

// waitForWorkerReady waits for a worker to register in etcd
func waitForWorkerReady(workerID string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)

	for time.Now().Before(deadline) {
		workerMutex.RLock()
		worker, exists := workers[workerID]
		if !exists {
			workerMutex.RUnlock()
			return fmt.Errorf("worker %s disappeared during startup", workerID)
		}

		if worker.State == WorkerStateReady || worker.State == WorkerStateIdle || worker.State == WorkerStateWarm {
			workerMutex.RUnlock()
			log.Printf("[WORKER_MANAGER] Worker %s is ready (state: %s)", workerID, worker.State)
			return nil
		}
		workerMutex.RUnlock()

		time.Sleep(2 * time.Second)
	}

	return fmt.Errorf("worker %s did not become ready within %v", workerID, timeout)
}

// removeExistingContainer removes any existing container with the given name
func removeExistingContainer(name string) error {
	ctx := context.Background()

	// List containers with the specified name
	filterArgs := filters.NewArgs()
	filterArgs.Add("name", name)

	containers, err := dockerCli.ContainerList(ctx, container.ListOptions{
		All:     true,
		Filters: filterArgs,
	})
	if err != nil {
		return fmt.Errorf("failed to list containers: %w", err)
	}

	for _, cont := range containers {
		log.Printf("[WORKER_MANAGER] Removing existing container %s", cont.ID[:12])

		// Stop if running
		timeout := 10
		stopOptions := container.StopOptions{
			Timeout: &timeout,
		}
		dockerCli.ContainerStop(ctx, cont.ID, stopOptions)

		// Remove
		removeOptions := container.RemoveOptions{
			Force:         true,
			RemoveVolumes: true,
		}
		if err := dockerCli.ContainerRemove(ctx, cont.ID, removeOptions); err != nil {
			log.Printf("[WORKER_MANAGER] Error removing container %s: %v", cont.ID[:12], err)
		}
	}

	return nil
}

// GetAllWorkers returns a copy of all workers (for monitoring)
func GetAllWorkers() map[string]WorkerInfo {
	workerMutex.RLock()
	defer workerMutex.RUnlock()

	result := make(map[string]WorkerInfo)
	for id, info := range workers {
		result[id] = *info
	}
	return result
}
