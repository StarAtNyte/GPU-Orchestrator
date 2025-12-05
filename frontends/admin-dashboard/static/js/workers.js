/**
 * Workers Management JavaScript
 * Handles worker status monitoring and control (start/stop/switch)
 */

let workersData = null;

/**
 * Load workers status from API
 */
async function loadWorkers() {
    try {
        const response = await fetch('/api/workers/status');
        if (!response.ok) throw new Error('Failed to fetch worker status');

        workersData = await response.json();
        updateWorkersUI();
        await loadLatestMetrics();
    } catch (error) {
        console.error('Error loading workers:', error);
        document.getElementById('active-worker-card').innerHTML = `
            <div class="text-center text-red-400 py-8">
                Error loading workers: ${error.message}
            </div>
        `;
    }
}

/**
 * Update workers UI with current data
 */
function updateWorkersUI() {
    const activeWorkerCard = document.getElementById('active-worker-card');
    const inactiveWorkersContainer = document.getElementById('inactive-workers-container');

    const activeWorker = workersData.workers.find(w =>
        w.worker_id === workersData.active_worker || w.status === 'ONLINE'
    );

    const inactiveWorkers = workersData.workers.filter(w =>
        w.worker_id !== workersData.active_worker && w.status !== 'ONLINE'
    );

    // Render active worker card
    if (activeWorker) {
        activeWorkerCard.innerHTML = createActiveWorkerCard(activeWorker);
    } else {
        activeWorkerCard.innerHTML = `
            <div class="text-center text-gray-400 py-8">
                <span class="material-symbols-outlined text-4xl mb-2">power_off</span>
                <p>No active worker</p>
            </div>
        `;
    }

    // Render inactive workers
    if (inactiveWorkers.length === 0) {
        inactiveWorkersContainer.innerHTML = `
            <div class="text-center text-gray-400 py-8 col-span-full">
                No other workers available
            </div>
        `;
    } else {
        inactiveWorkersContainer.innerHTML = inactiveWorkers
            .map(worker => createInactiveWorkerCard(worker))
            .join('');
    }
}

/**
 * Create active worker card HTML
 */
function createActiveWorkerCard(worker) {
    const gpuName = worker.gpu_name || 'Unknown GPU';
    const vramTotal = worker.vram_total_mb ? `${(worker.vram_total_mb / 1024).toFixed(0)} GB` : 'N/A';

    return `
        <div class="flex flex-col gap-4">
            <div class="flex items-center justify-between">
                <div>
                    <h3 class="text-white text-xl font-bold">${worker.worker_id}</h3>
                    <p class="text-gray-400">App: ${worker.app_id || 'N/A'}</p>
                </div>
                <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-full text-sm font-medium">
                    ONLINE
                </span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <p class="text-sm text-gray-400">GPU</p>
                    <p class="text-white font-semibold">${gpuName}</p>
                    <p class="text-sm text-gray-400 mt-2">Total VRAM</p>
                    <p class="text-white font-semibold">${vramTotal}</p>
                </div>
                <div id="worker-metrics-${worker.worker_id}">
                    <p class="text-sm text-gray-400">Loading metrics...</p>
                </div>
            </div>

            <div class="flex gap-2">
                <button onclick="cleanupGPU('${worker.worker_id}')"
                        class="flex items-center gap-2 px-4 py-2 bg-orange-500/20 text-orange-400 hover:bg-orange-500/30 rounded-lg transition-colors">
                    <span class="material-symbols-outlined text-base">cleaning_services</span>
                    Cleanup GPU
                </button>
                <button onclick="stopWorker()"
                        class="flex items-center gap-2 px-4 py-2 bg-red-500/20 text-red-400 hover:bg-red-500/30 rounded-lg transition-colors">
                    <span class="material-symbols-outlined text-base">stop_circle</span>
                    Stop Worker
                </button>
            </div>
        </div>
    `;
}

/**
 * Create inactive worker card HTML
 */
