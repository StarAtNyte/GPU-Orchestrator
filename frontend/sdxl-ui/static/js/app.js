// SDXL UI
const CFG = {
    storageKey: 'sdxl-ui:settings:v1',
    filePrefix: 'sdxl',
};

const MAX_RECENT = 8;
const POLL_MS = 1000;
const MAX_POLL_ERRORS = 10;

class ImageUI {
    constructor() {
        this.currentJobId = null;
        this.pollTimer = null;
        this.elapsedTimer = null;
        this.startedAt = 0;
        this.busy = false;
        this.pollErrors = 0;
        this.current = null; // { params, seed, info, src }
        this.recent = [];
        this.zoomLevel = 1;
        this.isDragging = false;
        this.startX = 0; this.startY = 0;
        this.translateX = 0; this.translateY = 0;

        const $ = (id) => document.getElementById(id);
        this.form = $('generateForm');
        this.promptEl = $('prompt');
        this.promptCount = $('promptCount');
        this.formError = $('formError');
        this.submitBtn = $('generateBtn');
        this.placeholderState = $('placeholderState');
        this.loadingState = $('loadingState');
        this.imageResultState = $('imageResultState');
        this.loadingText = $('loadingText');
        this.elapsedEl = $('elapsed');
        this.jobIdDisplay = $('jobIdDisplay');
        this.cancelBtn = $('cancelBtn');
        this.resultImage = $('resultImage');
        this.generationInfo = $('generationInfo');
        this.downloadBtn = $('downloadBtn');
        this.copyPromptBtn = $('copyPromptBtn');
        this.reuseSeedBtn = $('reuseSeedBtn');
        this.newBtn = $('newBtn');
        this.statusBadge = $('statusBadge');
        this.statusText = $('statusText');
        this.logoHome = $('logoHome');
        this.recentWrap = $('recentWrap');
        this.recentStrip = $('recentStrip');
        this.fullscreenModal = $('fullscreenModal');
        this.fullscreenImg = $('fullscreenImg');
        this.fullscreenClose = $('fullscreenClose');
        this.zoomInBtn = $('zoomInBtn');
        this.zoomOutBtn = $('zoomOutBtn');
        this.resetZoomBtn = $('resetZoomBtn');

        this.restoreSettings();
        this.updateCount();
        this.bind();
        this.checkHealth();
        setInterval(() => this.checkHealth(), 5000);
    }

    /* ---------- settings persistence ---------- */
    fieldEls() {
        return Array.from(this.form.querySelectorAll('textarea, select, input'));
    }

    restoreSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(CFG.storageKey) || '{}');
            this.fieldEls().forEach((el) => {
                if (!(el.name in saved)) return;
                if (el.tagName === 'SELECT' && !Array.from(el.options).some(o => o.value === saved[el.name])) return;
                el.value = saved[el.name];
            });
        } catch (e) { /* storage unavailable */ }
    }

    saveSettings() {
        try {
            const out = {};
            this.fieldEls().forEach((el) => { if (el.name) out[el.name] = el.value; });
            localStorage.setItem(CFG.storageKey, JSON.stringify(out));
        } catch (e) { /* ignore */ }
    }

    /* ---------- events ---------- */
    bind() {
        this.form.addEventListener('submit', (e) => this.handleSubmit(e));
        this.form.addEventListener('input', () => { this.saveSettings(); this.clearError(); });
        this.form.addEventListener('change', () => this.saveSettings());
        this.promptEl.addEventListener('input', () => this.updateCount());
        this.newBtn.addEventListener('click', () => { this.reset(); this.promptEl.focus(); });
        this.logoHome.addEventListener('click', () => this.reset());
        this.cancelBtn.addEventListener('click', () => this.cancel());
        this.downloadBtn.addEventListener('click', () => this.downloadImage());
        this.copyPromptBtn.addEventListener('click', () => this.copyPrompt());
        this.reuseSeedBtn.addEventListener('click', () => this.reuseSeed());

        const exampleBtns = document.querySelectorAll('.example-btn');
        exampleBtns.forEach((btn) => {
            btn.addEventListener('click', () => {
                this.promptEl.value = btn.getAttribute('data-prompt');
                exampleBtns.forEach(b => b.classList.toggle('selected', b === btn));
                this.updateCount();
                this.saveSettings();
                this.clearError();
                this.promptEl.focus();
            });
        });

        // Ctrl/Cmd+Enter submits from anywhere in the form
        this.form.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                if (this.form.requestSubmit) this.form.requestSubmit(this.submitBtn);
                else this.submitBtn.click();
            }
        });

        // Fullscreen
        this.resultImage.addEventListener('click', () => this.openFullscreen());
        this.resultImage.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.openFullscreen(); }
        });
        this.fullscreenClose.addEventListener('click', () => this.closeFullscreen());
        this.fullscreenModal.addEventListener('click', (e) => {
            if (e.target === this.fullscreenModal) this.closeFullscreen();
        });
        this.zoomInBtn.addEventListener('click', (e) => { e.stopPropagation(); this.zoomBy(0.5); });
        this.zoomOutBtn.addEventListener('click', (e) => { e.stopPropagation(); this.zoomBy(-0.5); });
        this.resetZoomBtn.addEventListener('click', (e) => { e.stopPropagation(); this.resetZoom(); });

        document.addEventListener('keydown', (e) => {
            if (!this.fullscreenModal.classList.contains('active')) return;
            if (e.key === 'Escape') this.closeFullscreen();
            else if (e.key === '+' || e.key === '=') this.zoomBy(0.5);
            else if (e.key === '-' || e.key === '_') this.zoomBy(-0.5);
            else if (e.key === '0') this.resetZoom();
        });

        this.fullscreenImg.addEventListener('mousedown', (e) => {
            if (this.zoomLevel > 1) {
                this.isDragging = true;
                this.startX = e.clientX - this.translateX;
                this.startY = e.clientY - this.translateY;
                this.fullscreenImg.style.cursor = 'grabbing';
                e.preventDefault();
            }
        });
        document.addEventListener('mousemove', (e) => {
            if (!this.isDragging) return;
            this.translateX = e.clientX - this.startX;
            this.translateY = e.clientY - this.startY;
            this.updateTransform();
        });
        document.addEventListener('mouseup', () => {
            this.isDragging = false;
            this.fullscreenImg.style.cursor = this.zoomLevel > 1 ? 'move' : 'zoom-out';
        });
        this.fullscreenImg.addEventListener('wheel', (e) => {
            e.preventDefault();
            this.zoomBy(e.deltaY < 0 ? 0.2 : -0.2);
        }, { passive: false });
    }

    updateCount() {
        const n = this.promptEl.value.length;
        this.promptCount.textContent = `${n} / ${this.promptEl.maxLength > 0 ? this.promptEl.maxLength : 2000}`;
    }

    /* ---------- errors / toast ---------- */
    showError(msg) {
        this.formError.textContent = msg;
        this.formError.hidden = false;
        this.promptEl.setAttribute('aria-invalid', msg && !this.promptEl.value.trim() ? 'true' : 'false');
    }

    clearError() {
        this.formError.hidden = true;
        this.formError.textContent = '';
        this.promptEl.removeAttribute('aria-invalid');
    }

    toast(msg) {
        let t = document.getElementById('uiToast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'uiToast';
            t.className = 'toast';
            t.setAttribute('role', 'status');
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.hidden = false;
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
    }

    /* ---------- health ---------- */
    async checkHealth() {
        try {
            const response = await fetch('health', { cache: 'no-store' });
            const data = await response.json();
            const ok = data.orchestrator === 'connected';
            this.statusBadge.classList.toggle('disconnected', !ok);
            this.statusText.textContent = ok ? 'Orchestrator connected' : 'Orchestrator unreachable';
        } catch (error) {
            this.statusBadge.classList.add('disconnected');
            this.statusText.textContent = 'Orchestrator unreachable';
        }
    }

    /* ---------- submit ---------- */
    readParams() {
        const f = new FormData(this.form);
        const prompt = (f.get('prompt') || '').trim();
        if (!prompt) return { error: 'Enter a prompt describing the image you want.' };
        if (prompt.length > 2000) return { error: 'Prompt is too long (max 2000 characters).' };
        const seedRaw = String(f.get('seed') || '').trim();
        let seed = null;
        if (seedRaw !== '') {
            seed = Number(seedRaw);
            if (!Number.isInteger(seed) || seed < 1 || seed > 2147483647) {
                return { error: 'Seed must be a whole number between 1 and 2147483647, or blank for random.' };
            }
        }
        const [w, h] = String(f.get('resolution')).split('x').map(Number);
        const steps = Number(f.get('steps'));
        const guidance = Number(f.get('guidance'));
        if (!Number.isInteger(steps) || steps < 10 || steps > 50) return { error: 'Steps must be a whole number from 10 to 50.' };
        if (!(guidance >= 1 && guidance <= 15)) return { error: 'Guidance must be between 1 and 15.' };
        const payload = {
            prompt,
            negative_prompt: (f.get('negative_prompt') || '').trim(),
            width: w,
            height: h,
            num_inference_steps: steps,
            guidance_scale: guidance,
            seed,
        };
        return { payload, seed, display: { prompt, w, h, steps, guidance } };
    }

    async handleSubmit(e) {
        e.preventDefault();
        if (this.busy) return;
        this.clearError();

        const parsed = this.readParams();
        if (parsed.error) { this.showError(parsed.error); return; }

        if (typeof Auth !== 'undefined' && !Auth.isLoggedIn()) { Auth.showModal(); return; }

        this.busy = true;
        this.submitBtn.disabled = true;
        this.showLoading('SUBMITTING JOB...');

        try {
            const response = await fetch('api/generate', {
                method: 'POST',
                headers: typeof Auth !== 'undefined' ? Auth.getAuthHeaders() : { 'Content-Type': 'application/json' },
                body: JSON.stringify(parsed.payload)
            });
            let result = {};
            try { result = await response.json(); } catch (_) { /* non-JSON */ }

            if (result.success) {
                this.currentJobId = result.job_id;
                this.pending = { params: parsed.display, seed: result.seed != null ? result.seed : parsed.seed };
                this.jobIdDisplay.textContent = `Job ID: ${result.job_id}`;
                this.loadingText.textContent = 'QUEUED...';
                this.startPolling();
            } else {
                let msg = result.error || result.detail || `Request failed (HTTP ${response.status})`;
                if (typeof msg !== 'string') msg = JSON.stringify(msg);
                if (result.gpu_unavailable) msg = msg + (result.retry_after ? ` Try again in about ${result.retry_after}s.` : '');
                this.reset();
                this.showError(msg);
            }
        } catch (error) {
            this.reset();
            this.showError('Could not reach the server: ' + error.message);
        }
    }

    /* ---------- loading / polling ---------- */
    showLoading(text) {
        this.placeholderState.hidden = true;
        this.imageResultState.hidden = true;
        this.loadingState.hidden = false;
        this.loadingText.textContent = text;
        this.jobIdDisplay.textContent = '';
        this.startedAt = Date.now();
        this.tickElapsed();
        clearInterval(this.elapsedTimer);
        this.elapsedTimer = setInterval(() => this.tickElapsed(), 1000);
    }

    tickElapsed() {
        const s = Math.floor((Date.now() - this.startedAt) / 1000);
        this.elapsedEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }

    stopTimers() {
        clearTimeout(this.pollTimer);
        this.pollTimer = null;
        clearInterval(this.elapsedTimer);
        this.elapsedTimer = null;
    }

    startPolling() {
        this.pollErrors = 0;
        const jobId = this.currentJobId;
        const tick = async () => {
            if (this.currentJobId !== jobId) return;
            try {
                const response = await fetch(`api/status/${jobId}`, { cache: 'no-store' });
                const data = await response.json();
                if (this.currentJobId !== jobId) return;
                this.pollErrors = 0;
                if (data.success) {
                    const status = data.status;
                    if (status === 'QUEUED') this.loadingText.textContent = 'WAITING IN QUEUE...';
                    else if (status === 'PROCESSING') this.loadingText.textContent = 'GENERATING IMAGE...';
                    else if (status === 'COMPLETED') { this.showResult(data); return; }
                    else if (status === 'FAILED') {
                        this.reset();
                        this.showError('Generation failed: ' + (data.error || 'unknown error'));
                        return;
                    }
                }
            } catch (error) {
                if (++this.pollErrors >= MAX_POLL_ERRORS) {
                    this.reset();
                    this.showError('Lost connection while waiting. The job may still finish, check History.');
                    return;
                }
                this.loadingText.textContent = 'RECONNECTING...';
            }
            this.pollTimer = setTimeout(tick, POLL_MS);
        };
        this.pollTimer = setTimeout(tick, POLL_MS);
    }

    cancel() {
        const id = this.currentJobId;
        this.reset();
        if (id) this.toast('Stopped waiting. The job may still finish; see History.');
    }

    /* ---------- result ---------- */
    showResult(data) {
        let imageData = null;
        if (data.result) {
            if (typeof data.result === 'object') imageData = data.result;
            else if (typeof data.result === 'string') {
                try { imageData = JSON.parse(data.result); } catch (e) { console.error('Failed to parse result:', e); }
            }
        }
        const pending = this.pending || { params: {}, seed: null };
        if (!imageData || !imageData.image_base64) {
            this.reset();
            this.showError('The job finished but returned no image.');
            return;
        }

        this.stopTimers();
        const seed = imageData.seed != null ? imageData.seed : pending.seed;
        const src = `data:image/png;base64,${imageData.image_base64}`;
        const entry = {
            jobId: this.currentJobId,
            src,
            seed,
            prompt: pending.params.prompt,
            info: `${pending.params.w}x${pending.params.h} \u2022 ${imageData.num_inference_steps || pending.params.steps} steps \u2022 Guidance: ${imageData.guidance_scale || pending.params.guidance} \u2022 Seed: ${seed != null ? seed : 'Random'}`,
        };
        this.display(entry);
        this.recent.unshift(entry);
        this.recent = this.recent.slice(0, MAX_RECENT);
        this.renderRecent();
        this.busy = false;
        this.submitBtn.disabled = false;
        this.currentJobId = null;
    }

    display(entry) {
        this.current = entry;
        this.placeholderState.hidden = true;
        this.loadingState.hidden = true;
        this.imageResultState.hidden = false;
        this.resultImage.src = entry.src;
        this.resultImage.alt = `Generated image: ${(entry.prompt || '').slice(0, 120)}`;
        this.generationInfo.textContent = entry.info;
        this.reuseSeedBtn.hidden = entry.seed == null || entry.seed === '';
        this.renderRecent();
    }

    renderRecent() {
        this.recentWrap.hidden = this.recent.length === 0;
        this.recentStrip.textContent = '';
        this.recent.forEach((entry, i) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'recent-item' + (this.current === entry ? ' active' : '');
            b.setAttribute('aria-label', `Show result ${i + 1}: ${(entry.prompt || '').slice(0, 60)}`);
            const img = document.createElement('img');
            img.src = entry.src;
            img.alt = '';
            b.appendChild(img);
            b.addEventListener('click', () => { if (!this.busy) this.display(entry); });
            this.recentStrip.appendChild(b);
        });
    }

    downloadImage() {
        if (!this.current) return;
        const link = document.createElement('a');
        link.href = this.current.src;
        link.download = `${CFG.filePrefix}_${this.current.jobId || 'image'}_${Date.now()}.png`;
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    async copyPrompt() {
        if (!this.current) return;
        try {
            await navigator.clipboard.writeText(this.current.prompt || '');
            this.toast('Prompt copied');
        } catch (e) {
            this.promptEl.value = this.current.prompt || '';
            this.toast('Prompt placed in the prompt box');
        }
    }

    reuseSeed() {
        if (!this.current || this.current.seed == null) return;
        const seedEl = document.getElementById('seed');
        seedEl.value = this.current.seed;
        const adv = document.getElementById('advanced');
        if (adv) adv.open = true;
        this.saveSettings();
        this.toast(`Seed ${this.current.seed} set for the next run`);
    }

    /* ---------- fullscreen ---------- */
    zoomBy(d) {
        this.zoomLevel = Math.min(5, Math.max(0.5, this.zoomLevel + d));
        this.updateTransform();
    }
    resetZoom() { this.zoomLevel = 1; this.translateX = 0; this.translateY = 0; this.updateTransform(); }

    openFullscreen() {
        this.fullscreenImg.src = this.resultImage.src;
        this.fullscreenModal.classList.add('active');
        this.resetZoom();
        this.fullscreenClose.focus();
    }

    closeFullscreen() {
        this.fullscreenModal.classList.remove('active');
        this.resetZoom();
        this.resultImage.focus();
    }

    updateTransform() {
        this.fullscreenImg.style.transform = `translate(${this.translateX}px, ${this.translateY}px) scale(${this.zoomLevel})`;
        this.fullscreenImg.style.cursor = this.zoomLevel > 1 ? 'move' : 'zoom-out';
    }

    /* ---------- reset ---------- */
    reset() {
        this.stopTimers();
        this.currentJobId = null;
        this.busy = false;
        this.submitBtn.disabled = false;
        this.loadingState.hidden = true;
        this.imageResultState.hidden = true;
        this.placeholderState.hidden = false;
        this.current = null;
        this.renderRecent();
    }
}

new ImageUI();
