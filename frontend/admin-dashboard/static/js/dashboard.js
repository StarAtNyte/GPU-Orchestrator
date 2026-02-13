/**
 * Dashboard Overview JavaScript
 * Enhanced with real-time updates, sorting, CSV export, and better UX
 */

// State management
let jobsData = [];
let currentSort = { field: 'created_at', direction: 'desc' };
let refreshInterval = null;
let retryCount = 0;
const MAX_RETRIES = 3;

/**
 * Load dashboard summary data with retry logic
 */
async function loadDashboardData() {
    try {
        // Remove skeleton loaders
        removeSkeletonLoaders();

        // Load all data in parallel
        const [summaryResponse, jobsResponse, workersResponse, configResponse] = await Promise.all([
            fetchWithRetry('api/metrics/summary'),
            fetchWithRetry('api/jobs?limit=10&offset=0'),
            fetchWithRetry('api/workers/status'),
            fetchWithRetry('api/config')
        ]);

        // Update UI components
        if (summaryResponse && summaryResponse.ok) {
            const summary = await summaryResponse.json();
            updateSummaryStats(summary);
        }

        if (jobsResponse && jobsResponse.ok) {
            const jobsResult = await jobsResponse.json();
            jobsData = jobsResult.jobs || [];
            updateRecentJobsTable(jobsData);
        }

        if (workersResponse && workersResponse.ok) {
            const workersData = await workersResponse.json();
            updateWorkerStatus(workersData);
        }

        if (configResponse && configResponse.ok) {
            const configData = await configResponse.json();
            updateAppsDisplay(configData.apps || []);
        }

        // Update last refresh timestamp
        updateLastRefreshTime();

        // Reset retry count on success
        retryCount = 0;

    } catch (error) {
        console.error('Error loading dashboard data:', error);
        showError('Failed to load dashboard data. Retrying...');

        // Implement exponential backoff retry
        if (retryCount < MAX_RETRIES) {
            retryCount++;
            const delay = Math.pow(2, retryCount) * 1000; // 2s, 4s, 8s
            setTimeout(loadDashboardData, delay);
        }
    }
}

/**
 * Fetch with retry logic and timeout
 */
async function fetchWithRetry(url, options = {}, retries = 2) {
    for (let i = 0; i <= retries; i++) {
        try {
            // Create abort controller for timeout
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000);

            const response = await fetch(url, {
                ...options,
                credentials: 'same-origin',  // Include cookies for authentication
                signal: controller.signal
            });

            clearTimeout(timeoutId);
            return response;
        } catch (error) {
            if (i === retries) throw error;
            // Exponential backoff: wait longer between retries
            await new Promise(resolve => setTimeout(resolve, 1000 * (i + 1)));
        }
    }
}

/**
 * Remove skeleton loaders after first load
 */
function removeSkeletonLoaders() {
    document.querySelectorAll('.skeleton-loader').forEach(el => {
        el.classList.remove('skeleton-loader');
    });
}

/**
 * Update last refresh timestamp
 */
function updateLastRefreshTime() {
    const now = new Date();
    const timeString = now.toLocaleTimeString();
    document.getElementById('last-update').textContent = timeString;
}

/**
 * Update summary statistics cards
 */
function updateSummaryStats(summary) {
    const totalJobsEl = document.getElementById('stat-total-jobs');
    const jobs24hEl = document.getElementById('stat-jobs-24h');
    const totalCostEl = document.getElementById('stat-total-cost');

    if (totalJobsEl) totalJobsEl.textContent = summary.total_jobs || '0';
    if (jobs24hEl) jobs24hEl.textContent = summary.jobs_24h || '0';
    if (totalCostEl) totalCostEl.textContent = `$${(summary.total_cost || 0).toFixed(2)}`;
}

/**
 * Update apps display section
 */
