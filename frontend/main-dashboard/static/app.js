// GPU-Polling hub: live GPU monitor + app cards that pause while the GPU is in use.

// Look for each app (by service key). Unknown apps fall back to a rotating dye + weave.
const LOOK = {
  'ai-chat':         { icon: 'chat',   a: '#4f46e5', t: '#eef0ff', t2: '#dfe2ff', p: 'kilim' },
  'sdxl':            { icon: 'image',  a: '#92400e', t: '#fbf1e6', t2: '#f1dcc2', p: 'check' },
  'z-image':         { icon: 'bolt',   a: '#a16207', t: '#fcf5e3', t2: '#f5e5b8', p: 'diamond' },
  'qwen-edit':       { icon: 'wand',   a: '#be185d', t: '#fdedf5', t2: '#f9d2e6', p: 'twill' },
  'qwen-variations': { icon: 'layers', a: '#6d28d9', t: '#f3eeff', t2: '#e2d6ff', p: 'herring' },
  'omnilottie':      { icon: 'play',   a: '#4d7c0f', t: '#f2f7e8', t2: '#dde9c6', p: 'wave' },
};
const DYES = [['#1d4ed8','#edf2ff','#d7e2ff'],['#be185d','#fdedf5','#f9d2e6'],['#4d7c0f','#f2f7e8','#dde9c6'],['#c2410c','#fff1ea','#ffdcc8']];
const WEAVES = ['twill','check','diamond','dots','kilim','wave','herring'];
const LABEL = { free: 'GPU free', warm: 'GPU ready', loading: 'Loading a model', busy: 'GPU busy', external: 'GPU in use', unknown: 'GPU status unknown', offline: 'GPU offline' };
const TITLE = { free: 'The GPU is free', warm: 'Ready — a model is loaded', loading: 'Loading a model', busy: 'Running a job', external: 'In use by another program', unknown: 'GPU status unknown', offline: 'Cannot reach the GPU' };

