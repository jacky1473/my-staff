import os
import sqlite3
import pytz
import logging
import random
import string
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import shutil
import json

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-in-production-please')

DB_PATH = '/data/attendance.db' if os.path.exists('/data') else 'attendance.db'
BACKUP_DIR = os.environ.get('BACKUP_DIR', './backups')

# Ensure backup directory exists
os.makedirs(BACKUP_DIR, exist_ok=True)

ALLOWED_DEPARTMENTS = ['IT', 'MIS', 'QA', 'TL', 'Manager', 'Management']
SHIFT_OPTIONS = [
    '09:00 AM - 06:00 PM',
    '10:00 AM - 07:00 PM',
    '12:00 PM - 09:00 PM',
    '02:00 PM - 11:00 PM',
    'Night Shift',
    'Flexible',
]
WEEKDAY_OPTIONS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

# Brute-force protection
_login_attempts = {}
MAX_ATTEMPTS    = 3  # Reduced from 5 to 3 for local network
LOCKOUT_MINUTES = 5   # Reduced from 15 to 5

# PIN storage: {username: {'pin': code, 'expires': datetime, 'role': 'Admin'|'Staff'}}
_pin_storage = {}
PIN_EXPIRY_MINUTES = 3  # 3 minute expiry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")   # Better concurrent access
    conn.execute("PRAGMA synchronous=NORMAL") # Faster writes, still safe
    return conn


