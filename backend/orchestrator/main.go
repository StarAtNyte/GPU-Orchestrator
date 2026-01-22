package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/golang-migrate/migrate/v4"
	"github.com/golang-migrate/migrate/v4/database/postgres"
	_ "github.com/golang-migrate/migrate/v4/source/file"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	clientv3 "go.etcd.io/etcd/client/v3"
	"google.golang.org/protobuf/proto"
	pb "gpu-orchestrator/proto"
)

var (
	rdb         *redis.Client
	db          *sql.DB
	etcdCli     *clientv3.Client
	ctx         = context.Background()
	workers     = make(map[string]*WorkerInfo) // In-memory worker registry
	appRegistry map[string]AppConfig           // App Registry loaded from YAML
)

// HTTP Request Payload
type SubmitRequest struct {
	AppID  string            `json:"app_id"`
	Params map[string]string `json:"params"` // Generic map
}

// WorkerInfo tracks worker metadata
type WorkerInfo struct {
	WorkerID      string
	LastHeartbeat time.Time
	Status        string
}

func main() {
	// 1. Load App Registry
	configPath := getEnv("APP_REGISTRY_PATH", "./config/apps.yaml")
	log.Printf("[INFO] Loading app registry from: %s", configPath)
	var err error
	appRegistry, err = LoadAppRegistry(configPath)
	if err != nil {
		log.Fatalf("[ERROR] Failed to load app registry: %v", err)
	}
	log.Printf("[INFO] Loaded %d apps from registry", len(appRegistry))

	// 2. Connect to Redis
	redisAddr := getEnv("REDIS_URL", "localhost:6379")
	rdb = redis.NewClient(&redis.Options{Addr: redisAddr})
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("[ERROR] Redis connection failed: %v", err)
	}
	log.Println("[INFO] Connected to Redis")

	// 2. Connect to PostgreSQL
	pgConnStr := fmt.Sprintf("host=%s user=%s password=%s dbname=%s sslmode=disable",
		getEnv("POSTGRES_HOST", "localhost"),
		getEnv("POSTGRES_USER", "postgres"),
		getEnv("POSTGRES_PASSWORD", "postgres"),
		getEnv("POSTGRES_DB", "gpu_orchestrator"),
	)
	db, err = sql.Open("postgres", pgConnStr)
	if err != nil {
		log.Fatalf("[ERROR] PostgreSQL connection failed: %v", err)
	}
	if err = db.Ping(); err != nil {
		log.Fatalf("[ERROR] PostgreSQL ping failed: %v", err)
	}
	log.Println("[INFO] Connected to PostgreSQL")

	// Run database migrations
	runMigrations(db)

	// 3. Connect to etcd
	etcdEndpoint := getEnv("ETCD_ENDPOINT", "localhost:2379")
	etcdCli, err = clientv3.New(clientv3.Config{
		Endpoints:   []string{etcdEndpoint},
		DialTimeout: 5 * time.Second,
	})
	if err != nil {
		log.Fatalf("[ERROR] etcd connection failed: %v", err)
	}
	defer etcdCli.Close()
	log.Println("[INFO] Connected to etcd")

	// 4. Start worker watcher (background goroutine)
	go watchWorkers()

	// 5. Start worker scheduler (background goroutine)
	go startWorkerScheduler()

	// 6. Start job timeout monitor (background goroutine)
	go monitorJobTimeouts()

	// 7. Start HTTP Server
	http.HandleFunc("/submit", submitJobHandler)
	http.HandleFunc("/status/", statusHandler)
	http.HandleFunc("/workers", workersHandler)
	http.HandleFunc("/health/gpu", gpuHealthHandler)

	// Admin API endpoints
	http.HandleFunc("/admin/jobs", adminJobsHandler)
	http.HandleFunc("/admin/jobs/", func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/cancel") {
			adminCancelJobHandler(w, r)
		} else if strings.HasSuffix(r.URL.Path, "/retry") {
			adminRetryJobHandler(w, r)
		} else {
			http.NotFound(w, r)
		}
	})
	http.HandleFunc("/admin/workers/status", adminWorkersStatusHandler)
	http.HandleFunc("/admin/workers/action", adminWorkerActionHandler)
	http.HandleFunc("/admin/metrics/gpu", adminGPUMetricsHandler)
	http.HandleFunc("/admin/metrics/latest", adminLatestMetricsHandler)
	http.HandleFunc("/admin/metrics/summary", adminSummaryHandler)
	http.HandleFunc("/admin/config", adminConfigHandler)

	log.Println("[INFO] Orchestrator running on :8080")
	log.Fatal(http.ListenAndServe(":8080", nil))
}