function updateAppsDisplay(apps) {
    const container = document.getElementById('apps-container');
    if (!container) return;

    // Convert apps object to array if needed
    let appsArray = [];
    if (Array.isArray(apps)) {
        appsArray = apps;
    } else if (apps && typeof apps === 'object') {
        // Convert object/map to array
        appsArray = Object.values(apps);
    }

    if (!appsArray || appsArray.length === 0) {
        container.innerHTML = `
            <div class="col-span-full text-center py-8 text-gray-400">
                <span class="material-symbols-outlined text-4xl mb-2">apps</span>
                <p>No applications configured</p>
            </div>
        `;
        return;
    }

    // App icon and URL mapping
    const appIcons = {
        'sdxl-image-gen': 'image',
        'z-image': 'photo_camera',
        'panorama-processor': 'panorama',
        'default': 'extension'
    };

    // Map app IDs to their frontend URLs (only for local apps with UIs)
    const appURLs = {
        'sdxl-image-gen': 'http://localhost:7862',
        'z-image': 'http://localhost:7861'
    };

    container.innerHTML = appsArray.map(app => {
        const icon = appIcons[app.ID] || appIcons['default'];
        const appURL = appURLs[app.ID] || app.Endpoint || null;
        const hasURL = appURL && appURL !== '#';

        // Card wrapper - clickable if has URL, otherwise just a div
        const wrapperStart = hasURL
            ? `<a href="${appURL}" target="_blank" class="rounded-xl p-6 border border-[#363168] glassmorphism-card hover:border-[#6366f1] transition-all cursor-pointer block no-underline">`
            : `<div class="rounded-xl p-6 border border-[#363168] glassmorphism-card">`;

        const wrapperEnd = hasURL ? '</a>' : '</div>';

        return `
            ${wrapperStart}
                <div class="flex flex-col items-center text-center">
                    <div class="bg-gradient-to-r from-[#6366f1] to-[#8b5cf6] rounded-full size-20 flex items-center justify-center mb-4">
                        <span class="material-symbols-outlined text-white text-4xl">${icon}</span>
                    </div>
                    <h3 class="text-white font-bold text-lg mb-2">${app.Name || app.ID}</h3>
                    <p class="text-gray-400 text-sm mb-3">${app.Description || 'No description'}</p>
                    <div class="flex flex-col gap-2 text-xs">
                        <div class="flex items-center gap-2 justify-center">
                            <span class="px-2 py-1 ${app.Type === 'local' ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'} rounded-full">
                                ${app.Type === 'local' ? 'Local GPU' : 'Cloud'}
                            </span>
                            ${hasURL
                                ? '<span class="px-2 py-1 bg-green-500/20 text-green-400 rounded-full flex items-center gap-1"><span class="size-1.5 bg-green-400 rounded-full"></span>Live</span>'
                                : '<span class="px-2 py-1 bg-gray-500/20 text-gray-400 rounded-full">API Only</span>'
                            }
                        </div>
                    </div>
                </div>
            ${wrapperEnd}
        `;
    }).join('');
}

/**
 * Update active worker status
 */
function updateWorkerStatus(workersData) {
    // Handle missing or malformed data
    if (!workersData || typeof workersData !== 'object') {
        console.warn('Invalid workers data:', workersData);
        workersData = { workers: [], active_worker: null };
    }

    const activeWorker = workersData.workers?.find(w =>
        w.worker_id === workersData.active_worker || w.status === 'ONLINE'
    );

    const statActiveWorker = document.getElementById('stat-active-worker');
    if (statActiveWorker) {
        if (activeWorker) {
            statActiveWorker.textContent = activeWorker.app_id || activeWorker.worker_id;
        } else {
            statActiveWorker.textContent = 'None';
        }
    }

    const workerStatusCard = document.getElementById('worker-status-card');
    if (!workerStatusCard) return;

    if (activeWorker) {
        workerStatusCard.innerHTML = `
            <div class="flex items-center justify-between mb-4">
                <div>
                    <h4 class="text-white font-bold text-lg">${activeWorker.worker_id}</h4>
                    <p class="text-gray-400">App: ${activeWorker.app_id || 'N/A'}</p>
                </div>
                <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-full text-sm flex items-center gap-1">
                    <span class="size-2 bg-green-400 rounded-full status-pulse"></span>
                    ONLINE
                </span>
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
                    <p class="text-sm text-gray-400">Utilization</p>
                    <p class="text-white font-semibold">${activeWorker.gpu_utilization || 0}%</p>
                </div>
            </div>
        `;
    } else {
        workerStatusCard.innerHTML = `
            <div class="text-center text-gray-400 py-8">
                <span class="material-symbols-outlined text-4xl mb-2">power_off</span>
                <p>No active worker</p>
                <p class="text-sm mt-2">Start a worker from the Workers page</p>
            </div>
        `;
    }
}

