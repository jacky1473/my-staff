#!/usr/bin/env python3
"""
=============================================================================
MyStaff Softphone - Windows Desktop Attendance Client
=============================================================================
Form Factor: Lightweight Softphone / Compact Taskbar Widget (MicroSIP / X-Lite style)
Platform: Windows Desktop (Python / Standalone .exe via PyInstaller)
Backend: Linux Server (REST API on port 5001)

Features:
- Micro widget form factor (~340x520) designed for Windows desktop
- 1-Click Softphone Punch In / Punch Out with live shift timer
- Persistent Auto-Login (Remember credentials / API Bearer token)
- 2FA Email OTP Verification Dialog
- Leave Request Dialog (Date range + Reason only - Admin assigns leave type!)
- Real-time HR Notices & Warning announcements
- System Tray (Notification Area) minimize & background monitoring
=============================================================================
"""

import sys
import os
import json
import time
import threading
from datetime import datetime, date

# Standard GUI libraries
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' library is required. Run: pip install requests")
    sys.exit(1)

# Optional system tray support (pystray + PIL)
HAS_TRAY = False
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ---------------------------------------------------------------------------
# Constants & Theming
# ---------------------------------------------------------------------------
CONFIG_FILE = os.path.expanduser("~/.mystaff_softphone_config.json")
DEFAULT_SERVER_URL = "http://127.0.0.1:5001"

# Softphone Dark Theme Colors (Slate & Cyber Glow)
COLOR_BG = "#0f172a"          # slate-900 (Main Window)
COLOR_PANEL = "#1e293b"       # slate-800 (Card Panels)
COLOR_BORDER = "#334155"      # slate-700
COLOR_TEXT_PRIMARY = "#f8fafc"# slate-50
COLOR_TEXT_MUTED = "#94a3b8"  # slate-400
COLOR_CYAN = "#00d9ff"        # Brand Accent Cyan
COLOR_GREEN = "#10b981"       # Online / Clock-In Emerald
COLOR_GREEN_HOVER = "#059669"
COLOR_RED = "#ef4444"         # Clock-Out / Alert Crimson
COLOR_RED_HOVER = "#dc2626"
COLOR_GOLD = "#f59e0b"        # Pending / Warning Amber
COLOR_INPUT_BG = "#090d16"    # Input fields