// Helper function to get environment variables with defaults
func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func submitJobHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Parse JSON
	var req SubmitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	// Validate App ID
	appConfig, exists := appRegistry[req.AppID]
	if !exists {
		http.Error(w, "Unknown App ID", http.StatusBadRequest)
		return
	}

	jobID := uuid.New().String()

	// Insert job into PostgreSQL (PENDING status)
	paramsJSON, _ := json.Marshal(req.Params)
	_, err := db.Exec(`
		INSERT INTO jobs (id, app_id, status, params, created_at)
		VALUES ($1, $2, $3, $4, NOW())
	`, jobID, req.AppID, "PENDING", paramsJSON)
	if err != nil {
		log.Printf("[ERROR] Failed to insert job into database: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	// ROUTING LOGIC
	if appConfig.Type == "local" {
		// == PATH A: Local GPU Worker ==

		// Create Protobuf
		pbJob := &pb.JobRequest{
			JobId:       jobID,
			AppId:       req.AppID,
			HandlerType: "local_gpu",
			Params:      req.Params, // Pass the map directly
		}

		// Serialize
		data, err := proto.Marshal(pbJob)
		if err != nil {
			http.Error(w, "Proto marshal error", http.StatusInternalServerError)
			return
		}

		// Push to specific Redis Stream
		err = rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: appConfig.Queue,
			Values: map[string]interface{}{"payload": data},
		}).Err()

		if err != nil {
			http.Error(w, "Redis error", http.StatusInternalServerError)
			return
		}

		// Update status to QUEUED
		db.Exec("UPDATE jobs SET status = 'QUEUED' WHERE id = $1", jobID)

		// Notify scheduler that this queue has pending jobs
		rdb.SAdd(ctx, "queues:with_jobs", appConfig.Queue)

		log.Printf("[INFO] [Local] Job %s queued on %s for %s", jobID, appConfig.Queue, req.AppID)

	} else if appConfig.Type == "modal" {
		// == PATH B: Modal Cloud Endpoint ==
		log.Printf("[INFO] [Modal] Proxying job %s to %s", jobID, appConfig.Endpoint)

		// Proxy the request to Modal endpoint
		err := proxyToModal(jobID, appConfig, req.Params)
		if err != nil {
			log.Printf("[ERROR] Failed to proxy to Modal: %v", err)
			db.Exec("UPDATE jobs SET status = 'FAILED', error_log = $1 WHERE id = $2", err.Error(), jobID)
			http.Error(w, "Cloud proxy error", http.StatusInternalServerError)
			return
		}

		// Update status to PROCESSING (since Modal handles it immediately)
		db.Exec("UPDATE jobs SET status = 'PROCESSING' WHERE id = $1", jobID)
		log.Printf("[INFO] [Modal] Job %s sent to cloud endpoint", jobID)
	} else {
		http.Error(w, "Unknown app type", http.StatusInternalServerError)
		return
	}

	// Response
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"job_id": jobID, "status": "queued"})
}

