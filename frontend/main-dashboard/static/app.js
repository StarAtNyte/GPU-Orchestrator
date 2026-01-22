class GPUOrchestratorHub {
    constructor() {
        this.gpuStatus = null;
        this.services = [];
        this.workers = [];
        
        // Elements
        this.gpuStatusIndicator = document.getElementById('gpuStatusIndicator');
        this.gpuStatusText = document.getElementById('gpuStatusText');
        this.statusText = document.getElementById('statusText');
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

    async loadGPUStatus() {
        try {
            const response = await fetch('/api/gpu/health');
            const data = await response.json();
            this.gpuStatus = data;

            if (data.status === 'ok') {
                if (data.is_available) {
                    this.gpuStatusIndicator.className = 'indicator ready';
                    this.gpuStatusText.textContent = `GPU Ready • ${data.free_vram_gb}GB free`;
                } else {
                    this.gpuStatusIndicator.className = 'indicator busy';
                    const jobs = data.active_jobs || 0;
                    this.gpuStatusText.textContent = `GPU Busy • ${jobs} job${jobs !== 1 ? 's' : ''} processing`;
                }
                this.statusText.textContent = 'Connected to orchestrator';
            } else {
                this.gpuStatusIndicator.className = 'indicator error';
                this.gpuStatusText.textContent = 'GPU Status Unknown';
                this.statusText.textContent = 'Orchestrator disconnected';
            }

            this.renderGPUMetrics();
        } catch (error) {
            this.gpuStatusIndicator.className = 'indicator error';
            this.gpuStatusText.textContent = 'Cannot reach orchestrator';
            this.statusText.textContent = 'Connection failed';
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

        document.getElementById('gpuDetail').textContent = 
            this.gpuStatus.is_available ? '✅ Ready for jobs' : '🔄 Processing jobs';
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
            this.servicesGrid.innerHTML = '<div class="loading">Failed to load services</div>';
        }
    }

    renderServices() {
        if (!this.services || this.services.length === 0) {
            this.servicesGrid.innerHTML = '<div class="loading">No services available</div>';
            return;
        }

        this.servicesGrid.innerHTML = this.services.map(service => `
            <div class="service-card">
                <div class="service-icon">${service.icon}</div>
                <h3>${service.name}</h3>
                <p>${service.description}</p>
                <div class="service-footer">
                    <div class="service-status">
                        <span class="status-dot ${service.status === 'online' ? '' : 'offline'}"></span>
                        <span>${service.status === 'online' ? 'Online' : 'Offline'}</span>
                    </div>
                    <a href="${service.url}" target="_blank" class="service-btn ${service.status === 'offline' ? 'disabled' : ''}">
                        Open
                    </a>
                </div>
            </div>
        `).join('');
    }

    async loadWorkers() {
        try {
            const response = await fetch('/api/workers');
            const data = await response.json();
            this.workers = data.workers || [];
            this.renderWorkers();
        } catch (error) {
            this.workersList.innerHTML = '<div class="loading">Failed to load workers</div>';
        }
    }

    renderWorkers() {
        if (!this.workers || this.workers.length === 0) {
            this.workersList.innerHTML = '<div class="loading">No workers registered</div>';
            return;
        }

        this.workersList.innerHTML = this.workers.map(worker => `
            <div class="worker-card ${worker.status === 'healthy' ? '' : 'offline'}">
                <div class="worker-name">${worker.worker_id}</div>
                <div class="worker-app">${worker.app_id || 'System'}</div>
                <div class="worker-status ${worker.status === 'healthy' ? '' : 'offline'}">
                    <span>●</span>
                    <span>${worker.status || 'unknown'}</span>
                </div>
            </div>
        `).join('');
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    new GPUOrchestratorHub();
});
