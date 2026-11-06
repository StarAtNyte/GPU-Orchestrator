/*
 * GPU Guard — shared by every app UI.
 *
 * - Polls api/gpu/health (proxied to the orchestrator's /health/gpu).
 * - Keeps the header badge (#gpuStatusIndicator|#gpuStatusDot + #gpuStatusText) accurate.
 * - Shows a banner and blocks submit actions while the GPU is in use.
 *
 * Usage:  GPUGuard.init({ submit: '#generateBtn', inputs: '#userInput' });
 *
 * Blocking is done with capture-phase listeners and aria-disabled so it never
 * fights with a page's own disabled/enabled bookkeeping.
 */
(function () {
  const COLORS = {
    free: '#10B981', warm: '#10B981', loading: '#F59E0B',
    busy: '#F97316', external: '#EF4444', unknown: '#9CA3AF', offline: '#9CA3AF',
  };
  const LABEL = {
    free: 'GPU Free', warm: 'GPU Ready', loading: 'GPU Loading model',
    busy: 'GPU Busy', external: 'GPU In Use', unknown: 'GPU Unknown', offline: 'Offline',
  };

  const G = {
    state: null,
    opts: { submit: '', inputs: '', pollMs: 3000, endpoint: 'api/gpu/health' },
    listeners: [],
    timer: null,
    failures: 0,

    /** true when a new request may be sent right now */
    isAvailable() { return !this.state || this.state.is_available !== false; },
    message() { return (this.state && this.state.message) || 'GPU is currently in use.'; },
    onChange(fn) { this.listeners.push(fn); },

    init(opts) {
      Object.assign(this.opts, opts || {});
      this._injectStyles();
      this._bindBlockers();
      this.refresh();
      this.timer = setInterval(() => this.refresh(), this.opts.pollMs);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) this.refresh(); });
    },

    async refresh() {
      try {
        const res = await fetch(this.opts.endpoint, { cache: 'no-store', signal: AbortSignal.timeout(4000) });
        const data = await res.json();
        this.failures = 0;
        if (data.state) this.state = data;
        else this.state = this._legacy(data);
      } catch (e) {
        // Can't reach the orchestrator at all: never claim the GPU is free.
        if (++this.failures >= 2) {
          this.state = { state: 'offline', is_available: false, message: 'Cannot reach the orchestrator' };
        }
      }
      this._render();
      this.listeners.forEach(fn => { try { fn(this.state); } catch (_) {} });
    },

    // Older orchestrator without the "state" field.
    _legacy(d) {
      if (d.status !== 'ok') return { state: 'unknown', is_available: d.is_available !== false, message: 'GPU status unknown' };
      const free = d.is_available;
      return { ...d, state: free ? 'free' : 'busy', message: free ? `Free • ${d.free_vram_gb} GB available` : 'GPU is in use' };
    },

    _render() {
      const s = this.state || { state: 'unknown', message: 'Checking GPU…' };
      const key = s.state in COLORS ? s.state : 'unknown';
      const color = COLORS[key];

      const dot = document.getElementById('gpuStatusIndicator') || document.getElementById('gpuStatusDot');
      const txt = document.getElementById('gpuStatusText');
      if (dot) {
        dot.style.background = color;
        dot.style.animation = (key === 'busy' || key === 'loading') ? 'pulse 2s infinite' : 'none';
      }
      if (txt) {
        txt.textContent = s.message || LABEL[key];
        txt.title = this._detail(s);
      }

      const blocked = !this.isAvailable();
      document.documentElement.classList.toggle('gpu-blocked', blocked);
      this._targets().forEach(el => {
        el.setAttribute('aria-disabled', blocked ? 'true' : 'false');
        el.classList.toggle('gpu-blocked-ctl', blocked);
        if (blocked) el.title = this.message(); else if (el.title === this._lastMsg) el.title = '';
      });
      this._lastMsg = blocked ? this.message() : '';
      this._banner(blocked, s, color);
    },

    _detail(s) {
      const bits = [];
      if (s.total_vram_gb) bits.push(`VRAM ${Number(s.used_vram_gb).toFixed(1)} / ${Number(s.total_vram_gb).toFixed(1)} GB`);
      if (s.orchestrator_vram_gb) bits.push(`ours ${Number(s.orchestrator_vram_gb).toFixed(1)} GB`);
      if (s.external_vram_gb) bits.push(`other ${Number(s.external_vram_gb).toFixed(1)} GB`);
      if (s.utilization_pct !== undefined) bits.push(`util ${Number(s.utilization_pct).toFixed(0)}%`);
      return bits.join(' • ');
    },

    _banner(blocked, s, color) {
      let b = document.getElementById('gpuGuardBanner');
      if (!blocked) { if (b) b.remove(); return; }
      if (!b) {
        b = document.createElement('div');
        b.id = 'gpuGuardBanner';
        b.setAttribute('role', 'status');
        b.setAttribute('aria-live', 'polite');
        document.body.prepend(b);
      }
      const retry = s.retry_after_seconds ? ` Checking again automatically.` : '';
      b.style.borderColor = color;
      b.innerHTML = `<span class="gg-dot" style="background:${color}"></span>` +
        `<strong>${LABEL[s.state] || 'GPU unavailable'}.</strong> ` +
        `<span>${this._esc(s.message || '')} — requests are paused until it is free.${retry}</span>`;
    },

    _esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; },

    _targets() {
      const sel = [this.opts.submit, this.opts.inputs].filter(Boolean).join(',');
      return sel ? Array.from(document.querySelectorAll(this.opts.submit || ':not(*)')) : [];
    },

    /** Shows a short toast; used when a click/submit is blocked. */
    toast(msg) {
      let t = document.getElementById('gpuGuardToast');
      if (!t) { t = document.createElement('div'); t.id = 'gpuGuardToast'; document.body.appendChild(t); }
      t.textContent = msg;
      t.classList.add('show');
      clearTimeout(this._tt);
      this._tt = setTimeout(() => t.classList.remove('show'), 3500);
    },

    _bindBlockers() {
      const submitSel = this.opts.submit;
      const block = (e) => {
        if (this.isAvailable()) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        this.toast(this.message());
      };
      if (submitSel) {
        document.addEventListener('click', (e) => {
          if (e.target.closest && e.target.closest(submitSel)) block(e);
        }, true);
        document.addEventListener('submit', (e) => {
          const btn = document.querySelector(submitSel);
          if (btn && e.target.contains(btn)) block(e);
        }, true);
      }
      if (this.opts.inputs) {
        document.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey && e.target.matches && e.target.matches(this.opts.inputs)) block(e);
        }, true);
      }
    },

    _injectStyles() {
      if (document.getElementById('gpuGuardStyles')) return;
      const st = document.createElement('style');
      st.id = 'gpuGuardStyles';
      st.textContent = `
        #gpuGuardBanner{position:sticky;top:0;z-index:9999;display:flex;align-items:center;gap:10px;
          padding:10px 16px;background:#fff7ed;color:#7c2d12;border-bottom:2px solid;font:14px/1.4 system-ui,sans-serif}
        @media (prefers-color-scheme: dark){#gpuGuardBanner{background:#2a1a10;color:#fed7aa}}
        #gpuGuardBanner .gg-dot{width:10px;height:10px;border-radius:50%;flex:none;animation:pulse 2s infinite}
        .gpu-blocked-ctl{opacity:.55;cursor:not-allowed !important}
        #gpuGuardToast{position:fixed;left:50%;bottom:28px;transform:translate(-50%,20px);opacity:0;pointer-events:none;
          max-width:90vw;padding:10px 16px;border-radius:10px;background:#111;color:#fff;font:14px system-ui,sans-serif;
          transition:all .2s;z-index:10000}
        #gpuGuardToast.show{opacity:1;transform:translate(-50%,0)}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}`;
      document.head.appendChild(st);
    },
  };

  window.GPUGuard = G;
})();
