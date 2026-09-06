import os
import sqlite3
import pytz
import logging
import random
import string
import io
import csv
import gzip
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import shutil
import json

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-in-production-please')

DB_PATH = os.environ.get('DB_PATH') or ('/data/attendance.db' if os.path.exists('/data') else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attendance.db'))
BACKUP_DIR = os.environ.get('BACKUP_DIR', './backups')

# Ensure backup directory exists
os.makedirs(BACKUP_DIR, exist_ok=True)

ALLOWED_DEPARTMENTS = ['IT', 'MIS', 'QA', 'TL','Trainer','Admin','Manager', 'Management']
SHIFT_OPTIONS = [
    '09:30 AM - 06:30 PM',
    '10:30 AM - 07:00 PM',
    '10:30 AM - 07:30 PM',
    '11:30 AM - 09:30 PM',
    '12:30 PM - 09:30 PM',
    '05:30 PM - 02:30 AM',
    'Night Shift',
    'Flexible',
]
WEEKDAY_OPTIONS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

# Brute-force protection & PIN security settings
MAX_ATTEMPTS       = 3  # Allowed attempts before lockout
LOCKOUT_MINUTES    = 5  # Lockout duration in minutes
PIN_EXPIRY_MINUTES = 3  # PIN expiry duration in minutes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")   # Better concurrent access
    conn.execute("PRAGMA synchronous=NORMAL") # Faster writes, still safe
    conn.execute("PRAGMA busy_timeout = 20000")
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA mmap_size = 268435456") # 256MB memory mapped I/O
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

    conn.execute('''CREATE TABLE IF NOT EXISTS leaves (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        leave_type   TEXT    NOT NULL,
        start_date   TEXT    NOT NULL,
        end_date     TEXT    NOT NULL,
        days         INTEGER NOT NULL DEFAULT 1,
        reason       TEXT,
        status       TEXT    DEFAULT 'Pending',
        applied_at   TEXT    NOT NULL,
        reviewed_by  TEXT,
        reviewed_at  TEXT,
        admin_remark TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        type       TEXT    DEFAULT 'notice',
        title      TEXT    NOT NULL,
        message    TEXT    NOT NULL,
        created_at TEXT    NOT NULL,
        created_by TEXT    NOT NULL DEFAULT 'Admin',
        is_read    INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')

    # Ensure notifications table has created_by column and user_id is nullable (for broadcast notices)
    try:
        cols_info = conn.execute("PRAGMA table_info(notifications)").fetchall()
        cols = [r[1] for r in cols_info]
        user_id_col = next((c for c in cols_info if c[1] == 'user_id'), None)
        user_id_is_not_null = (user_id_col and user_id_col[3] == 1)

        if 'created_by' not in cols or user_id_is_not_null:
            conn.execute('''CREATE TABLE IF NOT EXISTS notifications_v2 (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                type       TEXT    DEFAULT 'notice',
                title      TEXT    NOT NULL,
                message    TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                created_by TEXT    NOT NULL DEFAULT 'Admin',
                is_read    INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )''')
            cby_sql = "created_by" if 'created_by' in cols else "'Admin'"
            type_sql = "type" if 'type' in cols else "'notice'"
            conn.execute(f'''INSERT INTO notifications_v2 (id, user_id, type, title, message, created_at, created_by, is_read)
                            SELECT id, user_id, {type_sql}, title, message, created_at, {cby_sql}, COALESCE(is_read, 0)
                            FROM notifications''')
            conn.execute("DROP TABLE notifications")
            conn.execute("ALTER TABLE notifications_v2 RENAME TO notifications")
    except Exception as e:
        logger.error(f"Error migrating notifications table: {e}")

    _safe_alter(conn, "ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    _safe_alter(conn, "ALTER TABLE attendance ADD COLUMN total_hours TEXT")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN shift TEXT DEFAULT '09:00 AM - 06:00 PM'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN weekoff TEXT DEFAULT 'Sunday'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN pl_quota INTEGER DEFAULT 18")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN security_question TEXT DEFAULT 'What is your favorite color?'")
    _safe_alter(conn, "ALTER TABLE users ADD COLUMN security_answer TEXT DEFAULT 'blue'")
    _safe_alter(conn, "ALTER TABLE leaves ADD COLUMN reviewed_by TEXT")
    _safe_alter(conn, "ALTER TABLE leaves ADD COLUMN reviewed_at TEXT")
    _safe_alter(conn, "ALTER TABLE leaves ADD COLUMN admin_remark TEXT")

    # Tables for multi-worker concurrency (PIN auth & brute force lockout)
    conn.execute('''CREATE TABLE IF NOT EXISTS login_pins (
        username   TEXT PRIMARY KEY,
        pin        TEXT NOT NULL,
        expires    TEXT NOT NULL,
        role       TEXT NOT NULL,
        user_id    INTEGER NOT NULL,
        department TEXT NOT NULL
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS login_lockouts (
        username     TEXT PRIMARY KEY,
        attempts     INTEGER NOT NULL DEFAULT 1,
        locked_until TEXT
    )''')

    # High performance query indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role_dept ON users(role, department)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_user_date ON attendance(user_id, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leaves_user_status ON leaves(user_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leaves_status ON leaves(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_logs(user_id, timestamp)")

    conn.commit()
    conn.close()


def _safe_alter(conn, sql):
    try:
        conn.execute(sql)
    except Exception:
        pass


# Initialize database & run migrations on load (works with both python app.py and gunicorn)
init_db()

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


def calculate_hours_worked(clock_in, clock_out):
    """Calculate formatted working hours between clock_in and clock_out (HH:MM:SS)"""
    if not clock_in or not clock_out:
        return None
    try:
        t_in = datetime.strptime(clock_in, '%H:%M:%S')
        t_out = datetime.strptime(clock_out, '%H:%M:%S')
        if t_out < t_in:
            diff = (t_out + timedelta(days=1)) - t_in
        else:
            diff = t_out - t_in
        total_seconds = int(diff.total_seconds())
        hours = total_seconds // 3600
        mins = (total_seconds % 3600) // 60
        return f"{hours}h {mins:02d}m"
    except Exception:
        return None


def get_user_leave_summary(user_id, conn=None):
    """Get leave balances: PL (Paid Leave), UL (Unpaid Leave), LWP (Leave Without Pay)"""
    close_needed = False
    if conn is None:
        conn = get_db_connection()
        close_needed = True

    user = conn.execute("SELECT pl_quota FROM users WHERE id = ?", (user_id,)).fetchone()
    pl_quota = user['pl_quota'] if user and user['pl_quota'] is not None else 18

    row = conn.execute('''
        SELECT 
            SUM(CASE WHEN status IN ('PL', 'Leave') THEN 1 ELSE 0 END) AS pl_used,
            SUM(CASE WHEN status = 'UL' THEN 1 ELSE 0 END) AS ul_used,
            SUM(CASE WHEN status = 'LWP' THEN 1 ELSE 0 END) AS lwp_used
        FROM attendance WHERE user_id = ?
    ''', (user_id,)).fetchone()

    pl_used = (row['pl_used'] or 0) if row else 0
    ul_used = (row['ul_used'] or 0) if row else 0
    lwp_used = (row['lwp_used'] or 0) if row else 0

    if close_needed:
        conn.close()

    return {
        'pl_quota': pl_quota,
        'pl_used': pl_used,
        'pl_balance': max(0, pl_quota - pl_used),
        'ul_used': ul_used,
        'lwp_used': lwp_used,
        'total_leaves_taken': pl_used + ul_used + lwp_used
    }


def get_today_stats(conn=None):
    today = today_str()
    close_needed = False
    if conn is None:
        conn = get_db_connection()
        close_needed = True

    total = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'Admin'").fetchone()[0]

    # Optimized single aggregation query for attendance stats
    row = conn.execute('''
        SELECT 
            SUM(CASE WHEN clock_in IS NOT NULL THEN 1 ELSE 0 END) AS present,
            SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent,
            SUM(CASE WHEN status = 'PL' THEN 1 ELSE 0 END) AS pl_count,
            SUM(CASE WHEN status = 'UL' THEN 1 ELSE 0 END) AS ul_count,
            SUM(CASE WHEN status = 'LWP' THEN 1 ELSE 0 END) AS lwp_count,
            SUM(CASE WHEN status IN ('Leave', 'PL', 'UL', 'LWP') THEN 1 ELSE 0 END) AS on_leave
        FROM attendance WHERE date = ?
    ''', (today,)).fetchone()

    present   = (row['present'] or 0) if row else 0
    absent    = (row['absent'] or 0) if row else 0
    pl_count  = (row['pl_count'] or 0) if row else 0
    ul_count  = (row['ul_count'] or 0) if row else 0
    lwp_count = (row['lwp_count'] or 0) if row else 0
    on_leave  = (row['on_leave'] or 0) if row else 0

    try:
        pending_leaves = conn.execute("SELECT COUNT(*) FROM leaves WHERE status='Pending'").fetchone()[0]
    except Exception:
        pending_leaves = 0

    if close_needed:
        conn.close()

    not_arrived = max(0, total - present - absent - on_leave)
    return {
        'total': total,
        'present': present,
        'absent': absent,
        'on_leave': on_leave,
        'pl_today': pl_count,
        'ul_today': ul_count,
        'lwp_today': lwp_count,
        'pending_leaves': pending_leaves,
        'not_arrived': not_arrived,
    }


# ---------------------------------------------------------------------------
# PIN Generation & Verification (NO EMAIL NEEDED!)
# ---------------------------------------------------------------------------
def generate_pin():
    """Generate 4-digit PIN"""
    return ''.join(random.choices(string.digits, k=4))


def log_audit(action, details=None, user_id=None, conn=None):
    """Log admin actions — uses provided connection or creates one safely"""
    try:
        ip_addr = request.remote_addr if request else 'system'
        close_needed = False
        if conn is None:
            conn = get_db_connection()
            close_needed = True
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, details, ip_addr, timestamp) VALUES (?,?,?,?,?)",
            (user_id, action, details, ip_addr, ist_now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        if close_needed:
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Audit log error: {e}")


# ---------------------------------------------------------------------------
# Brute-force protection & Multi-Worker PIN Storage (SQLite-backed)
# ---------------------------------------------------------------------------
def _check_lockout(username):
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT attempts, locked_until FROM login_lockouts WHERE username = ?", (username,)).fetchone()
        conn.close()
        if not row:
            return False, 0
        attempts, locked_until_str = row['attempts'], row['locked_until']
        if locked_until_str:
            locked_until = datetime.fromisoformat(locked_until_str)
            if ist_now() < locked_until:
                remaining = int((locked_until - ist_now()).total_seconds())
                return True, remaining
        return False, 0
    except Exception as e:
        logger.error(f"Lockout check error: {e}")
        return False, 0


def _record_failed_attempt(username):
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT attempts FROM login_lockouts WHERE username = ?", (username,)).fetchone()
        count = (row['attempts'] + 1) if row else 1
        locked_until_str = None
        if count >= MAX_ATTEMPTS:
            locked_until = ist_now() + timedelta(minutes=LOCKOUT_MINUTES)
            locked_until_str = locked_until.isoformat()
            logger.warning("Account locked: %s after %d failed attempts", username, count)
        conn.execute("INSERT OR REPLACE INTO login_lockouts (username, attempts, locked_until) VALUES (?, ?, ?)",
                     (username, count, locked_until_str))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Record failed attempt error: {e}")


def _clear_attempts(username):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM login_lockouts WHERE username = ?", (username,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Clear attempts error: {e}")


def _store_pin(username, pin, role, user_id, department):
    try:
        conn = get_db_connection()
        expires = (ist_now() + timedelta(minutes=PIN_EXPIRY_MINUTES)).isoformat()
        conn.execute("INSERT OR REPLACE INTO login_pins (username, pin, expires, role, user_id, department) VALUES (?, ?, ?, ?, ?, ?)",
                     (username, pin, expires, role, user_id, department))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Store pin error: {e}")


def _get_pin(username):
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM login_pins WHERE username = ?", (username,)).fetchone()
        conn.close()
        if not row:
            return None
        return {
            'pin': row['pin'],
            'expires': datetime.fromisoformat(row['expires']),
            'role': row['role'],
            'user_id': row['user_id'],
            'department': row['department']
        }
    except Exception as e:
        logger.error(f"Get pin error: {e}")
        return None


def _clear_pin(username):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM login_pins WHERE username = ?", (username,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Clear pin error: {e}")


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
    if request.endpoint in ('setup', 'static', 'api_status'):
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
            _store_pin(username, pin, expected_role, user['id'], user['department'])

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
            locked, secs = _check_lockout(username)
            if locked:
                flash(f"Account locked for {LOCKOUT_MINUTES} minutes.")
            else:
                conn = get_db_connection()
                row = conn.execute("SELECT attempts FROM login_lockouts WHERE username = ?", (username,)).fetchone()
                conn.close()
                count = row['attempts'] if row else 1
                remaining = max(0, MAX_ATTEMPTS - count)
                flash(f"Invalid credentials. {remaining} attempt(s) left.")

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

    pin_data = _get_pin(username)
    if not pin_data:
        flash("PIN expired. Please login again.")
        if 'pending_login' in session:
            del session['pending_login']
        return redirect(url_for('login'))

    # Check PIN expiry
    if ist_now() > pin_data['expires']:
        flash("PIN expired. Please login again.")
        _clear_pin(username)
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
    _clear_pin(username)
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
                log_audit('PASSWORD_RESET', f"Username: {username}", user['id'])
                flash("✅ Password reset successfully. You may now log in.")
                return redirect(url_for('login'))
            else:
                conn.close()
                flash("Incorrect security answer. Access denied.")
                return redirect(url_for('forgot'))

    return render_template('forgot.html', step='1')


# ---------------------------------------------------------------------------
# High-Performance HTTP Caching & Compression Middleware
# ---------------------------------------------------------------------------
@app.after_request
def optimize_response(response):
    # Aggressive browser caching for static assets (CSS, JS, images, icons)
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'

    # Gzip compression for text, json, html, css, js responses > 500 bytes
    accept_encoding = request.headers.get('Accept-Encoding', '').lower()
    if ('gzip' in accept_encoding and
        response.status_code == 200 and
        not response.direct_passthrough and
        'Content-Encoding' not in response.headers):
        mimetype = response.mimetype
        if mimetype in ('text/html', 'text/css', 'application/javascript', 'application/json'):
            data = response.get_data()
            if len(data) > 500:
                buf = io.BytesIO()
                with gzip.GzipFile(mode='wb', fileobj=buf, compresslevel=5) as gz:
                    gz.write(data)
                compressed = buf.getvalue()
                if len(compressed) < len(data):
                    response.set_data(compressed)
                    response.headers['Content-Encoding'] = 'gzip'
                    response.headers['Content-Length'] = len(compressed)
                    response.headers['Vary'] = 'Accept-Encoding'
    return response


# ---------------------------------------------------------------------------
# Product Workflow & Interactive Guide
# ---------------------------------------------------------------------------
@app.route('/workflow')
def workflow():
    """Product workflow, operational guide & architecture tour"""
    conn = get_db_connection()
    company_row = conn.execute("SELECT name FROM company LIMIT 1").fetchone()
    conn.close()
    company_name = company_row['name'] if company_row else 'StaffPortal'
    return render_template('workflow.html', company_name=company_name)


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

    user_id = session['user_id']
    today_day = ist_now().strftime('%A')
    today = today_str()
    conn = get_db_connection()
    user_data = conn.execute(
        'SELECT shift, weekoff, pl_quota FROM users WHERE id = ?', (user_id,)
    ).fetchone()

    today_record = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? AND date = ?', (user_id, today)
    ).fetchone()

    logs = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 14',
        (user_id,)
    ).fetchall()

    try:
        leave_requests = conn.execute(
            'SELECT * FROM leaves WHERE user_id = ? ORDER BY applied_at DESC LIMIT 20',
            (user_id,)
        ).fetchall()
    except Exception:
        leave_requests = []

    try:
        notifications = conn.execute(
            '''SELECT * FROM notifications 
               WHERE user_id = ? OR user_id IS NULL 
               ORDER BY created_at DESC LIMIT 20''',
            (user_id,)
        ).fetchall()
    except Exception:
        notifications = []

    leave_summary = get_user_leave_summary(user_id, conn=conn)
    conn.close()

    today_hours = None
    if today_record and today_record['clock_in']:
        if today_record['clock_out']:
            today_hours = today_record['total_hours'] or calculate_hours_worked(today_record['clock_in'], today_record['clock_out'])
        else:
            today_hours = calculate_hours_worked(today_record['clock_in'], ist_now().strftime('%H:%M:%S'))

    return render_template(
        'staff.html',
        today_record=today_record,
        today_hours=today_hours,
        logs=logs,
        leave_summary=leave_summary,
        leave_requests=leave_requests,
        notifications=notifications,
        today_day=today_day,
        user_shift=user_data['shift'] if user_data else '09:00 AM - 06:00 PM',
        user_weekoff=user_data['weekoff'] if user_data else 'Sunday',
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


@app.route('/staff/leave/apply', methods=['POST'])
def apply_leave():
    if 'user_id' not in session or session['role'] != 'Staff':
        return redirect(url_for('login'))

    user_id = session['user_id']
    leave_type = request.form.get('leave_type', '').strip().upper()
    start_date = request.form.get('start_date', '').strip()
    end_date = request.form.get('end_date', '').strip()
    reason = request.form.get('reason', '').strip()

    if leave_type not in ('PL', 'UL', 'LWP'):
        flash("Invalid leave type. Please select PL, UL, or LWP.")
        return redirect(url_for('staff_dashboard'))

    if not start_date or not end_date:
        flash("Both Start Date and End Date are required.")
        return redirect(url_for('staff_dashboard'))

    try:
        d_start = datetime.strptime(start_date, '%Y-%m-%d').date()
        d_end = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        flash("Invalid date format. Use YYYY-MM-DD.")
        return redirect(url_for('staff_dashboard'))

    if d_end < d_start:
        flash("End date cannot be earlier than start date.")
        return redirect(url_for('staff_dashboard'))

    days_requested = (d_end - d_start).days + 1

    if leave_type == 'PL':
        summary = get_user_leave_summary(user_id)
        if days_requested > summary['pl_balance']:
            flash(f"Insufficient PL balance! Requested: {days_requested} day(s), Available: {summary['pl_balance']}. Consider applying for UL or LWP.")
            return redirect(url_for('staff_dashboard'))

    conn = get_db_connection()
    conn.execute(
        '''INSERT INTO leaves (user_id, leave_type, start_date, end_date, days, reason, status, applied_at)
           VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?)''',
        (user_id, leave_type, start_date, end_date, days_requested, reason, ist_now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()

    log_audit('LEAVE_APPLIED', f"{leave_type}: {start_date} to {end_date} ({days_requested}d)", user_id)
    flash(f"✅ Leave application submitted for {days_requested} day(s) ({leave_type}).")
    return redirect(url_for('staff_dashboard'))


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
            tot_hrs = calculate_hours_worked(record['clock_in'], now_time)
            conn.execute(
                'UPDATE attendance SET clock_out = ?, total_hours = ? WHERE id = ?',
                (now_time, tot_hrs, record['id'])
            )
            log_audit('CLOCK_OUT', today, user_id)
            flash(f'👋 Clocked out at {now_time}. Work duration: {tot_hrs or ""}')
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
    today = today_str()
    conn  = get_db_connection()
    users = conn.execute(
        "SELECT id, username, department, shift, weekoff, pl_quota FROM users WHERE role != 'Admin' ORDER BY department, username"
    ).fetchall()

    try:
        pending_leaves = conn.execute('''
            SELECT l.*, u.username, u.department
            FROM leaves l
            JOIN users u ON l.user_id = u.id
            WHERE l.status = 'Pending'
            ORDER BY l.applied_at ASC
        ''').fetchall()
    except Exception:
        pending_leaves = []

    try:
        recent_leaves = conn.execute('''
            SELECT l.*, u.username, u.department
            FROM leaves l
            JOIN users u ON l.user_id = u.id
            WHERE l.status != 'Pending'
            ORDER BY l.reviewed_at DESC LIMIT 15
        ''').fetchall()
    except Exception:
        recent_leaves = []

    try:
        warnings = conn.execute('''
            SELECT n.*, u.username
            FROM notifications n
            LEFT JOIN users u ON n.user_id = u.id
            ORDER BY n.created_at DESC LIMIT 30
        ''').fetchall()
    except Exception:
        warnings = []

    attendance_records = conn.execute('''
        SELECT u.username, u.department, u.shift, u.weekoff,
               a.clock_in, a.clock_out, a.total_hours, a.status
        FROM users u
        LEFT JOIN attendance a ON u.id = a.user_id AND a.date = ?
        WHERE u.role != 'Admin'
        ORDER BY u.department, u.username
    ''', (today,)).fetchall()

    stats = get_today_stats(conn=conn)
    conn.close()

    return render_template(
        'admin.html',
        users=users,
        pending_leaves=pending_leaves,
        recent_leaves=recent_leaves,
        warnings=warnings,
        attendance_records=attendance_records,
        today_day=today_day,
        shift_options=SHIFT_OPTIONS,
        weekday_options=WEEKDAY_OPTIONS,
        departments=ALLOWED_DEPARTMENTS,
        stats=stats,
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
            pl_quota = request.form.get('pl_quota', '18').strip()
            pl_val   = int(pl_quota) if pl_quota.isdigit() else 18

            if not new_user or not new_pass or dept not in ALLOWED_DEPARTMENTS:
                flash("Invalid input.")
            elif weekoff not in WEEKDAY_OPTIONS:
                flash("Invalid week-off day.")
            else:
                try:
                    conn.execute(
                        "INSERT INTO users (username, password, department, role, shift, weekoff, pl_quota, security_question, security_answer) VALUES (?,?,?,?,?,?,?,?,?)",
                        (new_user, generate_password_hash(new_pass), dept, 'Staff',
                         '09:00 AM - 06:00 PM', weekoff, pl_val, 'Set by admin', 'yes')
                    )
                    log_audit('USER_CREATED', f"{new_user} ({dept})", session['user_id'], conn=conn)
                    flash(f"✅ Employee '{new_user}' created with {pl_val} PL quota.")
                except sqlite3.IntegrityError:
                    flash(f"Username '{new_user}' already exists.")

        elif action_type == 'delete_user':
            target = request.form.get('target_user', '').strip()
            user = conn.execute("SELECT id FROM users WHERE username=? AND role!='Admin'", (target,)).fetchone()
            if user:
                conn.execute("DELETE FROM users WHERE id = ?", (user['id'],))
                log_audit('USER_DELETED', target, session['user_id'], conn=conn)
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
                log_audit('PASSWORD_RESET_ADMIN', target, session['user_id'], conn=conn)
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
                log_audit('SHIFT_UPDATED', f"{target}: {new_shift}", session['user_id'], conn=conn)
                flash(f"✅ Schedule updated for '{target}'.")

        elif action_type == 'update_pl_quota':
            target = request.form.get('target_user', '').strip()
            pl_quota = request.form.get('pl_quota', '').strip()
            if pl_quota.isdigit():
                conn.execute("UPDATE users SET pl_quota=? WHERE username=?", (int(pl_quota), target))
                log_audit('PL_QUOTA_UPDATED', f"{target}: {pl_quota}", session['user_id'], conn=conn)
                flash(f"✅ Annual PL Quota for '{target}' set to {pl_quota} days.")
            else:
                flash("Invalid quota value.")

        elif action_type == 'mark_leave':
            target = request.form.get('target_user', '').strip()
            status = request.form.get('status', '')
            if status not in ('PL', 'UL', 'LWP', 'Present', 'Absent', 'Half-Day'):
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
                    log_audit('STATUS_MARKED', f"{target}: {status}", session['user_id'], conn=conn)
                    flash(f"✅ '{target}' marked as {status}.")
                else:
                    flash("User not found.")

        elif action_type == 'approve_leave':
            leave_id = request.form.get('leave_id')
            admin_remark = request.form.get('admin_remark', '').strip()
            leave = conn.execute("SELECT * FROM leaves WHERE id = ?", (leave_id,)).fetchone()
            if leave and leave['status'] == 'Pending':
                now_str = ist_now().strftime('%Y-%m-%d %H:%M:%S')
                conn.execute(
                    "UPDATE leaves SET status='Approved', reviewed_by=?, reviewed_at=?, admin_remark=? WHERE id=?",
                    (session['username'], now_str, admin_remark, leave_id)
                )
                try:
                    cur_dt = datetime.strptime(leave['start_date'], '%Y-%m-%d').date()
                    end_dt = datetime.strptime(leave['end_date'], '%Y-%m-%d').date()
                    while cur_dt <= end_dt:
                        dt_s = cur_dt.strftime('%Y-%m-%d')
                        rec = conn.execute("SELECT id FROM attendance WHERE user_id=? AND date=?", (leave['user_id'], dt_s)).fetchone()
                        if rec:
                            conn.execute("UPDATE attendance SET status=? WHERE id=?", (leave['leave_type'], rec['id']))
                        else:
                            conn.execute(
                                "INSERT INTO attendance (user_id, date, status) VALUES (?,?,?)",
                                (leave['user_id'], dt_s, leave['leave_type'])
                            )
                        cur_dt += timedelta(days=1)
                except Exception as e:
                    logger.error(f"Error marking attendance for leave: {e}")

                conn.execute(
                    "INSERT INTO notifications (user_id, type, title, message, created_at, created_by) VALUES (?,?,?,?,?,?)",
                    (leave['user_id'], 'notice', f"Leave Approved: {leave['leave_type']}",
                     f"Your {leave['leave_type']} request from {leave['start_date']} to {leave['end_date']} has been approved. {admin_remark}",
                     now_str, session['username'])
                )
                log_audit('LEAVE_APPROVED', f"Leave #{leave_id} ({leave['leave_type']})", session['user_id'], conn=conn)
                flash(f"✅ Leave #{leave_id} approved as {leave['leave_type']}.")
            else:
                flash("Leave record not found or already reviewed.")

        elif action_type == 'reject_leave':
            leave_id = request.form.get('leave_id')
            admin_remark = request.form.get('admin_remark', '').strip()
            leave = conn.execute("SELECT * FROM leaves WHERE id = ?", (leave_id,)).fetchone()
            if leave and leave['status'] == 'Pending':
                now_str = ist_now().strftime('%Y-%m-%d %H:%M:%S')
                conn.execute(
                    "UPDATE leaves SET status='Rejected', reviewed_by=?, reviewed_at=?, admin_remark=? WHERE id=?",
                    (session['username'], now_str, admin_remark, leave_id)
                )
                conn.execute(
                    "INSERT INTO notifications (user_id, type, title, message, created_at, created_by) VALUES (?,?,?,?,?,?)",
                    (leave['user_id'], 'warning', f"Leave Rejected: {leave['leave_type']}",
                     f"Your {leave['leave_type']} request ({leave['start_date']} to {leave['end_date']}) was rejected. Reason: {admin_remark or 'No remark'}",
                     now_str, session['username'])
                )
                log_audit('LEAVE_REJECTED', f"Leave #{leave_id}", session['user_id'], conn=conn)
                flash(f"Leave #{leave_id} rejected.")
            else:
                flash("Leave record not found or already reviewed.")

        elif action_type == 'issue_warning':
            target = request.form.get('target_user', '').strip()
            warn_title = request.form.get('warn_title', '').strip()
            warn_msg = request.form.get('warn_message', '').strip()
            warn_type = request.form.get('warn_type', 'warning')
            if not warn_title or not warn_msg:
                flash("Title and message are required.")
            else:
                now_str = ist_now().strftime('%Y-%m-%d %H:%M:%S')
                target_id = None
                if target != 'All':
                    u = conn.execute("SELECT id FROM users WHERE username=?", (target,)).fetchone()
                    if u:
                        target_id = u['id']
                    else:
                        flash("User not found.")
                        target = None
                if target:
                    conn.execute(
                        "INSERT INTO notifications (user_id, type, title, message, created_at, created_by) VALUES (?,?,?,?,?,?)",
                        (target_id, warn_type, warn_title, warn_msg, now_str, session['username'])
                    )
                    log_audit('WARNING_ISSUED', f"To {target}: {warn_title}", session['user_id'], conn=conn)
                    flash(f"✅ Warning/Notice issued to {target}.")

        elif action_type == 'delete_warning':
            warn_id = request.form.get('warn_id', '')
            if warn_id.isdigit():
                conn.execute("DELETE FROM notifications WHERE id=?", (int(warn_id),))
                log_audit('WARNING_DELETED', f"ID: {warn_id}", session['user_id'], conn=conn)
                flash("✅ Notice/Warning removed.")
            else:
                flash("Invalid ID.")

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
                log_audit('ANNOUNCEMENT_POSTED', title, session['user_id'], conn=conn)
                flash(f"✅ Announcement posted.")

        elif action_type == 'delete_announcement':
            ann_id = request.form.get('ann_id', '')
            if ann_id.isdigit():
                conn.execute("DELETE FROM announcements WHERE id=?", (int(ann_id),))
                log_audit('ANNOUNCEMENT_DELETED', f"ID: {ann_id}", session['user_id'], conn=conn)
                flash("✅ Announcement removed.")
            else:
                flash("Invalid ID.")

        elif action_type == 'update_company':
            new_name = request.form.get('company_name', '').strip()
            if not new_name:
                flash("Company name required.")
            else:
                conn.execute("UPDATE company SET name=? WHERE id=1", (new_name,))
                log_audit('COMPANY_UPDATED', new_name, session['user_id'], conn=conn)
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
# Bulk User Onboarding (Industry Standard CSV / Excel / Paste)
# ---------------------------------------------------------------------------
@app.route('/admin/users/bulk-upload', methods=['POST'])
def bulk_upload_users():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('login'))

    default_dept = request.form.get('default_department', 'IT')
    default_shift = request.form.get('default_shift', '09:30 AM - 06:30 PM')
    default_weekoff = request.form.get('default_weekoff', 'Sunday')
    try:
        default_pl = int(request.form.get('default_pl', 18) or 18)
    except Exception:
        default_pl = 18

    records = []
    file = request.files.get('file')
    bulk_text = request.form.get('bulk_text', '').strip()

    # 1. Process uploaded file (.csv, .xlsx, .xls)
    if file and file.filename:
        filename = file.filename.lower()
        try:
            if filename.endswith('.csv'):
                content = file.stream.read().decode('utf-8', errors='ignore')
                stream = io.StringIO(content)
                reader = csv.DictReader(stream)
                for row in reader:
                    records.append(row)
            elif filename.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file)
                df.columns = [str(c).strip().lower() for c in df.columns]
                for _, row in df.iterrows():
                    records.append(row.to_dict())
            else:
                flash("Unsupported file format! Please upload a .csv or .xlsx file.")
                return redirect(url_for('admin_dashboard'))
        except Exception as e:
            flash(f"Error reading file: {e}")
            return redirect(url_for('admin_dashboard'))

    # 2. Or process pasted text
    elif bulk_text:
        lines = [l.strip() for l in bulk_text.splitlines() if l.strip()]
        for line in lines:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 1 and parts[0]:
                records.append({
                    'username': parts[0],
                    'password': parts[1] if len(parts) > 1 else 'Staff@123',
                    'department': parts[2] if len(parts) > 2 else default_dept,
                    'shift': parts[3] if len(parts) > 3 else default_shift,
                    'weekoff': parts[4] if len(parts) > 4 else default_weekoff,
                    'pl_quota': parts[5] if len(parts) > 5 else default_pl,
                })
    else:
        flash("No file selected or text provided for bulk upload.")
        return redirect(url_for('admin_dashboard'))

    if not records:
        flash("No valid employee records found to import.")
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    existing_users = set(r[0].lower() for r in conn.execute("SELECT username FROM users").fetchall())

    added_count = 0
    skipped_duplicates = []
    to_insert = []

    def get_val(rec, keys, default=''):
        for k in keys:
            for rk in rec:
                if str(rk).strip().lower() == k.lower() and rec[rk] is not None:
                    val = str(rec[rk]).strip()
                    if val and val.lower() != 'nan':
                        return val
        return default

    for rec in records:
        u_name = get_val(rec, ['username', 'user', 'name', 'employee_name'])
        if not u_name:
            continue
        u_name = u_name.strip()
        if len(u_name) < 2:
            continue

        if u_name.lower() in existing_users:
            skipped_duplicates.append(u_name)
            continue

        u_pass = get_val(rec, ['password', 'pass'], 'Staff@123')
        if len(u_pass) < 4:
            u_pass = 'Staff@123'
        u_dept = get_val(rec, ['department', 'dept'], default_dept)
        if u_dept not in ALLOWED_DEPARTMENTS:
            u_dept = default_dept if default_dept in ALLOWED_DEPARTMENTS else ALLOWED_DEPARTMENTS[0]
        u_shift = get_val(rec, ['shift'], default_shift)
        if u_shift not in SHIFT_OPTIONS:
            u_shift = default_shift
        u_weekoff = get_val(rec, ['weekoff', 'week_off'], default_weekoff)
        if u_weekoff not in WEEKDAY_OPTIONS:
            u_weekoff = default_weekoff
        u_pl = get_val(rec, ['pl_quota', 'pl'], str(default_pl))
        try:
            pl_val = int(float(u_pl))
        except Exception:
            pl_val = default_pl

        hashed_pass = generate_password_hash(u_pass)
        to_insert.append((
            u_name, hashed_pass, u_dept, 'Staff', u_shift, u_weekoff, pl_val, 'Set by admin', 'yes'
        ))
        existing_users.add(u_name.lower())
        added_count += 1

    if to_insert:
        conn.executemany(
            '''INSERT INTO users (username, password, department, role, shift, weekoff, pl_quota, security_question, security_answer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            to_insert
        )
        conn.commit()
        log_audit('BULK_USERS_CREATED', f"Imported {added_count} staff employees", session['user_id'], conn=conn)

    conn.close()

    msg = f"✅ Bulk Import Complete: Successfully added {added_count} new employees!"
    if skipped_duplicates:
        dup_names = ', '.join(skipped_duplicates[:5]) + ('...' if len(skipped_duplicates) > 5 else '')
        msg += f" {len(skipped_duplicates)} skipped (already exist: {dup_names})."
    flash(msg, "success" if added_count > 0 else "warning")
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/users/template')
def download_user_template():
    """Download CSV template for bulk employee import"""
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('login'))

    csv_content = (
        "username,password,department,shift,weekoff,pl_quota\n"
        "rahul_kumar,Staff@123,IT,09:30 AM - 06:30 PM,Sunday,18\n"
        "priya_patel,Staff@123,MIS,10:30 AM - 07:30 PM,Sunday,18\n"
        "aman_sharma,Staff@123,QA,09:30 AM - 06:30 PM,Saturday,18\n"
        "pooja_verma,Staff@123,Management,09:30 AM - 06:30 PM,Sunday,18\n"
    )
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=employee_bulk_template.csv"}
    )


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
    total_staff = 0
    in_office   = 0
    try:
        conn = get_db_connection()
        total_staff = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'Admin'").fetchone()[0]
        in_office   = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE date=? AND clock_in IS NOT NULL AND clock_out IS NULL",
            (today,)
        ).fetchone()[0]
        conn.close()
    except Exception:
        pass
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