// watchWorkers monitors etcd for worker registration and heartbeats
func watchWorkers() {
	log.Println("[INFO] Watching for workers in etcd...")

	// First, load existing workers from etcd
	resp, err := etcdCli.Get(ctx, "/workers/", clientv3.WithPrefix())
	if err != nil {
		log.Printf("[ERROR] Failed to load existing workers from etcd: %v", err)
	} else {
		for _, kv := range resp.Kvs {
			workerID := string(kv.Key)[len("/workers/"):]
			workers[workerID] = &WorkerInfo{
				WorkerID:      workerID,
				LastHeartbeat: time.Now(),
				Status:        "ONLINE",
			}
			log.Printf("[INFO] Loaded existing worker: %s", workerID)
		}
		log.Printf("[INFO] Loaded %d existing worker(s) from etcd", len(resp.Kvs))
	}

	// Watch the /workers/ prefix for all worker registrations
	watchChan := etcdCli.Watch(ctx, "/workers/", clientv3.WithPrefix())

	for watchResp := range watchChan {
		for _, event := range watchResp.Events {
			workerID := string(event.Kv.Key)[len("/workers/"):]

			switch event.Type {
			case clientv3.EventTypePut:
				// Worker registered or sent heartbeat
				workers[workerID] = &WorkerInfo{
					WorkerID:      workerID,
					LastHeartbeat: time.Now(),
					Status:        "ONLINE",
				}
				log.Printf("[INFO] Worker %s is ONLINE", workerID)

			case clientv3.EventTypeDelete:
				// Worker lease expired (crashed or stopped)
				delete(workers, workerID)
				log.Printf("[WARN] Worker %s went OFFLINE", workerID)
			}
		}
	}
}

// statusHandler returns job status from PostgreSQL
func statusHandler(w http.ResponseWriter, r *http.Request) {
	// Extract job_id from URL path /status/{job_id}
	jobID := r.URL.Path[len("/status/"):]
	if jobID == "" {
		http.Error(w, "Missing job_id", http.StatusBadRequest)
		return
	}

	var status string
	var createdAt, completedAt sql.NullTime
	var output, errorLog sql.NullString
	err := db.QueryRow(`
		SELECT status, created_at, completed_at, output, error_log
		FROM jobs
		WHERE id = $1
	`, jobID).Scan(&status, &createdAt, &completedAt, &output, &errorLog)

	if err == sql.ErrNoRows {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	} else if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	response := map[string]interface{}{
		"job_id": jobID,
		"status": status,
	}
	if createdAt.Valid {
		response["created_at"] = createdAt.Time
	}
	if completedAt.Valid {
		response["completed_at"] = completedAt.Time
	}
	if output.Valid && output.String != "" {
		// Parse JSON output
		var outputData interface{}
		if err := json.Unmarshal([]byte(output.String), &outputData); err == nil {
			response["result"] = outputData
		} else {
			response["result"] = output.String
		}
	}
	if errorLog.Valid && errorLog.String != "" {
		response["error_log"] = errorLog.String
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// workersHandler returns list of active workers
func workersHandler(w http.ResponseWriter, r *http.Request) {
	workerList := make([]WorkerInfo, 0, len(workers))
	for _, worker := range workers {
		workerList = append(workerList, *worker)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"count":   len(workerList),
		"workers": workerList,
	})
}

// runMigrations runs database migrations
func runMigrations(db *sql.DB) {
	driver, err := postgres.WithInstance(db, &postgres.Config{})
	if err != nil {
		log.Fatalf("[ERROR] Could not create migration driver: %v", err)
	}

	m, err := migrate.NewWithDatabaseInstance(
		"file://migrations",
		"postgres", driver)
	if err != nil {
		log.Fatalf("[ERROR] Could not create migrate instance: %v", err)
	}

	if err := m.Up(); err != nil && err != migrate.ErrNoChange {
		log.Fatalf("[ERROR] Migration failed: %v", err)
	}

	if err == migrate.ErrNoChange {
		log.Println("[INFO] Database schema is up to date")
	} else {
		log.Println("[INFO] Database migrations applied successfully")
	}
}

// switchWorker calls the worker manager to switch to the correct worker
func switchWorker(appID string) error {
	// Call python worker manager script
	cmd := exec.Command("python3", "/app/scripts/worker_manager.py", "app", appID)
	output, err := cmd.CombinedOutput()

	if err != nil {
		return fmt.Errorf("worker manager error: %s - %v", string(output), err)
	}

	// Update active worker tracking
	workerName := getWorkerNameForApp(appID)
	setCurrentActiveWorker(workerName)

	// Track when worker started (for idle timeout calculation)
	rdb.Set(ctx, "orchestrator:worker_started_at", time.Now().Format(time.RFC3339), 0)

	log.Printf("[SUCCESS] Worker ready for app: %s", appID)
	return nil
}

// startWorkerScheduler runs the worker scheduler loop in background
func startWorkerScheduler() {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("[SCHEDULER] Panic recovered: %v", r)
		}
	}()

	log.Println("[SCHEDULER] Starting worker scheduler...")
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	log.Println("[SCHEDULER] Ticker created, entering loop...")
	for range ticker.C {
		log.Println("[SCHEDULER] Ticker fired, calling scheduleNextWorker()")
		func() {
			defer func() {
				if r := recover(); r != nil {
					log.Printf("[SCHEDULER] Panic in scheduleNextWorker: %v", r)
				}
			}()
			scheduleNextWorker()
		}()
	}
	log.Println("[SCHEDULER] Scheduler loop exited!")
}

