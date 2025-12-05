package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// AdminJobsResponse represents the response for listing jobs
type AdminJobsResponse struct {
	Jobs   []JobInfo `json:"jobs"`
	Total  int       `json:"total"`
	Page   int       `json:"page"`
	Limit  int       `json:"limit"`
	Offset int       `json:"offset"`
}

// JobInfo represents a job with all its details
type JobInfo struct {
	ID          string     `json:"id"`
	AppID       string     `json:"app_id"`
	Status      string     `json:"status"`
	CreatedAt   *time.Time `json:"created_at"`
	StartedAt   *time.Time `json:"started_at"`
	CompletedAt *time.Time `json:"completed_at"`
	WorkerID    *string    `json:"worker_id"`
	Cost        *float64   `json:"cost_estimate"`
	ErrorLog    *string    `json:"error_log"`
	Duration    *float64   `json:"duration_seconds"`
}

// WorkerStatusResponse represents worker status information
type WorkerStatusResponse struct {
	Workers      []WorkerStatusInfo `json:"workers"`
	ActiveWorker *string            `json:"active_worker"`
}

// WorkerStatusInfo represents detailed worker information
type WorkerStatusInfo struct {
	WorkerID     string  `json:"worker_id"`
	AppID        string  `json:"app_id"`
	Status       string  `json:"status"`
	GPUName      *string `json:"gpu_name"`
	VRAMTotal    *int    `json:"vram_total_mb"`
	LastHeartbeat *time.Time `json:"last_heartbeat"`
}

// AdminJobsHandler handles GET /admin/jobs - List jobs with filtering
func adminJobsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Enable CORS for admin dashboard
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Parse query parameters
	status := r.URL.Query().Get("status")
	appID := r.URL.Query().Get("app_id")
	workerID := r.URL.Query().Get("worker_id")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))

	if limit <= 0 {
		limit = 50
	}

	// Build query
	query := `
		SELECT
			id, app_id, status, created_at, started_at, completed_at,
			worker_id, cost_estimate, error_log,
			EXTRACT(EPOCH FROM (completed_at - started_at)) as duration_seconds
		FROM jobs
		WHERE 1=1
	`
	args := []interface{}{}
	argCount := 1

	if status != "" {
		query += fmt.Sprintf(" AND status = $%d", argCount)
		args = append(args, status)
		argCount++
	}

	if appID != "" {
		query += fmt.Sprintf(" AND app_id = $%d", argCount)
		args = append(args, appID)
		argCount++
	}

	if workerID != "" {
		query += fmt.Sprintf(" AND worker_id = $%d", argCount)
		args = append(args, workerID)
		argCount++
	}

	query += " ORDER BY created_at DESC"
	query += fmt.Sprintf(" LIMIT $%d OFFSET $%d", argCount, argCount+1)
	args = append(args, limit, offset)

	// Execute query
	rows, err := db.Query(query, args...)
	if err != nil {
		log.Printf("[ERROR] Failed to query jobs: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	jobs := []JobInfo{}
	for rows.Next() {
		var job JobInfo
		err := rows.Scan(
			&job.ID, &job.AppID, &job.Status, &job.CreatedAt,
			&job.StartedAt, &job.CompletedAt, &job.WorkerID,
			&job.Cost, &job.ErrorLog, &job.Duration,
		)
		if err != nil {
			log.Printf("[ERROR] Failed to scan job: %v", err)
			continue
		}
		jobs = append(jobs, job)
	}

	// Get total count
	countQuery := "SELECT COUNT(*) FROM jobs WHERE 1=1"
	countArgs := []interface{}{}
	countArgNum := 1

	if status != "" {
		countQuery += fmt.Sprintf(" AND status = $%d", countArgNum)
		countArgs = append(countArgs, status)
		countArgNum++
	}
	if appID != "" {
		countQuery += fmt.Sprintf(" AND app_id = $%d", countArgNum)
		countArgs = append(countArgs, appID)
		countArgNum++
	}
	if workerID != "" {
		countQuery += fmt.Sprintf(" AND worker_id = $%d", countArgNum)
		countArgs = append(countArgs, workerID)
	}

	var total int
	db.QueryRow(countQuery, countArgs...).Scan(&total)

	response := AdminJobsResponse{
		Jobs:   jobs,
		Total:  total,
		Page:   offset / limit,
		Limit:  limit,
		Offset: offset,
	}

	json.NewEncoder(w).Encode(response)
}

// AdminCancelJobHandler handles POST /admin/jobs/{id}/cancel
func adminCancelJobHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Extract job ID from path
	path := strings.TrimPrefix(r.URL.Path, "/admin/jobs/")
	jobID := strings.TrimSuffix(path, "/cancel")

	// Update job status to FAILED
	result, err := db.Exec(`
		UPDATE jobs
		SET status = 'FAILED',
		    error_log = 'Cancelled by admin',
		    completed_at = NOW()
		WHERE id = $1
		  AND status IN ('PENDING', 'QUEUED', 'PROCESSING')
	`, jobID)

	if err != nil {
		log.Printf("[ERROR] Failed to cancel job: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	rowsAffected, _ := result.RowsAffected()
	if rowsAffected == 0 {
		http.Error(w, "Job not found or cannot be cancelled", http.StatusNotFound)
		return
	}

	log.Printf("[INFO] Job %s cancelled by admin", jobID)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": true,
		"job_id":  jobID,
		"message": "Job cancelled successfully",
	})
}

