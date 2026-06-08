/* =============================================================
   STAFF PORTAL — Premium JS v2
   ============================================================= */

// TOAST NOTIFICATIONS
class Toast {
  static show(title, message, type = 'info', duration = 4000) {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.className = 'toast-container';
      document.body.appendChild(container);
    }

    const icons = {
      success: '✅',
      error: '❌',
      warning: '⚠️',
      info: 'ℹ️'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <div class="toast-icon">${icons[type] || icons.info}</div>
      <div class="toast-content">
        <div class="toast-title">${title}</div>
        <div class="toast-message">${message}</div>
      </div>
      <button class="toast-close" aria-label="Close notification">✕</button>
    `;

    container.appendChild(toast);

    toast.querySelector('.toast-close').addEventListener('click', () => {
      toast.style.animation = 'slideIn 0.3s ease-out reverse';
      setTimeout(() => toast.remove(), 300);
    });

    if (duration > 0) {
      setTimeout(() => {
        if (toast.parentElement) {
          toast.style.animation = 'slideIn 0.3s ease-out reverse';
          setTimeout(() => toast.remove(), 300);
        }
      }, duration);
    }
  }
}

// THEME TOGGLE
class ThemeManager {
  static init() {
    const saved = localStorage.getItem('theme') || 'dark-mode';
    document.documentElement.className = saved;
    this.setupToggle();
  }

  static setupToggle() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;

    btn.addEventListener('click', () => {
      const isDark = document.documentElement.classList.contains('dark-mode');
      const newTheme = isDark ? 'light-mode' : 'dark-mode';
      document.documentElement.className = newTheme;
      localStorage.setItem('theme', newTheme);
      btn.textContent = isDark ? '🌙' : '☀️';
    });

    // Set initial icon
    const isDark = document.documentElement.classList.contains('dark-mode');
    btn.textContent = isDark ? '🌙' : '☀️';
  }

  static toggle() {
    const isDark = document.documentElement.classList.contains('dark-mode');
    this.setupToggle();
  }
}

// LIVE CLOCK
function initClock() {
  const el = document.getElementById('liveClock');
  if (!el) return;

  function tick() {
    try {
      const now = new Date();
      const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
      const h = String(ist.getHours()).padStart(2, '0');
      const m = String(ist.getMinutes()).padStart(2, '0');
      const s = String(ist.getSeconds()).padStart(2, '0');
      el.textContent = `${h}:${m}:${s} IST`;
    } catch (e) {
      el.textContent = '--:--:--';
    }
  }
  tick();
  setInterval(tick, 1000);
}

// PASSWORD FIELD TOGGLES
function initPasswordToggles() {
  document.querySelectorAll('.pw-toggle').forEach(btn => {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      const input = this.previousElementSibling;
      if (!input) return;
      const isText = input.type === 'text';
      input.type = isText ? 'password' : 'text';
      this.textContent = isText ? '👁️' : '🙈';
      this.setAttribute('aria-label', isText ? 'Show password' : 'Hide password');
    });
  });
}

// SIDEBAR TABS
function initSidebarTabs() {
  const tabs = document.querySelectorAll('.portal-tab');
  if (!tabs.length) return;

  tabs.forEach(tab => {
    tab.addEventListener('click', function () {
      const target = this.getAttribute('data-target');
      if (!target) return;

      // Update active tab
      tabs.forEach(t => t.classList.remove('active'));
      this.classList.add('active');

      // Update visible pane
      document.querySelectorAll('.portal-pane').forEach(pane => {
        pane.style.display = pane.id === target ? 'block' : 'none';
      });

      // Persist
      try {
        sessionStorage.setItem('activeTab', target);
      } catch (e) {}
    });
  });

  // Restore last tab
  try {
    const saved = sessionStorage.getItem('activeTab');
    if (saved) {
      const tab = document.querySelector(`[data-target="${saved}"]`);
      if (tab) tab.click();
    }
  } catch (e) {}
}

// CONFIRM DESTRUCTIVE ACTIONS
function initConfirmActions() {
  document.querySelectorAll('[data-confirm]').forEach(el => {
    el.addEventListener('click', function (e) {
      if (!confirm(this.getAttribute('data-confirm'))) {
        e.preventDefault();
        return false;
      }
    });
  });
}

// MARK ACTIVE MOBILE NAV
function initMobileNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.mobile-bottom-nav a').forEach(a => {
    const href = a.getAttribute('href');
    a.classList.remove('active');
    if (href && (path === href || (href !== '/' && path.startsWith(href)))) {
      a.classList.add('active');
    }
  });
}

// AUTO REFRESH ROSTER
function initAutoRefresh() {
  const table = document.getElementById('rosterTable');
  if (!table) return;

  const badge = document.getElementById('refreshBadge');
  let remaining = 60;

  const countdown = setInterval(() => {
    remaining--;
    if (badge) badge.textContent = remaining + 's';
    if (remaining <= 0) {
      clearInterval(countdown);
      // Smooth reload
      const scrollPos = window.scrollY;
      location.reload();
      window.scrollTo(0, scrollPos);
    }
  }, 1000);
}

// REDIRECT FLASH ALERTS TO TOASTS
function initAlertToasts() {
  document.querySelectorAll('.flash-alert').forEach(el => {
    const text = el.textContent.trim();
    const type = el.classList.contains('alert-success') ? 'success'
      : el.classList.contains('alert-danger') ? 'error'
        : el.classList.contains('alert-warning') ? 'warning'
          : 'info';
    const title = type.charAt(0).toUpperCase() + type.slice(1);
    Toast.show(title, text, type, 5000);
    el.style.display = 'none';
  });
}

// FORM VALIDATION WITH FEEDBACK
function initFormValidation() {
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', function (e) {
      const inputs = this.querySelectorAll('[required]');
      let isValid = true;

      inputs.forEach(input => {
        if (!input.value.trim()) {
          input.style.borderColor = '#ef4444';
          isValid = false;
        } else {
          input.style.borderColor = '';
        }
      });

      if (!isValid) {
        e.preventDefault();
        Toast.show('Validation Error', 'Please fill all required fields', 'error');
      }
    });
  });
}

// LOADING STATE
function setLoading(buttonEl, loading = true) {
  if (!buttonEl) return;
  if (loading) {
    buttonEl.dataset.originalText = buttonEl.textContent;
    buttonEl.textContent = '⏳ Loading...';
    buttonEl.disabled = true;
  } else {
    buttonEl.textContent = buttonEl.dataset.originalText || 'Submit';
    buttonEl.disabled = false;
  }
}

// FETCH WITH ERROR HANDLING
async function fetchWithToast(url, options = {}) {
  try {
    const response = await fetch(url, options);
    const data = await response.json();

    if (!response.ok) {
      Toast.show('Error', data.error || 'Request failed', 'error');
      return null;
    }

    return data;
  } catch (err) {
    Toast.show('Error', err.message || 'Network error', 'error');
    return null;
  }
}

// INITIALIZE ALL
document.addEventListener('DOMContentLoaded', () => {
  ThemeManager.init();
  initClock();
  initPasswordToggles();
  initSidebarTabs();
  initConfirmActions();
  initMobileNav();
  initAutoRefresh();
  initAlertToasts();
  initFormValidation();
});

// Export for use in templates
window.Toast = Toast;
window.setLoading = setLoading;
window.fetchWithToast = fetchWithToast;

// =============================================================
// SILENT ACTIVITY TRACKER (staff side — invisible to staff)
// Tracks mouse & keyboard activity on the web app only
// Sends heartbeat to server every 10 seconds
// =============================================================
(function() {
  // Only run if user is logged in (not on login/setup pages)
  const noTrackPages = ['/login', '/setup', '/forgot', '/verify-pin'];
  if (noTrackPages.some(p => window.location.pathname.startsWith(p))) return;

  let hasActivity = false;
  const HEARTBEAT_INTERVAL = 10000; // 10 seconds

  // Detect mouse movement silently
  document.addEventListener('mousemove', () => { hasActivity = true; }, { passive: true });
  document.addEventListener('mousedown', () => { hasActivity = true; }, { passive: true });

  // Detect keyboard silently
  document.addEventListener('keydown', () => { hasActivity = true; }, { passive: true });

  // Detect touch (mobile)
  document.addEventListener('touchstart', () => { hasActivity = true; }, { passive: true });
  document.addEventListener('touchmove',  () => { hasActivity = true; }, { passive: true });

  // Detect scroll
  document.addEventListener('scroll', () => { hasActivity = true; }, { passive: true });

  // Send heartbeat
  function sendHeartbeat() {
    const payload = {
      page:   window.location.pathname,
      active: hasActivity,
    };
    hasActivity = false; // Reset after each heartbeat

    fetch('/api/heartbeat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {}); // Silent fail — staff won't see errors
  }

  // Send immediately on load, then every 10 seconds
  sendHeartbeat();
  setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);

  // Send on page visibility change (tab switch)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sendHeartbeat();
  });

  // Send before page unload
  window.addEventListener('beforeunload', () => {
    navigator.sendBeacon('/api/heartbeat', JSON.stringify({ page: window.location.pathname, active: false }));
  });
})();
