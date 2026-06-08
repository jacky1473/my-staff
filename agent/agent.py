"""
StaffPortal Windows Agent v1.0
================================
- Runs silently in the background on Windows
- Detects mouse & keyboard activity system-wide
- Sends heartbeat to StaffPortal server every 30 seconds
- No browser needed
- Staff sees nothing (no window, no taskbar icon)

INSTALL:
  1. Edit CONFIG section below (SERVER_URL, USERNAME, PASSWORD)
  2. Run install.bat as Administrator
  3. Done! Agent starts automatically with Windows
"""

import sys
import os
import time
import json
import threading
import ctypes
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG — Edit these before deploying
# ─────────────────────────────────────────────
SERVER_URL  = "http://192.168.1.100:5000"   # Your Flask server IP
USERNAME    = "john"                          # Staff username
PASSWORD    = "pass123"                       # Staff password
HEARTBEAT   = 30                              # Seconds between heartbeats
IDLE_AFTER  = 60                              # Seconds without activity = idle
LOG_FILE    = os.path.join(os.environ.get('APPDATA','C:/'), 'StaffAgent', 'agent.log')
# ─────────────────────────────────────────────

# Hide console window on Windows
if sys.platform == 'win32':
    try:
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0
        )
    except Exception:
        pass

# Setup logging
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('StaffAgent')

# ─────────────────────────────────────────────
# Activity Detection (using ctypes — no pip needed)
# ─────────────────────────────────────────────
class ActivityMonitor:
    """Detects mouse & keyboard activity using Windows API"""

    def __init__(self):
        self.last_activity = time.time()
        self.last_mouse_pos = (0, 0)
        self._running = False

    def get_mouse_pos(self):
        """Get current mouse position using Windows API"""
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)

    def get_last_input_time(self):
        """Get time since last user input (Windows API)"""
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint),
            ]
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        millis_since = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis_since / 1000.0  # seconds since last input

    def is_active(self):
        """Returns True if user had any activity in last IDLE_AFTER seconds"""
        try:
            secs_idle = self.get_last_input_time()
            return secs_idle < IDLE_AFTER
        except Exception:
            return False

    def get_status(self):
        """Returns 'active' or 'idle'"""
        return 'active' if self.is_active() else 'idle'


# ─────────────────────────────────────────────
# Server Communication
# ─────────────────────────────────────────────
class ServerClient:
    """Handles all communication with Flask server"""

    def __init__(self):
        self.session_cookie = None
        self.logged_in = False

    def _request(self, path, data=None, method='GET'):
        """Make HTTP request to server"""
        url = SERVER_URL.rstrip('/') + path
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'StaffPortalAgent/1.0',
        }
        if self.session_cookie:
            headers['Cookie'] = self.session_cookie

        try:
            body = json.dumps(data).encode('utf-8') if data else None
            req = urllib.request.Request(
                url, data=body, headers=headers,
                method=method if body else 'GET'
            )
            if body:
                req.method = 'POST'

            with urllib.request.urlopen(req, timeout=10) as resp:
                # Capture session cookie
                set_cookie = resp.headers.get('Set-Cookie')
                if set_cookie:
                    self.session_cookie = set_cookie.split(';')[0]
                return json.loads(resp.read().decode('utf-8')), resp.status

        except urllib.error.HTTPError as e:
            return None, e.code
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return None, 0

    def login(self):
        """Login to staff portal"""
        try:
            logger.info(f"Attempting login for: {USERNAME}")

            # Step 1: POST login credentials
            data, status = self._request('/api/agent-login', {
                'username': USERNAME,
                'password': PASSWORD,
            })

            if status == 200 and data and data.get('ok'):
                self.logged_in = True
                logger.info("Login successful")
                return True
            else:
                logger.warning(f"Login failed: status={status}, data={data}")
                return False

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def send_heartbeat(self, status, page='/agent'):
        """Send activity heartbeat to server"""
        if not self.logged_in:
            if not self.login():
                return False

        data, http_status = self._request('/api/heartbeat', {
            'page': page,
            'active': status == 'active',
            'source': 'windows_agent',
        })

        if http_status == 401:
            # Session expired — re-login
            self.logged_in = False
            logger.info("Session expired, re-logging in...")
            return False

        if http_status == 200:
            return True

        return False

    def send_offline(self):
        """Mark user as offline before shutdown"""
        if not self.logged_in:
            return
        try:
            self._request('/api/heartbeat', {
                'page': '/agent',
                'active': False,
                'offline': True,
            })
        except Exception:
            pass


# ─────────────────────────────────────────────
# Main Agent
# ─────────────────────────────────────────────
class StaffAgent:

    def __init__(self):
        self.monitor = ActivityMonitor()
        self.client  = ServerClient()
        self.running = True

    def run(self):
        logger.info("=" * 50)
        logger.info(f"StaffPortal Agent started")
        logger.info(f"Server: {SERVER_URL}")
        logger.info(f"User:   {USERNAME}")
        logger.info(f"Heartbeat every {HEARTBEAT}s")
        logger.info("=" * 50)

        # Initial login
        retry = 0
        while self.running and not self.client.logged_in:
            if self.client.login():
                break
            retry += 1
            wait = min(retry * 30, 300)  # Back off up to 5 minutes
            logger.info(f"Retrying login in {wait}s (attempt {retry})")
            time.sleep(wait)

        # Main heartbeat loop
        while self.running:
            try:
                status = self.monitor.get_status()
                idle_secs = self.monitor.get_last_input_time()

                ok = self.client.send_heartbeat(status)

                if ok:
                    logger.info(f"Heartbeat OK — Status: {status} | Idle: {idle_secs:.0f}s")
                else:
                    logger.warning("Heartbeat failed — will retry")

            except Exception as e:
                logger.error(f"Agent loop error: {e}")

            # Wait for next heartbeat
            time.sleep(HEARTBEAT)

    def stop(self):
        logger.info("Agent stopping — marking offline")
        self.running = False
        self.client.send_offline()


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == '__main__':
    agent = StaffAgent()

    # Handle shutdown gracefully (Ctrl+C or Windows shutdown)
    import signal
    def shutdown(signum, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    agent.run()
