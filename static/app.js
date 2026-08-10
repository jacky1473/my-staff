'use strict';

// ── TOAST ──────────────────────────────────────────────
class Toast {
  static show(title, msg, type = 'info', duration = 4000) {
    let c = document.getElementById('toast-container');
    if (!c) {
      c = document.createElement('div');
      c.id = 'toast-container';
      c.className = 'toast-container';
      document.body.appendChild(c);
    }
    const icons = { success: '✓', error: '✕', warning: '!', info: 'i' };
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.innerHTML = `<span style="font-weight:600;font-size:.8rem;">${icons[type]||'i'}</span>
      <div style="flex:1;"><div style="font-weight:600;font-size:.8rem;">${title}</div>
      <div style="font-size:.75rem;color:var(--text-muted);margin-top:1px;">${msg}</div></div>
      <button class="toast-close" onclick="this.closest('.toast').remove()">✕</button>`;
    c.appendChild(t);
    if (duration > 0) setTimeout(() => t.remove(), duration);
  }
}

// ── THEME ───────────────────────────────────────────────
class Theme {
  static init() {
    const saved = localStorage.getItem('theme') || 'dark-mode';
    document.documentElement.className = saved;
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.textContent = saved === 'dark-mode' ? '🌙' : '☀️';
      btn.addEventListener('click', () => {
        const dark = document.documentElement.classList.contains('dark-mode');
        const next = dark ? 'light-mode' : 'dark-mode';
        document.documentElement.className = next;
        localStorage.setItem('theme', next);
        btn.textContent = next === 'dark-mode' ? '🌙' : '☀️';
      });
    }
  }
}

// ── CLOCK ───────────────────────────────────────────────
function initClock() {
  const el = document.getElementById('liveClock');
  if (!el) return;
  const tick = () => {
    try {
      const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
      el.textContent = now.toTimeString().slice(0, 8) + ' IST';
    } catch { el.textContent = '--:--:--'; }
  };
  tick();
  setInterval(tick, 1000);
}

// ── PASSWORD TOGGLES ────────────────────────────────────
function initPasswordToggles() {
  document.querySelectorAll('.pw-toggle').forEach(btn => {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      const input = this.previousElementSibling;
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      this.textContent = show ? '🙈' : '👁';
    });
  });
}

// ── SIDEBAR TABS ────────────────────────────────────────
function initSidebarTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', function () {
      const target = this.dataset.target;
      if (!target) return;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      this.classList.add('active');
      const pane = document.getElementById(target);
      if (pane) pane.classList.add('active');
      try { sessionStorage.setItem('activeTab', target); } catch {}
    });
  });
  try {
    const saved = sessionStorage.getItem('activeTab');
    if (saved) {
      const btn = document.querySelector(`[data-target="${saved}"]`);
      if (btn) btn.click();
    }
  } catch {}
}

// ── CONFIRM ACTIONS ─────────────────────────────────────
function initConfirmActions() {
  document.querySelectorAll('[data-confirm]').forEach(el => {
    el.addEventListener('click', function (e) {
      if (!confirm(this.dataset.confirm)) e.preventDefault();
    });
  });
}

// ── MOBILE NAV ──────────────────────────────────────────
function initMobileNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.mobile-nav a').forEach(a => {
    const href = a.getAttribute('href');
    a.classList.toggle('active', href && (path === href || (href !== '/' && path.startsWith(href))));
  });
}

// ── AUTO REFRESH ────────────────────────────────────────
function initAutoRefresh() {
  const table = document.getElementById('rosterTable');
  if (!table) return;
  const badge = document.getElementById('refreshBadge');
  let t = 60;
  setInterval(() => {
    t--;
    if (badge) badge.textContent = t + 's';
    if (t <= 0) location.reload();
  }, 1000);
}

// ── LOGIN ERROR SHAKE ────────────────────────────────────
function initLoginErrorShake() {
  const hasError = document.querySelector('.flash-alert.alert-danger');
  if (!hasError) return;
  const panel = document.querySelector('.login-panel.active') || document;
  const pwInput = panel.querySelector('input[type="password"]');
  const userInput = panel.querySelector('input[name="username"]');
  [pwInput, userInput].forEach(input => {
    if (!input) return;
    input.classList.add('input-error', 'shake-error');
    input.addEventListener('animationend', () => input.classList.remove('shake-error'), { once: true });
    input.addEventListener('input', () => input.classList.remove('input-error'), { once: true });
  });
  if (pwInput) { pwInput.value = ''; pwInput.focus(); }
}

// ── FLASH → TOAST ───────────────────────────────────────
function initAlertToasts() {
  document.querySelectorAll('.flash-alert').forEach(el => {
    const text = el.textContent.trim();
    const type = el.classList.contains('alert-success') ? 'success'
      : el.classList.contains('alert-danger') ? 'error'
      : el.classList.contains('alert-warning') ? 'warning' : 'info';
    Toast.show(type.charAt(0).toUpperCase() + type.slice(1), text, type);
    el.style.display = 'none';
  });
}

// ── SILENT ACTIVITY TRACKER ─────────────────────────────
(function () {
  const skip = ['/login', '/setup', '/forgot', '/verify-pin', '/agent-launch'];
  if (skip.some(p => window.location.pathname.startsWith(p))) return;
  let active = false;
  ['mousemove','mousedown','keydown','touchstart','scroll'].forEach(ev =>
    document.addEventListener(ev, () => { active = true; }, { passive: true })
  );
  function beat() {
    fetch('/api/heartbeat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: window.location.pathname, active }),
      keepalive: true,
    }).catch(() => {});
    active = false;
  }
  beat();
  setInterval(beat, 10000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') beat(); });
  window.addEventListener('beforeunload', () => {
    navigator.sendBeacon('/api/heartbeat', JSON.stringify({ page: window.location.pathname, active: false }));
  });
})();

// ── INIT ────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  Theme.init();
  initClock();
  initPasswordToggles();
  initSidebarTabs();
  initConfirmActions();
  initMobileNav();
  initAutoRefresh();
  initLoginErrorShake();
  initAlertToasts();
});

window.Toast = Toast;
