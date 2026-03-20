let currentJobId = null;
let pollInterval = null;
let lottieAnimation = null;
let animationData = null;
let activeTab = 'text';
let generatedCount = 0;

// ── Accordion ──────────────────────────────────────────────────────────────

function toggleAccordion() {
    const header = document.getElementById('advancedToggle');
    const body = document.getElementById('advancedBody');
    header.classList.toggle('open');
    body.classList.toggle('open');
}

// ── Tabs ───────────────────────────────────────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        activeTab = btn.dataset.tab;
        document.getElementById('tab-' + activeTab).classList.add('active');
        switchExamplesTab(activeTab);
    });
});

function switchExamplesTab(tab) {
    document.querySelectorAll('.examples-tab-content').forEach(c => { c.style.display = 'none'; });
    const el = document.getElementById(tab + 'Examples');
    if (el && el.innerHTML.trim()) el.style.display = 'block';
}

// ── File select / drag-drop ────────────────────────────────────────────────

function onFileSelect(type, input) {
    const file = input.files[0];
    const nameEl = document.getElementById(type + 'DZName');
    const preview = document.getElementById(type + 'DZPreview');
    const area = document.getElementById(type + 'DZ');

    nameEl.textContent = file ? file.name : '';
    if (file) { area.classList.add('has-file'); } else { area.classList.remove('has-file'); }
    preview.innerHTML = '';

    if (!file) return;

    if (type === 'image' && file.type.startsWith('image/')) {
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        preview.appendChild(img);
    } else if (type === 'video') {
        const vid = document.createElement('video');
        vid.src = URL.createObjectURL(file);
        vid.muted = true; vid.loop = true; vid.autoplay = true;
        vid.style.maxHeight = '120px'; vid.style.borderRadius = '8px';
        preview.appendChild(vid);
    }
}

['imageDZ', 'videoDZ'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const type = id === 'imageDZ' ? 'image' : 'video';
    const input = document.getElementById(type + 'File');

    if (input) {
        input.addEventListener('change', () => onFileSelect(type, input));
    }

    ['dragenter', 'dragover'].forEach(ev => {
        el.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); el.classList.add('drag-over'); });
    });
    ['dragleave', 'drop'].forEach(ev => {
        el.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); el.classList.remove('drag-over'); });
    });
    el.addEventListener('drop', e => {
        const file = e.dataTransfer.files[0];
        if (file && input) {
            const dt = new DataTransfer();
            dt.items.add(file);
            input.files = dt.files;
            onFileSelect(type, input);
        }
    });
});

// ── Health checks ──────────────────────────────────────────────────────────

async function checkHealth() {
    const badge = document.getElementById('statusBadge');
    const spinner = document.getElementById('statusSpinner');
    const text = document.getElementById('statusText');
    try {
        const r = await fetch('health', { signal: AbortSignal.timeout(3000) });
        const d = await r.json();
        if (d.orchestrator === 'connected') {
            badge.className = 'status-badge ready';
            spinner.style.display = 'none';
            text.textContent = 'Orchestrator Connected';
        } else {
            badge.className = 'status-badge error';
            spinner.style.display = 'none';
            text.textContent = 'Orchestrator Disconnected';
        }
    } catch {
        badge.className = 'status-badge error';
        spinner.style.display = 'none';
        text.textContent = 'Offline';
    }
}

async function checkGPUHealth() {
    const dot = document.getElementById('gpuStatusDot');
    const text = document.getElementById('gpuStatusText');
    const badge = document.getElementById('gpuStatusBadge');
    try {
        // Use /api/workers as source of truth for active processing state
        const [gpuRes, workersRes] = await Promise.all([
            fetch('api/gpu/health', { signal: AbortSignal.timeout(4000) }),
            fetch('api/workers', { signal: AbortSignal.timeout(4000) })
        ]);
        const gpu = await gpuRes.json();
        const workers = await workersRes.json();

        const processingWorkers = (workers.workers || []).filter(w => w.State === 'PROCESSING' || w.State === 'STARTING');
        const activeCount = processingWorkers.length;

        if (activeCount > 0) {
            const w = processingWorkers[0];
            dot.style.background = '#f97316'; dot.style.animation = 'pulse 2s infinite';
            text.textContent = `GPU Busy \u00b7 ${w.AppID}`;
            badge.className = 'status-badge';
        } else if (gpu.status === 'ok') {
            const util = parseFloat(gpu.utilization_pct) || 0;
            if (util > 30) {
                dot.style.background = '#fbbf24'; dot.style.animation = 'none';
                text.textContent = `GPU Reserved \u00b7 ${util.toFixed(1)}%`;
                badge.className = 'status-badge';
            } else {
                dot.style.background = '#10b981'; dot.style.animation = 'none';
                text.textContent = `GPU Ready \u00b7 ${gpu.free_vram_gb}GB free`;
                badge.className = 'status-badge ready';
            }
        } else {
            dot.style.background = '#fbbf24';
            text.textContent = 'GPU Status Unknown';
        }
    } catch {
        dot.style.background = '#fbbf24';
        text.textContent = 'GPU Status Unknown';
    }
}

