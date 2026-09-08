# Staff Attendance & HR Management Portal

> **A modern, full-featured, self-hosted workforce management web portal built with Python Flask, SQLite, and Docker.**

---

## 🌟 Key Features

### 👤 Employee / Staff Dashboard
* **1-Click Punch In / Out**: Real-time shift tracking, elapsed hours calculation, and live clock.
* **Shift & Week-off Display**: Clear indication of allocated shifts and designated rest days.
* **Personal Timesheets**: View past punch history, total hours worked, and attendance status (Present, Late, Leave).
* **Leave Application Portal**: Request statutory leaves (PL, UL, LWP) with automated balance calculation and review remarks.
* **2FA Security**: Multi-factor PIN verification for all user sessions.
* **Mobile-First Responsive UI**: Accessible seamlessly on desktop, tablet, and mobile browsers with Dark/Light mode toggle.

### 🛡️ Admin Management Console
* **Live Workforce Dashboard**: Real-time counter of employees currently in office, on leave, or off-duty.
* **Employee Management**: Add, update shifts, reassign week-offs, or soft-delete (relieve) employees while preserving historical audit records.
* **Statutory Leave Governance**: 1-click Approve or Reject leave requests with administrative remarks; automatic balance deduction.
* **Payroll Ledger Export**: 1-click export of monthly attendance registers and work hours into standard `.xlsx` (Excel) spreadsheets.
* **Disciplinary & Company Broadcasts**: Issue official notices, warnings, and announcements directly to staff dashboards.
* **Data Safety & Backups**: Automated midnight snapshots and manual 1-click database backup/restore directly from the UI.

### 🔒 Enterprise-Grade Security
* **Zero External Dependencies**: Self-hosted SQLite database — 100% private with no third-party data tracking.
* **Concurrency Engine**: SQLite in Write-Ahead-Logging (`WAL`) mode with Gunicorn multi-worker multi-threading.
* **Password Hashing**: Industry-standard cryptographic hashing (PBKDF2/scrypt).
* **Brute-Force Protection**: Automatic temporary account lockout after multiple failed authentication attempts.
* **CSRF Protection**: Form token validation on all state-changing endpoints.

---

## 🚀 Quick Start Guide

### Option 1: Run with Docker (Recommended)

Requires [Docker Desktop](https://www.docker.com/) (Windows/macOS) or Docker Engine (Linux).

```bash
# 1. Clone or unzip the project folder
cd my-staff

# 2. Start the container in detached mode
docker compose up -d

# 3. Open http://localhost:5000 in your browser
```

To view logs:
```bash
docker compose logs -f
```

To stop the container:
```bash
docker compose down
```

---

### Option 2: Run with Python Locally

Requires **Python 3.9+**.

```bash
# 1. Navigate to the project directory
cd my-staff

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate       # On Linux/macOS
# .\venv\Scripts\activate     # On Windows

# 3. Install required packages
pip install -r requirements.txt

# 4. Launch the application
python app.py

# 5. Open http://localhost:5000 in your browser
```

---

## 🧙 First-Run Setup Wizard

When you open the portal for the very first time on a fresh database:
1. You will be automatically redirected to the **Setup Wizard (`/setup`)**.
2. Enter your **Company / Organization Name**.
3. Choose your initial **Admin Username** and **Password**.
4. Click **Complete Setup** — the database schema initializes automatically, and you are ready to manage your staff!

---

## ⚙️ Environment Variables

Copy `.env.example` to `.env` to customize your installation:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SECRET_KEY` | *(auto-generated)* | Flask session signing secret key — change in production! |
| `PORT` | `5000` | Port on which the web application will bind |
| `FLASK_DEBUG` | `false` | Enable Flask interactive debug mode (development only) |
| `DB_PATH` | `./attendance.db` | Custom file path for the SQLite database |
| `BACKUP_DIR` | `./backups` | Directory path where automated snapshots are stored |

---

## 📁 Project Structure

```text
├── app.py                     # Main application, routes, and business logic
├── Dockerfile                 # Production multi-worker container configuration
├── docker-compose.yml         # 1-command Docker deployment definition
├── requirements.txt           # Python dependencies (Flask, Pandas, Gunicorn, etc.)
├── .env.example               # Environment variables configuration template
├── LICENSE.md                 # Commercial software license agreement
├── test_login.py              # Automated headless Selenium UI & auth test suite
├── backups/                   # Point-in-time database snapshot storage
├── static/
│   ├── app.css                # Polished Dark/Light theme styles
│   ├── app.js                 # UI interactions, clocks, and theme toggling
│   └── *.svg                  # Vector application icons and assets
└── templates/                 # Jinja2 HTML5 templates
    ├── base.html              # Core layout template
    ├── login.html             # Multi-role authentication interface
    ├── pin_verify.html        # 2FA PIN entry screen
    ├── setup.html             # First-run company onboarding wizard
    ├── staff.html             # Staff timesheet & leave portal
    ├── admin.html             # Administrative control center
    ├── analytics.html         # Visual metrics & charts
    └── system.html            # Server health & backup restore console
```

---

## 🧪 Automated Testing

An automated headless Firefox Selenium integration test suite is included to verify login panels, 2FA workflows, and authentication guards:

```bash
python3 test_login.py
```

---

## 📄 License & Commercial Terms

This software is licensed under the **Commercial Software License Agreement** (see [LICENSE.md](LICENSE.md)). 
* Permitted: Internal deployment, customization, white-labeling, and integration into internal operations.
* Prohibited: Reselling, sub-licensing, or redistributing the raw source code on public marketplaces.
