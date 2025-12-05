/**
 * Dashboard Overview JavaScript
 * Displays summary statistics and recent activity
 */

/**
 * Load dashboard summary data
 */
async function loadDashboardData() {
    try {
        // Load summary metrics
        const summaryResponse = await fetch('/api/metrics/summary');
        if (summaryResponse.ok) {
            const summary = await summaryResponse.json();
            updateSummaryStats(summary);
        }

        // Load recent jobs
        const jobsResponse = await fetch('/api/jobs?limit=10&offset=0');
        if (jobsResponse.ok) {
            const jobsData = await jobsResponse.json();
            updateRecentJobsTable(jobsData.jobs || []);
        }

        // Load worker status
        const workersResponse = await fetch('/api/workers/status');
        if (workersResponse.ok) {
            const workersData = await workersResponse.json();
            updateWorkerStatus(workersData);
        }

    } catch (error) {
        console.error('Error loading dashboard data:', error);
        showError('Failed to load dashboard data');
    }
}

/**
 * Update summary statistics cards
 */
function updateSummaryStats(summary) {
    document.getElementById('stat-total-jobs').textContent = summary.total_jobs || '0';
    document.getElementById('stat-jobs-24h').textContent = summary.jobs_24h || '0';
    document.getElementById('stat-total-cost').textContent = `$${(summary.total_cost || 0).toFixed(2)}`;
}

/**
 * Update active worker status
 */
function updateWorkerStatus(workersData) {
    const activeWorker = workersData.workers.find(w =>
        w.worker_id === workersData.active_worker || w.status === 'ONLINE'
    );

    const statActiveWorker = document.getElementById('stat-active-worker');
    if (activeWorker) {
        statActiveWorker.textContent = activeWorker.app_id || activeWorker.worker_id;
    } else {
        statActiveWorker.textContent = 'None';
    }

    const workerStatusCard = document.getElementById('worker-status-card');
    if (activeWorker) {
        workerStatusCard.innerHTML = `
            <div class="flex items-center justify-between mb-4">
                <div>
                    <h4 class="text-white font-bold text-lg">${activeWorker.worker_id}</h4>
                    <p class="text-gray-400">App: ${activeWorker.app_id || 'N/A'}</p>
                </div>
                <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-full text-sm">ONLINE</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                    <p class="text-sm text-gray-400">GPU</p>
                    <p class="text-white font-semibold">${activeWorker.gpu_name || 'N/A'}</p>
                </div>
                <div>
                    <p class="text-sm text-gray-400">VRAM</p>
                    <p class="text-white font-semibold">${activeWorker.vram_total_mb ? (activeWorker.vram_total_mb / 1024).toFixed(0) + ' GB' : 'N/A'}</p>
                </div>
                <div>
                    <p class="text-sm text-gray-400">Status</p>
                    <p class="text-green-400 font-semibold">Active</p>
                </div>
            </div>
        `;
    } else {
        workerStatusCard.innerHTML = `
            <div class="text-center text-gray-400 py-8">
                <span class="material-symbols-outlined text-4xl mb-2">power_off</span>
                <p>No active worker</p>
            </div>
        `;
    }
}

/**
 * Update recent jobs table
 */
function updateRecentJobsTable(jobs) {
    const tbody = document.getElementById('recent-jobs-tbody');

    if (jobs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="px-6 py-8 text-center text-gray-400">
                    No recent jobs
                </td>
            </tr>
        `;
        return;
    }

    const statusColors = {
        'COMPLETED': 'bg-green-500/20 text-green-400',
        'FAILED': 'bg-red-500/20 text-red-400',
        'PROCESSING': 'bg-blue-500/20 text-blue-400',
        'QUEUED': 'bg-yellow-500/20 text-yellow-400',
        'PENDING': 'bg-gray-500/20 text-gray-400'
    };

    tbody.innerHTML = jobs.slice(0, 10).map(job => {
        const statusClass = statusColors[job.status] || 'bg-gray-500/20 text-gray-400';
        const createdAt = new Date(job.created_at).toLocaleString();
        const duration = job.duration_seconds
            ? formatDuration(job.duration_seconds)
            : job.status === 'PROCESSING' ? 'Running...' : '-';
        const cost = job.cost_estimate ? `$${job.cost_estimate.toFixed(2)}` : '-';

        return `
            <tr class="border-b border-[#363168]">
                <td class="px-6 py-4 font-medium text-white whitespace-nowrap" title="${job.id}">
                    ${job.id.substring(0, 8)}...
                </td>
                <td class="px-6 py-4">${job.app_id}</td>
                <td class="px-6 py-4">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusClass}">
                        ${job.status}
                    </span>
                </td>
                <td class="px-6 py-4">${createdAt}</td>
                <td class="px-6 py-4">${duration}</td>
                <td class="px-6 py-4">${cost}</td>
            </tr>
        `;
    }).join('');
}

/**
 * Format duration in seconds
 */
function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '-';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
        return `${hours}h ${minutes}m ${secs}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

/**
 * Initialize dashboard
 */
document.addEventListener('DOMContentLoaded', () => {
    loadDashboardData();

    // Auto-refresh every 10 seconds
    setInterval(loadDashboardData, 10000);
});
