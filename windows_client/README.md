# MyStaff Softphone — Windows Desktop Attendance Client

Lightweight softphone-style Windows desktop application for employee shift attendance, clock-in/out, live timer, and leave applications. Designed to replace web browser logins for staff members.

---

## 📱 Form Factor & UX Reference

- **Softphone Structure**: Similar to MicroSIP / X-Lite, the app runs in a compact widget window (~340×520px).
- **Zero Browser Requirement for Staff**: Staff never needs to open a web browser to clock in, clock out, or apply for leave.
- **Admin Remains on Web Portal**: Administrative dashboards, approvals, tracking, and leave quota management remain on the central web portal.
- **Leave Classification Rule**: Staff submits leave dates and reasons without picking leave types. The Admin decides and classifies whether the request is **PL (Paid Leave)**, **UL (Unpaid Leave)**, or **LWP (Leave Without Pay)**. The Leave Tracker remains exclusively with the Admin.

---

## 🚀 How to Run on Windows

### Option 1: Run Directly via Python
1. Ensure Python 3.9+ is installed on Windows.
2. Open PowerShell or Command Prompt inside the `windows_client` folder:
   ```cmd
   pip install -r requirements.txt
   python softphone.py
   ```

### Option 2: Build Standalone `.exe` (1-Click)
1. Double-click `build_exe.bat` on your Windows PC.
2. It will automatically install requirements and compile the app using PyInstaller.
3. Your standalone executable will be ready in:
   ```
   dist\MyStaff-Softphone.exe
   ```
4. Distribute `MyStaff-Softphone.exe` to staff members' Windows machines. No installation or Python required on end-user machines!

---

## ⚙️ Configuration & Connection

1. On first launch, enter:
   - **Server URL**: `http://<YOUR_LINUX_SERVER_IP>:5001`
   - **Username**: Staff username (e.g. `farman`)
   - **Password**: Staff password
2. If 2FA Email OTP is enabled, enter the 4-digit code sent to your registered email.
3. The softphone securely stores the authentication token in `~/.mystaff_softphone_config.json`.
4. Subsequent launches **automatically log in** directly to the main softphone screen!

---

## 🛠️ Features

| Feature | Description |
|---|---|
| **Dialer Punch In / Out** | 1-Click punch in and out with live ticking shift timer |
| **System Tray** | Minimizes to Windows Taskbar notification area with right-click quick actions |
| **Leave Applications** | Staff applies with Start Date, End Date, and Reason (No leave type selection) |
| **Admin Decision** | Admin assigns PL, UL, or LWP upon approval in the web portal |
| **HR Notices** | Real-time bulletin notifications and official warnings displayed directly in app |
| **Always Synced** | Background sync polls server every 30 seconds |