// scheduleNextWorker checks queues and switches workers intelligently
func scheduleNextWorker() {
	log.Println("[SCHEDULER] Tick - checking for work...")
	// 1. Get current active worker
	currentWorker := getCurrentActiveWorker()
	log.Printf("[SCHEDULER DEBUG] Current worker: '%s'", currentWorker)

	// 2. If current worker is busy, don't interrupt
	if currentWorker != "" && isWorkerBusy(currentWorker) {
		log.Printf("[SCHEDULER DEBUG] Worker %s is busy, skipping", currentWorker)
		return
	}

	// 3. Check all queues for pending jobs (priority order)
	type queueInfo struct {
		appID      string
		queue      string
		length     int64
		workerName string
	}

	var queuesWithJobs []queueInfo

	for appID, appConfig := range appRegistry {
		if appConfig.Type != "local" {
			continue
		}

		// Check for PENDING messages in the consumer group
		// This ensures we only count unprocessed jobs, not acknowledged/stuck messages
		groupName := appID + "-workers" // Consumer group naming: {app-id}-workers

		pending, err := rdb.XPending(ctx, appConfig.Queue, groupName).Result()
		if err != nil {
			// Consumer group might not exist yet, check stream length as fallback
			queueLength, err := rdb.XLen(ctx, appConfig.Queue).Result()
			if err != nil {
				log.Printf("[SCHEDULER] Error checking queue %s: %v", appConfig.Queue, err)
				continue
			}
			// Only act if there are messages in a new queue
			if queueLength > 0 {
				workerName := getWorkerNameForApp(appID)
				queuesWithJobs = append(queuesWithJobs, queueInfo{
					appID:      appID,
					queue:      appConfig.Queue,
					length:     queueLength,
					workerName: workerName,
				})
			}
			continue
		}

		// Count ONLY pending (unacknowledged) messages
		pendingCount := pending.Count

		// ALSO check for messages waiting to be consumed (lag)
		// Lag means messages are in the stream but haven't been read yet
		// This happens when no worker is active to consume them
		totalJobsWaiting := pendingCount
		if pending.Count == 0 {
			// Check stream length to find unconsumed messages
			queueLength, err := rdb.XLen(ctx, appConfig.Queue).Result()
			if err == nil && queueLength > 0 {
				// Get consumer group info to check lag
				groupInfo, err := rdb.XInfoGroups(ctx, appConfig.Queue).Result()
				if err == nil && len(groupInfo) > 0 {
					for _, group := range groupInfo {
						if group.Name == groupName {
							totalJobsWaiting = group.Lag
							break
						}
					}
				}
			}
		}

		if totalJobsWaiting > 0 {
			workerName := getWorkerNameForApp(appID)
			queuesWithJobs = append(queuesWithJobs, queueInfo{
				appID:      appID,
				queue:      appConfig.Queue,
				length:     totalJobsWaiting,
				workerName: workerName,
			})
		} else {
			// No pending jobs, remove from set
			rdb.SRem(ctx, "queues:with_jobs", appConfig.Queue)
		}
	}

	// 4. If we found jobs in queues
	if len(queuesWithJobs) > 0 {
		// Pick the first queue with jobs (could add priority logic here)
		selected := queuesWithJobs[0]

		// Only switch if it's a different worker
		if selected.workerName != currentWorker {
			log.Printf("[SCHEDULER] Switching to %s for app %s (queue: %s, pending: %d jobs)",
				selected.workerName, selected.appID, selected.queue, selected.length)

			if err := switchWorker(selected.appID); err != nil {
				log.Printf("[SCHEDULER] Failed to switch worker: %v", err)
			}
		}
		return
	}

	// 5. No jobs in any queue - handle idle timeout
	if currentWorker != "" {
		idleTime := getWorkerIdleTime(currentWorker)
		idleTimeout := 300 * time.Second // 5 minutes from config

		log.Printf("[SCHEDULER DEBUG] Worker %s idle time: %v (timeout: %v)", currentWorker, idleTime, idleTimeout)

		if idleTime > idleTimeout {
			log.Printf("[SCHEDULER] Stopping idle worker: %s (idle for %v)", currentWorker, idleTime)
			if err := stopAllWorkers(); err != nil {
				log.Printf("[SCHEDULER] Failed to stop idle worker: %v", err)
			}
		}
	}
}