checkHealth(); setInterval(checkHealth, 10000);
checkGPUHealth(); setInterval(checkGPUHealth, 5000);

// ── Logo home ──────────────────────────────────────────────────────────────

document.getElementById('logoHome').addEventListener('click', () => ui.reset());

// ── UI object ──────────────────────────────────────────────────────────────

const ui = {
    fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    },

    async handleSubmit() {
        if (typeof Auth !== 'undefined' && !Auth.isLoggedIn()) { Auth.showModal(); return; }

        let data = {};

        if (activeTab === 'text') {
            const prompt = document.getElementById('textPrompt').value.trim();
            if (!prompt) { alert('Please enter a prompt.'); return; }
            data = { task_type: 'text', prompt };
        } else if (activeTab === 'image') {
            const input = document.getElementById('imageFile');
            if (!input || !input.files[0]) { alert('Please upload an image.'); return; }
            data = {
                task_type: 'image',
                prompt: document.getElementById('imageDesc').value || 'A simple animation',
                image_base64: await this.fileToBase64(input.files[0])
            };
        } else {
            const input = document.getElementById('videoFile');
            if (!input || !input.files[0]) { alert('Please upload a video.'); return; }
            data = { task_type: 'video', video_base64: await this.fileToBase64(input.files[0]) };
        }

        data.max_tokens = parseInt(document.getElementById('maxTokens').value) || 5556;
        data.temperature = parseFloat(document.getElementById('temperature').value) || 0.9;
        data.top_p = parseFloat(document.getElementById('topP').value) || 0.25;
        data.top_k = parseInt(document.getElementById('topK').value) || 5;
        data.use_sampling = document.getElementById('useSampling').checked;

        // Set UI to loading
        const btn = document.getElementById('generateBtn');
        btn.disabled = true;
        btn.innerHTML = '<div class="btn-spinner"></div> Generating...';

        const placeholder = document.getElementById('placeholder');
        const overlay = document.getElementById('loadingOverlay');
        const toolbar = document.getElementById('toolbar');
        const loadingText = document.getElementById('loadingText');
        const jobIdDisplay = document.getElementById('jobIdDisplay');

        // Clear previous lottie
        const oldMount = document.getElementById('lottie-mount');
        if (oldMount) oldMount.remove();
        if (lottieAnimation) { lottieAnimation.destroy(); lottieAnimation = null; }

        placeholder.classList.add('hidden');
        toolbar.classList.add('hidden');
        document.getElementById('jsonPane').style.display = 'none';
        document.getElementById('outputMeta').textContent = '';
        overlay.classList.add('visible');
        loadingText.textContent = 'SUBMITTING JOB...';
        jobIdDisplay.textContent = '';

        try {
            const response = await fetch('api/generate', {
                method: 'POST',
                headers: typeof Auth !== 'undefined' ? Auth.getAuthHeaders() : { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            const result = await response.json();

            if (result.success) {
                currentJobId = result.job_id;
                jobIdDisplay.textContent = `Job: ${result.job_id}`;
                loadingText.textContent = 'QUEUED — waiting to process...';
                this.startPolling();
            } else {
                alert('Failed to submit: ' + (result.error || 'Unknown error'));
                this.resetLoading(btn);
            }
        } catch (err) {
            alert('Error: ' + err.message);
            this.resetLoading(btn);
        }
    },

    startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(async () => {
            try {
                const r = await fetch(`api/status/${currentJobId}`);
                const d = await r.json();
                if (!d.success) return;

                const loadingText = document.getElementById('loadingText');
                if (d.status === 'QUEUED') {
                    loadingText.textContent = 'QUEUED — waiting to process...';
                } else if (d.status === 'PROCESSING') {
                    loadingText.textContent = 'GENERATING — this may take 1–5 minutes...';
                } else if (d.status === 'COMPLETED') {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    this.showResult(d);
                } else if (d.status === 'FAILED') {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    alert('Job failed: ' + (d.error || 'Unknown error'));
                    this.reset();
                }
            } catch (err) {
                console.error('Polling error:', err);
            }
        }, 2000);
    },

    showResult(data) {
        const overlay = document.getElementById('loadingOverlay');
        const toolbar = document.getElementById('toolbar');
        const btn = document.getElementById('generateBtn');

        overlay.classList.remove('visible');
        btn.disabled = false;
        btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> Generate Animation';

        let resultData = data.result;
        if (typeof resultData === 'string') {
            try { resultData = JSON.parse(resultData); } catch(e) { resultData = null; }
        }

        if (!resultData || !resultData.animation) {
            alert('No animation in result');
            this.reset();
            return;
        }

        let anim = resultData.animation;
        if (typeof anim === 'string') {
            try { anim = JSON.parse(anim); } catch(e) { alert('Invalid animation data'); this.reset(); return; }
        }

        animationData = anim;
        this.renderAnimation(anim);

        // Meta
        const tokens = resultData.tokens_generated || '—';
        const layers = resultData.layers || anim.layers?.length || '—';
        const elapsed = resultData.elapsed_seconds ? resultData.elapsed_seconds.toFixed(1) + 's' : '—';
        document.getElementById('outputMeta').textContent = `${tokens} tokens \u00b7 ${layers} layers \u00b7 ${elapsed}`;

        // Stats bar
        generatedCount++;
        document.getElementById('statGenerated').textContent = generatedCount;
        document.getElementById('statTokens').textContent = tokens;
        document.getElementById('statLayers').textContent = layers;
        document.getElementById('statDuration').textContent = elapsed;

        // JSON pane
        const jsonStr = JSON.stringify(anim, null, 2);
        const lines = jsonStr.split('\n');
        document.getElementById('jsonViewer').textContent =
            lines.slice(0, 100).join('\n') + (lines.length > 100 ? '\n\u2026' : '');

        toolbar.classList.remove('hidden');
    },

    renderAnimation(data) {
        const area = document.getElementById('previewArea');
        const oldMount = document.getElementById('lottie-mount');
        if (oldMount) oldMount.remove();
        if (lottieAnimation) { lottieAnimation.destroy(); lottieAnimation = null; }

        const mount = document.createElement('div');
        mount.id = 'lottie-mount';
        area.appendChild(mount);

        const w = data.w || 512, h = data.h || 512;
        const size = Math.min(mount.clientWidth - 40, mount.clientHeight - 40, 500);
        const ratio = w / h;
        const elW = ratio >= 1 ? size : size * ratio;
        const elH = ratio >= 1 ? size / ratio : size;

        const el = document.createElement('div');
        el.style.cssText = `width:${elW}px;height:${elH}px;`;
        mount.appendChild(el);

        lottieAnimation = lottie.loadAnimation({
            container: el, renderer: 'svg', loop: true, autoplay: true, animationData: data
        });
    },

    toggleJsonPane() {
        const pane = document.getElementById('jsonPane');
        pane.style.display = pane.style.display === 'block' ? 'none' : 'block';
    },

    downloadJson() {
        if (!animationData) return;
        const blob = new Blob([JSON.stringify(animationData, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `omnilottie_${currentJobId || Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(a.href);
    },

    resetLoading(btn) {
        document.getElementById('loadingOverlay').classList.remove('visible');
        document.getElementById('placeholder').classList.remove('hidden');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> Generate Animation';
        }
    },

    reset() {
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        if (lottieAnimation) { lottieAnimation.destroy(); lottieAnimation = null; }
        animationData = null;
        currentJobId = null;

        const oldMount = document.getElementById('lottie-mount');
        if (oldMount) oldMount.remove();

        document.getElementById('loadingOverlay').classList.remove('visible');
        document.getElementById('placeholder').classList.remove('hidden');
        document.getElementById('toolbar').classList.add('hidden');
        document.getElementById('jsonPane').style.display = 'none';
        document.getElementById('outputMeta').textContent = '';

        const btn = document.getElementById('generateBtn');
        btn.disabled = false;
        btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> Generate Animation';
    }
};

document.getElementById('downloadBtn').addEventListener('click', () => ui.downloadJson());

// ── History ────────────────────────────────────────────────────────────────

let historyVisible = false;

function toggleHistory() {
    const panel = document.getElementById('historyPanel');
    historyVisible = !historyVisible;
    panel.style.display = historyVisible ? 'block' : 'none';
    if (historyVisible) loadHistory();
}

async function loadHistory() {
    const content = document.getElementById('historyContent');
    content.innerHTML = '<div style="text-align:center;color:var(--gray-400);font-family:\'JetBrains Mono\',monospace;font-size:13px;padding:32px 0;">Loading...</div>';

    if (typeof Auth !== 'undefined' && !Auth.isLoggedIn()) {
        content.innerHTML = '<div style="text-align:center;color:var(--gray-400);font-size:13px;padding:32px 0;">Sign in to view history.</div>';
        return;
    }

    try {
        const headers = typeof Auth !== 'undefined' ? Auth.getAuthHeaders() : {};
        const r = await fetch('api/user/jobs', { headers });
        const data = await r.json();
        const jobs = data.jobs || data || [];

        if (!jobs.length) {
            content.innerHTML = '<div style="text-align:center;color:var(--gray-400);font-family:\'JetBrains Mono\',monospace;font-size:13px;padding:32px 0;">No jobs yet.</div>';
            return;
        }

        content.innerHTML = '';
        jobs.slice(0, 30).forEach(job => {
            const params = job.params || {};
            const taskType = params.task_type || 'text';
            const prompt = params.prompt || (taskType === 'image' ? '[image input]' : taskType === 'video' ? '[video input]' : '—');
            const status = job.status || 'UNKNOWN';
            const created = job.created_at ? new Date(job.created_at).toLocaleString() : '';

            const statusColor = { COMPLETED: '#10b981', FAILED: '#ef4444', PROCESSING: '#f97316', QUEUED: '#fbbf24' }[status] || '#9ca3af';

            const row = document.createElement('div');
            row.className = 'history-row';
            row.innerHTML = `
                <span class="history-status" style="background:${statusColor};"></span>
                <div class="history-thumb">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                </div>
                <div class="history-info">
                    <div class="history-prompt">${prompt}</div>
                    <div class="history-meta">${taskType} &middot; ${created} &middot; ${job.job_id ? job.job_id.slice(0,8) : ''}&hellip;</div>
                </div>
                <span class="history-badge ${status}">${status}</span>
            `;

            if (status === 'COMPLETED') {
                row.onclick = () => loadHistoryResult(job.job_id);
            }

            content.appendChild(row);
        });
    } catch (e) {
        content.innerHTML = `<div style="text-align:center;color:var(--error);font-size:13px;padding:32px 0;">Failed to load history: ${e.message}</div>`;
    }
}

async function loadHistoryResult(jobId) {
    try {
        const r = await fetch(`api/status/${jobId}`);
        const data = await r.json();
        if (data.success) ui.showResult(data);
    } catch(e) {
        alert('Failed to load result: ' + e.message);
    }
}

// ── Examples ───────────────────────────────────────────────────────────────

async function loadExamples() {
    try {
        const r = await fetch('examples');
        const data = await r.json();
        let hasAny = false;

        if (data.text && data.text.length) {
            hasAny = true;
            const sec = document.getElementById('textExamples');
            const list = document.createElement('div');
            list.className = 'example-chips-list';
            data.text.slice(0, 10).forEach(t => {
                const btn = document.createElement('button');
                btn.className = 'example-chip';
                btn.textContent = t;
                btn.title = t;
                btn.onclick = () => {
                    document.getElementById('textPrompt').value = t;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                };
                list.appendChild(btn);
            });
            sec.appendChild(list);
        }

        if (data.images && data.images.length) {
            hasAny = true;
            const sec = document.getElementById('imageExamples');
            const row = document.createElement('div');
            row.className = 'example-media-row';
            data.images.slice(0, 16).forEach(img => {
                const wrap = document.createElement('div');
                wrap.className = 'example-img-chip';
                wrap.title = img.description || 'Example image';
                const el = document.createElement('img');
                el.src = img.url; el.loading = 'lazy';
                wrap.appendChild(el);
                wrap.onclick = async () => {
                    const res = await fetch(img.url);
                    const blob = await res.blob();
                    const file = new File([blob], img.url.split('/').pop(), { type: blob.type });
                    const dt = new DataTransfer(); dt.items.add(file);
                    const input = document.getElementById('imageFile');
                    input.files = dt.files;
                    onFileSelect('image', input);
                    if (img.description) document.getElementById('imageDesc').value = img.description;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                };
                row.appendChild(wrap);
            });
            sec.appendChild(row);
        }

        if (data.videos && data.videos.length) {
            hasAny = true;
            const sec = document.getElementById('videoExamples');
            const row = document.createElement('div');
            row.className = 'example-media-row';
            data.videos.slice(0, 12).forEach(url => {
                const wrap = document.createElement('div');
                wrap.className = 'example-vid-chip';
                wrap.title = url.split('/').pop();
                const vid = document.createElement('video');
                vid.src = url; vid.muted = true; vid.preload = 'metadata';
                wrap.appendChild(vid);
                wrap.onmouseenter = () => vid.play();
                wrap.onmouseleave = () => { vid.pause(); vid.currentTime = 0; };
                wrap.onclick = async () => {
                    const res = await fetch(url);
                    const blob = await res.blob();
                    const file = new File([blob], url.split('/').pop(), { type: 'video/mp4' });
                    const dt = new DataTransfer(); dt.items.add(file);
                    const input = document.getElementById('videoFile');
                    input.files = dt.files;
                    onFileSelect('video', input);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                };
                row.appendChild(wrap);
            });
            sec.appendChild(row);
        }

        if (hasAny) {
            document.getElementById('examplesPanel').style.display = 'block';
            switchExamplesTab(activeTab);
        }
    } catch(e) { /* examples are optional */ }
}

loadExamples();
