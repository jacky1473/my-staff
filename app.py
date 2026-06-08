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

    _safe_alter(conn, "ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN shift TEXT DEFAULT '09:00 AM - 06:00 PM'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN weekoff TEXT DEFAULT 'Sunday'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN security_question TEXT DEFAULT 'What is your favorite color?'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN security_answer TEXT DEFAULT 'blue'")

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
        SELECT u.username, u.department, u.shift, u.weekoff,
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

    log_audit('LOGIN_SUCCESS', f"Role: {pending.get('role')}", pending.get('user_id'))
    logger.info("Login successful: %s (%s)", username, pending.get('role'))
    flash("✅ Logged in successfully!", "success")
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
        "SELECT id, username, department, shift, weekoff FROM users WHERE role != 'Admin' ORDER BY department, username"
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
            new_user = request.form.get('new_username', '').strip()
            new_pass = request.form.get('new_password', '')
            dept     = request.form.get('department', '')
            weekoff  = request.form.get('weekoff', 'Sunday')

            if not new_user or not new_pass or dept not in ALLOWED_DEPARTMENTS:
                flash("Invalid input.")
            elif weekoff not in WEEKDAY_OPTIONS:
                flash("Invalid week-off day.")
            else:
                try:
                    conn.execute(
                        "INSERT INTO users (username, password, department, role, shift, weekoff, security_question, security_answer) VALUES (?,?,?,?,?,?,?,?)",
                        (new_user, generate_password_hash(new_pass), dept, 'Staff',
                         '09:00 AM - 06:00 PM', weekoff, 'Set by admin', 'yes')
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
        SELECT u.username, u.department, u.shift, u.weekoff,
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
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
