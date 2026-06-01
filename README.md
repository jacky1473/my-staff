# Staff Attendance Portal

A lightweight web-based employee attendance management system built with Flask and SQLite.

## Features

- **Staff**: Clock in / clock out, view personal attendance logs, see team presence
- **Admin**: Full employee management (add, delete, shift changes), export Excel reports, force attendance status
- **Security**: Hashed passwords, session-based auth, department validation, CSRF-safe forms
- **Setup wizard**: First-run initialization with company name and admin credentials

## Tech Stack

- Python 3.9 + Flask
- SQLite (persistent via Docker volume)
- Bootstrap 5
- Pandas + openpyxl (Excel exports)
- Docker + Jenkins CI/CD

## Quick Start (Local)

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Environment Variables

| Variable      | Default                          | Description                        |
|---------------|----------------------------------|------------------------------------|
| `SECRET_KEY`  | `change-this-in-production-please` | Flask session secret key — **set this in production** |
| `FLASK_DEBUG` | `false`                          | Enable Flask debug mode            |

## Docker / Jenkins

The Jenkinsfile automates:
1. Pull code from branch
2. Build Docker image (`attendance-app:latest`)
3. Stop old container, start new one (port 5000, volume `attendance_db_vol`)

```bash
docker build -t attendance-app:latest .
docker run -d --name attendance-inst -p 5000:5000 \
  -v attendance_db_vol:/data \
  -e SECRET_KEY=your-strong-secret \
  --restart unless-stopped \
  attendance-app:latest
```

## API

**GET /api/status** — Returns system health and live attendance metrics.

```json
{
  "system_status": "Healthy",
  "current_time_ist": "14:30:00",
  "metrics": {
    "total_registered_staff": 12,
    "active_in_office_now": 7
  }
}
```