/**
 * Update recent jobs table with sorting
 */
function updateRecentJobsTable(jobs) {
    const tbody = document.getElementById('recent-jobs-tbody');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="px-6 py-8 text-center text-gray-400">
                    <span class="material-symbols-outlined text-4xl mb-2">inbox</span>
                    <p>No recent jobs</p>
                </td>
            </tr>
        `;
        return;
    }

    // Apply sorting
    const sortedJobs = sortJobs([...jobs]);

    const statusColors = {
        'COMPLETED': 'bg-green-500/20 text-green-400',
        'FAILED': 'bg-red-500/20 text-red-400',
        'PROCESSING': 'bg-blue-500/20 text-blue-400',
        'QUEUED': 'bg-yellow-500/20 text-yellow-400',
        'PENDING': 'bg-gray-500/20 text-gray-400',
        'CANCELLED': 'bg-orange-500/20 text-orange-400'
    };

    tbody.innerHTML = sortedJobs.map(job => {
        const statusClass = statusColors[job.status] || 'bg-gray-500/20 text-gray-400';
        const createdAt = new Date(job.created_at).toLocaleString();
        const duration = job.duration_seconds
            ? formatDuration(job.duration_seconds)
            : job.status === 'PROCESSING' ? 'Running...' : '-';
        const cost = job.cost_estimate ? `$${job.cost_estimate.toFixed(2)}` : '-';
        const shortId = job.id.substring(0, 8);

        return `
            <tr class="border-b border-[#363168] hover:bg-[#252249]/50 transition-colors">
                <td class="px-6 py-4 font-medium text-white whitespace-nowrap">
                    <div class="flex items-center gap-2">
                        <span class="font-mono">${shortId}...</span>
                        <button onclick="copyToClipboard('${job.id}', this)"
                                class="material-symbols-outlined text-gray-400 hover:text-white text-sm cursor-pointer relative"
                                title="Copy full ID">
                            content_copy
                        </button>
                    </div>
                </td>
                <td class="px-6 py-4">${job.app_id}</td>
                <td class="px-6 py-4">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusClass}">
                        ${job.status}
                    </span>
                </td>
                <td class="px-6 py-4 text-sm">${createdAt}</td>
                <td class="px-6 py-4">${duration}</td>
                <td class="px-6 py-4">${cost}</td>
                <td class="px-6 py-4">
                    <div class="flex items-center gap-2">
                        <button onclick="viewJob('${job.id}')"
                                class="text-blue-400 hover:text-blue-300 text-sm"
                                title="View details">
                            <span class="material-symbols-outlined text-lg">visibility</span>
                        </button>
                        ${job.status === 'PROCESSING' ? `
                            <button onclick="cancelJob('${job.id}')"
                                    class="text-red-400 hover:text-red-300 text-sm"
                                    title="Cancel job">
                                <span class="material-symbols-outlined text-lg">cancel</span>
                            </button>
                        ` : ''}
                        ${job.status === 'FAILED' ? `
                            <button onclick="retryJob('${job.id}')"
                                    class="text-green-400 hover:text-green-300 text-sm"
                                    title="Retry job">
                                <span class="material-symbols-outlined text-lg">refresh</span>
                            </button>
                        ` : ''}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

/**
 * Sort jobs based on current sort settings
 */
function sortJobs(jobs) {
    return jobs.sort((a, b) => {
        let aVal = a[currentSort.field];
        let bVal = b[currentSort.field];

        // Handle null/undefined values
        if (aVal === null || aVal === undefined) return 1;
        if (bVal === null || bVal === undefined) return -1;

        // Convert to comparable values
        if (currentSort.field === 'created_at') {
            aVal = new Date(aVal).getTime();
            bVal = new Date(bVal).getTime();
        }

        if (currentSort.direction === 'asc') {
            return aVal > bVal ? 1 : -1;
        } else {
            return aVal < bVal ? 1 : -1;
        }
    });
}

