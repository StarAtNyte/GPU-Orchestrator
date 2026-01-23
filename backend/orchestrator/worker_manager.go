package main

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/client"
)

// WorkerState represents the current state of a worker
type WorkerState string

const (
	WorkerStateAbsent     WorkerState = "ABSENT"
	WorkerStateStarting   WorkerState = "STARTING"
	WorkerStateReady      WorkerState = "READY"
	WorkerStateProcessing WorkerState = "PROCESSING"
	WorkerStateIdle       WorkerState = "IDLE"
	WorkerStateStopping   WorkerState = "STOPPING"
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
}

var (
	workers            map[string]*WorkerInfo
	workerMutex        sync.RWMutex
	gpuAllocationMutex sync.Mutex
	dockerCli          *client.Client
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

	// Check if worker already exists
	workerMutex.Lock()
	if existing := workers[workerID]; existing != nil {
		workerMutex.Unlock()
		return "", fmt.Errorf("worker %s already exists in state %s", workerID, existing.State)
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
			fmt.Sprintf("POSTGRES_HOST=%s", "postgres"),
			fmt.Sprintf("POSTGRES_USER=%s", "admin"),
			fmt.Sprintf("POSTGRES_PASSWORD=%s", "password123"),
			fmt.Sprintf("POSTGRES_DB=%s", "orchestrator_db"),
			fmt.Sprintf("ETCD_HOST=%s", "etcd"),
			fmt.Sprintf("WORKER_ID=%s", workerID),
			fmt.Sprintf("APP_ID=%s", appID),
			fmt.Sprintf("QUEUE_NAME=%s", appConfig.Queue),
		},
	}

	// Host configuration with GPU support
	hostConfig := &container.HostConfig{
		NetworkMode: "backend_default", // Use the network from docker-compose
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
			StopWorker(workerID)
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

	// Check if worker is processing a job
	if workerInfo.State == WorkerStateProcessing {
		workerMutex.Unlock()
		return fmt.Errorf("worker %s is processing job %s, cannot stop", workerID, workerInfo.CurrentJobID)
	}

	workerInfo.State = WorkerStateStopping
	containerID := workerInfo.ContainerID
	workerMutex.Unlock()

	log.Printf("[WORKER_MANAGER] Stopping worker %s (container: %s)", workerID, containerID[:12])

	ctx := context.Background()
	timeout := 30
	stopOptions := container.StopOptions{
		Timeout: &timeout,
	}

	// Stop the container
	if err := dockerCli.ContainerStop(ctx, containerID, stopOptions); err != nil {
		log.Printf("[WORKER_MANAGER] Error stopping container %s: %v", containerID[:12], err)
	}

	// Remove the container
	removeOptions := container.RemoveOptions{
		Force:         true,
		RemoveVolumes: true,
	}
	if err := dockerCli.ContainerRemove(ctx, containerID, removeOptions); err != nil {
		log.Printf("[WORKER_MANAGER] Error removing container %s: %v", containerID[:12], err)
	}

	// Remove from workers map
	workerMutex.Lock()
	delete(workers, workerID)
	workerMutex.Unlock()

	log.Printf("[WORKER_MANAGER] Worker %s stopped and removed", workerID)
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

// IsGPUAvailable checks if GPU is available for a new worker
func IsGPUAvailable() (bool, error) {
	// Check if any worker is currently running
	workerMutex.RLock()
	hasActiveWorker := false
	for _, worker := range workers {
		if worker.State != WorkerStateAbsent && worker.State != WorkerStateStopping {
			hasActiveWorker = true
			break
		}
	}
	workerMutex.RUnlock()

	// For single-GPU setup, only one worker can run at a time
	if hasActiveWorker {
		return false, nil
	}

	// Check GPU utilization via nvidia-smi (optional additional check)
	// For now, we just check worker count
	return true, nil
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

		if worker.State == WorkerStateReady {
			workerMutex.RUnlock()
			log.Printf("[WORKER_MANAGER] Worker %s is ready", workerID)
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