def init_db():
    conn = get_db_connection()

    conn.execute('''CREATE TABLE IF NOT EXISTS company (
        id   INTEGER PRIMARY KEY,
        name TEXT    NOT NULL
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        username          TEXT UNIQUE NOT NULL,
        password          TEXT NOT NULL,
        department        TEXT NOT NULL,
        role              TEXT NOT NULL,
        shift             TEXT DEFAULT '09:00 AM - 06:00 PM',
        weekoff           TEXT DEFAULT 'Sunday',
        security_question TEXT DEFAULT 'What is your favorite color?',
        security_answer   TEXT DEFAULT 'blue'
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER NOT NULL,
        date      TEXT    NOT NULL,
        clock_in  TEXT,
        clock_out TEXT,
        status    TEXT DEFAULT 'Present',
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS announcements (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT    NOT NULL,
        body       TEXT    NOT NULL,
        priority   TEXT    DEFAULT 'normal',
        created_at TEXT    NOT NULL,
        created_by TEXT    NOT NULL,
        active     INTEGER DEFAULT 1
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER,
        action    TEXT NOT NULL,
        details   TEXT,
        ip_addr   TEXT,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS activity_logs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL UNIQUE,
        username     TEXT NOT NULL,
        status       TEXT DEFAULT 'offline',
        current_page TEXT DEFAULT '/',
        ip_addr      TEXT,
        last_seen    TEXT,
        last_active  TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')

    _safe_alter(conn, "ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN shift TEXT DEFAULT '09:00 AM - 06:00 PM'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN weekoff TEXT DEFAULT 'Sunday'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN security_question TEXT DEFAULT 'What is your favorite color?'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN security_answer TEXT DEFAULT 'blue'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN nickname TEXT DEFAULT ''")

    conn.commit()
    conn.close()


def _safe_alter(conn, sql):
    try:
        conn.execute(sql)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ist_now():
    return datetime.now(pytz.timezone('Asia/Kolkata'))


def today_str():
    return ist_now().strftime('%Y-%m-%d')


def get_todays_roster():
    today = today_str()
    conn  = get_db_connection()
    roster = conn.execute('''
        SELECT u.username, u.full_name, u.nickname, u.department, u.shift, u.weekoff,
               a.clock_in, a.clock_out, a.status
        FROM   users u
        LEFT JOIN attendance a ON u.id = a.user_id AND a.date = ?
        WHERE  u.role != 'Admin'
        ORDER  BY u.department, u.username
    ''', (today,)).fetchall()
    conn.close()
    return roster


def get_active_announcements():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM announcements WHERE active = 1 ORDER BY priority DESC, created_at DESC"
    ).fetchall()
    conn.close()
    return rows


def get_today_stats():
    today = today_str()
    conn  = get_db_connection()
    total   = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'Admin'").fetchone()[0]
    present = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND clock_in IS NOT NULL", (today,)
    ).fetchone()[0]
    absent  = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Absent'", (today,)
    ).fetchone()[0]
    on_leave = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Leave'", (today,)
    ).fetchone()[0]
    late = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Late'", (today,)
    ).fetchone()[0]
    in_office = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND clock_in IS NOT NULL AND clock_out IS NULL", (today,)
    ).fetchone()[0]
    conn.close()
    not_arrived = total - present - absent - on_leave
    return {
        'total': total,
        'present': present,
        'absent': absent,
        'on_leave': on_leave,
        'late': late,
        'in_office': in_office,
        'not_arrived': max(not_arrived, 0),
    }


# ---------------------------------------------------------------------------
# PIN Generation & Verification (NO EMAIL NEEDED!)
# ---------------------------------------------------------------------------
def generate_pin():
    """Generate 4-digit PIN"""
    return ''.join(random.choices(string.digits, k=4))


def log_audit(action, details=None, user_id=None):
    """Log admin actions — uses own connection with timeout to avoid locking"""
    try:
        ip_addr = request.remote_addr if request else 'system'
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")  # Allows concurrent reads
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, details, ip_addr, timestamp) VALUES (?,?,?,?,?)",
            (user_id, action, details, ip_addr, ist_now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Audit log error: {e}")


# ---------------------------------------------------------------------------
# Brute-force protection
# ---------------------------------------------------------------------------
def _check_lockout(username):
    entry = _login_attempts.get(username)
    if not entry:
        return False, 0
    count, locked_until = entry
    if locked_until and ist_now() < locked_until:
        remaining = int((locked_until - ist_now()).total_seconds())
        return True, remaining
    return False, 0


def _record_failed_attempt(username):
    entry = _login_attempts.get(username, [0, None])
    count = entry[0] + 1
    locked_until = None
    if count >= MAX_ATTEMPTS:
        locked_until = ist_now() + timedelta(minutes=LOCKOUT_MINUTES)
        logger.warning("Account locked: %s after %d failed attempts", username, count)
    _login_attempts[username] = [count, locked_until]


def _clear_attempts(username):
    _login_attempts.pop(username, None)


# ---------------------------------------------------------------------------
# Backup Functions
# ---------------------------------------------------------------------------
def create_backup():
    """Create database backup"""
    try:
        timestamp = ist_now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(BACKUP_DIR, f'attendance_backup_{timestamp}.db')
        shutil.copy2(DB_PATH, backup_file)
        logger.info(f"Backup created: {backup_file}")
        return backup_file
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return None


def get_backups():
    """List all available backups"""
    try:
        backups = []
        for file in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if file.startswith('attendance_backup_') and file.endswith('.db'):
                filepath = os.path.join(BACKUP_DIR, file)
                size = os.path.getsize(filepath)
                mtime = os.path.getmtime(filepath)
                backups.append({
                    'filename': file,
                    'filepath': filepath,
                    'size': size,
                    'mtime': datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
        return backups[:7]  # Keep last 7 backups
    except Exception as e:
        logger.error(f"Get backups failed: {e}")
        return []


def restore_backup(backup_file):
    """Restore from backup"""
    try:
        shutil.copy2(backup_file, DB_PATH)
        logger.info(f"Database restored from {backup_file}")
        return True
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return False


def cleanup_old_backups():
    """Keep only last 7 backups"""
    try:
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('attendance_backup_')])
        if len(backups) > 7:
            for old_backup in backups[:-7]:
                os.remove(os.path.join(BACKUP_DIR, old_backup))
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")


# ---------------------------------------------------------------------------
# Context Processor
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    company_name = "Enterprise"
    try:
        conn  = get_db_connection()
        comp  = conn.execute("SELECT name FROM company LIMIT 1").fetchone()
        conn.close()
        if comp:
            company_name = comp['name']
    except Exception:
        pass
    return dict(
        company_name=company_name,
        current_year=ist_now().year,
    )


# ---------------------------------------------------------------------------
# Before Request
# ---------------------------------------------------------------------------
@app.before_request
def check_setup():
    if request.endpoint in ('setup', 'static'):
        return
    try:
        conn = get_db_connection()
        comp = conn.execute("SELECT * FROM company").fetchone()
        conn.close()
        if not comp:
            return redirect(url_for('setup'))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
@app.route('/setup', methods=['GET', 'POST'])
def setup():
    conn = get_db_connection()
    comp = conn.execute("SELECT * FROM company").fetchone()
    if comp:
        conn.close()
        return redirect(url_for('login'))

    if request.method == 'POST':
        company_name = request.form.get('company_name', '').strip()
        admin_user   = request.form.get('admin_username', '').strip()
        admin_pass   = request.form.get('admin_password', '')
        sec_q        = request.form.get('security_question', '').strip()
        sec_a        = request.form.get('security_answer', '').strip().lower()

        if not all([company_name, admin_user, admin_pass, sec_q, sec_a]):
            flash("All fields are required.")
            conn.close()
            return render_template('setup.html')

        hashed = generate_password_hash(admin_pass)
        conn.execute("INSERT INTO company (name) VALUES (?)", (company_name,))
        existing_admin = conn.execute("SELECT id FROM users WHERE role='Admin'").fetchone()
        if existing_admin:
            conn.execute(
                "UPDATE users SET username=?, password=?, security_question=?, security_answer=? WHERE id=?",
                (admin_user, hashed, sec_q, sec_a, existing_admin['id'])
            )
        else:
            conn.execute(
                "INSERT INTO users (username, password, department, role, shift, weekoff, security_question, security_answer) VALUES (?,?,?,?,?,?,?,?)",
                (admin_user, hashed, 'Management', 'Admin', 'Flexible', 'Sunday', sec_q, sec_a)
            )
        conn.commit()
        conn.close()
        create_backup()  # Create first backup after setup
        flash("✅ System initialized! Welcome to your portal.")
        return redirect(url_for('login'))

    conn.close()
    return render_template('setup.html')


# ---------------------------------------------------------------------------
# NEW LOGIN with PIN (Not Email OTP!)
# ---------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        login_type = request.form.get('login_type', 'staff')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # Check lockout
        locked, secs = _check_lockout(username)
        if locked:
            mins = secs // 60 + 1
            flash(f"Account locked. Try again in {mins} minute(s).")
            return render_template('login.html')

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        # Verify role matches login type
        expected_role = 'Admin' if login_type == 'admin' else 'Staff'
        if user and user['role'] == expected_role and check_password_hash(user['password'], password):
            if user['role'] != 'Admin' and user['department'] not in ALLOWED_DEPARTMENTS:
                flash('Access denied: unauthorized department.')
                return redirect(url_for('login'))

            # Generate PIN (4 digits, NO email needed!)
            pin = generate_pin()
            pin_expires = ist_now() + timedelta(minutes=PIN_EXPIRY_MINUTES)
            _pin_storage[username] = {
                'pin': pin,
                'expires': pin_expires,
                'role': expected_role,
                'user_id': user['id'],
                'department': user['department']
            }

            # Store in session for verification page
            session['pending_login'] = {
                'username': username,
                'role': expected_role,
                'user_id': user['id'],
                'department': user['department']
            }

            flash(f"✅ Your PIN is: {pin}", "success")
            return render_template('pin_verify.html', username=username, pin_display=pin, login_type=login_type)
        else:
            _record_failed_attempt(username)
            count = _login_attempts.get(username, [0])[0]
            remaining = MAX_ATTEMPTS - count
            if remaining > 0:
                flash(f"Invalid credentials. {remaining} attempt(s) left.")
            else:
                flash(f"Account locked for {LOCKOUT_MINUTES} minutes.")

    return render_template('login.html')


@app.route('/verify-pin', methods=['POST'])
def verify_pin():
    """Verify PIN and complete login"""
    pin_entered = request.form.get('pin', '').strip()
    username = request.form.get('username', '').strip()

    if 'pending_login' not in session:
        flash("Session expired. Please login again.")
        return redirect(url_for('login'))
    
    if session.get('pending_login', {}).get('username') != username:
        flash("Session mismatch. Please login again.")
        return redirect(url_for('login'))

    if username not in _pin_storage:
        flash("PIN expired. Please login again.")
        if 'pending_login' in session:
            del session['pending_login']
        return redirect(url_for('login'))

    pin_data = _pin_storage[username]

    # Check PIN expiry
    if ist_now() > pin_data['expires']:
        flash("PIN expired. Please login again.")
        del _pin_storage[username]
        if 'pending_login' in session:
            del session['pending_login']
        return redirect(url_for('login'))

    # Verify PIN
    if pin_data['pin'] != pin_entered:
        flash("Invalid PIN. Try again.")
        # Re-render with the same PIN displayed again
        pending = session.get('pending_login', {})
        login_type = pending.get('role', 'Staff').lower()
        return render_template('pin_verify.html', username=username, pin_display=pin_data['pin'], login_type=login_type)

    # PIN verified! Complete login
    pending = session.get('pending_login', {})
    if not pending:
        flash("Session expired. Please login again.")
        return redirect(url_for('login'))
    
    _clear_attempts(username)
    del _pin_storage[username]
    del session['pending_login']

    session['user_id']    = pending.get('user_id')
    session['username']   = username
    session['department'] = pending.get('department')
    session['role']       = pending.get('role')

    # Store display name (nickname > full_name > username)
    try:
        conn = get_db_connection()
        urow = conn.execute("SELECT full_name, nickname FROM users WHERE id=?", (pending.get('user_id'),)).fetchone()
        conn.close()
        if urow:
            session['display_name'] = urow['nickname'] or urow['full_name'] or username
        else:
            session['display_name'] = username
    except Exception:
        session['display_name'] = username

    log_audit('LOGIN_SUCCESS', f"Role: {pending.get('role')}", pending.get('user_id'))
    logger.info("Login successful: %s (%s)", username, pending.get('role'))
    flash("✅ Logged in successfully!", "success")

    # Staff → trigger agent download page first
    if pending.get('role') == 'Staff':
        return redirect(url_for('agent_launch'))

    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    username = session.get('username')
    role = session.get('role')
    log_audit('LOGOUT', f"Role: {role}", user_id)
    logger.info("Logout: %s", username)
    session.clear()
    flash("Logged out successfully.")
    return redirect(url_for('login'))


@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    step = request.args.get('step', '1')
    if request.method == 'POST':
        conn = get_db_connection()
        if step == '1':
            username = request.form.get('username', '').strip()
            user = conn.execute(
                "SELECT security_question FROM users WHERE username = ?", (username,)
            ).fetchone()
            conn.close()
            if user:
                return render_template('forgot.html', step='2', username=username, question=user['security_question'])
            flash("No account found with that username.")
            return redirect(url_for('forgot'))

        elif step == '2':
            username = request.form.get('username', '').strip()
            answer   = request.form.get('security_answer', '').strip().lower()
            new_pass = request.form.get('new_password', '')

            if not new_pass or len(new_pass) < 6:
                conn.close()
                flash("New password must be at least 6 characters.")
                return redirect(url_for('forgot'))

            user = conn.execute(
                "SELECT id, security_answer FROM users WHERE username = ?", (username,)
            ).fetchone()
            if user and user['security_answer'] == answer:
                conn.execute(
                    "UPDATE users SET password = ? WHERE username = ?",
                    (generate_password_hash(new_pass), username)
                )
                conn.commit()
                conn.close()
                _clear_attempts(username)
                log_audit('PASSWORD_RESET', username=user['id'])
                flash("✅ Password reset successfully. You may now log in.")
                return redirect(url_for('login'))
            else:
                conn.close()
                flash("Incorrect security answer. Access denied.")
                return redirect(url_for('forgot'))

    return render_template('forgot.html', step='1')


# ---------------------------------------------------------------------------
# Index & Routing
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session['role'] == 'Admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('staff_dashboard'))


# ---------------------------------------------------------------------------
# Staff Dashboard
# ---------------------------------------------------------------------------
@app.route('/staff')
def staff_dashboard():
    if 'user_id' not in session or session['role'] != 'Staff':
        return redirect(url_for('login'))

    today_day = ist_now().strftime('%A')
    conn      = get_db_connection()
    user_data = conn.execute(
        'SELECT shift, weekoff FROM users WHERE id = ?', (session['user_id'],)
    ).fetchone()
    logs = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 30',
        (session['user_id'],)
    ).fetchall()
    conn.close()

    return render_template(
        'staff.html',
        logs=logs,
        roster=get_todays_roster(),
        today_day=today_day,
        user_shift=user_data['shift'] if user_data else '09:00 AM - 06:00 PM',
        user_weekoff=user_data['weekoff'] if user_data else 'Sunday',
        announcements=get_active_announcements(),
    )


@app.route('/staff/history')
def staff_history():
    if 'user_id' not in session or session['role'] != 'Staff':
        return redirect(url_for('login'))
    conn = get_db_connection()
    logs = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('history.html', logs=logs)


@app.route('/clock', methods=['POST'])
def clock():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    action   = request.form.get('action')
    user_id  = session['user_id']
    now      = ist_now()
    today    = now.strftime('%Y-%m-%d')
    now_time = now.strftime('%H:%M:%S')

    conn   = get_db_connection()
    record = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? AND date = ?', (user_id, today)
    ).fetchone()

    if action == 'in':
        if not record:
            conn.execute(
                'INSERT INTO attendance (user_id, date, clock_in, status) VALUES (?,?,?,?)',
                (user_id, today, now_time, 'Present')
            )
            log_audit('CLOCK_IN', today, user_id)
            flash(f'✅ Clocked in at {now_time}')
        else:
            flash('You have already clocked in today.')
    elif action == 'out':
        if record and not record['clock_out']:
            conn.execute(
                'UPDATE attendance SET clock_out = ? WHERE id = ?', (now_time, record['id'])
            )
            log_audit('CLOCK_OUT', today, user_id)
            flash(f'👋 Clocked out at {now_time}')
        else:
            flash('You must clock in first, or have already clocked out.')

    conn.commit()
    conn.close()
    return redirect(url_for('staff_dashboard'))


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------
@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    today_day = ist_now().strftime('%A')
    conn  = get_db_connection()
    users = conn.execute(
        "SELECT id, username, full_name, nickname, department, shift, weekoff FROM users WHERE role != 'Admin' ORDER BY department, username"
    ).fetchall()
    announcements = conn.execute(
        "SELECT * FROM announcements ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()

    return render_template(
        'admin.html',
        users=users,
        roster=get_todays_roster(),
        today_day=today_day,
        shift_options=SHIFT_OPTIONS,
        weekday_options=WEEKDAY_OPTIONS,
        departments=ALLOWED_DEPARTMENTS,
        stats=get_today_stats(),
        announcements=announcements,
    )


@app.route('/admin/analytics')
def admin_analytics():
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    rows = conn.execute('''
        SELECT
            a.date,
            COUNT(DISTINCT a.user_id) AS total_marked,
            SUM(CASE WHEN a.clock_in IS NOT NULL THEN 1 ELSE 0 END) AS present,
            SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent,
            SUM(CASE WHEN a.status = 'Leave' THEN 1 ELSE 0 END) AS on_leave
        FROM attendance a
        WHERE a.date >= date('now', '-30 days')
        GROUP BY a.date
        ORDER BY a.date DESC
    ''').fetchall()

    user_summary = conn.execute('''
        SELECT
            u.username, u.department,
            COUNT(a.id) AS total_days,
            SUM(CASE WHEN a.clock_in IS NOT NULL THEN 1 ELSE 0 END) AS present_days,
            SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent_days,
            SUM(CASE WHEN a.status = 'Leave' THEN 1 ELSE 0 END) AS leave_days
        FROM users u
        LEFT JOIN attendance a ON u.id = a.user_id AND a.date >= date('now', '-30 days')
        WHERE u.role != 'Admin'
        GROUP BY u.id
        ORDER BY absent_days DESC, u.username
    ''').fetchall()

    conn.close()
    return render_template('analytics.html', daily_rows=rows, user_summary=user_summary)


@app.route('/admin/system')
def admin_system():
    """Server status & backup management"""
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    try:
        db_size = os.path.getsize(DB_PATH) / (1024 * 1024)  # MB
    except:
        db_size = 0

    backups = get_backups()
    conn = get_db_connection()
    user_count = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'Admin'").fetchone()[0]
    conn.close()

    return render_template('system.html', db_size=db_size, backups=backups, user_count=user_count)


@app.route('/admin/backup', methods=['POST'])
def admin_backup():
    """Create backup manually"""
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    backup_file = create_backup()
    if backup_file:
        log_audit('BACKUP_CREATED', backup_file, session['user_id'])
        flash(f"✅ Backup created successfully!")
    else:
        flash("❌ Backup failed. Check disk space.")

    return redirect(url_for('admin_system'))


@app.route('/admin/restore/<backup_name>', methods=['POST'])
def admin_restore(backup_name):
    """Restore from backup"""
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    backup_file = os.path.join(BACKUP_DIR, backup_name)
    
    # Security check: ensure file is in backup directory
    if not backup_file.startswith(BACKUP_DIR) or not os.path.exists(backup_file):
        flash("❌ Invalid backup file.")
        return redirect(url_for('admin_system'))

    if restore_backup(backup_file):
        log_audit('DATABASE_RESTORED', backup_name, session['user_id'])
        flash(f"✅ Database restored from {backup_name}!")
    else:
        flash("❌ Restore failed. Check permissions.")

    return redirect(url_for('admin_system'))


@app.route('/admin_action', methods=['POST'])
def admin_action():
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    action_type = request.form.get('action_type')
    conn = get_db_connection()

    try:
        if action_type == 'add_user':
            new_user  = request.form.get('new_username', '').strip()
            new_pass  = request.form.get('new_password', '')
            full_name = request.form.get('full_name', '').strip()
            nickname  = request.form.get('nickname', '').strip()
            dept      = request.form.get('department', '')
            weekoff   = request.form.get('weekoff', 'Sunday')

            if not new_user or not new_pass or dept not in ALLOWED_DEPARTMENTS:
                flash("Invalid input.")
            elif weekoff not in WEEKDAY_OPTIONS:
                flash("Invalid week-off day.")
            else:
                try:
                    conn.execute(
                        "INSERT INTO users (username, password, full_name, nickname, department, role, shift, weekoff, security_question, security_answer) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (new_user, generate_password_hash(new_pass), full_name, nickname,
                         dept, 'Staff', '09:00 AM - 06:00 PM', weekoff, 'Set by admin', 'yes')
                    )
                    log_audit('USER_CREATED', f"{new_user} ({dept})", session['user_id'])
                    flash(f"✅ Employee '{new_user}' created.")
                except sqlite3.IntegrityError:
                    flash(f"Username '{new_user}' already exists.")

        elif action_type == 'delete_user':
            target = request.form.get('target_user', '').strip()
            user = conn.execute("SELECT id FROM users WHERE username=? AND role!='Admin'", (target,)).fetchone()
            if user:
                conn.execute("DELETE FROM users WHERE id = ?", (user['id'],))
                log_audit('USER_DELETED', target, session['user_id'])
                flash(f"✅ Employee '{target}' removed.")
            else:
                flash("User not found.")

        elif action_type == 'reset_password':
            target   = request.form.get('target_user', '').strip()
            new_pass = request.form.get('new_password', '')
            if len(new_pass) < 6:
                flash("Password must be 6+ characters.")
            else:
                conn.execute(
                    "UPDATE users SET password=? WHERE username=?",
                    (generate_password_hash(new_pass), target)
                )
                log_audit('PASSWORD_RESET_ADMIN', target, session['user_id'])
                flash(f"✅ Password reset for '{target}'.")

        elif action_type == 'change_shift':
            target      = request.form.get('target_user', '').strip()
            new_shift   = request.form.get('new_shift', '')
            new_weekoff = request.form.get('new_weekoff', '')
            if new_shift not in SHIFT_OPTIONS or new_weekoff not in WEEKDAY_OPTIONS:
                flash("Invalid shift or week-off.")
            else:
                conn.execute(
                    "UPDATE users SET shift=?, weekoff=? WHERE username=?",
                    (new_shift, new_weekoff, target)
                )
                log_audit('SHIFT_UPDATED', f"{target}: {new_shift}", session['user_id'])
                flash(f"✅ Schedule updated for '{target}'.")

        elif action_type == 'mark_leave':
            target = request.form.get('target_user', '').strip()
            status = request.form.get('status', '')
            if status not in ('Leave', 'Absent'):
                flash("Invalid status.")
            else:
                today = today_str()
                user  = conn.execute('SELECT id FROM users WHERE username=?', (target,)).fetchone()
                if user:
                    record = conn.execute(
                        'SELECT id FROM attendance WHERE user_id=? AND date=?', (user['id'], today)
                    ).fetchone()
                    if record:
                        conn.execute('UPDATE attendance SET status=? WHERE id=?', (status, record['id']))
                    else:
                        conn.execute(
                            'INSERT INTO attendance (user_id, date, status) VALUES (?,?,?)',
                            (user['id'], today, status)
                        )
                    log_audit('STATUS_MARKED', f"{target}: {status}", session['user_id'])
                    flash(f"✅ '{target}' marked as {status}.")
                else:
                    flash("User not found.")

        elif action_type == 'post_announcement':
            title    = request.form.get('ann_title', '').strip()
            body     = request.form.get('ann_body', '').strip()
            priority = request.form.get('ann_priority', 'normal')
            if not title or not body:
                flash("Title and message required.")
            elif priority not in ('normal', 'high', 'urgent'):
                flash("Invalid priority.")
            else:
                conn.execute(
                    "INSERT INTO announcements (title, body, priority, created_at, created_by, active) VALUES (?,?,?,?,?,1)",
                    (title, body, priority, ist_now().strftime('%Y-%m-%d %H:%M:%S'), session['username'])
                )
                log_audit('ANNOUNCEMENT_POSTED', title, session['user_id'])
                flash(f"✅ Announcement posted.")

        elif action_type == 'delete_announcement':
            ann_id = request.form.get('ann_id', '')
            if ann_id.isdigit():
                conn.execute("DELETE FROM announcements WHERE id=?", (int(ann_id),))
                log_audit('ANNOUNCEMENT_DELETED', f"ID: {ann_id}", session['user_id'])
                flash("✅ Announcement removed.")
            else:
                flash("Invalid ID.")

        elif action_type == 'update_profile':
            target    = request.form.get('target_user', '').strip()
            full_name = request.form.get('full_name', '').strip()
            nickname  = request.form.get('nickname', '').strip()
            conn.execute(
                "UPDATE users SET full_name=?, nickname=? WHERE username=?",
                (full_name, nickname, target)
            )
            log_audit('PROFILE_UPDATED', f"{target}: {full_name} / {nickname}", session['user_id'])
            flash(f"✅ Profile updated for '{target}'.")

        elif action_type == 'update_company':
            new_name = request.form.get('company_name', '').strip()
            if not new_name:
                flash("Company name required.")
            else:
                conn.execute("UPDATE company SET name=? WHERE id=1", (new_name,))
                log_audit('COMPANY_UPDATED', new_name, session['user_id'])
                flash(f"✅ Company name updated.")

        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error(f"Admin action error: {e}")
        flash("❌ Error. Please try again.")
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@app.route('/export/<string:report_type>')
def export_excel(report_type):
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    if report_type not in ('weekly', 'monthly', 'all'):
        flash("Invalid report type.")
        return redirect(url_for('admin_dashboard'))

    target_user = request.args.get('target_user', 'All')
    conn = get_db_connection()
    df   = pd.read_sql_query('''
        SELECT u.username, u.full_name, u.nickname, u.department, u.shift, u.weekoff,
               a.date, a.clock_in, a.clock_out, a.status
        FROM   attendance a
        JOIN   users u ON a.user_id = u.id
        ORDER  BY a.date DESC
    ''', conn)
    conn.close()

    if df.empty:
        flash("No attendance data.")
        return redirect(url_for('admin_dashboard'))

    df['date'] = pd.to_datetime(df['date'])
    now = datetime.now()
    if report_type == 'weekly':
        df = df[df['date'] >= (now - timedelta(days=7))]
    elif report_type == 'monthly':
        df = df[df['date'] >= (now - timedelta(days=30))]

    if target_user != 'All':
        df = df[df['username'] == target_user]

    if df.empty:
        flash("No records found.")
        return redirect(url_for('admin_dashboard'))

    df['date']  = df['date'].dt.strftime('%Y-%m-%d')
    safe_user   = target_user.replace(' ', '_')
    output_path = f"/tmp/{report_type}_attendance_{safe_user}.xlsx"
    df.to_excel(output_path, index=False)
    log_audit('EXPORT', f"{report_type} ({target_user})", session['user_id'])
    return send_file(output_path, as_attachment=True)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# LIVE ACTIVITY TRACKING
# ---------------------------------------------------------------------------

IDLE_SECONDS    = 60    # 1 minute no activity → idle
OFFLINE_SECONDS = 300   # 5 minutes no heartbeat → offline

@app.route('/api/agent-login', methods=['POST'])
def agent_login():
    """Windows agent login — returns session for heartbeat"""
    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'ok': False, 'error': 'Missing credentials'}), 400

    # Check lockout
    locked, secs = _check_lockout(username)
    if locked:
        return jsonify({'ok': False, 'error': f'Locked for {secs}s'}), 429

    conn = get_db_connection()
    user = conn.execute(
        'SELECT * FROM users WHERE username = ?', (username,)
    ).fetchone()
    conn.close()

    if user and check_password_hash(user['password'], password):
        _clear_attempts(username)
        session['user_id']    = user['id']
        session['username']   = user['username']
        session['department'] = user['department']
        session['role']       = user['role']
        log_audit('AGENT_LOGIN', f"Windows agent login: {username}", user['id'])
        return jsonify({'ok': True, 'role': user['role']})

    _record_failed_attempt(username)
    return jsonify({'ok': False, 'error': 'Invalid credentials'}), 401


@app.route('/api/agent-logout', methods=['POST'])
def agent_logout():
    """Mark agent as offline on shutdown"""
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401

    user_id  = session['user_id']
    username = session['username']
    now_str  = ist_now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE activity_logs SET status='offline', last_seen=? WHERE user_id=?",
            (now_str, user_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Agent logout error: {e}")

    log_audit('AGENT_LOGOUT', f"Windows agent logout: {username}", user_id)
    return jsonify({'ok': True})


@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """Called every 10s from staff browser — silent tracking"""
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401

    data        = request.get_json(silent=True) or {}
    user_id     = session['user_id']
    username    = session['username']
    current_page= data.get('page', '/')
    has_activity= data.get('active', False)  # True = mouse/keyboard detected
    is_offline  = data.get('offline', False)  # True = agent shutting down
    ip_addr     = request.remote_addr
    now_str     = ist_now().strftime('%Y-%m-%d %H:%M:%S')

    # If agent is shutting down, mark offline immediately
    if is_offline:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE activity_logs SET status='offline', last_seen=? WHERE user_id=?",
                (now_str, user_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Offline mark error: {e}")
        return jsonify({'ok': True})

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        existing = conn.execute(
            "SELECT id, last_active FROM activity_logs WHERE user_id=?", (user_id,)
        ).fetchone()

        if existing:
            # Update last_seen always; update last_active only if activity
            if has_activity:
                conn.execute(
                    "UPDATE activity_logs SET status='active', last_seen=?, last_active=?, current_page=?, ip_addr=? WHERE user_id=?",
                    (now_str, now_str, current_page, ip_addr, user_id)
                )
            else:
                # Compute idle vs active from last_active
                last_active = existing[1] or now_str
                try:
                    la = datetime.strptime(last_active, '%Y-%m-%d %H:%M:%S')
                    la = pytz.timezone('Asia/Kolkata').localize(la)
                    diff = (ist_now() - la).total_seconds()
                except:
                    diff = 0
                status = 'active' if diff < IDLE_SECONDS else 'idle'
                conn.execute(
                    "UPDATE activity_logs SET status=?, last_seen=?, current_page=?, ip_addr=? WHERE user_id=?",
                    (status, now_str, current_page, ip_addr, user_id)
                )
        else:
            conn.execute(
                "INSERT INTO activity_logs (user_id, username, status, current_page, ip_addr, last_seen, last_active) VALUES (?,?,?,?,?,?,?)",
                (user_id, username, 'active', current_page, ip_addr, now_str, now_str)
            )

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Heartbeat error: {e}")

    return jsonify({'ok': True})


@app.route('/api/activity')
def get_activity():
    """Admin only — returns live activity of all staff"""
    if 'user_id' not in session or session['role'] != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 401

    now = ist_now()
    try:
        conn = get_db_connection()

        # Get all staff
        staff = conn.execute(
            "SELECT id, username, full_name, nickname, department, shift FROM users WHERE role != 'Admin' ORDER BY department, username"
        ).fetchall()

        # Get activity logs
        activity = conn.execute(
            "SELECT user_id, status, current_page, ip_addr, last_seen, last_active FROM activity_logs"
        ).fetchall()
        conn.close()

        activity_map = {row['user_id']: row for row in activity}

        result = []
        for s in staff:
            act = activity_map.get(s['id'])
            if act:
                # Recalculate status based on time elapsed
                last_seen = act['last_seen']
                try:
                    ls = datetime.strptime(last_seen, '%Y-%m-%d %H:%M:%S')
                    ls = pytz.timezone('Asia/Kolkata').localize(ls)
                    elapsed = (now - ls).total_seconds()
                except:
                    elapsed = 9999

                if elapsed > OFFLINE_SECONDS:
                    status = 'offline'
                elif elapsed > IDLE_SECONDS:
                    status = 'idle'
                else:
                    status = act['status'] or 'active'

                last_active = act['last_active'] or '—'
                # Format last seen nicely
                try:
                    ls_dt = datetime.strptime(last_seen, '%Y-%m-%d %H:%M:%S')
                    ls_dt = pytz.timezone('Asia/Kolkata').localize(ls_dt)
                    secs  = int((now - ls_dt).total_seconds())
                    if secs < 60:   ago = f"{secs}s ago"
                    elif secs < 3600: ago = f"{secs//60}m ago"
                    else:           ago = f"{secs//3600}h ago"
                except:
                    ago = '—'

                result.append({
                    'user_id':      s['id'],
                    'username':     s['username'],
                    'department':   s['department'],
                    'shift':        s['shift'],
                    'status':       status,
                    'current_page': act['current_page'],
                    'ip_addr':      act['ip_addr'],
                    'last_seen':    ago,
                    'last_active':  last_active,
                })
            else:
                result.append({
                    'user_id':      s['id'],
                    'username':     s['username'],
                    'department':   s['department'],
                    'shift':        s['shift'],
                    'status':       'offline',
                    'current_page': '—',
                    'ip_addr':      '—',
                    'last_seen':    'Never',
                    'last_active':  '—',
                })

        # Summary counts
        summary = {
            'active':  sum(1 for r in result if r['status'] == 'active'),
            'idle':    sum(1 for r in result if r['status'] == 'idle'),
            'offline': sum(1 for r in result if r['status'] == 'offline'),
            'total':   len(result),
        }

        return jsonify({'staff': result, 'summary': summary, 'updated': now.strftime('%H:%M:%S')})

    except Exception as e:
        logger.error(f"Activity fetch error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/admin/activity')
def admin_activity():
    """Live activity monitor page"""
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))
    return render_template('activity.html')


@app.route('/api/status')
def api_status():
    today       = today_str()
    server_time = ist_now().strftime('%H:%M:%S')
    conn = get_db_connection()
    total_staff = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'Admin'").fetchone()[0]
    in_office   = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND clock_in IS NOT NULL AND clock_out IS NULL",
        (today,)
    ).fetchone()[0]
    conn.close()
    return jsonify({
        "system_status": "Healthy",
        "current_time_ist": server_time,
        "metrics": {
            "total_registered_staff": total_staff,
            "active_in_office_now": in_office,
        }
    })


# ---------------------------------------------------------------------------
# Auto-backup on startup
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# AUTO-AGENT LAUNCH (runs after staff login — no installation needed)
# ---------------------------------------------------------------------------

@app.route('/agent-launch')
def agent_launch():
    """Page shown after staff login — auto-triggers agent download"""
    if 'user_id' not in session or session['role'] != 'Staff':
        return redirect(url_for('login'))
    return render_template('agent_launch.html')


@app.route('/download-agent')
def download_agent():
    """Serve a personalized .bat file — fixes VBS pythonw path issue"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    username  = session['username']
    server    = f"{request.scheme}://{request.host}"
    url       = f"{server}/agent-script?u={username}"

    # Build bat using triple-quoted string — avoids escaping issues
    bat_lines = [
        "@echo off",
        f":: StaffPortal Agent for {username}",
        "setlocal EnableDelayedExpansion",
        "set AGENT_DIR=%APPDATA%\\StaffAgent",
        "mkdir \"%AGENT_DIR%\" 2>nul",
        "",
        ":: Find Python full path",
        "set PYTHONW=",
        "for /f \"delims=\" %%i in (\'where pythonw.exe 2^>nul\') do if \"!PYTHONW!\"==\"\" set PYTHONW=%%i",
        "if \"%PYTHONW%\"==\"\" for /f \"delims=\" %%i in (\'where python.exe 2^>nul\') do if \"!PYTHONW!\"==\"\" set PYTHONW=%%i",
        "if \"%PYTHONW%\"==\"\" (",
        "  echo Python not found. Install Python 3 first.",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        ":: Download agent script from server",
        f'powershell -Command \"(New-Object Net.WebClient).DownloadFile(\'{url}\', \'%AGENT_DIR%\\\\agent.py\')\"',
        "if not exist \"%AGENT_DIR%\\agent.py\" (",
        "  echo Download failed. Check network.",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        ":: Write VBS launcher using full Python path",
        "(",
        "echo Set objShell = WScript.CreateObject^(\"WScript.Shell\"^)",
        "echo objShell.Run Chr^(34^) ^& \"%PYTHONW%\" ^& Chr^(34^) ^& \" \" ^& Chr^(34^) ^& \"%AGENT_DIR%\\agent.py\" ^& Chr^(34^), 0, False",
        ") > \"%AGENT_DIR%\\launch.vbs\"",
        "",
        ":: Add to Windows Startup",
        "copy /y \"%AGENT_DIR%\\launch.vbs\" \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\StaffPortalAgent.vbs\" >nul",
        "",
        ":: Start agent now",
        "wscript.exe \"%AGENT_DIR%\\launch.vbs\"",
        "exit",
    ]
    bat = "\r\n".join(bat_lines)

    from flask import Response
    return Response(
        bat,
        mimetype='application/octet-stream',
        headers={'Content-Disposition': f'attachment; filename=start_agent_{username}.bat'}
    )


@app.route('/agent-script')
def agent_script():
    """Serve personalized agent.py with credentials embedded"""
    if 'user_id' not in session:
        # Allow download via URL param as fallback
        u = request.args.get('u', '')
        if not u:
            return "Unauthorized", 401
    else:
        u = session.get('username', request.args.get('u', ''))

    server_ip = f"{request.scheme}://{request.host}"

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    conn.close()

    if not user:
        return "User not found", 404

    # Read base agent template and embed credentials
    agent_template_path = os.path.join(os.path.dirname(__file__), 'agent', 'agent.py')
    if not os.path.exists(agent_template_path):
        return "Agent template not found", 404

    with open(agent_template_path, 'r') as f:
        content = f.read()

    # Embed server URL and username
    content = content.replace('http://192.168.1.100:5000', server_ip)
    content = content.replace('"john"', f'"{u}"')

    from flask import Response
    return Response(content, mimetype='text/plain')


# ---------------------------------------------------------------------------
# AUTO LATE / ABSENT SCHEDULER
# ---------------------------------------------------------------------------

import threading

def parse_shift_start(shift_str):
    """Parse shift string like '09:00 AM - 06:00 PM' → datetime.time"""
    try:
        if not shift_str or shift_str.lower() in ('flexible', 'night shift'):
            return None
        start_part = shift_str.split('-')[0].strip()  # e.g. "09:00 AM"
        from datetime import datetime as dt
        return dt.strptime(start_part, '%I:%M %p').time()
    except Exception:
        return None


def run_attendance_checker():
    """
    Background thread: runs every 60 seconds
    - 30 mins after shift start with no clock-in → mark LATE
    - 60 mins after shift start with no clock-in → mark ABSENT
    """
    import time as time_module

    while True:
        try:
            now_ist      = ist_now()
            today        = now_ist.strftime('%Y-%m-%d')
            today_day    = now_ist.strftime('%A')  # e.g. "Monday"
            now_time     = now_ist.time()

            conn = get_db_connection()
            staff = conn.execute(
                "SELECT id, username, shift, weekoff FROM users WHERE role='Staff'"
            ).fetchall()

            for s in staff:
                # Skip if today is their weekoff
                if s['weekoff'] == today_day:
                    continue

                shift_start = parse_shift_start(s['shift'])
                if not shift_start:
                    continue

                # Calculate minutes since shift started
                from datetime import datetime as dt, timedelta as td
                shift_dt = dt.combine(now_ist.date(), shift_start)
                now_dt   = dt.combine(now_ist.date(), now_time)
                mins_late = (now_dt - shift_dt).total_seconds() / 60

                if mins_late < 0:
                    continue  # Shift hasn't started yet

                # Check existing attendance record
                record = conn.execute(
                    "SELECT * FROM attendance WHERE user_id=? AND date=?",
                    (s['id'], today)
                ).fetchone()

                if record and record['clock_in']:
                    continue  # Already clocked in — no action needed

                if record and record['status'] in ('Absent', 'Leave'):
                    continue  # Already marked

                if mins_late >= 60:
                    # 60+ mins late → ABSENT
                    if record:
                        conn.execute(
                            "UPDATE attendance SET status='Absent' WHERE user_id=? AND date=?",
                            (s['id'], today)
                        )
                    else:
                        conn.execute(
                            "INSERT INTO attendance (user_id, date, status) VALUES (?,?,?)",
                            (s['id'], today, 'Absent')
                        )
                    log_audit('AUTO_ABSENT',
                              f"{s['username']} — {mins_late:.0f} mins past shift start",
                              s['id'])
                    logger.info(f"AUTO-ABSENT: {s['username']} ({mins_late:.0f} mins late)")

                elif mins_late >= 30:
                    # 30-59 mins late → LATE
                    if record:
                        if record['status'] != 'Late':
                            conn.execute(
                                "UPDATE attendance SET status='Late' WHERE user_id=? AND date=?",
                                (s['id'], today)
                            )
                    else:
                        conn.execute(
                            "INSERT INTO attendance (user_id, date, status) VALUES (?,?,?)",
                            (s['id'], today, 'Late')
                        )
                    log_audit('AUTO_LATE',
                              f"{s['username']} — {mins_late:.0f} mins past shift start",
                              s['id'])
                    logger.info(f"AUTO-LATE: {s['username']} ({mins_late:.0f} mins late)")

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"Attendance checker error: {e}")

        time_module.sleep(60)  # Check every 60 seconds


def start_autobackup():
    """Create backup on startup"""
    import atexit
    import signal
    
    def backup_handler(signum=None, frame=None):
        logger.info("Creating backup before shutdown...")
        create_backup()
        cleanup_old_backups()
    
    atexit.register(backup_handler)
    signal.signal(signal.SIGTERM, backup_handler)
    signal.signal(signal.SIGINT, backup_handler)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    init_db()
    start_autobackup()

    # Start auto Late/Absent background scheduler
    checker = threading.Thread(target=run_attendance_checker, daemon=True)
    checker.start()
    logger.info("✅ Auto Late/Absent scheduler started")

    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