/**
 * Handle table header click for sorting
 */
function handleSort(field) {
    if (currentSort.field === field) {
        // Toggle direction
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        // New field, default to descending
        currentSort.field = field;
        currentSort.direction = 'desc';
    }

    // Update table
    updateRecentJobsTable(jobsData);

    // Update sort indicators
    updateSortIndicators();
}

/**
 * Update sort indicators in table headers
 */
function updateSortIndicators() {
    document.querySelectorAll('th[data-sort]').forEach(th => {
        const icon = th.querySelector('.material-symbols-outlined');
        const field = th.getAttribute('data-sort');

        if (field === currentSort.field) {
            icon.textContent = currentSort.direction === 'asc' ? 'arrow_upward' : 'arrow_downward';
        } else {
            icon.textContent = 'unfold_more';
        }
    });
}

/**
 * Export jobs to CSV
 */
function exportToCSV() {
    if (!jobsData || jobsData.length === 0) {
        showError('No data to export');
        return;
    }

    // Define CSV headers
    const headers = ['Job ID', 'App ID', 'Status', 'Created At', 'Duration (s)', 'Cost'];

    // Convert jobs to CSV rows
    const rows = jobsData.map(job => [
        job.id,
        job.app_id,
        job.status,
        job.created_at,
        job.duration_seconds || '',
        job.cost_estimate || ''
    ]);

    // Combine headers and rows
    const csvContent = [
        headers.join(','),
        ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    // Create download link
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    link.setAttribute('href', url);
    link.setAttribute('download', `jobs_export_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    showNotification('Jobs exported to CSV', 'success');
}

/**
 * Copy text to clipboard
 */
function copyToClipboard(text, buttonEl) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Job ID copied to clipboard', 'success');

        // Visual feedback
        const originalText = buttonEl.textContent;
        buttonEl.textContent = 'check';
        setTimeout(() => {
            buttonEl.textContent = originalText;
        }, 1000);
    }).catch(err => {
        console.error('Failed to copy:', err);
        showError('Failed to copy to clipboard');
    });
}

/**
 * View job details (navigate to jobs page with filter)
 */
function viewJob(jobId) {
    window.location.href = `jobs?job_id=${jobId}`;
}

/**
 * Cancel a job
 */
async function cancelJob(jobId) {
    if (!confirm('Are you sure you want to cancel this job?')) return;

    try {
        const response = await fetch(`api/jobs/${jobId}/cancel`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }
        });

        if (response.ok) {
            showNotification('Job cancelled successfully', 'success');
            loadDashboardData(); // Reload data
        } else {
            const error = await response.json();
            showError(error.detail || 'Failed to cancel job');
        }
    } catch (error) {
        console.error('Error cancelling job:', error);
        showError('Failed to cancel job');
    }
}

/**
 * Retry a failed job
 */
async function retryJob(jobId) {
    try {
        const response = await fetch(`api/jobs/${jobId}/retry`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }
        });

        if (response.ok) {
            showNotification('Job queued for retry', 'success');
            loadDashboardData(); // Reload data
        } else {
            const error = await response.json();
            showError(error.detail || 'Failed to retry job');
        }
    } catch (error) {
        console.error('Error retrying job:', error);
        showError('Failed to retry job');
    }
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
    // Initial data load
    loadDashboardData();

    // Set up auto-refresh (every 10 seconds)
    refreshInterval = setInterval(loadDashboardData, 10000);

    // Manual refresh button
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadDashboardData();
            showNotification('Dashboard refreshed', 'info');
        });
    }

    // CSV export button
    const exportBtn = document.getElementById('export-csv-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportToCSV);
    }

    // Table header sorting
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const field = th.getAttribute('data-sort');
            handleSort(field);
        });
    });

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        if (refreshInterval) {
            clearInterval(refreshInterval);
        }
    });
});
