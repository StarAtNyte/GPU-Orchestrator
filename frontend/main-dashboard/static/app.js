class GPUOrchestratorHub {
    constructor() {
        this.services = [];

        this.statusDot  = document.getElementById('statusDot');
        this.statusText = document.getElementById('systemStatusText');
        this.servicesGrid = document.getElementById('servicesGrid');
        this.searchEl = document.getElementById('serviceSearch');
        this.summaryEl = document.getElementById('serviceSummary');
        this.refreshBtn = document.getElementById('refreshServices');
        this.query = '';

        if (this.searchEl) {
            try { this.query = localStorage.getItem('hub_service_filter') || ''; this.searchEl.value = this.query; } catch (e) { /* ignore */ }
            this.searchEl.addEventListener('input', () => {
                this.query = this.searchEl.value.trim().toLowerCase();
                try { localStorage.setItem('hub_service_filter', this.searchEl.value); } catch (e) { /* ignore */ }
                this.renderServices();
            });
        }
        if (this.refreshBtn) this.refreshBtn.addEventListener('click', () => this.loadServices());
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) { this.loadGPUStatus(); this.loadServices(); }
        });

        this.init();
    }

    async init() {
        await Promise.all([this.loadGPUStatus(), this.loadServices()]);
        setInterval(() => { if (!document.hidden) this.loadGPUStatus(); }, 3000);
        setInterval(() => { if (!document.hidden) this.loadServices(); }, 30000);
    }

    esc(v) {
        const d = document.createElement('div');
        d.textContent = v == null ? '' : String(v);
        return d.innerHTML.replace(/"/g, '&quot;');
    }

    safeUrl(u) {
        try {
            const url = new URL(u, location.href);
            return /^https?:$/.test(url.protocol) ? url.href : '#';
        } catch (e) { return '#'; }
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
        this.statusText.title = text;
    }

    fetchWithTimeout(url, ms = 6000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), ms);
        return fetch(url, { signal: controller.signal }).finally(() => clearTimeout(timer));
    }

    async loadGPUStatus() {
        try {
            const gpuRes = await this.fetchWithTimeout('api/gpu/health');
            const data = await gpuRes.json();

            if (data.status !== 'ok' && !data.state) {
                this.setStatus('red', 'Offline');
            } else if (data.state === 'busy' || data.state === 'loading') {
                this.setStatus('orange', data.message || 'GPU busy');
            } else if (data.state === 'external') {
                this.setStatus('red', data.message || 'GPU in use by another process');
            } else if (data.state === 'unknown' || data.state === 'offline') {
                this.setStatus('yellow', 'GPU status unknown');
            } else {
                this.setStatus('emerald', data.message || 'GPU free');
            }
        } catch {
            this.setStatus('red', 'Connection failed');
        }
    }

    async loadServices() {
        if (this.refreshBtn) this.refreshBtn.disabled = true;
        try {
            const response = await this.fetchWithTimeout('api/services', 10000);
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            this.services = data.services || [];
            this.renderServices();
        } catch (error) {
            if (!this.services.length) {
                this.servicesGrid.setAttribute('aria-busy', 'false');
                this.servicesGrid.innerHTML = '<div class="col-span-full text-center text-[#aaa] py-12" role="alert">' +
                    'Could not load services. <button type="button" id="retryServices" class="underline">Retry</button></div>';
                const r = document.getElementById('retryServices');
                if (r) r.addEventListener('click', () => this.loadServices());
            }
        } finally {
            if (this.refreshBtn) this.refreshBtn.disabled = false;
        }
    }

    getServiceIcon(icon) {
        const iconMap = {
            '🎨': 'image_search',
            '🖼️': 'blur_on',
            '⚡': 'bolt',
            '🎤': 'mic',
            '⚙️': 'settings_input_component',
            '💬': 'chat'
        };
        return iconMap[icon] || 'apps';
    }

    renderServices() {
        this.servicesGrid.setAttribute('aria-busy', 'false');
        if (!this.services || this.services.length === 0) {
            this.servicesGrid.innerHTML = '<div class="col-span-full text-center text-[#aaa] py-12">No services are registered yet.</div>';
            if (this.summaryEl) this.summaryEl.textContent = '';
            return;
        }

        const online = this.services.filter(s => s.status === 'online').length;
        if (this.summaryEl) this.summaryEl.textContent = `(${online} of ${this.services.length} online)`;

        const q = this.query;
        const list = q
            ? this.services.filter(s => [s.name, s.app_id, s.description].join(' ').toLowerCase().includes(q))
            : this.services;
        if (list.length === 0) {
            this.servicesGrid.innerHTML = '<div class="col-span-full text-center text-[#aaa] py-12">No services match your filter.</div>';
            return;
        }

        this.servicesGrid.innerHTML = list.map(service => {
            const isOffline = service.status !== 'online';
            const statusColor = isOffline ? 'text-[#bbb]' : 'text-emerald-500';
            const statusText  = isOffline ? 'Offline' : 'Online';
            const materialIcon = this.getServiceIcon(service.icon);
            const opacityClass = isOffline ? 'opacity-50' : '';

            return `
            <a class="card-hover transition-all duration-150 flex flex-col bg-white border border-[#e0e0e0] rounded-xl p-7 items-center text-center ${opacityClass}"
                href="${this.esc(this.safeUrl(service.url))}" target="_blank" rel="noopener"
                aria-label="${this.esc(service.name)}, ${statusText}">
                <div class="mb-5 p-4 rounded-2xl bg-[#f5f5f5] text-[#555]">
                    <span class="material-symbols-outlined text-5xl" aria-hidden="true">${materialIcon}</span>
                </div>
                <h3 class="text-lg font-semibold mb-2">${this.esc(service.name)}</h3>
                <div class="flex items-center gap-2 mb-3">
                    <span class="${statusColor} text-sm font-medium">${statusText}</span>
                    <span class="w-1 h-1 rounded-full bg-[#ccc]"></span>
                    <span class="text-[#aaa] text-sm">${this.esc(service.app_id || 'System')}</span>
                </div>
                <p class="text-[#888] text-sm leading-relaxed">${this.esc(service.description)}</p>
            </a>`;
        }).join('');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.hub = new GPUOrchestratorHub();
});
