import os
import sqlite3
import pytz
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-in-production-please')

DB_PATH = '/data/attendance.db' if os.path.exists('/data') else 'attendance.db'

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

# Brute-force protection: {username: [fail_count, lockout_until_datetime or None]}
_login_attempts = {}
MAX_ATTEMPTS    = 5
LOCKOUT_MINUTES = 15

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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

    # Announcements / Notice Board
    conn.execute('''CREATE TABLE IF NOT EXISTS announcements (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT    NOT NULL,
        body       TEXT    NOT NULL,
        priority   TEXT    DEFAULT 'normal',
        created_at TEXT    NOT NULL,
        created_by TEXT    NOT NULL,
        active     INTEGER DEFAULT 1
    )''')

    # Safe schema upgrades for existing deployments
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
    """Summary counts for admin dashboard header cards."""
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
# Brute-force protection
# ---------------------------------------------------------------------------
def _check_lockout(username):
    """Returns (is_locked, seconds_remaining)."""
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
# Before Request — force first-run setup
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
# Setup (first run)
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
        flash("System initialized! Welcome to your portal.")
        return redirect(url_for('login'))

    conn.close()
    return render_template('setup.html')


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # Lockout check
        locked, secs = _check_lockout(username)
        if locked:
            mins = secs // 60 + 1
            flash(f"Account locked after too many failed attempts. Try again in {mins} minute(s).")
            return render_template('login.html')

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            if user['role'] != 'Admin' and user['department'] not in ALLOWED_DEPARTMENTS:
                flash('Access denied: unauthorized department.')
                return redirect(url_for('login'))
            _clear_attempts(username)
            session['user_id']    = user['id']
            session['username']   = user['username']
            session['department'] = user['department']
            session['role']       = user['role']
            logger.info("Login OK: %s (%s)", user['username'], user['role'])
            return redirect(url_for('index'))
        else:
            _record_failed_attempt(username)
            count = _login_attempts.get(username, [0])[0]
            remaining = MAX_ATTEMPTS - count
            if remaining > 0:
                flash(f"Invalid username or password. {remaining} attempt(s) remaining.")
            else:
                flash(f"Account locked for {LOCKOUT_MINUTES} minutes due to too many failed attempts.")

    return render_template('login.html')


@app.route('/logout')
def logout():
    logger.info("Logout: %s", session.get('username', '?'))
    session.clear()
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
                "SELECT security_answer FROM users WHERE username = ?", (username,)
            ).fetchone()
            if user and user['security_answer'] == answer:
                conn.execute(
                    "UPDATE users SET password = ? WHERE username = ?",
                    (generate_password_hash(new_pass), username)
                )
                conn.commit()
                conn.close()
                _clear_attempts(username)
                flash("Password reset successfully. You may now log in.")
                return redirect(url_for('login'))
            else:
                conn.close()
                flash("Incorrect security answer. Access denied.")
                return redirect(url_for('forgot'))

    return render_template('forgot.html', step='1')


