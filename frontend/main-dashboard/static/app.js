class GPUOrchestratorHub {
    constructor() {
        this.services = [];

        this.statusDot  = document.getElementById('statusDot');
        this.statusText = document.getElementById('systemStatusText');
        this.servicesGrid = document.getElementById('servicesGrid');

        this.init();
    }

    async init() {
        await Promise.all([this.loadGPUStatus(), this.loadServices()]);
        setInterval(() => this.loadGPUStatus(), 3000);
    }

    setStatus(color, text) {
        const colorMap = {
            'emerald': '#22c55e',
            'yellow':  '#eab308',
            'orange':  '#f97316',
            'red':     '#ef4444',
        };
        this.statusDot.style.background = colorMap[color] || '#aaa';
        this.statusText.textContent = text;
    }

    fetchWithTimeout(url, ms = 6000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), ms);
        return fetch(url, { signal: controller.signal }).finally(() => clearTimeout(timer));
    }

    async loadGPUStatus() {
        try {
            const response = await this.fetchWithTimeout('api/gpu/health');
            const data = await response.json();

            if (data.status === 'ok') {
                const jobs = data.active_jobs || 0;
                const utilization = parseFloat(data.utilization_pct) || 0;

                if (data.is_available) {
                    this.setStatus('emerald', 'All systems operational');
                } else if (jobs > 0) {
                    this.setStatus('orange', `Busy — ${jobs} active job${jobs !== 1 ? 's' : ''}`);
                } else if (utilization > 30) {
                    this.setStatus('yellow', 'Reserved');
                } else {
                    this.setStatus('emerald', 'Idle');
                }
            } else {
                this.setStatus('red', 'Offline');
            }
        } catch {
            this.setStatus('red', 'Connection failed');
        }
    }

    async loadServices() {
        try {
            const response = await this.fetchWithTimeout('api/services');
            const data = await response.json();
            this.services = data.services;
            this.renderServices();
        } catch (error) {
            this.servicesGrid.innerHTML = '<div class="text-center text-[#aaa] py-12">Failed to load services</div>';
        }
    }

    getServiceIcon(icon) {
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
            this.servicesGrid.innerHTML = '<div class="text-center text-[#aaa] py-12">No services available</div>';
            return;
        }

        this.servicesGrid.innerHTML = this.services.map(service => {
            const isOffline = service.status !== 'online';
            const statusColor = isOffline ? 'text-[#bbb]' : 'text-emerald-500';
            const statusText  = isOffline ? 'Offline' : 'Online';
            const materialIcon = this.getServiceIcon(service.icon);
            const opacityClass = isOffline ? 'opacity-50' : '';

            return `
            <a class="card-hover transition-all duration-150 flex flex-col bg-white border border-[#e0e0e0] rounded-xl p-7 items-center text-center ${opacityClass}"
                href="${service.url}" target="_blank">
                <div class="mb-5 p-4 rounded-2xl bg-[#f5f5f5] text-[#555]">
                    <span class="material-symbols-outlined text-5xl">${materialIcon}</span>
                </div>
                <h3 class="text-lg font-semibold mb-2">${service.name}</h3>
                <div class="flex items-center gap-2 mb-3">
                    <span class="${statusColor} text-sm font-medium">${statusText}</span>
                    <span class="w-1 h-1 rounded-full bg-[#ccc]"></span>
                    <span class="text-[#aaa] text-sm">${service.app_id || 'System'}</span>
                </div>
                <p class="text-[#888] text-sm leading-relaxed">${service.description}</p>
            </a>`;
        }).join('');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new GPUOrchestratorHub();
});
