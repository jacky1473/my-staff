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
        pass  # Column already exists — that's fine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ist_now():
    return datetime.now(pytz.timezone('Asia/Kolkata'))


def today_str():
    return ist_now().strftime('%Y-%m-%d')


def get_todays_roster():
    today = today_str()
    conn = get_db_connection()
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


def login_required(role=None):
    """Decorator-style guard — used inline in each route."""
    pass  # Routes check manually for clarity

# ---------------------------------------------------------------------------
# Context Processor
# ---------------------------------------------------------------------------
@app.context_processor
def inject_company():
    company_name = "Enterprise"
    try:
        conn = get_db_connection()
        comp = conn.execute("SELECT name FROM company LIMIT 1").fetchone()
        conn.close()
        if comp:
            company_name = comp['name']
    except Exception:
        pass
    return dict(company_name=company_name)

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
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            if user['role'] != 'Admin' and user['department'] not in ALLOWED_DEPARTMENTS:
                flash('Access denied: unauthorized department.')
                return redirect(url_for('login'))
            session['user_id']    = user['id']
            session['username']   = user['username']
            session['department'] = user['department']
            session['role']       = user['role']
            logger.info("Login: %s (%s)", user['username'], user['role'])
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.')

    return render_template('login.html')


@app.route('/logout')
def logout():
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
                flash("Password reset successfully. You may now log in.")
                return redirect(url_for('login'))
            else:
                conn.close()
                flash("Incorrect security answer. Access denied.")
                return redirect(url_for('forgot'))

    return render_template('forgot.html', step='1')

# ---------------------------------------------------------------------------
# Index redirect
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session['role'] == 'Admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('staff_dashboard'))

# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
@app.route('/staff')
def staff_dashboard():
    if 'user_id' not in session or session['role'] != 'Staff':
        return redirect(url_for('login'))

    today_day = ist_now().strftime('%A')
    conn = get_db_connection()
    user_data = conn.execute(
        'SELECT shift, weekoff FROM users WHERE id = ?', (session['user_id'],)
    ).fetchone()
    logs = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 14',
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
    )


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
# Admin
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
    conn.close()

    return render_template(
        'admin.html',
        users=users,
        roster=get_todays_roster(),
        today_day=today_day,
        shift_options=SHIFT_OPTIONS,
        weekday_options=WEEKDAY_OPTIONS,
        departments=ALLOWED_DEPARTMENTS,
    )


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
            user = conn.execute("SELECT id FROM users WHERE username = ? AND role != 'Admin'", (target,)).fetchone()
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
                    "UPDATE users SET password = ? WHERE username = ?",
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
                    "UPDATE users SET shift = ?, weekoff = ? WHERE username = ?",
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
                user  = conn.execute('SELECT id FROM users WHERE username = ?', (target,)).fetchone()
                if user:
                    record = conn.execute(
                        'SELECT id FROM attendance WHERE user_id = ? AND date = ?', (user['id'], today)
                    ).fetchone()
                    if record:
                        conn.execute('UPDATE attendance SET status = ? WHERE id = ?', (status, record['id']))
                    else:
                        conn.execute(
                            'INSERT INTO attendance (user_id, date, status) VALUES (?,?,?)',
                            (user['id'], today, status)
                        )
                    flash(f"'{target}' marked as {status} for today.")
                else:
                    flash("User not found.")
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
    df = pd.read_sql_query('''
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

    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
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
        "SELECT COUNT(*) FROM attendance WHERE date = ? AND clock_in IS NOT NULL AND clock_out IS NULL",
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