// getCurrentActiveWorker returns the currently active worker name
func getCurrentActiveWorker() string {
	// First check Redis
	val, err := rdb.Get(ctx, "orchestrator:active_worker").Result()
	if err == nil && val != "" {
		return val
	}

	// Fallback: check worker_manager state file (in case orchestrator restarted)
	cmd := exec.Command("cat", "/tmp/gpu_orchestrator_active_worker.txt")
	output, err := cmd.CombinedOutput()
	if err == nil && len(output) > 0 {
		active := strings.TrimSpace(string(output))
		if active != "" {
			// Sync Redis state
			setCurrentActiveWorker(active)

			// Also sync worker_started_at if not set
			_, err := rdb.Get(ctx, "orchestrator:worker_started_at").Result()
			if err != nil {
				// Try to get container start time from Docker
				containerName := active + "-1" // Assumes naming convention
				inspectCmd := exec.Command("docker", "inspect", containerName, "--format={{.State.StartedAt}}")
				inspectOut, err := inspectCmd.CombinedOutput()
				if err == nil && len(inspectOut) > 0 {
					startedAtStr := strings.TrimSpace(string(inspectOut))
					// Parse and convert to RFC3339
					startTime, err := time.Parse(time.RFC3339, startedAtStr)
					if err == nil {
						rdb.Set(ctx, "orchestrator:worker_started_at", startTime.Format(time.RFC3339), 0)
						log.Printf("[SCHEDULER] Synced worker_started_at for %s: %s", active, startTime.Format(time.RFC3339))
					}
				}
			}

			return active
		}
	}

	return ""
}

// setCurrentActiveWorker sets the active worker name
func setCurrentActiveWorker(workerName string) {
	if workerName == "" {
		rdb.Del(ctx, "orchestrator:active_worker")
		rdb.Del(ctx, "orchestrator:worker_started_at")
	} else {
		rdb.Set(ctx, "orchestrator:active_worker", workerName, 0)
	}
}

// isWorkerBusy checks if worker is currently processing a job
func isWorkerBusy(workerName string) bool {
	// Check if worker has recently marked itself as active
	key := fmt.Sprintf("worker:%s:last_active", workerName)
	val, err := rdb.Get(ctx, key).Result()
	if err != nil {
		// Key doesn't exist or expired = worker is idle
		return false
	}

	// Parse the timestamp
	lastActiveTime, err := time.Parse(time.RFC3339, val)
	if err != nil {
		return false
	}

	// Consider busy if active within last 60 seconds
	return time.Since(lastActiveTime) < 60*time.Second
}