// AdminRetryJobHandler handles POST /admin/jobs/{id}/retry
func adminRetryJobHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Extract job ID from path
	path := strings.TrimPrefix(r.URL.Path, "/admin/jobs/")
	originalJobID := strings.TrimSuffix(path, "/retry")

	// Get original job
	var appID string
	var params []byte
	err := db.QueryRow(`
		SELECT app_id, params
		FROM jobs
		WHERE id = $1 AND status = 'FAILED'
	`, originalJobID).Scan(&appID, &params)

	if err != nil {
		log.Printf("[ERROR] Failed to get original job: %v", err)
		http.Error(w, "Job not found or not in FAILED status", http.StatusNotFound)
		return
	}

	// Parse params
	var paramsMap map[string]string
	json.Unmarshal(params, &paramsMap)

	// Validate app exists
	appConfig, exists := appRegistry[appID]
	if !exists {
		http.Error(w, "App not found in registry", http.StatusBadRequest)
		return
	}

	// Create new job
	newJobID := fmt.Sprintf("%s-retry-%d", originalJobID[:8], time.Now().Unix())
	paramsJSON, _ := json.Marshal(paramsMap)

	_, err = db.Exec(`
		INSERT INTO jobs (id, app_id, status, params, created_at)
		VALUES ($1, $2, 'PENDING', $3, NOW())
	`, newJobID, appID, paramsJSON)

	if err != nil {
		log.Printf("[ERROR] Failed to create retry job: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	// Queue the job (simplified - just mark as QUEUED for now)
	db.Exec("UPDATE jobs SET status = 'QUEUED' WHERE id = $1", newJobID)
	rdb.SAdd(ctx, "queues:with_jobs", appConfig.Queue)

	log.Printf("[INFO] Job %s retried as %s", originalJobID, newJobID)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":     true,
		"new_job_id":  newJobID,
		"original_id": originalJobID,
		"message":     "Job retry created successfully",
	})
}

// AdminWorkersStatusHandler handles GET /admin/workers/status
func adminWorkersStatusHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Get workers from in-memory map (populated from etcd)
	workersList := []WorkerStatusInfo{}

	for workerID, workerInfo := range workers {
		// Get additional worker details from etcd
		resp, err := etcdCli.Get(ctx, fmt.Sprintf("/workers/%s", workerID))

		var appID string
		var gpuName *string
		var vramTotal *int

		if err == nil && len(resp.Kvs) > 0 {
			// Parse worker info: "app=sdxl-image-gen,queue=jobs:sdxl,status=ONLINE"
			workerData := string(resp.Kvs[0].Value)
			parts := strings.Split(workerData, ",")
			for _, part := range parts {
				kv := strings.SplitN(part, "=", 2)
				if len(kv) == 2 {
					if kv[0] == "app" {
						appID = kv[1]
					}
				}
			}
		}

		// If we couldn't parse app_id, try to infer from worker_id
		if appID == "" {
			// e.g., "z-image-worker-1" -> "z-image"
			idParts := strings.Split(workerID, "-worker")
			if len(idParts) > 0 {
				appID = idParts[0]
			}
		}

		// Try to get GPU info from latest metrics
		var latestVRAM int
		var latestGPUName string
		err = db.QueryRow(`
			SELECT vram_total_mb, gpu_id
			FROM gpu_metrics
			WHERE worker_id = $1
			ORDER BY time DESC
			LIMIT 1
		`, workerID).Scan(&latestVRAM, &latestGPUName)

		if err == nil && latestVRAM > 0 {
			vramTotal = &latestVRAM
			if latestGPUName != "" {
				gpuName = &latestGPUName
			}
		}

		workersList = append(workersList, WorkerStatusInfo{
			WorkerID:      workerID,
			AppID:         appID,
			Status:        workerInfo.Status,
			GPUName:       gpuName,
			VRAMTotal:     vramTotal,
			LastHeartbeat: &workerInfo.LastHeartbeat,
		})
	}

	// Get active worker from worker_manager state file
	var activeWorker *string
	cmd := exec.Command("cat", "/tmp/gpu_orchestrator_active_worker.txt")
	output, err := cmd.CombinedOutput()
	if err == nil && len(output) > 0 {
		active := strings.TrimSpace(string(output))
		if active != "" {
			activeWorker = &active
		}
	}

	response := WorkerStatusResponse{
		Workers:      workersList,
		ActiveWorker: activeWorker,
	}

	json.NewEncoder(w).Encode(response)
}