# ---------------------------------------------------------------------------
# Index
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
    """Full attendance history for logged-in staff member."""
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
            flash(f'Clocked in at {now_time} ✅')
        else:
            flash('You have already clocked in today.')
    elif action == 'out':
        if record and not record['clock_out']:
            conn.execute(
                'UPDATE attendance SET clock_out = ? WHERE id = ?', (now_time, record['id'])
            )
            flash(f'Clocked out at {now_time} 👋')
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
    """30-day attendance analytics page."""
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    conn = get_db_connection()

    # Last 30 days daily summary
    rows = conn.execute('''
        SELECT
            a.date,
            COUNT(DISTINCT a.user_id)                                        AS total_marked,
            SUM(CASE WHEN a.clock_in IS NOT NULL THEN 1 ELSE 0 END)          AS present,
            SUM(CASE WHEN a.status = 'Absent'    THEN 1 ELSE 0 END)          AS absent,
            SUM(CASE WHEN a.status = 'Leave'     THEN 1 ELSE 0 END)          AS on_leave
        FROM attendance a
        WHERE a.date >= date('now', '-30 days')
        GROUP BY a.date
        ORDER BY a.date DESC
    ''').fetchall()

    # Per-user summary (last 30 days)
    user_summary = conn.execute('''
        SELECT
            u.username,
            u.department,
            COUNT(a.id)                                                   AS total_days,
            SUM(CASE WHEN a.clock_in IS NOT NULL THEN 1 ELSE 0 END)       AS present_days,
            SUM(CASE WHEN a.status = 'Absent'    THEN 1 ELSE 0 END)       AS absent_days,
            SUM(CASE WHEN a.status = 'Leave'     THEN 1 ELSE 0 END)       AS leave_days
        FROM users u
        LEFT JOIN attendance a ON u.id = a.user_id AND a.date >= date('now', '-30 days')
        WHERE u.role != 'Admin'
        GROUP BY u.id
        ORDER BY absent_days DESC, u.username
    ''').fetchall()

    conn.close()
    return render_template('analytics.html', daily_rows=rows, user_summary=user_summary)


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
                flash("Invalid input: check username, password and department.")
            elif weekoff not in WEEKDAY_OPTIONS:
                flash("Invalid week-off day.")
            else:
                try:
                    conn.execute(
                        "INSERT INTO users (username, password, department, role, shift, weekoff, security_question, security_answer) VALUES (?,?,?,?,?,?,?,?)",
                        (new_user, generate_password_hash(new_pass), dept, 'Staff',
                         '09:00 AM - 06:00 PM', weekoff, 'Set by admin', 'yes')
                    )
                    flash(f"Employee '{new_user}' created ({dept}, {weekoff} off).")
                except sqlite3.IntegrityError:
                    flash(f"Username '{new_user}' already exists.")

        elif action_type == 'delete_user':
            target = request.form.get('target_user', '').strip()
            user = conn.execute("SELECT id FROM users WHERE username=? AND role!='Admin'", (target,)).fetchone()
            if user:
                conn.execute("DELETE FROM users WHERE id = ?", (user['id'],))
                flash(f"Employee '{target}' and all their records have been removed.")
            else:
                flash("User not found or cannot delete an admin.")

        elif action_type == 'reset_password':
            target   = request.form.get('target_user', '').strip()
            new_pass = request.form.get('new_password', '')
            if len(new_pass) < 6:
                flash("Password must be at least 6 characters.")
            else:
                conn.execute(
                    "UPDATE users SET password=? WHERE username=?",
                    (generate_password_hash(new_pass), target)
                )
                flash(f"Password reset for '{target}'.")

        elif action_type == 'change_shift':
            target      = request.form.get('target_user', '').strip()
            new_shift   = request.form.get('new_shift', '')
            new_weekoff = request.form.get('new_weekoff', '')
            if new_shift not in SHIFT_OPTIONS or new_weekoff not in WEEKDAY_OPTIONS:
                flash("Invalid shift or week-off value.")
            else:
                conn.execute(
                    "UPDATE users SET shift=?, weekoff=? WHERE username=?",
                    (new_shift, new_weekoff, target)
                )
                flash(f"Schedule updated for '{target}': {new_shift}, {new_weekoff} off.")

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
                    flash(f"'{target}' marked as {status} for today.")
                else:
                    flash("User not found.")

        elif action_type == 'post_announcement':
            title    = request.form.get('ann_title', '').strip()
            body     = request.form.get('ann_body', '').strip()
            priority = request.form.get('ann_priority', 'normal')
            if not title or not body:
                flash("Announcement title and message are required.")
            elif priority not in ('normal', 'high', 'urgent'):
                flash("Invalid priority.")
            else:
                conn.execute(
                    "INSERT INTO announcements (title, body, priority, created_at, created_by, active) VALUES (?,?,?,?,?,1)",
                    (title, body, priority, ist_now().strftime('%Y-%m-%d %H:%M:%S'), session['username'])
                )
                flash(f"Announcement '{title}' posted to all staff.")

        elif action_type == 'delete_announcement':
            ann_id = request.form.get('ann_id', '')
            if ann_id.isdigit():
                conn.execute("DELETE FROM announcements WHERE id=?", (int(ann_id),))
                flash("Announcement removed.")
            else:
                flash("Invalid announcement ID.")

        elif action_type == 'update_company':
            new_name = request.form.get('company_name', '').strip()
            if not new_name:
                flash("Company name cannot be empty.")
            else:
                conn.execute("UPDATE company SET name=? WHERE id=1", (new_name,))
                flash(f"Company name updated to '{new_name}'.")

        else:
            flash("Unknown action.")

        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error("Admin action error: %s", e)
        flash("An error occurred. Please try again.")
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
        flash("No attendance data to export.")
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
        flash("No records found for the selected filter.")
        return redirect(url_for('admin_dashboard'))

    df['date']  = df['date'].dt.strftime('%Y-%m-%d')
    safe_user   = target_user.replace(' ', '_')
    output_path = f"/tmp/{report_type}_attendance_{safe_user}.xlsx"
    df.to_excel(output_path, index=False)
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
            "active_in_office_now":   in_office,
        }
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    init_db()
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