// getWorkerIdleTime returns how long the worker has been idle
func getWorkerIdleTime(workerName string) time.Duration {
	// Query database for last completed job for this worker
	var lastCompletedAt sql.NullTime
	err := db.QueryRow(`
		SELECT MAX(completed_at)
		FROM jobs
		WHERE status IN ('COMPLETED', 'FAILED')
		  AND worker_id LIKE $1
	`, workerName+"%").Scan(&lastCompletedAt)

	if err != nil || !lastCompletedAt.Valid {
		// No completed jobs found - check when worker started
		val, err := rdb.Get(ctx, "orchestrator:worker_started_at").Result()
		if err != nil {
			// No record, assume just started, return 0 to prevent premature shutdown
			log.Printf("[SCHEDULER DEBUG] No worker_started_at found for %s, returning 0", workerName)
			return 0
		}

		startTime, err := time.Parse(time.RFC3339, val)
		if err != nil {
			log.Printf("[SCHEDULER DEBUG] Failed to parse worker_started_at for %s: %v", workerName, err)
			return 0
		}

		idleTime := time.Since(startTime)
		log.Printf("[SCHEDULER DEBUG] Worker %s has no completed jobs, using start time. Idle: %v", workerName, idleTime)
		return idleTime
	}

	idleTime := time.Since(lastCompletedAt.Time)
	log.Printf("[SCHEDULER DEBUG] Worker %s last completed job at %v. Idle: %v", workerName, lastCompletedAt.Time, idleTime)
	return idleTime
}

// getWorkerNameForApp returns the worker name for a given app_id
func getWorkerNameForApp(appID string) string {
	// This maps app_id to worker name based on workers.yaml
	// For now, simple mapping (could load from config)
	mapping := map[string]string{
		"sdxl-image-gen": "sdxl-worker",
		"z-image":        "z-image-worker",
	}
	return mapping[appID]
}

// stopAllWorkers stops all GPU workers
func stopAllWorkers() error {
	cmd := exec.Command("python3", "/app/scripts/worker_manager.py", "stop")
	output, err := cmd.CombinedOutput()

	if err != nil {
		return fmt.Errorf("worker manager error: %s - %v", string(output), err)
	}

	setCurrentActiveWorker("")
	// Clear worker start time tracking
	rdb.Del(ctx, "orchestrator:worker_started_at")

	log.Printf("[SUCCESS] All workers stopped")
	return nil
}

// proxyToModal forwards a job request to a Modal endpoint
func proxyToModal(jobID string, appConfig AppConfig, params map[string]string) error {
	// Prepare request payload
	payload := map[string]interface{}{
		"job_id": jobID,
		"params": params,
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal payload: %w", err)
	}

	// Create HTTP request
	timeout := time.Duration(appConfig.TimeoutSeconds) * time.Second
	if appConfig.TimeoutSeconds == 0 {
		timeout = 300 * time.Second // Default 5 minutes
	}

	client := &http.Client{
		Timeout: timeout,
	}

	req, err := http.NewRequest("POST", appConfig.Endpoint, bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Job-ID", jobID)
	req.Header.Set("X-App-ID", appConfig.ID)

	// Send request
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to send request: %w", err)
	}
	defer resp.Body.Close()

	// Read response
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read response: %w", err)
	}

	// Check status code
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("modal endpoint returned status %d: %s", resp.StatusCode, string(body))
	}

	log.Printf("[INFO] Modal response for job %s: %s", jobID, string(body))
	return nil
}

// gpuHealthHandler returns current GPU health status for polling
func gpuHealthHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	// Get GPU info using nvidia-smi
	gpuHealth := getGPUHealth()
	json.NewEncoder(w).Encode(gpuHealth)
}

