/**
 * auth.js — JWT auth module (global)
 *
 * Two modes:
 *   1. Redirect mode  — set window.AUTH_REDIRECT_URL before loading this script.
 *      If the user is not logged in, they are sent to that URL with a ?return= param.
 *      Used by every app that is NOT the main dashboard.
 *
 *   2. Modal mode (default) — if AUTH_REDIRECT_URL is not set, falls back to an
 *      inline login/signup modal. Used as a fallback.
 *
 * All pages share the same localStorage key so the token is global across the domain.
 */
(function () {
  const TOKEN_KEY = 'gpu_auth_token';

  // ── Token helpers ────────────────────────────────────────────────────────────

  function getToken() { return localStorage.getItem(TOKEN_KEY); }
  function setToken(token) { localStorage.setItem(TOKEN_KEY, token); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  function decodePayload(token) {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      return JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    } catch { return null; }
  }

  function getUsername() {
    const token = getToken();
    if (!token) return null;
    const p = decodePayload(token);
    return p ? p.username : null;
  }

  function isLoggedIn() {
    const token = getToken();
    if (!token) return false;
    const p = decodePayload(token);
    if (!p) return false;
    if (p.exp && Date.now() / 1000 > p.exp) { clearToken(); return false; }
    return true;
  }

  function getAuthHeaders() {
    return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (getToken() || '') };
  }

  async function fetchWithAuth(url, options = {}) {
    options.headers = { ...getAuthHeaders(), ...(options.headers || {}) };
    const response = await fetch(url, options);
    if (response.status === 401) { clearToken(); redirectToLogin(); }
    return response;
  }

  // ── Redirect helpers ─────────────────────────────────────────────────────────

  function redirectToLogin() {
    const redirectUrl = window.AUTH_REDIRECT_URL;
    if (redirectUrl) {
      window.location.href = redirectUrl + '?return=' + encodeURIComponent(window.location.href);
    } else {
      showModal();
    }
  }

  function getReturnUrl() {
    return new URLSearchParams(window.location.search).get('return') || null;
  }

  // ── Modal (fallback) ─────────────────────────────────────────────────────────

  function injectStyles() {
    if (document.getElementById('auth-modal-styles')) return;
    const style = document.createElement('style');
    style.id = 'auth-modal-styles';
    style.textContent = `
      #auth-overlay {
        position: fixed; inset: 0; z-index: 9999;
        background: rgba(0,0,0,0.55); backdrop-filter: blur(4px);
        display: flex; align-items: center; justify-content: center;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      }
      #auth-box {
        background: #fff; border-radius: 12px; padding: 36px 32px;
        width: 100%; max-width: 400px; box-shadow: 0 20px 60px rgba(0,0,0,0.18);
      }
      #auth-box h2 { margin: 0 0 6px; font-size: 22px; font-weight: 700; color: #111; }
      #auth-box p.auth-subtitle { margin: 0 0 24px; font-size: 14px; color: #6b7280; }
      .auth-tabs { display: flex; border-bottom: 2px solid #e5e7eb; margin-bottom: 24px; }
      .auth-tab { flex: 1; padding: 10px; text-align: center; cursor: pointer; font-size: 14px; font-weight: 500; color: #6b7280; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color .15s, border-color .15s; }
      .auth-tab.active { color: #4f46e5; border-bottom-color: #4f46e5; }
      .auth-form { display: none; }
      .auth-form.active { display: block; }
      .auth-field { margin-bottom: 16px; }
      .auth-field label { display: block; font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 6px; }
      .auth-field input { width: 100%; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; font-family: inherit; outline: none; transition: border-color .15s; box-sizing: border-box; }
      .auth-field input:focus { border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79,70,229,0.1); }
      .auth-submit { width: 100%; padding: 11px; background: #4f46e5; color: #fff; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: background .15s; margin-top: 4px; }
      .auth-submit:hover { background: #4338ca; }
      .auth-submit:disabled { background: #a5b4fc; cursor: not-allowed; }
      .auth-error { margin-top: 12px; padding: 10px 12px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; font-size: 13px; color: #dc2626; display: none; }
      #auth-user-pill { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; background: #f3f4f6; border-radius: 20px; font-size: 13px; font-weight: 500; color: #374151; }
      #auth-logout-btn { background: none; border: none; cursor: pointer; color: #6b7280; font-size: 12px; text-decoration: underline; padding: 0; }
      #auth-logout-btn:hover { color: #111; }
    `;
    document.head.appendChild(style);
  }

  function injectModal() {
    if (document.getElementById('auth-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'auth-overlay';
    overlay.innerHTML = `
      <div id="auth-box">
        <h2>GPU Workspace</h2>
        <p class="auth-subtitle">Sign in to generate images and chat.</p>
        <div class="auth-tabs">
          <div class="auth-tab active" data-tab="login">Sign In</div>
          <div class="auth-tab" data-tab="signup">Create Account</div>
        </div>
        <div class="auth-form active" id="auth-login-form">
          <div class="auth-field"><label>Email</label><input type="email" id="login-email" placeholder="you@example.com" autocomplete="email" /></div>
          <div class="auth-field"><label>Password</label><input type="password" id="login-password" placeholder="••••••••" autocomplete="current-password" /></div>
          <button class="auth-submit" id="login-submit">Sign In</button>
          <div class="auth-error" id="login-error"></div>
        </div>
        <div class="auth-form" id="auth-signup-form">
          <div class="auth-field"><label>Username</label><input type="text" id="signup-username" placeholder="cooluser123" autocomplete="username" /></div>
          <div class="auth-field"><label>Email</label><input type="email" id="signup-email" placeholder="you@example.com" autocomplete="email" /></div>
          <div class="auth-field"><label>Password</label><input type="password" id="signup-password" placeholder="Min. 8 characters" autocomplete="new-password" /></div>
          <button class="auth-submit" id="signup-submit">Create Account</button>
          <div class="auth-error" id="signup-error"></div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.querySelectorAll('.auth-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        overlay.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        overlay.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('auth-' + tab.dataset.tab + '-form').classList.add('active');
      });
    });

    document.getElementById('login-submit').addEventListener('click', () => submitLogin(
      document.getElementById('login-email').value.trim(),
      document.getElementById('login-password').value,
      document.getElementById('login-error'),
      document.getElementById('login-submit')
    ));
    ['login-email', 'login-password'].forEach(id =>
      document.getElementById(id).addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('login-submit').click(); })
    );

    document.getElementById('signup-submit').addEventListener('click', () => submitSignup(
      document.getElementById('signup-username').value.trim(),
      document.getElementById('signup-email').value.trim(),
      document.getElementById('signup-password').value,
      document.getElementById('signup-error'),
      document.getElementById('signup-submit')
    ));
    ['signup-username', 'signup-email', 'signup-password'].forEach(id =>
      document.getElementById(id).addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('signup-submit').click(); })
    );
  }

  function showError(el, msg) { el.textContent = msg; el.style.display = 'block'; }

  async function submitLogin(email, password, errEl, btn) {
    errEl.style.display = 'none';
    if (!email || !password) { showError(errEl, 'Please fill in all fields.'); return; }
    btn.disabled = true; btn.textContent = 'Signing in…';
    try {
      const res = await fetch('auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
      const data = await res.json();
      if (res.ok) onAuthSuccess(data);
      else showError(errEl, data.error || 'Sign in failed.');
    } catch { showError(errEl, 'Network error. Please try again.'); }
    finally { btn.disabled = false; btn.textContent = 'Sign In'; }
  }

  async function submitSignup(username, email, password, errEl, btn) {
    errEl.style.display = 'none';
    if (!username || !email || !password) { showError(errEl, 'Please fill in all fields.'); return; }
    btn.disabled = true; btn.textContent = 'Creating account…';
    try {
      const res = await fetch('auth/signup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, email, password }) });
      const data = await res.json();
      if (res.ok) onAuthSuccess(data);
      else showError(errEl, data.error || 'Sign up failed.');
    } catch { showError(errEl, 'Network error. Please try again.'); }
    finally { btn.disabled = false; btn.textContent = 'Create Account'; }
  }

  function showModal() {
    injectStyles();
    injectModal();
    const overlay = document.getElementById('auth-overlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function hideModal() {
    const overlay = document.getElementById('auth-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  function onAuthSuccess(data) {
    setToken(data.token);
    hideModal();
    updateNavUserIndicator(data.username);
    // Redirect back if we came from another page
    const returnUrl = getReturnUrl();
    if (returnUrl) {
      window.location.href = returnUrl;
      return;
    }
    // Notify any listeners (used by main dashboard inline form)
    if (typeof window.onAuthSuccessCallback === 'function') {
      window.onAuthSuccessCallback(data);
    }
  }

  // ── Nav user indicator ────────────────────────────────────────────────────────

  function updateNavUserIndicator(username) {
    let container = document.getElementById('auth-user-area');
    if (!container) {
      const navContent = document.querySelector('.nav-content');
      if (navContent) {
        container = document.createElement('div');
        container.id = 'auth-user-area';
        container.style.cssText = 'display:flex;align-items:center;gap:8px;';
        navContent.appendChild(container);
      }
    }
    if (!container) return;
    container.innerHTML = `
      <div id="auth-user-pill">
        <span>@${username}</span>
        <button id="auth-logout-btn" title="Sign out">Sign out</button>
      </div>`;
    document.getElementById('auth-logout-btn').addEventListener('click', () => {
      clearToken();
      location.reload();
    });
  }

  // ── Guest mode ───────────────────────────────────────────────────────────────

  const GUEST_KEY = 'gpu_guest_mode';
  function isGuest() { return localStorage.getItem(GUEST_KEY) === 'true'; }

  // ── Init ─────────────────────────────────────────────────────────────────────

  function init() {
    injectStyles();
    if (!isLoggedIn() && !isGuest()) {
      redirectToLogin();
    } else {
      const username = isGuest() ? 'Guest' : getUsername();
      if (username) updateNavUserIndicator(username);
    }
  }

  window.Auth = {
    getToken, setToken, clearToken,
    getUsername, isLoggedIn, isGuest,
    getAuthHeaders, fetchWithAuth,
    showModal, hideModal,
    submitLogin, submitSignup, onAuthSuccess,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
