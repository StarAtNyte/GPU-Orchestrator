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
	appRegistry map[string]AppConfig // App Registry loaded from YAML
	// workers is declared in worker_manager.go
)

// HTTP Request Payload
type SubmitRequest struct {
	AppID    string            `json:"app_id"`
	Username string            `json:"username"` // User identifier
	Params   map[string]string `json:"params"`   // Generic map
}

// WorkerInfo is defined in worker_manager.go

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

	// 4. Initialize worker manager
	if err := InitWorkerManager(); err != nil {
		log.Fatalf("[ERROR] Worker manager initialization failed: %v", err)
	}
	log.Println("[INFO] Worker manager initialized")

	// 5. Start worker watcher (background goroutine)
	go watchWorkers()

	// 6. Start job timeout monitor (background goroutine)
	go monitorJobTimeouts()

	// 7. Start stream monitor (watches for pending jobs)
	go monitorStreams()

	// 8. Start idle timeout monitor (stops idle workers)
	go monitorIdleWorkers()

	// 9. Start job completion tracker (updates worker state)
	go monitorJobCompletions()

	// 10. Start HTTP Server
	http.HandleFunc("/submit", submitJobHandler)
	http.HandleFunc("/status/", statusHandler)
	http.HandleFunc("/workers", workersHandler)
	http.HandleFunc("/health/gpu", gpuHealthHandler)

	// User history endpoints
	http.HandleFunc("/user/jobs", userJobsHandler)
	http.HandleFunc("/user/jobs/", userJobDetailsHandler)

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
	// Add CORS headers
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle preflight
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

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

	// Validate username
	if req.Username == "" {
		http.Error(w, "Username is required", http.StatusBadRequest)
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
		INSERT INTO jobs (id, app_id, status, params, username, created_at)
		VALUES ($1, $2, $3, $4, $5, NOW())
	`, jobID, req.AppID, "PENDING", paramsJSON, req.Username)
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

		// Try to start worker immediately if none exists
		go func() {
			worker := GetWorkerForApp(req.AppID)
			if worker == nil {
				isAvailable, err := IsGPUAvailable()
				if err != nil {
					log.Printf("[SUBMIT] Error checking GPU availability: %v", err)
					return
				}

				if isAvailable {
					log.Printf("[SUBMIT] Starting worker for app %s", req.AppID)
					_, err := StartWorkerForApp(req.AppID)
					if err != nil {
						log.Printf("[SUBMIT] Worker start failed: %v", err)
					}
				} else {
					log.Printf("[SUBMIT] GPU busy, job %s queued", jobID)
				}
			}
		}()

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
		workerMutex.Lock()
		for _, kv := range resp.Kvs {
			workerID := string(kv.Key)[len("/workers/"):]

			// Check if worker already exists in our registry (started by us)
			if existing := workers[workerID]; existing != nil {
				existing.LastHeartbeat = time.Now()
				if existing.State == WorkerStateStarting {
					existing.State = WorkerStateReady
				}
				log.Printf("[INFO] Updated existing worker: %s (state: %s)", workerID, existing.State)
			} else {
				// External worker (not started by orchestrator)
				workers[workerID] = &WorkerInfo{
					WorkerID:         workerID,
					LastHeartbeat:    time.Now(),
					State:            WorkerStateReady,
					LastActivityTime: time.Now(),
					IdleTimeoutSeconds: 300,
				}
				log.Printf("[INFO] Loaded external worker: %s", workerID)
			}
		}
		workerMutex.Unlock()
		log.Printf("[INFO] Loaded %d existing worker(s) from etcd", len(resp.Kvs))
	}

	// Watch the /workers/ prefix for all worker registrations
	watchChan := etcdCli.Watch(ctx, "/workers/", clientv3.WithPrefix())

	for watchResp := range watchChan {
		for _, event := range watchResp.Events {
			workerID := strings.TrimPrefix(string(event.Kv.Key), "/workers/")

			switch event.Type {
			case clientv3.EventTypePut:
				workerMutex.Lock()

				// Parse worker info from etcd value
				if existing := workers[workerID]; existing != nil {
					existing.LastHeartbeat = time.Now()
					if existing.State == WorkerStateStarting {
						existing.State = WorkerStateReady
						log.Printf("[ETCD] Worker %s transitioned to READY", workerID)
					}
				} else {
					// Externally started worker
					workers[workerID] = &WorkerInfo{
						WorkerID:           workerID,
						LastHeartbeat:      time.Now(),
						State:              WorkerStateReady,
						LastActivityTime:   time.Now(),
						IdleTimeoutSeconds: 300,
					}
					log.Printf("[ETCD] External worker %s registered (state: READY)", workerID)
				}

				workerMutex.Unlock()

			case clientv3.EventTypeDelete:
				workerMutex.Lock()

				workerID := strings.TrimPrefix(string(event.Kv.Key), "/workers/")
				if existing := workers[workerID]; existing != nil {
					if existing.State == WorkerStateProcessing {
						log.Printf("[ETCD] Worker %s crashed during job %s",
							workerID, existing.CurrentJobID)
						if existing.CurrentJobID != "" {
							db.Exec(`UPDATE jobs SET status = 'FAILED',
								error_log = 'Worker crashed', completed_at = NOW()
								WHERE id = $1`, existing.CurrentJobID)
						}
					}
					delete(workers, workerID)
					log.Printf("[ETCD] Worker %s went OFFLINE (previous state: %s)", workerID, existing.State)
				}

				workerMutex.Unlock()
			}
		}
	}
}

// statusHandler returns job status from PostgreSQL
func statusHandler(w http.ResponseWriter, r *http.Request) {
	// Add CORS headers
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle preflight
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

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

// userJobsHandler returns all jobs for a specific user
func userJobsHandler(w http.ResponseWriter, r *http.Request) {
	// Add CORS headers
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle preflight
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get username from query parameter
	username := r.URL.Query().Get("username")
	if username == "" {
		http.Error(w, "Username parameter is required", http.StatusBadRequest)
		return
	}

	// Optional filters
	appID := r.URL.Query().Get("app_id")
	status := r.URL.Query().Get("status")

	// Build query
	query := `
		SELECT id, app_id, status, created_at, started_at, completed_at, params, output, error_log
		FROM jobs
		WHERE username = $1
	`
	args := []interface{}{username}

	if appID != "" {
		query += " AND app_id = $" + fmt.Sprintf("%d", len(args)+1)
		args = append(args, appID)
	}

	if status != "" {
		query += " AND status = $" + fmt.Sprintf("%d", len(args)+1)
		args = append(args, status)
	}

	query += " ORDER BY created_at DESC LIMIT 100"

	rows, err := db.Query(query, args...)
	if err != nil {
		log.Printf("[ERROR] Failed to query user jobs: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	jobs := []map[string]interface{}{}

	for rows.Next() {
		var jobID, appID, status string
		var createdAt, startedAt, completedAt sql.NullTime
		var params, output, errorLog sql.NullString

		err := rows.Scan(&jobID, &appID, &status, &createdAt, &startedAt, &completedAt, &params, &output, &errorLog)
		if err != nil {
			log.Printf("[ERROR] Failed to scan job row: %v", err)
			continue
		}

		job := map[string]interface{}{
			"job_id":  jobID,
			"app_id":  appID,
			"status":  status,
		}

		if createdAt.Valid {
			job["created_at"] = createdAt.Time.Format(time.RFC3339)
		}
		if startedAt.Valid {
			job["started_at"] = startedAt.Time.Format(time.RFC3339)
		}
		if completedAt.Valid {
			job["completed_at"] = completedAt.Time.Format(time.RFC3339)
		}

		// Parse params
		if params.Valid && params.String != "" {
			var paramsData interface{}
			if err := json.Unmarshal([]byte(params.String), &paramsData); err == nil {
				job["params"] = paramsData
			}
		}

		// Parse output (only include if completed)
		if status == "COMPLETED" && output.Valid && output.String != "" {
			var outputData interface{}
			if err := json.Unmarshal([]byte(output.String), &outputData); err == nil {
				job["output"] = outputData
			}
		}

		if errorLog.Valid && errorLog.String != "" {
			job["error_log"] = errorLog.String
		}

		jobs = append(jobs, job)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"username": username,
		"count":    len(jobs),
		"jobs":     jobs,
	})
}

// userJobDetailsHandler returns detailed information about a specific job
func userJobDetailsHandler(w http.ResponseWriter, r *http.Request) {
	// Add CORS headers
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle preflight
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Extract job_id from URL path /user/jobs/{job_id}
	jobID := strings.TrimPrefix(r.URL.Path, "/user/jobs/")
	if jobID == "" {
		http.Error(w, "Missing job_id", http.StatusBadRequest)
		return
	}

	// Get username from query parameter (for authorization)
	username := r.URL.Query().Get("username")
	if username == "" {
		http.Error(w, "Username parameter is required", http.StatusBadRequest)
		return
	}

	var status, appID, jobUsername string
	var createdAt, startedAt, completedAt sql.NullTime
	var params, output, errorLog sql.NullString

	err := db.QueryRow(`
		SELECT status, app_id, username, created_at, started_at, completed_at, params, output, error_log
		FROM jobs
		WHERE id = $1
	`, jobID).Scan(&status, &appID, &jobUsername, &createdAt, &startedAt, &completedAt, &params, &output, &errorLog)

	if err == sql.ErrNoRows {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	} else if err != nil {
		log.Printf("[ERROR] Database error: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	// Verify the job belongs to the requesting user
	if jobUsername != username {
		http.Error(w, "Unauthorized - job does not belong to this user", http.StatusForbidden)
		return
	}

	response := map[string]interface{}{
		"job_id":   jobID,
		"app_id":   appID,
		"status":   status,
		"username": jobUsername,
	}

	if createdAt.Valid {
		response["created_at"] = createdAt.Time.Format(time.RFC3339)
	}
	if startedAt.Valid {
		response["started_at"] = startedAt.Time.Format(time.RFC3339)
	}
	if completedAt.Valid {
		response["completed_at"] = completedAt.Time.Format(time.RFC3339)
	}

	// Parse params
	if params.Valid && params.String != "" {
		var paramsData interface{}
		if err := json.Unmarshal([]byte(params.String), &paramsData); err == nil {
			response["params"] = paramsData
		}
	}

	// Parse output
	if output.Valid && output.String != "" {
		var outputData interface{}
		if err := json.Unmarshal([]byte(output.String), &outputData); err == nil {
			response["result"] = outputData
		}
	}

	if errorLog.Valid && errorLog.String != "" {
		response["error_log"] = errorLog.String
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
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

// getWorkerNameForApp returns the worker name for a given app_id
func getWorkerNameForApp(appID string) string {
	mapping := map[string]string{
		"sdxl-image-gen":   "sdxl-worker",
		"z-image":          "z-image-worker",
		"qwen-image-2512":  "qwen-worker",
	}
	return mapping[appID]
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

// monitorStreams watches Redis streams for pending jobs and starts workers as needed
func monitorStreams() {
	log.Println("[STREAM MONITOR] Starting stream monitor...")
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		// Get all queues with jobs
		queues, err := rdb.SMembers(ctx, "queues:with_jobs").Result()
		if err != nil {
			log.Printf("[STREAM MONITOR] Error reading queues:with_jobs: %v", err)
			continue
		}

		for _, queueName := range queues {
			// Check for pending jobs
			length, err := rdb.XLen(ctx, queueName).Result()
			if err != nil {
				log.Printf("[STREAM MONITOR] Error checking queue %s: %v", queueName, err)
				continue
			}

			if length == 0 {
				rdb.SRem(ctx, "queues:with_jobs", queueName)
				continue
			}

			// Find app for this queue
			appID := getAppIDForQueue(queueName)
			if appID == "" {
				log.Printf("[STREAM MONITOR] No app found for queue %s", queueName)
				continue
			}

			// Check if worker exists
			worker := GetWorkerForApp(appID)
			if worker == nil {
				// Try to start worker
				isAvailable, err := IsGPUAvailable()
				if err != nil {
					log.Printf("[STREAM MONITOR] Error checking GPU availability: %v", err)
					continue
				}

				if isAvailable {
					log.Printf("[STREAM MONITOR] Starting worker for app %s (queue: %s, pending: %d)", appID, queueName, length)
					go StartWorkerForApp(appID)
				} else {
					log.Printf("[STREAM MONITOR] GPU busy, job queued for app %s (pending: %d)", appID, length)
				}
			}
		}
	}
}

// monitorIdleWorkers stops workers that have been idle for too long
func monitorIdleWorkers() {
	log.Println("[IDLE MONITOR] Starting idle worker monitor...")
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		workerMutex.RLock()
		workersToStop := []string{}

		for workerID, info := range workers {
			if info.State == WorkerStateIdle {
				idleDuration := time.Since(info.LastActivityTime)
				timeout := time.Duration(info.IdleTimeoutSeconds) * time.Second

				if idleDuration > timeout {
					log.Printf("[IDLE MONITOR] Worker %s idle for %v (timeout: %v), stopping",
						workerID, idleDuration, timeout)
					workersToStop = append(workersToStop, workerID)
				}
			}
		}
		workerMutex.RUnlock()

		for _, workerID := range workersToStop {
			go StopWorker(workerID)
		}
	}
}

// monitorJobCompletions watches for job completions and updates worker state
func monitorJobCompletions() {
	log.Println("[JOB MONITOR] Starting job completion monitor...")
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		workerMutex.Lock()

		for workerID, info := range workers {
			if info.State == WorkerStateProcessing && info.CurrentJobID != "" {
				var status string
				err := db.QueryRow(
					"SELECT status FROM jobs WHERE id = $1",
					info.CurrentJobID,
				).Scan(&status)

				if err == nil && (status == "COMPLETED" || status == "FAILED") {
					info.State = WorkerStateIdle
					info.LastActivityTime = time.Now()
					info.CurrentJobID = ""
					log.Printf("[JOB MONITOR] Worker %s completed job, now IDLE", workerID)
				}
			}
		}

		workerMutex.Unlock()
	}
}

// getAppIDForQueue returns the app ID for a given queue name
func getAppIDForQueue(queueName string) string {
	for appID, config := range appRegistry {
		if config.Queue == queueName {
			return appID
		}
	}
	return ""
}