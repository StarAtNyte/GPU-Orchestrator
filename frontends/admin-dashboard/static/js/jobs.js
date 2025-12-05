/**
 * Jobs Management JavaScript
 * Handles job listing, filtering, canceling, and retrying
 */

let currentPage = 0;
let pageSize = 50;
let currentFilters = {
    status: '',
    app_id: '',
    search: ''
};
let allJobsData = {}; // Store full job data for modal display

// Status color mapping
const statusColors = {
    'COMPLETED': 'bg-green-500/20 text-green-400',
    'FAILED': 'bg-red-500/20 text-red-400',
    'PROCESSING': 'bg-blue-500/20 text-blue-400',
    'QUEUED': 'bg-yellow-500/20 text-yellow-400',
    'PENDING': 'bg-gray-500/20 text-gray-400'
};

/**
 * Load jobs from API
 */
async function loadJobs() {
    const tbody = document.getElementById('jobs-tbody');
    const paginationInfo = document.getElementById('pagination-info');
    const btnPrev = document.getElementById('btn-prev');
    const btnNext = document.getElementById('btn-next');

    try {
        const params = new URLSearchParams({
            status: currentFilters.status,
            app_id: currentFilters.app_id,
            limit: pageSize,
            offset: currentPage * pageSize
        });

        const response = await fetch(`/api/jobs?${params}`);
        if (!response.ok) throw new Error('Failed to fetch jobs');

        const data = await response.json();

        // Filter by search term if provided (client-side)
        let jobs = data.jobs || [];
        if (currentFilters.search) {
            jobs = jobs.filter(job =>
                job.id.toLowerCase().includes(currentFilters.search.toLowerCase())
            );
        }

        // Store job data for modal access
        jobs.forEach(job => {
            allJobsData[job.id] = job;
        });

        // Update table
        if (jobs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="px-6 py-8 text-center text-gray-400">
                        No jobs found
                    </td>
                </tr>
            `;
        } else {
            tbody.innerHTML = jobs.map(job => createJobRow(job)).join('');
        }

        // Update pagination
        const showing = jobs.length;
        const total = data.total || 0;
        const start = currentPage * pageSize + 1;
        const end = start + showing - 1;

        paginationInfo.textContent = `Showing ${start}-${end} of ${total} jobs`;

        // Update pagination buttons
        btnPrev.disabled = currentPage === 0;
        btnNext.disabled = (currentPage + 1) * pageSize >= total;

    } catch (error) {
        console.error('Error loading jobs:', error);
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="px-6 py-8 text-center text-red-400">
                    Error loading jobs: ${error.message}
                </td>
            </tr>
        `;
    }
}

/**
 * Create job table row HTML
 */
function createJobRow(job) {
    const statusClass = statusColors[job.status] || 'bg-gray-500/20 text-gray-400';
    const duration = job.duration_seconds
        ? formatDuration(job.duration_seconds)
        : job.status === 'PROCESSING' ? 'In progress...' : '-';
    const cost = job.cost_estimate ? `$${job.cost_estimate.toFixed(2)}` : '-';
    const createdAt = new Date(job.created_at).toLocaleString();
    const workerShort = job.worker_id ? job.worker_id.substring(0, 15) : '-';

    // Action buttons based on status
    let actions = '';
    if (job.status === 'PROCESSING' || job.status === 'QUEUED' || job.status === 'PENDING') {
        actions = `
            <button onclick="cancelJob('${job.id}')" class="px-2 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30">
                Cancel
            </button>
        `;
    } else if (job.status === 'FAILED') {
        actions = `
            <button onclick="retryJob('${job.id}')" class="px-2 py-1 text-xs bg-blue-500/20 text-blue-400 rounded hover:bg-blue-500/30">
                Retry
            </button>
        `;
    }

    return `
        <tr class="border-b border-[#363168] hover:bg-[#252249]/30 cursor-pointer transition-colors" onclick="showJobDetails('${job.id}')">
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
            <td class="px-6 py-4" title="${job.worker_id || ''}">${workerShort}</td>
            <td class="px-6 py-4">${cost}</td>
            <td class="px-6 py-4" onclick="event.stopPropagation()">
                ${actions}
            </td>
        </tr>
    `;
}

/**
 * Format duration in seconds to human-readable string
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
 * Cancel a job
 */
async function cancelJob(jobId) {
    if (!confirm(`Cancel job ${jobId.substring(0, 8)}...?`)) {
        return;
    }

    try {
        const response = await fetch(`/api/jobs/${jobId}/cancel`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to cancel job');
        }

        const result = await response.json();
        showNotification('Job cancelled successfully', 'success');
        loadJobs(); // Reload jobs list
    } catch (error) {
        console.error('Error cancelling job:', error);
        showError(`Failed to cancel job: ${error.message}`);
    }
}

/**
 * Retry a failed job
 */
async function retryJob(jobId) {
    if (!confirm(`Retry job ${jobId.substring(0, 8)}...?`)) {
        return;
    }

    try {
        const response = await fetch(`/api/jobs/${jobId}/retry`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to retry job');
        }

        const result = await response.json();
        showNotification(`Job retry created: ${result.new_job_id}`, 'success');
        loadJobs(); // Reload jobs list
    } catch (error) {
        console.error('Error retrying job:', error);
        showError(`Failed to retry job: ${error.message}`);
    }
}

/**
 * Apply filters
 */
function applyFilters() {
    currentFilters.status = document.getElementById('filter-status').value;
    currentFilters.app_id = document.getElementById('filter-app').value;
    currentFilters.search = document.getElementById('search-job-id').value;
    pageSize = parseInt(document.getElementById('filter-limit').value) || 50;
    currentPage = 0; // Reset to first page
    loadJobs();
}

/**
 * Next page
 */
function nextPage() {
    currentPage++;
    loadJobs();
}

/**
 * Previous page
 */
function previousPage() {
    if (currentPage > 0) {
        currentPage--;
        loadJobs();
    }
}

/**
 * Load available apps for filter dropdown
 */
async function loadApps() {
    try {
        // Get unique app IDs from jobs (simplified approach)
        const response = await fetch('/api/jobs?limit=1000');
        if (!response.ok) return;

        const data = await response.json();
        const apps = [...new Set(data.jobs.map(job => job.app_id))];

        const filterApp = document.getElementById('filter-app');
        apps.forEach(appId => {
            const option = document.createElement('option');
            option.value = appId;
            option.textContent = appId;
            filterApp.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading apps:', error);
    }
}

/**
 * Initialize real-time updates with SSE
 */
function initRealTimeUpdates() {
    // Uncomment to enable real-time updates via SSE
    // const eventSource = new EventSource('/stream/jobs');
    // eventSource.onmessage = (event) => {
    //     const data = JSON.parse(event.data);
    //     // Update table if on first page without filters
    //     if (currentPage === 0 && !currentFilters.status && !currentFilters.app_id) {
    //         loadJobs();
    //     }
    // };
    // eventSource.onerror = () => {
    //     eventSource.close();
    // };
}

/**
 * Show job details in modal
 */
function showJobDetails(jobId) {
    const job = allJobsData[jobId];
    if (!job) {
        showError('Job details not found');
        return;
    }

    const modal = document.getElementById('job-detail-modal');
    const modalJobId = document.getElementById('modal-job-id');
    const modalContent = document.getElementById('modal-job-content');

    modalJobId.textContent = job.id;

    // Build job details HTML
    const statusClass = statusColors[job.status] || 'bg-gray-500/20 text-gray-400';
    const duration = job.duration_seconds ? formatDuration(job.duration_seconds) : '-';
    const cost = job.cost_estimate ? `$${job.cost_estimate.toFixed(2)}` : '-';
    const createdAt = new Date(job.created_at).toLocaleString();
    const startedAt = job.started_at ? new Date(job.started_at).toLocaleString() : '-';
    const completedAt = job.completed_at ? new Date(job.completed_at).toLocaleString() : '-';

    let actionsHtml = '';
    if (job.status === 'PROCESSING' || job.status === 'QUEUED' || job.status === 'PENDING') {
        actionsHtml = `
            <button onclick="cancelJob('${job.id}')" class="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30">
                Cancel Job
            </button>
        `;
    } else if (job.status === 'FAILED') {
        actionsHtml = `
            <button onclick="retryJob('${job.id}')" class="px-4 py-2 bg-blue-500/20 text-blue-400 rounded-lg hover:bg-blue-500/30">
                Retry Job
            </button>
        `;
    }

    modalContent.innerHTML = `
        <div class="grid grid-cols-2 gap-6">
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Status</h3>
                <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${statusClass}">
                    ${job.status}
                </span>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">App ID</h3>
                <p class="text-white">${job.app_id}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Worker ID</h3>
                <p class="text-white font-mono text-sm">${job.worker_id || '-'}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Duration</h3>
                <p class="text-white">${duration}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Cost Estimate</h3>
                <p class="text-white">${cost}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Priority</h3>
                <p class="text-white">${job.priority || 0}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Created At</h3>
                <p class="text-white text-sm">${createdAt}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Started At</h3>
                <p class="text-white text-sm">${startedAt}</p>
            </div>
            <div>
                <h3 class="text-sm font-medium text-gray-400 mb-1">Completed At</h3>
                <p class="text-white text-sm">${completedAt}</p>
            </div>
        </div>

        ${job.error_message ? `
        <div class="mt-6">
            <h3 class="text-sm font-medium text-gray-400 mb-2">Error Message</h3>
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                <p class="text-red-400 text-sm font-mono whitespace-pre-wrap">${job.error_message}</p>
            </div>
        </div>
        ` : ''}

        ${job.input_data ? `
        <div class="mt-6">
            <h3 class="text-sm font-medium text-gray-400 mb-2">Input Data</h3>
            <div class="bg-[#252249] border border-[#363168] rounded-lg p-4">
                <pre class="text-gray-300 text-sm overflow-x-auto">${JSON.stringify(job.input_data, null, 2)}</pre>
            </div>
        </div>
        ` : ''}

        ${job.result ? `
        <div class="mt-6">
            <h3 class="text-sm font-medium text-gray-400 mb-2">Result</h3>
            <div class="bg-[#252249] border border-[#363168] rounded-lg p-4">
                <pre class="text-gray-300 text-sm overflow-x-auto">${JSON.stringify(job.result, null, 2)}</pre>
            </div>
        </div>
        ` : ''}

        ${actionsHtml ? `
        <div class="mt-6 pt-6 border-t border-[#363168] flex gap-3">
            ${actionsHtml}
            <button onclick="closeJobModal()" class="px-4 py-2 bg-[#252249] border border-[#363168] text-white rounded-lg hover:bg-[#363168]">
                Close
            </button>
        </div>
        ` : ''}
    `;

    modal.classList.remove('hidden');
}

/**
 * Close job detail modal
 */
function closeJobModal() {
    const modal = document.getElementById('job-detail-modal');
    modal.classList.add('hidden');
}

/**
 * Initialize page
 */
document.addEventListener('DOMContentLoaded', () => {
    loadJobs();
    loadApps();
    // initRealTimeUpdates();

    // Auto-refresh every 10 seconds
    setInterval(loadJobs, 10000);

    // Add enter key listener for search
    document.getElementById('search-job-id').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            applyFilters();
        }
    });

    // Close modal when clicking outside
    document.getElementById('job-detail-modal').addEventListener('click', (e) => {
        if (e.target.id === 'job-detail-modal') {
            closeJobModal();
        }
    });

    // Close modal with Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeJobModal();
        }
    });
});
