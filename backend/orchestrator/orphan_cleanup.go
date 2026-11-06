package main

import "log"

// cleanupOrphanedJobs fails PENDING/QUEUED/PROCESSING jobs older than 15 minutes
// that no live worker owns. A job is "owned" if a worker reports it as its current
// job, or (for queued jobs) a worker for that app is starting/processing/warm.
func cleanupOrphanedJobs() {
	rows, err := db.Query(`SELECT id, app_id, status FROM jobs
		WHERE status IN ('PENDING','QUEUED','PROCESSING')
		  AND created_at < NOW() - INTERVAL '15 minutes'`)
	if err != nil {
		log.Printf("[CLEANUP] query failed: %v", err)
		return
	}
	type row struct{ id, app, status string }
	var cands []row
	for rows.Next() {
		var r row
		if rows.Scan(&r.id, &r.app, &r.status) == nil {
			cands = append(cands, r)
		}
	}
	rows.Close()
	if len(cands) == 0 {
		return
	}
	cur := map[string]bool{}
	liveApp := map[string]bool{}
	for _, w := range GetAllWorkers() {
		if w.CurrentJobID != "" {
			cur[w.CurrentJobID] = true
		}
		switch w.State {
		case WorkerStateStarting, WorkerStateProcessing, WorkerStateWarm:
			liveApp[w.AppID] = true
		}
	}
	n := 0
	for _, c := range cands {
		if cur[c.id] || (c.status != "PROCESSING" && liveApp[c.app]) {
			continue
		}
		if _, err := db.Exec(`UPDATE jobs SET status='FAILED', completed_at=NOW(),
			error_log='auto-cleaned: no worker owns this job' WHERE id=$1 AND status IN ('PENDING','QUEUED','PROCESSING')`, c.id); err == nil {
			n++
		}
	}
	if n > 0 {
		log.Printf("[CLEANUP] Marked %d orphaned job(s) as FAILED", n)
	}
}