// getGPUHealth queries nvidia-smi for GPU status
func getGPUHealth() map[string]interface{} {
	// Query nvidia-smi for GPU memory info
	cmd := exec.Command("nvidia-smi", "--query-gpu=memory.free,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits")
	output, err := cmd.CombinedOutput()

	if err != nil {
		log.Printf("[HEALTH] nvidia-smi failed: %v", err)
		return map[string]interface{}{
			"status":       "error",
			"error":        fmt.Sprintf("Failed to query GPU: %v", err),
			"timestamp":    time.Now().Format(time.RFC3339),
			"is_available": false,
		}
	}

	// Parse output: "free_mb, used_mb, total_mb, utilization_percent"
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	if len(lines) == 0 {
		return map[string]interface{}{
			"status":       "error",
			"error":        "No GPU data returned",
			"timestamp":    time.Now().Format(time.RFC3339),
			"is_available": false,
		}
	}

	// Parse the first GPU (for multi-GPU, you could expand this)
	parts := strings.Split(strings.TrimSpace(lines[0]), ",")
	if len(parts) < 4 {
		return map[string]interface{}{
			"status":       "error",
			"error":        "Unexpected nvidia-smi output format",
			"timestamp":    time.Now().Format(time.RFC3339),
			"is_available": false,
		}
	}

	freeMB := strings.TrimSpace(parts[0])
	usedMB := strings.TrimSpace(parts[1])
	totalMB := strings.TrimSpace(parts[2])
	utilization := strings.TrimSpace(parts[3])

	freeGB := 0.0
	usedGB := 0.0
	totalGB := 0.0
	utilPercent := 0.0

	fmt.Sscanf(freeMB, "%f", &freeGB)
	fmt.Sscanf(usedMB, "%f", &usedGB)
	fmt.Sscanf(totalMB, "%f", &totalGB)
	fmt.Sscanf(utilization, "%f", &utilPercent)

	freeGB /= 1024.0
	usedGB /= 1024.0
	totalGB /= 1024.0

	// Determine if GPU is busy (>5% utilization)
	isBusy := utilPercent > 5.0

	// Check if there are any PROCESSING jobs
	var activeJobs int
	db.QueryRow("SELECT COUNT(*) FROM jobs WHERE status = 'PROCESSING'").Scan(&activeJobs)

	return map[string]interface{}{
		"status":          "ok",
		"timestamp":       time.Now().Format(time.RFC3339),
		"is_available":    !isBusy && activeJobs == 0,
		"is_busy":         isBusy,
		"active_jobs":     activeJobs,
		"free_vram_gb":    fmt.Sprintf("%.2f", freeGB),
		"used_vram_gb":    fmt.Sprintf("%.2f", usedGB),
		"total_vram_gb":   fmt.Sprintf("%.2f", totalGB),
		"utilization_pct": fmt.Sprintf("%.1f", utilPercent),
	}
}

// monitorJobTimeouts runs a background job to mark stale QUEUED/PROCESSING jobs as FAILED
func monitorJobTimeouts() {
	log.Println("[TIMEOUT MONITOR] Starting job timeout monitor...")
	ticker := time.NewTicker(30 * time.Second) // Check every 30 seconds
	defer ticker.Stop()

	for range ticker.C {
		// Timeout for QUEUED jobs: 10 minutes
		result, err := db.Exec(`
			UPDATE jobs
			SET status = 'FAILED',
			    error_log = 'Job timed out - no worker available within 10 minutes',
			    completed_at = NOW()
			WHERE status = 'QUEUED'
			  AND created_at < NOW() - INTERVAL '10 minutes'
		`)

		if err != nil {
			log.Printf("[TIMEOUT MONITOR] Error updating queued jobs: %v", err)
		} else {
			rowsAffected, _ := result.RowsAffected()
			if rowsAffected > 0 {
				log.Printf("[TIMEOUT MONITOR] Marked %d QUEUED jobs as FAILED (timeout: 10 minutes)", rowsAffected)
			}
		}

		// Timeout for PROCESSING jobs: 30 minutes (in case worker crashed without updating)
		result, err = db.Exec(`
			UPDATE jobs
			SET status = 'FAILED',
			    error_log = 'Job timed out - processing exceeded 30 minutes',
			    completed_at = NOW()
			WHERE status = 'PROCESSING'
			  AND started_at IS NOT NULL
			  AND started_at < NOW() - INTERVAL '30 minutes'
		`)

		if err != nil {
			log.Printf("[TIMEOUT MONITOR] Error updating processing jobs: %v", err)
		} else {
			rowsAffected, _ := result.RowsAffected()
			if rowsAffected > 0 {
				log.Printf("[TIMEOUT MONITOR] Marked %d PROCESSING jobs as FAILED (timeout: 30 minutes)", rowsAffected)
			}
		}
	}
}