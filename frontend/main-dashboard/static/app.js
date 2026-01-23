class GPUOrchestratorHub {
    constructor() {
        this.gpuStatus = null;
        this.services = [];
        this.workers = [];

        // Elements
        this.systemStatusBadge = document.getElementById('systemStatusBadge');
        this.systemStatusText = document.getElementById('systemStatusText');
        this.servicesGrid = document.getElementById('servicesGrid');
        this.workersList = document.getElementById('workersList');

        // Start updates
        this.init();
    }

    async init() {
        // Load initial data
        await this.loadGPUStatus();
        await this.loadServices();
        await this.loadWorkers();

        // Setup polling
        setInterval(() => this.loadGPUStatus(), 3000);
        setInterval(() => this.loadWorkers(), 10000);
    }

    updateStatusBadge(color, statusText) {
        // Update badge container
        this.systemStatusBadge.className = `flex items-center gap-2 bg-${color}-500/10 px-3 py-1.5 rounded-full border border-${color}-500/20`;

        // Update badge HTML with proper structure
        this.systemStatusBadge.innerHTML = `
            <span class="relative flex h-2 w-2">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-${color}-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2 w-2 bg-${color}-500"></span>
            </span>
            <span class="text-${color}-500 text-xs font-bold uppercase tracking-wider">${statusText}</span>
        `;
    }

    async loadGPUStatus() {
        try {
            const response = await fetch('/api/gpu/health');
            const data = await response.json();
            this.gpuStatus = data;

            if (data.status === 'ok') {
                const jobs = data.active_jobs || 0;
                const utilization = parseFloat(data.utilization_pct) || 0;

                if (data.is_available) {
                    // System online - green
                    this.updateStatusBadge('emerald', 'System Status: Online');
                } else if (jobs > 0) {
                    // System busy with active jobs - orange
                    this.updateStatusBadge('orange', `System Status: Busy (${jobs} job${jobs !== 1 ? 's' : ''})`);
                } else if (utilization > 30) {
                    // High utilization, no active jobs - reserved for something
                    this.updateStatusBadge('yellow', 'System Status: Reserved');
                } else {
                    // Low background activity but no active jobs - idle and ready
                    this.updateStatusBadge('emerald', 'System Status: Idle');
                }
            } else {
                // System error - red
                this.updateStatusBadge('red', 'System Status: Offline');
            }

            this.renderGPUMetrics();
        } catch (error) {
            console.error('Failed to load GPU status:', error);
            // Connection error - red
            this.updateStatusBadge('red', 'System Status: Connection Failed');
        }
    }

    renderGPUMetrics() {
        if (!this.gpuStatus || this.gpuStatus.status !== 'ok') {
            document.getElementById('gpuDetail').textContent = 'Cannot connect to orchestrator';
            document.getElementById('freeVram').textContent = '-';
            document.getElementById('usedVram').textContent = '-';
            document.getElementById('utilization').textContent = '-';
            document.getElementById('activeJobs').textContent = '-';
            return;
        }

        const statusEmoji = this.gpuStatus.is_available ? '✅' : '🔄';
        const statusText = this.gpuStatus.is_available ? 'Ready for jobs' : 'Processing jobs';
        document.getElementById('gpuDetail').textContent = `${statusEmoji} ${statusText}`;
        document.getElementById('freeVram').textContent = this.gpuStatus.free_vram_gb + ' GB';
        document.getElementById('usedVram').textContent = this.gpuStatus.used_vram_gb + ' GB';
        document.getElementById('utilization').textContent = this.gpuStatus.utilization_pct + '%';
        document.getElementById('activeJobs').textContent = this.gpuStatus.active_jobs || 0;
    }

    async loadServices() {
        try {
            const response = await fetch('/api/services');
            const data = await response.json();
            this.services = data.services;
            this.renderServices();
        } catch (error) {
            this.servicesGrid.innerHTML = '<div class="text-center text-slate-400 py-12">Failed to load services</div>';
        }
    }

    getServiceIcon(icon) {
        // Map emojis to Material Symbols
        const iconMap = {
            '🎨': 'image_search',
            '🖼️': 'blur_on',
            '⚡': 'bolt',
            '🎤': 'mic',
            '⚙️': 'settings_input_component'
        };
        return iconMap[icon] || 'apps';
    }

    renderServices() {
        if (!this.services || this.services.length === 0) {
            this.servicesGrid.innerHTML = '<div class="text-center text-slate-400 py-12">No services available</div>';
            return;
        }

        this.servicesGrid.innerHTML = this.services.map(service => {
            const statusColor = service.status === 'online' ? 'emerald' : 'slate';
            const statusText = service.status === 'online' ? 'Ready' : 'Offline';
            const materialIcon = this.getServiceIcon(service.icon);
            const opacityClass = service.status === 'offline' ? 'opacity-70' : '';

            return `
            <a class="group card-hover transition-all duration-300 flex flex-col bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl p-8 items-center text-center ${opacityClass}"
                href="${service.url}" target="_blank">
                <div class="mb-6 p-4 rounded-2xl bg-primary/10 text-primary group-hover:scale-110 transition-transform duration-300">
                    <span class="material-symbols-outlined text-5xl">${materialIcon}</span>
                </div>
                <h3 class="text-xl font-bold mb-2">${service.name}</h3>
                <div class="flex items-center gap-2 mb-4">
                    <span class="text-${statusColor}-500 text-sm font-medium">${statusText}</span>
                    <span class="w-1 h-1 rounded-full bg-slate-400"></span>
                    <span class="text-slate-400 text-sm">${service.app_id || 'System'}</span>
                </div>
                <p class="text-slate-500 dark:text-slate-400 text-sm leading-relaxed">
                    ${service.description}
                </p>
            </a>
        `;
        }).join('');
    }

    async loadWorkers() {
        try {
            const response = await fetch('/api/workers');
            const data = await response.json();
            this.workers = data.workers || [];
            this.renderWorkers();
        } catch (error) {
            this.workersList.innerHTML = '<div class="text-center text-slate-400 py-12">Failed to load workers</div>';
        }
    }

    renderWorkers() {
        if (!this.workers || this.workers.length === 0) {
            this.workersList.innerHTML = '<div class="text-center text-slate-400 py-12">No workers registered</div>';
            return;
        }

        this.workersList.innerHTML = this.workers.map(worker => {
            const isHealthy = worker.status === 'healthy';
            const statusColor = isHealthy ? 'emerald' : 'red';
            const borderColor = isHealthy ? 'border-emerald-500' : 'border-red-500';

            return `
            <div class="bg-white dark:bg-slate-900/50 border-l-4 ${borderColor} border-t border-r border-b border-slate-200 dark:border-slate-800 rounded-lg p-4">
                <div class="flex items-center justify-between mb-2">
                    <h4 class="font-bold text-lg">${worker.worker_id}</h4>
                    <span class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-${statusColor}-500"></span>
                        <span class="text-${statusColor}-500 text-sm font-medium capitalize">${worker.status || 'unknown'}</span>
                    </span>
                </div>
                <div class="text-slate-500 dark:text-slate-400 text-sm">
                    <span class="font-semibold">App:</span> ${worker.app_id || 'System'}
                </div>
            </div>
        `;
        }).join('');
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    new GPUOrchestratorHub();
});