class GPUOrchestratorHub {
  constructor() {
    this.services = [];
    this.gpu = { state: 'unknown', is_available: false, message: 'Checking the GPU…' };
    this.fails = 0;
    this.grid = document.getElementById('servicesGrid');
    this.searchEl = document.getElementById('serviceSearch');
    this.summaryEl = document.getElementById('serviceSummary');
    this.refreshBtn = document.getElementById('refreshServices');
    this.query = '';
    try { this.query = (localStorage.getItem('hub_service_filter') || '').toLowerCase(); this.searchEl.value = this.query; } catch (e) {}
    this.searchEl.addEventListener('input', () => {
      this.query = this.searchEl.value.trim().toLowerCase();
      try { localStorage.setItem('hub_service_filter', this.searchEl.value); } catch (e) {}
      this.render();
    });
    this.refreshBtn.addEventListener('click', () => this.loadServices());
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { this.loadGPU(); this.loadServices(); } });
    // Block opening an app while the GPU is in use.
    document.addEventListener('click', e => {
      const el = e.target.closest && e.target.closest('[data-gpu="1"]');
      if (el && !this.gpu.is_available) { e.preventDefault(); this.toast((this.gpu.message || 'The GPU is in use') + '. Try again when it is free.'); }
    });
    this.loadGPU(); this.loadServices();
    setInterval(() => { if (!document.hidden) this.loadGPU(); }, 3000);
    setInterval(() => { if (!document.hidden) this.loadServices(); }, 30000);
  }

  $(id) { return document.getElementById(id); }
  esc(v) { const d = document.createElement('div'); d.textContent = v == null ? '' : String(v); return d.innerHTML.replace(/"/g, '&quot;'); }
  safeUrl(u) { try { const url = new URL(u, location.href); return /^https?:$/.test(url.protocol) ? url.href : '#'; } catch (e) { return '#'; } }
  ico(n) { return `<svg class="i"><use href="#i-${n}"/></svg>`; }
  toast(m) { const t = this.$('toast'); t.textContent = m; t.classList.add('show'); clearTimeout(this.tt); this.tt = setTimeout(() => t.classList.remove('show'), 3500); }
  fetchT(url, ms = 6000) { const c = new AbortController(); const t = setTimeout(() => c.abort(), ms); return fetch(url, { signal: c.signal, cache: 'no-store' }).finally(() => clearTimeout(t)); }

  async loadGPU() {
    try {
      const d = await (await this.fetchT('api/gpu/health', 5000)).json();
      this.fails = 0;
      this.gpu = d.state ? d : { ...d, state: d.is_available ? 'free' : (d.status === 'ok' ? 'busy' : 'offline'), message: d.error || (d.is_available ? 'GPU free' : 'GPU in use') };
    } catch (e) {
      // Never report the GPU as free when we cannot read it.
      if (++this.fails >= 2) this.gpu = { state: 'offline', is_available: false, message: 'Cannot reach the GPU status service' };
    }
    this.paintGPU();
  }

  appName(id) { const s = this.services.find(x => x.app_id === id || x.key === id); return s ? s.name : id; }

  paintGPU() {
    const g = this.gpu, s = LABEL[g.state] ? g.state : 'unknown', blocked = !g.is_available;
    const used = +g.used_vram_gb || 0, total = +g.total_vram_gb || 0, ours = +g.orchestrator_vram_gb || 0, ext = +g.external_vram_gb || 0;
    const util = g.utilization_pct !== undefined ? Number(g.utilization_pct) : null;
    // top bar pill
    this.$('gpuPill').dataset.s = s; this.$('gpuPill').title = g.message || '';
    this.$('pillLabel').textContent = LABEL[s] + (blocked && util !== null ? ` · ${util.toFixed(0)}%` : '');
    this.$('pillBar').style.width = total ? Math.min(100, used / total * 100) + '%' : '0';
    this.$('pillVram').textContent = total ? `${used.toFixed(1)} / ${total.toFixed(0)} GB` : '';
    // monitor
    this.$('monitor').dataset.s = s;
    this.$('monTitle').textContent = TITLE[s];
    this.$('monMsg').textContent = g.message || '';
    const wait = g.retry_after_seconds ? ` · checking again every few seconds` : '';
    this.$('monNote').textContent = blocked ? 'Apps are paused until the GPU is free' + wait : 'Apps are open — pick one to start';
    this.$('vUsed').textContent = total ? `${used.toFixed(1)} / ${total.toFixed(1)} GB` : '–';
    this.$('vOurs').style.width = total ? (ours / total * 100) + '%' : '0';
    this.$('vExt').style.width = total ? (Math.min(ext, total - ours) / total * 100) + '%' : '0';
    this.$('lOurs').textContent = total ? ours.toFixed(1) + ' GB' : '–';
    this.$('lExt').textContent = total ? ext.toFixed(1) + ' GB' : '–';
    this.$('lFree').textContent = total ? `${(+g.free_vram_gb || 0).toFixed(1)} GB free` : '';
    this.$('sUtil').textContent = util !== null ? util.toFixed(0) + '%' : '–';
    this.$('sActive').textContent = g.active_app ? this.appName(g.active_app) : (s === 'external' ? 'Another program' : 'Nothing');
    this.$('sJobs').textContent = g.active_jobs !== undefined ? String(g.active_jobs) : '–';
    // gate app cards
    document.querySelectorAll('[data-gpu="1"]').forEach(el => {
      el.classList.toggle('blocked', blocked);
      el.setAttribute('aria-disabled', blocked ? 'true' : 'false');
      el.title = blocked ? (g.message || 'The GPU is in use') : '';
      const st = el.querySelector('.st'); if (st && !st.classList.contains('off')) st.textContent = blocked ? 'Paused' : 'Ready';
    });
  }

  async loadServices() {
    this.refreshBtn.disabled = true;
    try {
      const r = await this.fetchT('api/services', 10000);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      this.services = (await r.json()).services || [];
      this.render();
    } catch (e) {
      if (!this.services.length) {
        this.grid.setAttribute('aria-busy', 'false');
        this.grid.innerHTML = '<div class="empty" role="alert">Could not load the app list. <button type="button" class="linkbtn" id="retryServices">Try again</button></div>';
        this.$('retryServices').addEventListener('click', () => this.loadServices());
      }
    } finally { this.refreshBtn.disabled = false; }
  }

  render() {
    this.grid.setAttribute('aria-busy', 'false');
    const online = this.services.filter(s => s.status === 'online').length;
    this.summaryEl.textContent = this.services.length ? `${online} of ${this.services.length} online` : '';
    const q = this.query;
    const list = this.services.filter(s => !q || [s.name, s.app_id, s.description].join(' ').toLowerCase().includes(q));
    if (!this.services.length) { this.grid.innerHTML = '<div class="empty">No apps are registered yet.</div>'; return; }
    if (!list.length) { this.grid.innerHTML = '<div class="empty">No apps match that filter.</div>'; return; }
    this.grid.innerHTML = list.map((s, i) => {
      const d = DYES[i % DYES.length];
      const L = { icon: 'spark', a: d[0], t: d[1], t2: d[2], p: WEAVES[i % WEAVES.length], ...(LOOK[s.key] || {}) };
      const off = s.status !== 'online';
      const path = (s.url || '').replace(/^https?:\/\/[^/]+/, '');
      return `<a class="sw${off ? ' off' : ''}" href="${this.esc(this.safeUrl(s.url))}" data-gpu="1" style="--a:${L.a};--t:${L.t};--t2:${L.t2}" aria-label="${this.esc(s.name)}">
        <div class="weave p-${L.p}"><span class="motif" aria-hidden="true">${this.ico(L.icon)}</span><div class="pause"><span>Paused · GPU in use</span></div></div>
        <div class="body"><h3>${this.esc(s.name)}</h3><p>${this.esc(s.description)}</p>
          <div class="row"><span class="st${off ? ' off' : ''}">${off ? 'Offline' : 'Ready'}</span><span class="path">${this.esc(path)}</span><span class="go">${this.ico('arrow')}</span></div>
        </div></a>`;
    }).join('');
    this.paintGPU();
  }
}

document.addEventListener('DOMContentLoaded', () => { window.hub = new GPUOrchestratorHub(); });