// AdminWorkerActionHandler handles worker control actions
func adminWorkerActionHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Parse request body
	var req struct {
		WorkerName string `json:"worker_name"`
		Action     string `json:"action"` // start, stop, switch
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	var cmd *exec.Cmd
	var actionDesc string

	switch req.Action {
	case "start":
		cmd = exec.Command("python3", "/app/scripts/worker_manager.py", "start", req.WorkerName)
		actionDesc = fmt.Sprintf("Starting worker %s", req.WorkerName)
	case "stop":
		cmd = exec.Command("python3", "/app/scripts/worker_manager.py", "stop")
		actionDesc = "Stopping active worker"
	case "switch":
		cmd = exec.Command("python3", "/app/scripts/worker_manager.py", "switch", req.WorkerName)
		actionDesc = fmt.Sprintf("Switching to worker %s", req.WorkerName)
	default:
		http.Error(w, "Invalid action", http.StatusBadRequest)
		return
	}

	log.Printf("[INFO] Admin action: %s", actionDesc)

	// Execute command
	output, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("[ERROR] Worker action failed: %v - %s", err, string(output))
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": false,
			"message": fmt.Sprintf("Action failed: %s", string(output)),
			"error":   err.Error(),
		})
		return
	}

	log.Printf("[INFO] Worker action completed: %s", string(output))
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": true,
		"message": actionDesc + " completed successfully",
		"output":  string(output),
	})
}

// AdminGPUMetricsHandler handles GET /admin/metrics/gpu
func adminGPUMetricsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	workerID := r.URL.Query().Get("worker_id")
	timeRange := r.URL.Query().Get("range")

	if workerID == "" {
		http.Error(w, "worker_id required", http.StatusBadRequest)
		return
	}

	// Calculate time range
	var interval string
	var startTime time.Time

	switch timeRange {
	case "1h":
		startTime = time.Now().Add(-1 * time.Hour)
		interval = "1 minute"
	case "6h":
		startTime = time.Now().Add(-6 * time.Hour)
		interval = "5 minutes"
	case "24h":
		startTime = time.Now().Add(-24 * time.Hour)
		interval = "15 minutes"
	case "7d":
		startTime = time.Now().Add(-7 * 24 * time.Hour)
		interval = "1 hour"
	default:
		startTime = time.Now().Add(-24 * time.Hour)
		interval = "15 minutes"
	}

	// Query time-series data
	query := `
		SELECT
			time_bucket($1, time) AS bucket,
			gpu_id,
			AVG(gpu_utilization) as avg_utilization,
			AVG(vram_used_mb) as avg_vram_used,
			AVG(vram_total_mb) as avg_vram_total,
			AVG(temperature_c) as avg_temperature,
			AVG(power_draw_w) as avg_power
		FROM gpu_metrics
		WHERE worker_id = $2
		  AND time >= $3
		GROUP BY bucket, gpu_id
		ORDER BY bucket, gpu_id
	`

	rows, err := db.Query(query, interval, workerID, startTime)
	if err != nil {
		log.Printf("[ERROR] Failed to query GPU metrics: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type MetricPoint struct {
		Time         time.Time `json:"time"`
		GPUID        int       `json:"gpu_id"`
		Utilization  float64   `json:"utilization"`
		VRAMUsed     float64   `json:"vram_used_mb"`
		VRAMTotal    float64   `json:"vram_total_mb"`
		Temperature  float64   `json:"temperature_c"`
		PowerDraw    float64   `json:"power_draw_w"`
	}

	metrics := []MetricPoint{}
	for rows.Next() {
		var metric MetricPoint
		err := rows.Scan(
			&metric.Time, &metric.GPUID, &metric.Utilization,
			&metric.VRAMUsed, &metric.VRAMTotal, &metric.Temperature,
			&metric.PowerDraw,
		)
		if err != nil {
			log.Printf("[ERROR] Failed to scan metric: %v", err)
			continue
		}
		metrics = append(metrics, metric)
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"worker_id": workerID,
		"range":     timeRange,
		"interval":  interval,
		"metrics":   metrics,
	})
}