class SoftphoneAPIClient:
    """Manages secure communication with the Linux backend server."""

    def __init__(self, base_url=DEFAULT_SERVER_URL, token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MyStaff-Softphone-Windows/1.0"})

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def test_connection(self):
        try:
            r = self.session.get(f"{self.base_url}/api/status", timeout=4)
            return r.status_code == 200, r.json() if r.status_code == 200 else {}
        except Exception as e:
            return False, {"error": str(e)}

    def login(self, username, password):
        try:
            r = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": username, "password": password},
                headers=self._headers(),
                timeout=8
            )
            return r.status_code, r.json()
        except Exception as e:
            return 0, {"error": f"Connection failed: {e}"}

    def verify_pin(self, username, pin):
        try:
            r = self.session.post(
                f"{self.base_url}/api/auth/verify-pin",
                json={"username": username, "pin": pin, "device_info": "Windows Softphone"},
                headers=self._headers(),
                timeout=8
            )
            return r.status_code, r.json()
        except Exception as e:
            return 0, {"error": f"Connection failed: {e}"}

    def logout(self):
        try:
            r = self.session.post(f"{self.base_url}/api/auth/logout", headers=self._headers(), timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def get_status(self):
        try:
            r = self.session.get(f"{self.base_url}/api/staff/status", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                return True, r.json()
            return False, r.json()
        except Exception as e:
            return False, {"error": str(e)}

    def clock(self, action):
        try:
            r = self.session.post(
                f"{self.base_url}/api/staff/clock",
                json={"action": action},
                headers=self._headers(),
                timeout=8
            )
            return r.status_code == 200, r.json()
        except Exception as e:
            return False, {"error": str(e)}

    def apply_leave(self, start_date, end_date, reason):
        try:
            r = self.session.post(
                f"{self.base_url}/api/staff/leave/apply",
                json={"start_date": start_date, "end_date": end_date, "reason": reason},
                headers=self._headers(),
                timeout=8
            )
            return r.status_code == 200, r.json()
        except Exception as e:
            return False, {"error": str(e)}

    def get_leaves(self):
        try:
            r = self.session.get(f"{self.base_url}/api/staff/leaves", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                return True, r.json().get("leaves", [])
            return False, []
        except Exception:
            return False, []


class SoftphoneApp(tk.Tk):
    """Main Softphone GUI Application."""

    def __init__(self):
        super().__init__()

        self.title("MyStaff Softphone")
        self.geometry("340x520")
        self.resizable(False, False)
        self.configure(bg=COLOR_BG)

        # Application state
        self.config_data = self.load_config()
        self.api = SoftphoneAPIClient(
            base_url=self.config_data.get("server_url", DEFAULT_SERVER_URL),
            token=self.config_data.get("token")
        )
        self.current_user = None
        self.attendance_data = {}
        self.notifications = []
        self.recent_leaves = []
        self.is_clocked_in = False
        self.clock_in_time = None
        self.poll_active = True

        # System tray variables
        self.tray_icon = None

        # Build UI
        self.container = tk.Frame(self, bg=COLOR_BG)
        self.container.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close_clicked)

        # Start Clock Ticker
        self.after(1000, self.update_clock_timer)

        # Decide initial screen
        if self.api.token:
            self.show_main_screen()
            self.refresh_status_async()
            self.start_background_polling()
        else:
            self.show_login_screen()

        # Initialize System Tray in background if available
        if HAS_TRAY:
            self.init_system_tray()

    # -----------------------------------------------------------------------
    # Configuration Management
    # -----------------------------------------------------------------------
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"server_url": DEFAULT_SERVER_URL, "username": "", "token": None}

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config_data, f, indent=2)
        except Exception as e:
            print("[WARN] Failed to save config:", e)

    # -----------------------------------------------------------------------
    # Screen Switching
    # -----------------------------------------------------------------------
    def clear_container(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    # -----------------------------------------------------------------------
    # 1. LOGIN SCREEN
    # -----------------------------------------------------------------------
    def show_login_screen(self):
        self.clear_container()

        header_frame = tk.Frame(self.container, bg=COLOR_BG, pady=16)
        header_frame.pack(fill="x")

        lbl_logo = tk.Label(header_frame, text="📞", font=("Segoe UI Emoji", 32), bg=COLOR_BG, fg=COLOR_CYAN)
        lbl_logo.pack()

        lbl_title = tk.Label(header_frame, text="MyStaff Softphone", font=("Segoe UI", 14, "bold"), bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY)
        lbl_title.pack()

        lbl_sub = tk.Label(header_frame, text="Staff Attendance Client", font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_MUTED)
        lbl_sub.pack()

        # Login Form Card
        form_card = tk.Frame(self.container, bg=COLOR_PANEL, bd=1, relief="flat", padx=16, pady=16)
        form_card.pack(fill="x", padx=16, pady=6)

        # Server URL
        tk.Label(form_card, text="SERVER URL", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        self.ent_server = tk.Entry(form_card, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, bd=1, relief="flat", font=("Segoe UI", 9))
        self.ent_server.insert(0, self.config_data.get("server_url", DEFAULT_SERVER_URL))
        self.ent_server.pack(fill="x", pady=(0, 10), ipady=4)

        # Username
        tk.Label(form_card, text="USERNAME", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        self.ent_user = tk.Entry(form_card, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, bd=1, relief="flat", font=("Segoe UI", 9))
        self.ent_user.insert(0, self.config_data.get("username", ""))
        self.ent_user.pack(fill="x", pady=(0, 10), ipady=4)

        # Password
        tk.Label(form_card, text="PASSWORD", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        self.ent_pass = tk.Entry(form_card, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, show="•", bd=1, relief="flat", font=("Segoe UI", 9))
        self.ent_pass.pack(fill="x", pady=(0, 14), ipady=4)

        # Login Button
        self.btn_login = tk.Button(
            form_card,
            text="CONNECT & SIGN IN",
            font=("Segoe UI", 10, "bold"),
            bg=COLOR_CYAN,
            fg="#000000",
            activebackground="#38bdf8",
            activeforeground="#000000",
            relief="flat",
            cursor="hand2",
            command=self.handle_login
        )
        self.btn_login.pack(fill="x", ipady=6)

        # Status / Feedback label
        self.lbl_login_msg = tk.Label(self.container, text="", font=("Segoe UI", 8), bg=COLOR_BG, fg=COLOR_GOLD, wraplength=300)
        self.lbl_login_msg.pack(pady=8)

    def handle_login(self):
        server = self.ent_server.get().strip().rstrip("/")
        username = self.ent_user.get().strip()
        password = self.ent_pass.get()

        if not username or not password:
            self.lbl_login_msg.config(text="Please enter username and password.", fg=COLOR_RED)
            return

        self.btn_login.config(state="disabled", text="Connecting...")
        self.lbl_login_msg.config(text="Verifying credentials with server...", fg=COLOR_TEXT_MUTED)

        def worker():
            self.api.base_url = server
            code, res = self.api.login(username, password)
            self.after(0, lambda: self.login_response_callback(server, username, code, res))

        threading.Thread(target=worker, daemon=True).start()

    def login_response_callback(self, server, username, code, res):
        self.btn_login.config(state="normal", text="CONNECT & SIGN IN")

        if code == 200 and res.get("status") == "otp_required":
            self.config_data["server_url"] = server
            self.config_data["username"] = username
            self.save_config()
            self.show_otp_dialog(username, res.get("masked_email", "your email"))
        elif code == 200 and res.get("token"):
            self.config_data["server_url"] = server
            self.config_data["username"] = username
            self.config_data["token"] = res.get("token")
            self.save_config()
            self.api.token = res.get("token")
            self.current_user = res.get("user")
            self.show_main_screen()
            self.refresh_status_async()
            self.start_background_polling()
        else:
            err = res.get("error", "Login failed. Check server URL and credentials.")
            self.lbl_login_msg.config(text=err, fg=COLOR_RED)

    # -----------------------------------------------------------------------
    # 2. OTP / 2FA VERIFICATION DIALOG
    # -----------------------------------------------------------------------
    def show_otp_dialog(self, username, masked_email):
        dlg = tk.Toplevel(self)
        dlg.title("2FA Verification")
        dlg.geometry("300x260")
        dlg.resizable(False, False)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="🔐", font=("Segoe UI Emoji", 24), bg=COLOR_BG, fg=COLOR_GOLD).pack(pady=(12, 2))
        tk.Label(dlg, text="Verification Code", font=("Segoe UI", 12, "bold"), bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY).pack()
        tk.Label(dlg, text=f"Enter 4-digit PIN sent to:\n{masked_email}", font=("Segoe UI", 8), bg=COLOR_BG, fg=COLOR_TEXT_MUTED, justify="center").pack(pady=4)

        ent_pin = tk.Entry(dlg, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, font=("Segoe UI", 16, "bold"), justify="center", bd=1, relief="flat")
        ent_pin.pack(pady=10, ipady=4, padx=50, fill="x")
        ent_pin.focus_set()

        lbl_msg = tk.Label(dlg, text="", font=("Segoe UI", 8), bg=COLOR_BG, fg=COLOR_RED)
        lbl_msg.pack()

        def do_verify():
            pin = ent_pin.get().strip()
            if not pin or len(pin) != 4:
                lbl_msg.config(text="Please enter valid 4-digit PIN.")
                return

            lbl_msg.config(text="Verifying...", fg=COLOR_TEXT_MUTED)
            code, res = self.api.verify_pin(username, pin)
            if code == 200 and res.get("status") == "success":
                self.config_data["token"] = res.get("token")
                self.save_config()
                self.api.token = res.get("token")
                self.current_user = res.get("user")
                dlg.destroy()
                self.show_main_screen()
                self.refresh_status_async()
                self.start_background_polling()
            else:
                lbl_msg.config(text=res.get("error", "Invalid or expired PIN."), fg=COLOR_RED)

        btn = tk.Button(dlg, text="VERIFY & CONTINUE", font=("Segoe UI", 9, "bold"), bg=COLOR_CYAN, fg="#000", command=do_verify, relief="flat", cursor="hand2")
        btn.pack(pady=6, padx=30, fill="x", ipady=4)

        ent_pin.bind("<Return>", lambda e: do_verify())

    # -----------------------------------------------------------------------
    # 3. MAIN SOFTPHONE SCREEN
    # -----------------------------------------------------------------------
    def show_main_screen(self):
        self.clear_container()

        # TOP BAR
        top_bar = tk.Frame(self.container, bg=COLOR_PANEL, height=44, padx=12, pady=6)
        top_bar.pack(fill="x")

        # Status Dot & Title
        title_box = tk.Frame(top_bar, bg=COLOR_PANEL)
        title_box.pack(side="left")

        self.lbl_status_dot = tk.Label(title_box, text="●", font=("Segoe UI", 12), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED)
        self.lbl_status_dot.pack(side="left", padx=(0, 4))

        self.lbl_user_name = tk.Label(title_box, text=self.config_data.get("username", "Staff"), font=("Segoe UI", 9, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_PRIMARY)
        self.lbl_user_name.pack(side="left")

        self.lbl_dept = tk.Label(title_box, text="", font=("Segoe UI", 7), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED)
        self.lbl_dept.pack(side="left", padx=(4, 0))

        # Top Right Actions (Sync & Settings)
        action_box = tk.Frame(top_bar, bg=COLOR_PANEL)
        action_box.pack(side="right")

        btn_sync = tk.Button(action_box, text="🔄", font=("Segoe UI Emoji", 9), bg=COLOR_PANEL, fg=COLOR_TEXT_PRIMARY, bd=0, relief="flat", cursor="hand2", command=self.refresh_status_async)
        btn_sync.pack(side="left", padx=2)

        btn_sett = tk.Button(action_box, text="⚙️", font=("Segoe UI Emoji", 9), bg=COLOR_PANEL, fg=COLOR_TEXT_PRIMARY, bd=0, relief="flat", cursor="hand2", command=self.show_settings_dialog)
        btn_sett.pack(side="left", padx=2)

        # CLOCK & DATE STRIP
        clock_strip = tk.Frame(self.container, bg=COLOR_BG, pady=10)
        clock_strip.pack(fill="x")

        self.lbl_live_clock = tk.Label(clock_strip, text="00:00:00", font=("Consolas", 22, "bold"), bg=COLOR_BG, fg=COLOR_CYAN)
        self.lbl_live_clock.pack()

        self.lbl_live_date = tk.Label(clock_strip, text="", font=("Segoe UI", 8), bg=COLOR_BG, fg=COLOR_TEXT_MUTED)
        self.lbl_live_date.pack()

        # CENTRAL SOFTPHONE ACTION CARD
        self.call_card = tk.Frame(self.container, bg=COLOR_PANEL, bd=1, relief="flat", padx=14, pady=16)
        self.call_card.pack(fill="x", padx=16, pady=4)

        # Big Softphone Punch Button
        self.btn_softphone_action = tk.Button(
            self.call_card,
            text="📞 CLOCK IN",
            font=("Segoe UI", 13, "bold"),
            bg=COLOR_GREEN,
            fg="#ffffff",
            activebackground=COLOR_GREEN_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief="flat",
            cursor="hand2",
            command=self.handle_clock_toggle
        )
        self.btn_softphone_action.pack(fill="x", ipady=12, pady=(0, 10))

        # Shift Status Label
        self.lbl_shift_status = tk.Label(
            self.call_card,
            text="Status: Ready to Clock In",
            font=("Segoe UI", 9),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT_MUTED
        )
        self.lbl_shift_status.pack()

        # Shift Duration Running Timer
        self.lbl_shift_timer = tk.Label(
            self.call_card,
            text="Duration: —",
            font=("Segoe UI", 8),
            bg=COLOR_PANEL,
            fg=COLOR_CYAN
        )
        self.lbl_shift_timer.pack(pady=(2, 0))

        # TIMINGS GRID (Clock in / Clock out)
        grid_frame = tk.Frame(self.call_card, bg=COLOR_PANEL, pady=6)
        grid_frame.pack(fill="x")

        f_in = tk.Frame(grid_frame, bg=COLOR_PANEL)
        f_in.pack(side="left", expand=True)
        tk.Label(f_in, text="CLOCK IN", font=("Segoe UI", 7), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED).pack()
        self.lbl_in_time = tk.Label(f_in, text="—", font=("Segoe UI", 9, "bold"), bg=COLOR_PANEL, fg=COLOR_GREEN)
        self.lbl_in_time.pack()

        f_out = tk.Frame(grid_frame, bg=COLOR_PANEL)
        f_out.pack(side="right", expand=True)
        tk.Label(f_out, text="CLOCK OUT", font=("Segoe UI", 7), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED).pack()
        self.lbl_out_time = tk.Label(f_out, text="—", font=("Segoe UI", 9, "bold"), bg=COLOR_PANEL, fg=COLOR_GOLD)
        self.lbl_out_time.pack()

        # NOTICES MINI TICKER
        self.notice_card = tk.Frame(self.container, bg=COLOR_BG, padx=16, pady=4)
        self.notice_card.pack(fill="x")

        self.lbl_notice_ticker = tk.Label(
            self.notice_card,
            text="📢 No active HR announcements",
            font=("Segoe UI", 8),
            bg=COLOR_BG,
            fg=COLOR_TEXT_MUTED,
            anchor="w",
            wraplength=300
        )
        self.lbl_notice_ticker.pack(fill="x")

        # BOTTOM SOFTPHONE TOOLBAR (TABS)
        bottom_nav = tk.Frame(self.container, bg=COLOR_PANEL, height=44, padx=8, pady=4)
        bottom_nav.pack(side="bottom", fill="x")

        btn_leave = tk.Button(
            bottom_nav,
            text="🌴 Apply Leave",
            font=("Segoe UI", 8, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_CYAN,
            bd=0,
            relief="flat",
            cursor="hand2",
            command=self.show_leave_dialog
        )
        btn_leave.pack(side="left", expand=True, fill="x", padx=2, ipady=4)

        btn_notices = tk.Button(
            bottom_nav,
            text="🔔 Notices",
            font=("Segoe UI", 8),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT_PRIMARY,
            bd=0,
            relief="flat",
            cursor="hand2",
            command=self.show_notices_dialog
        )
        btn_notices.pack(side="left", expand=True, fill="x", padx=2, ipady=4)

        btn_tray = tk.Button(
            bottom_nav,
            text="📥 Minimize",
            font=("Segoe UI", 8),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT_MUTED,
            bd=0,
            relief="flat",
            cursor="hand2",
            command=self.minimize_to_tray
        )
        btn_tray.pack(side="left", expand=True, fill="x", padx=2, ipady=4)

    # -----------------------------------------------------------------------
    # Clock In / Clock Out Action
    # -----------------------------------------------------------------------
    def handle_clock_toggle(self):
        if not self.is_clocked_in:
            # Action: CLOCK IN
            self.btn_softphone_action.config(state="disabled", text="Clocking in...")
            def worker():
                ok, res = self.api.clock("in")
                self.after(0, lambda: self.handle_clock_result(ok, res, "in"))
            threading.Thread(target=worker, daemon=True).start()
        else:
            # Action: CLOCK OUT (Confirm first)
            if messagebox.askyesno("Confirm Clock Out", "Are you sure you want to end your shift and Clock OUT?"):
                self.btn_softphone_action.config(state="disabled", text="Clocking out...")
                def worker():
                    ok, res = self.api.clock("out")
                    self.after(0, lambda: self.handle_clock_result(ok, res, "out"))
                threading.Thread(target=worker, daemon=True).start()

    def handle_clock_result(self, ok, res, action):
        self.btn_softphone_action.config(state="normal")
        if ok:
            msg = res.get("message", "Success")
            self.refresh_status_async()
            if HAS_TRAY and self.tray_icon:
                try:
                    self.tray_icon.notify(msg, "MyStaff Softphone")
                except Exception:
                    pass
        else:
            err = res.get("error", "Action failed.")
            messagebox.showerror("Attendance Error", err)

    # -----------------------------------------------------------------------
    # Background Sync & Status Refresh
    # -----------------------------------------------------------------------
    def refresh_status_async(self):
        def worker():
            ok, data = self.api.get_status()
            if ok:
                self.after(0, lambda: self.update_status_ui(data))
            else:
                if data.get("error") == "Invalid or expired token":
                    self.after(0, self.handle_token_expired)
        threading.Thread(target=worker, daemon=True).start()

    def update_status_ui(self, data):
        user = data.get("user", {})
        att = data.get("attendance", {})
        self.current_user = user
        self.attendance_data = att
        self.notifications = data.get("notifications", [])
        self.recent_leaves = data.get("recent_leaves", [])

        # Update Username & Dept
        if hasattr(self, "lbl_user_name"):
            self.lbl_user_name.config(text=user.get("username", "Staff"))
            self.lbl_dept.config(text=f"({user.get('department', '')})")

        # Today's Date
        today_date = data.get("today_date", "")
        today_day = data.get("today_day", "")
        if hasattr(self, "lbl_live_date"):
            self.lbl_live_date.config(text=f"{today_day}, {today_date}")

        # Attendance Record
        clock_in = att.get("clock_in")
        clock_out = att.get("clock_out")
        status = att.get("status", "Not Clocked In")
        is_in = att.get("is_clocked_in", False)
        is_done = att.get("is_completed", False)

        self.is_clocked_in = is_in
        self.clock_in_time = clock_in

        if hasattr(self, "lbl_in_time"):
            self.lbl_in_time.config(text=clock_in if clock_in else "—")
            self.lbl_out_time.config(text=clock_out if clock_out else "—")

        # Softphone Button & Status
        if hasattr(self, "btn_softphone_action"):
            if is_in:
                # Active in Shift
                self.lbl_status_dot.config(fg=COLOR_GREEN)
                self.lbl_shift_status.config(text="🟢 Active Shift in Progress", fg=COLOR_GREEN)
                self.btn_softphone_action.config(
                    text="🚪 CLOCK OUT",
                    bg=COLOR_RED,
                    activebackground=COLOR_RED_HOVER
                )
            elif is_done:
                # Shift Finished for today
                self.lbl_status_dot.config(fg=COLOR_TEXT_MUTED)
                self.lbl_shift_status.config(text=f"🏁 Shift Completed ({att.get('duration', '')})", fg=COLOR_TEXT_MUTED)
                self.btn_softphone_action.config(
                    text="🏁 SHIFT ENDED",
                    bg=COLOR_PANEL,
                    state="disabled"
                )
            elif status in ("PL", "UL", "LWP"):
                # On Leave
                self.lbl_status_dot.config(fg=COLOR_GOLD)
                self.lbl_shift_status.config(text=f"🌴 On Approved Leave ({status})", fg=COLOR_GOLD)
                self.btn_softphone_action.config(
                    text="🌴 ON LEAVE",
                    bg=COLOR_PANEL,
                    state="disabled"
                )
            else:
                # Not clocked in
                self.lbl_status_dot.config(fg=COLOR_TEXT_MUTED)
                self.lbl_shift_status.config(text="Ready to Clock In", fg=COLOR_TEXT_MUTED)
                self.btn_softphone_action.config(
                    text="📞 CLOCK IN",
                    bg=COLOR_GREEN,
                    activebackground=COLOR_GREEN_HOVER,
                    state="normal"
                )

        # Update Notice Ticker
        if hasattr(self, "lbl_notice_ticker"):
            if self.notifications:
                n = self.notifications[0]
                icon = "🚨" if n.get("type") in ("warning", "urgent") else "📢"
                self.lbl_notice_ticker.config(text=f"{icon} {n.get('title')}: {n.get('message')}")
            else:
                self.lbl_notice_ticker.config(text="📢 No active HR announcements")

    def handle_token_expired(self):
        self.api.token = None
        self.config_data["token"] = None
        self.save_config()
        messagebox.showinfo("Session Expired", "Your session has expired. Please sign in again.")
        self.show_login_screen()

    def start_background_polling(self):
        """Polls server every 30 seconds for state updates."""
        def poller():
            while self.poll_active:
                time.sleep(30)
                if self.api.token and self.poll_active:
                    self.refresh_status_async()
        threading.Thread(target=poller, daemon=True).start()

    # -----------------------------------------------------------------------
    # Live Clock & Shift Duration Ticker
    # -----------------------------------------------------------------------
    def update_clock_timer(self):
        now = datetime.now()
        if hasattr(self, "lbl_live_clock"):
            self.lbl_live_clock.config(text=now.strftime("%H:%M:%S"))

        if self.is_clocked_in and self.clock_in_time and hasattr(self, "lbl_shift_timer"):
            try:
                # Calculate running duration
                parts = [int(p) for p in self.clock_in_time.split(":")]
                cin = now.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)
                diff = now - cin
                if diff.total_seconds() >= 0:
                    secs = int(diff.total_seconds())
                    hrs = secs // 3600
                    mins = (secs % 3600) // 60
                    s = secs % 60
                    self.lbl_shift_timer.config(text=f"Duration: {hrs:02d}h {mins:02d}m {s:02d}s")
            except Exception:
                pass

        self.after(1000, self.update_clock_timer)

    # -----------------------------------------------------------------------
    # 4. APPLY LEAVE DIALOG (NO LEAVE TYPE SELECTION!)
    # -----------------------------------------------------------------------
    def show_leave_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("🌴 Request Leave")
        dlg.geometry("320x390")
        dlg.resizable(False, False)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="🌴 Submit Leave Request", font=("Segoe UI", 11, "bold"), bg=COLOR_BG, fg=COLOR_CYAN).pack(pady=(12, 2))
        tk.Label(
            dlg,
            text="Dates and reason will be reviewed.\nAdmin will classify and assign the Leave Type.",
            font=("Segoe UI", 7),
            bg=COLOR_BG,
            fg=COLOR_TEXT_MUTED,
            justify="center"
        ).pack(pady=(0, 8))

        form = tk.Frame(dlg, bg=COLOR_PANEL, padx=12, pady=10)
        form.pack(fill="x", padx=12)

        today_s = date.today().strftime("%Y-%m-%d")

        # Start Date
        tk.Label(form, text="START DATE (YYYY-MM-DD)", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        ent_start = tk.Entry(form, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, bd=1, relief="flat", font=("Segoe UI", 9))
        ent_start.insert(0, today_s)
        ent_start.pack(fill="x", pady=(0, 8), ipady=3)

        # End Date
        tk.Label(form, text="END DATE (YYYY-MM-DD)", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        ent_end = tk.Entry(form, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, bd=1, relief="flat", font=("Segoe UI", 9))
        ent_end.insert(0, today_s)
        ent_end.pack(fill="x", pady=(0, 8), ipady=3)

        # Reason
        tk.Label(form, text="REASON", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        ent_reason = tk.Entry(form, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, bd=1, relief="flat", font=("Segoe UI", 9))
        ent_reason.pack(fill="x", pady=(0, 10), ipady=3)

        lbl_msg = tk.Label(dlg, text="", font=("Segoe UI", 8), bg=COLOR_BG, fg=COLOR_RED)
        lbl_msg.pack(pady=2)

        def do_submit():
            s_date = ent_start.get().strip()
            e_date = ent_end.get().strip()
            reason = ent_reason.get().strip()

            if not s_date or not e_date or not reason:
                lbl_msg.config(text="All fields are required.", fg=COLOR_RED)
                return

            lbl_msg.config(text="Submitting to Admin...", fg=COLOR_TEXT_MUTED)
            ok, res = self.api.apply_leave(s_date, e_date, reason)
            if ok:
                messagebox.showinfo("Application Submitted", res.get("message", "Leave application submitted to Admin."))
                dlg.destroy()
                self.refresh_status_async()
            else:
                lbl_msg.config(text=res.get("error", "Submission failed."), fg=COLOR_RED)

        btn_submit = tk.Button(dlg, text="SUBMIT APPLICATION", font=("Segoe UI", 9, "bold"), bg=COLOR_CYAN, fg="#000", relief="flat", cursor="hand2", command=do_submit)
        btn_submit.pack(fill="x", padx=12, pady=6, ipady=4)

        # Recent Leave Status Box
        rec_frame = tk.Frame(dlg, bg=COLOR_PANEL, padx=8, pady=6)
        rec_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        tk.Label(rec_frame, text="Recent Applications:", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED).pack(anchor="w")

        if self.recent_leaves:
            for l in self.recent_leaves[:3]:
                st_color = COLOR_GREEN if l.get("status") == "Approved" else (COLOR_RED if l.get("status") == "Rejected" else COLOR_GOLD)
                st_text = f"Approved ({l.get('leave_type')})" if l.get("status") == "Approved" else l.get("status")
                t_str = f"• {l.get('start_date')} ({l.get('days')}d) — {st_text}"
                tk.Label(rec_frame, text=t_str, font=("Segoe UI", 7), bg=COLOR_PANEL, fg=st_color, anchor="w").pack(fill="x")
        else:
            tk.Label(rec_frame, text="No previous leave records.", font=("Segoe UI", 7), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED).pack()

    # -----------------------------------------------------------------------
    # 5. NOTICES DIALOG
    # -----------------------------------------------------------------------
    def show_notices_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("📢 HR Notices & Bulletins")
        dlg.geometry("320x360")
        dlg.resizable(False, False)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self)

        tk.Label(dlg, text="📢 Company Announcements", font=("Segoe UI", 11, "bold"), bg=COLOR_BG, fg=COLOR_CYAN).pack(pady=(12, 6))

        scroll_frame = tk.Frame(dlg, bg=COLOR_BG)
        scroll_frame.pack(fill="both", expand=True, padx=12, pady=4)

        if not self.notifications:
            tk.Label(scroll_frame, text="No active notices or warnings.", font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_MUTED).pack(pady=40)
        else:
            for n in self.notifications:
                is_warn = n.get("type") in ("warning", "urgent")
                card = tk.Frame(scroll_frame, bg=COLOR_PANEL, bd=1, relief="flat", padx=8, pady=6)
                card.pack(fill="x", pady=4)

                color = COLOR_RED if is_warn else COLOR_CYAN
                icon = "🚨 " if is_warn else "📢 "
                tk.Label(card, text=icon + n.get("title", ""), font=("Segoe UI", 8, "bold"), bg=COLOR_PANEL, fg=color, anchor="w").pack(fill="x")
                tk.Label(card, text=n.get("message", ""), font=("Segoe UI", 8), bg=COLOR_PANEL, fg=COLOR_TEXT_PRIMARY, wraplength=280, justify="left").pack(fill="x", pady=2)
                tk.Label(card, text=n.get("created_at", "")[:16], font=("Segoe UI", 6), bg=COLOR_PANEL, fg=COLOR_TEXT_MUTED, anchor="e").pack(fill="x")

    # -----------------------------------------------------------------------
    # 6. SETTINGS DIALOG
    # -----------------------------------------------------------------------
    def show_settings_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Settings")
        dlg.geometry("300x260")
        dlg.resizable(False, False)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self)

        tk.Label(dlg, text="⚙️ Softphone Settings", font=("Segoe UI", 11, "bold"), bg=COLOR_BG, fg=COLOR_CYAN).pack(pady=(12, 10))

        form = tk.Frame(dlg, bg=COLOR_PANEL, padx=12, pady=10)
        form.pack(fill="x", padx=12)

        tk.Label(form, text="SERVER BACKEND URL", font=("Segoe UI", 7, "bold"), bg=COLOR_PANEL, fg=COLOR_CYAN).pack(anchor="w", pady=(0, 2))
        ent_s = tk.Entry(form, bg=COLOR_INPUT_BG, fg=COLOR_TEXT_PRIMARY, insertbackground=COLOR_CYAN, bd=1, relief="flat", font=("Segoe UI", 9))
        ent_s.insert(0, self.config_data.get("server_url", DEFAULT_SERVER_URL))
        ent_s.pack(fill="x", pady=(0, 8), ipady=3)

        def save_settings():
            new_url = ent_s.get().strip().rstrip("/")
            self.config_data["server_url"] = new_url
            self.save_config()
            self.api.base_url = new_url
            messagebox.showinfo("Saved", "Settings updated successfully.")
            dlg.destroy()

        btn_save = tk.Button(form, text="Save Settings", font=("Segoe UI", 8, "bold"), bg=COLOR_CYAN, fg="#000", relief="flat", cursor="hand2", command=save_settings)
        btn_save.pack(fill="x", pady=4, ipady=3)

        btn_logout = tk.Button(dlg, text="🚪 Sign Out / Switch User", font=("Segoe UI", 8), bg=COLOR_PANEL, fg=COLOR_RED, relief="flat", cursor="hand2", command=lambda: [dlg.destroy(), self.handle_sign_out()])
        btn_logout.pack(fill="x", padx=12, pady=10, ipady=4)

    def handle_sign_out(self):
        if messagebox.askyesno("Sign Out", "Are you sure you want to sign out from the softphone?"):
            self.api.logout()
            self.api.token = None
            self.config_data["token"] = None
            self.save_config()
            self.show_login_screen()

    # -----------------------------------------------------------------------
    # System Tray Integration
    # -----------------------------------------------------------------------
    def init_system_tray(self):
        """Initializes pystray icon for Windows System Tray."""
        try:
            # Create a simple dynamic tray icon (circle with dot)
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((4, 4, 60, 60), fill="#0f172a", outline="#00d9ff", width=4)
            draw.ellipse((22, 22, 42, 42), fill="#10b981")

            menu = pystray.Menu(
                pystray.MenuItem("Open Softphone", self.tray_restore),
                pystray.MenuItem("Clock IN", lambda: self.tray_action("in")),
                pystray.MenuItem("Clock OUT", lambda: self.tray_action("out")),
                pystray.MenuItem("Exit", self.tray_exit)
            )

            self.tray_icon = pystray.Icon("MyStaffSoftphone", img, "MyStaff Softphone", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            print("[WARN] Could not initialize system tray:", e)

    def minimize_to_tray(self):
        self.withdraw()
        if HAS_TRAY and self.tray_icon:
            try:
                self.tray_icon.notify("MyStaff Softphone minimized to tray. Double click or right-click to restore.", "MyStaff")
            except Exception:
                pass

    def tray_restore(self, icon=None, item=None):
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def tray_action(self, action):
        ok, res = self.api.clock(action)
        self.after(0, lambda: self.handle_clock_result(ok, res, action))

    def tray_exit(self, icon=None, item=None):
        self.poll_active = False
        if self.tray_icon:
            self.tray_icon.stop()
        self.after(0, self.destroy)

    def on_close_clicked(self):
        if HAS_TRAY and self.tray_icon:
            self.minimize_to_tray()
        else:
            self.poll_active = False
            self.destroy()


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = SoftphoneApp()
    app.mainloop()