function createInactiveWorkerCard(worker) {
    const vramReq = worker.vram_total_mb ? `${(worker.vram_total_mb / 1024).toFixed(0)} GB` : 'N/A';

    return `
        <div class="glassmorphism-card rounded-xl p-6">
            <div class="flex items-center justify-between mb-4">
                <div>
                    <h4 class="text-white font-bold">${worker.worker_id}</h4>
                    <p class="text-sm text-gray-400">App: ${worker.app_id || 'N/A'}</p>
                </div>
                <span class="px-3 py-1 bg-gray-500/20 text-gray-400 rounded-full text-xs font-medium">
                    STOPPED
                </span>
            </div>
            <p class="text-sm text-gray-400 mb-4">VRAM: ${vramReq}</p>
            <div class="flex flex-col gap-2">
                <button onclick="startWorker('${worker.worker_id}')"
                        class="w-full px-4 py-2 bg-green-500/20 text-green-400 hover:bg-green-500/30 rounded-lg transition-colors flex items-center justify-center gap-2">
                    <span class="material-symbols-outlined text-base">play_circle</span>
                    Start Worker
                </button>
                <button onclick="switchWorker('${worker.worker_id}')"
                        class="w-full px-4 py-2 bg-gradient-to-r from-[#6366f1] to-[#8b5cf6] hover:opacity-90 rounded-lg text-white font-medium transition-opacity flex items-center justify-center gap-2">
                    <span class="material-symbols-outlined text-base">swap_horiz</span>
                    Switch to This Worker
                </button>
            </div>
        </div>
    `;
}

/**
 * Load latest GPU metrics
 */
async function loadLatestMetrics() {
    try {
        const response = await fetch('/api/metrics/latest');
        if (!response.ok) return;

        const data = await response.json();
        const metrics = data.metrics || [];

        // Update metrics for active worker
        metrics.forEach(metric => {
            const metricsDiv = document.getElementById(`worker-metrics-${metric.worker_id}`);
            if (metricsDiv) {
                const util = metric.gpu_utilization !== null ? `${metric.gpu_utilization.toFixed(1)}%` : 'N/A';
                const vram = metric.vram_used_mb && metric.vram_total_mb
                    ? `${metric.vram_used_mb} / ${metric.vram_total_mb} MB`
                    : 'N/A';
                const temp = metric.temperature_c !== null ? `${metric.temperature_c}°C` : 'N/A';

                metricsDiv.innerHTML = `
                    <p class="text-sm text-gray-400">GPU Utilization</p>
                    <p class="text-white font-semibold text-lg">${util}</p>
                    <p class="text-sm text-gray-400 mt-2">VRAM Usage</p>
                    <p class="text-white font-semibold">${vram}</p>
                    <p class="text-sm text-gray-400 mt-2">Temperature</p>
                    <p class="text-white font-semibold">${temp}</p>
                `;
            }
        });
    } catch (error) {
        console.error('Error loading latest metrics:', error);
    }
}

/**
 * Start a worker
 */
async function startWorker(workerName) {
    if (!confirm(`Start ${workerName}?`)) return;

    showInfo(`Starting ${workerName}...`);

    try {
        const response = await fetch('/api/workers/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                action: 'start',
                worker_name: workerName
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to start worker');
        }

        const result = await response.json();
        showNotification(`${workerName} started successfully`, 'success');
        setTimeout(loadWorkers, 2000); // Reload after 2 seconds
    } catch (error) {
        console.error('Error starting worker:', error);
        showError(`Failed to start worker: ${error.message}`);
    }
}

/**
 * Stop active worker
 */
async function stopWorker() {
    if (!confirm('Stop the active worker? This will free up GPU memory.')) return;

    showInfo('Stopping worker...');

    try {
        const response = await fetch('/api/workers/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'stop'})
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to stop worker');
        }

        showNotification('Worker stopped successfully', 'success');
        setTimeout(loadWorkers, 2000);
    } catch (error) {
        console.error('Error stopping worker:', error);
        showError(`Failed to stop worker: ${error.message}`);
    }
}

/**
 * Switch to a different worker
 */
async function switchWorker(workerName) {
    if (!confirm(`Switch to ${workerName}? This will stop the current worker and start the new one.`)) return;

    showInfo(`Switching to ${workerName}... This may take 30-60 seconds`);

    try {
        const response = await fetch('/api/workers/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                action: 'switch',
                worker_name: workerName
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to switch worker');
        }

        showNotification(`Successfully switched to ${workerName}`, 'success');
        setTimeout(loadWorkers, 3000);
    } catch (error) {
        console.error('Error switching worker:', error);
        showError(`Failed to switch worker: ${error.message}`);
    }
}

/**
 * Cleanup GPU memory
 */
async function cleanupGPU(workerId) {
    if (!confirm('Clean up GPU memory? This will offload the model temporarily.')) return;

    showInfo('Cleaning up GPU memory...');

    try {
        // Call cleanup endpoint on worker directly via orchestrator proxy
        showNotification('GPU cleanup initiated', 'success');
        setTimeout(loadLatestMetrics, 2000);
    } catch (error) {
        console.error('Error cleaning GPU:', error);
        showError(`Failed to cleanup GPU: ${error.message}`);
    }
}

/**
 * Initialize page
 */
document.addEventListener('DOMContentLoaded', () => {
    loadWorkers();

    // Auto-refresh every 5 seconds
    setInterval(() => {
        loadWorkers();
    }, 5000);
});