// AdminLatestMetricsHandler handles GET /admin/metrics/latest
func adminLatestMetricsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Get latest metrics for all workers
	query := `
		SELECT DISTINCT ON (worker_id, gpu_id)
			worker_id, gpu_id, time,
			gpu_utilization, vram_used_mb, vram_total_mb,
			temperature_c, power_draw_w
		FROM gpu_metrics
		WHERE time >= NOW() - INTERVAL '1 minute'
		ORDER BY worker_id, gpu_id, time DESC
	`

	rows, err := db.Query(query)
	if err != nil {
		log.Printf("[ERROR] Failed to query latest metrics: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type LatestMetric struct {
		WorkerID    string    `json:"worker_id"`
		GPUID       int       `json:"gpu_id"`
		Time        time.Time `json:"time"`
		Utilization *float64  `json:"gpu_utilization"`
		VRAMUsed    *int      `json:"vram_used_mb"`
		VRAMTotal   *int      `json:"vram_total_mb"`
		Temperature *int      `json:"temperature_c"`
		PowerDraw   *int      `json:"power_draw_w"`
	}

	metrics := []LatestMetric{}
	for rows.Next() {
		var metric LatestMetric
		err := rows.Scan(
			&metric.WorkerID, &metric.GPUID, &metric.Time,
			&metric.Utilization, &metric.VRAMUsed, &metric.VRAMTotal,
			&metric.Temperature, &metric.PowerDraw,
		)
		if err != nil {
			log.Printf("[ERROR] Failed to scan latest metric: %v", err)
			continue
		}
		metrics = append(metrics, metric)
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"metrics": metrics,
	})
}

// AdminSummaryHandler handles GET /admin/metrics/summary
func adminSummaryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Get total jobs
	var totalJobs int
	db.QueryRow("SELECT COUNT(*) FROM jobs").Scan(&totalJobs)

	// Get jobs last 24h
	var jobs24h int
	db.QueryRow("SELECT COUNT(*) FROM jobs WHERE created_at >= NOW() - INTERVAL '24 hours'").Scan(&jobs24h)

	// Get total cost
	var totalCost float64
	db.QueryRow("SELECT COALESCE(SUM(cost_estimate), 0) FROM jobs WHERE status = 'COMPLETED'").Scan(&totalCost)

	// Get jobs by status
	rows, _ := db.Query("SELECT status, COUNT(*) FROM jobs GROUP BY status")
	jobsByStatus := make(map[string]int)
	for rows.Next() {
		var status string
		var count int
		rows.Scan(&status, &count)
		jobsByStatus[status] = count
	}
	rows.Close()

	// Get jobs by app
	rows, _ = db.Query("SELECT app_id, COUNT(*) FROM jobs GROUP BY app_id")
	jobsByApp := make(map[string]int)
	for rows.Next() {
		var appID string
		var count int
		rows.Scan(&appID, &count)
		jobsByApp[appID] = count
	}
	rows.Close()

	json.NewEncoder(w).Encode(map[string]interface{}{
		"total_jobs":     totalJobs,
		"jobs_24h":       jobs24h,
		"total_cost":     totalCost,
		"jobs_by_status": jobsByStatus,
		"jobs_by_app":    jobsByApp,
	})
}

// AdminConfigHandler handles GET /admin/config
func adminConfigHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// Return the app registry
	json.NewEncoder(w).Encode(map[string]interface{}{
		"apps": appRegistry,
	})
}
