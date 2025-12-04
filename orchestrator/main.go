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

	// 5. Start HTTP Server
	http.HandleFunc("/submit", submitJobHandler)
	http.HandleFunc("/status/", statusHandler)
	http.HandleFunc("/workers", workersHandler)

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

	// Switch to correct worker before job submission (for local GPU jobs)
	if appConfig.Type == "local" {
		log.Printf("[INFO] Ensuring correct worker is active for app: %s", req.AppID)
		if err := switchWorker(req.AppID); err != nil {
			log.Printf("[ERROR] Failed to switch worker: %v", err)
			http.Error(w, fmt.Sprintf("Failed to prepare worker: %v", err), http.StatusInternalServerError)
			return
		}
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

	log.Printf("[SUCCESS] Worker ready for app: %s", appID)
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