import os
import sqlite3
import pytz
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

app = Flask(__name__)
app.secret_key = 'super_secret_office_key'
DB_PATH = '/data/attendance.db' if os.path.exists('/data') else 'attendance.db'

ALLOWED_DEPARTMENTS = ['IT', 'MIS', 'QA', 'TL', 'Manager', 'Management']

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Core Tables
    conn.execute('''CREATE TABLE IF NOT EXISTS company (id INTEGER PRIMARY KEY, name TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, department TEXT NOT NULL, role TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT NOT NULL,
            clock_in TEXT, clock_out TEXT, FOREIGN KEY (user_id) REFERENCES users (id))''')
            
    # Safely upgrade existing tables
    try: conn.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    except: pass 
    try: conn.execute("ALTER TABLE users ADD COLUMN shift TEXT DEFAULT '09:00 AM - 06:00 PM'")
    except: pass 
    try: conn.execute("ALTER TABLE users ADD COLUMN weekoff TEXT DEFAULT 'Sunday'")
    except: pass 
    try: conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT DEFAULT 'What is your favorite color?'")
    except: pass 
    try: conn.execute("ALTER TABLE users ADD COLUMN security_answer TEXT DEFAULT 'blue'")
    except: pass 
    
    conn.commit()
    conn.close()

# Make the Company Name available to all HTML files automatically
@app.context_processor
def inject_company():
    company_name = "Enterprise"
    try:
        conn = get_db_connection()
        comp = conn.execute("SELECT name FROM company LIMIT 1").fetchone()
        if comp: company_name = comp['name']
        conn.close()
    except: pass
    return dict(company_name=company_name)

# THE GATEKEEPER: Force setup if no company exists
@app.before_request
def check_setup():
    allowed_routes = ['setup', 'static']
    if request.endpoint not in allowed_routes:
        try:
            conn = get_db_connection()
            comp = conn.execute("SELECT * FROM company").fetchone()
            conn.close()
            if not comp:
                return redirect(url_for('setup'))
        except: pass

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    conn = get_db_connection()
    comp = conn.execute("SELECT * FROM company").fetchone()
    if comp:
        conn.close()
        return redirect(url_for('login')) # Prevent re-setup

    if request.method == 'POST':
        company_name = request.form['company_name']
        admin_user = request.form['admin_username']
        admin_pass = generate_password_hash(request.form['admin_password'])
        sec_q = request.form['security_question']
        sec_a = request.form['security_answer'].strip().lower()

        conn.execute("INSERT INTO company (name) VALUES (?)", (company_name,))
        
        # Smart update: If admin already exists from older version, update them. Else, create.
        existing_admin = conn.execute("SELECT id FROM users WHERE role='Admin'").fetchone()
        if existing_admin:
            conn.execute("UPDATE users SET username=?, password=?, security_question=?, security_answer=? WHERE id=?",
                         (admin_user, admin_pass, sec_q, sec_a, existing_admin['id']))
        else:
            conn.execute("INSERT INTO users (username, password, department, role, shift, weekoff, security_question, security_answer) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (admin_user, admin_pass, 'Management', 'Admin', 'Flexible', 'Sunday', sec_q, sec_a))
        
        conn.commit()
        conn.close()
        flash("System Initialized! Welcome to your new portal.")
        return redirect(url_for('login'))
        
    conn.close()
    return render_template('setup.html')

@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    step = request.args.get('step', '1')
    if request.method == 'POST':
        conn = get_db_connection()
        if step == '1':
            username = request.form['username']
            user = conn.execute("SELECT security_question FROM users WHERE username=?", (username,)).fetchone()
            conn.close()
            if user:
                return render_template('forgot.html', step='2', username=username, question=user['security_question'])
            else:
                flash("Account not found.")
                return redirect(url_for('forgot'))
        elif step == '2':
            username = request.form['username']
            answer = request.form['security_answer'].strip().lower()
            new_pass = generate_password_hash(request.form['new_password'])
            
            user = conn.execute("SELECT security_answer FROM users WHERE username=?", (username,)).fetchone()
            if user and user['security_answer'] == answer:
                conn.execute("UPDATE users SET password=? WHERE username=?", (new_pass, username))
                conn.commit()
                conn.close()
                flash("Password successfully recovered. You may now log in.")
                return redirect(url_for('login'))
            else:
                conn.close()
                flash("Security answer is incorrect. Access Denied.")
                return redirect(url_for('forgot'))
                
    return render_template('forgot.html', step='1')

def get_todays_roster():
    ist_timezone = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist_timezone).strftime('%Y-%m-%d')
    conn = get_db_connection()
    query = '''
        SELECT u.username, u.department, u.shift, u.weekoff, a.clock_in, a.clock_out, a.status 
        FROM users u 
        LEFT JOIN attendance a ON u.id = a.user_id AND a.date = ?
        WHERE u.role != 'Admin'
        ORDER BY u.department, u.username
    '''
    roster = conn.execute(query, (today,)).fetchall()
    conn.close()
    return roster

@app.route('/')
def index():
    if 'user_id' not in session: return redirect(url_for('login'))
    if session['role'] == 'Admin': return redirect(url_for('admin_dashboard'))
    return redirect(url_for('staff_dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            if user['department'] not in ALLOWED_DEPARTMENTS and user['role'] != 'Admin':
                flash('Access Denied: Unauthorized department.')
                return redirect(url_for('login'))
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['department'] = user['department']
            session['role'] = user['role']
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials. Please try again.')
    return render_template('login.html')

@app.route('/clock', methods=['POST'])
def clock():
    if 'user_id' not in session: return redirect(url_for('login'))
    action = request.form['action']
    user_id = session['user_id']
    ist_timezone = pytz.timezone('Asia/Kolkata')
    local_time = datetime.now(ist_timezone)
    today = local_time.strftime('%Y-%m-%d')
    now_time = local_time.strftime('%H:%M:%S')
    
    conn = get_db_connection()
    record = conn.execute('SELECT * FROM attendance WHERE user_id = ? AND date = ?', (user_id, today)).fetchone()
    
    if action == 'in':
        if not record:
            conn.execute('INSERT INTO attendance (user_id, date, clock_in, status) VALUES (?, ?, ?, ?)', (user_id, today, now_time, 'Present'))
            flash('Clocked In successfully!')
        else:
            flash('You have already Clocked In today.')
    elif action == 'out':
        if record and not record['clock_out']:
            conn.execute('UPDATE attendance SET clock_out = ? WHERE id = ?', (now_time, record['id']))
            flash('Clocked Out successfully!')
        else:
            flash('You must Clock In first or have already Clocked Out.')
            
    conn.commit()
    conn.close()
    return redirect(url_for('staff_dashboard'))

@app.route('/staff')
def staff_dashboard():
    if 'user_id' not in session or session['role'] != 'Staff': return redirect(url_for('login'))
    ist_timezone = pytz.timezone('Asia/Kolkata')
    today_day = datetime.now(ist_timezone).strftime('%A')
    conn = get_db_connection()
    user_data = conn.execute('SELECT shift, weekoff FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    user_shift = user_data['shift'] if user_data else '09:00 AM - 06:00 PM'
    user_weekoff = user_data['weekoff'] if user_data else 'Sunday'
    logs = conn.execute('SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 10', (session['user_id'],)).fetchall()
    conn.close()
    return render_template('staff.html', logs=logs, roster=get_todays_roster(), today_day=today_day, user_shift=user_shift, user_weekoff=user_weekoff)

@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'Admin': return redirect(url_for('login'))
    ist_timezone = pytz.timezone('Asia/Kolkata')
    today_day = datetime.now(ist_timezone).strftime('%A')
    conn = get_db_connection()
    users = conn.execute("SELECT username, shift, weekoff FROM users WHERE role != 'Admin' ORDER BY username").fetchall()
    conn.close()
    return render_template('admin.html', users=users, roster=get_todays_roster(), today_day=today_day)

@app.route('/admin_action', methods=['POST'])
def admin_action():
    if 'user_id' not in session or session['role'] != 'Admin': return redirect(url_for('login'))
    action_type = request.form.get('action_type')
    conn = get_db_connection()
    
    if action_type == 'add_user':
        new_user = request.form['new_username']
        new_pass = generate_password_hash(request.form['new_password'])
        dept = request.form['department']
        weekoff = request.form.get('weekoff', 'Sunday')
        try:
            conn.execute("INSERT INTO users (username, password, department, role, shift, weekoff, security_question, security_answer) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                         (new_user, new_pass, dept, 'Staff', '09:00 AM - 06:00 PM', weekoff, 'Assigned by admin?', 'yes'))
            flash(f"User '{new_user}' created with {weekoff} off.")
        except sqlite3.IntegrityError:
            flash("Error: Username exists.")
            
    elif action_type == 'reset_password':
        target = request.form['target_user']
        new_pass = generate_password_hash(request.form['new_password'])
        conn.execute("UPDATE users SET password = ? WHERE username = ?", (new_pass, target))
        flash(f"Password reset for {target}.")
        
    elif action_type == 'change_shift':
        target = request.form['target_user']
        new_shift = request.form['new_shift']
        new_weekoff = request.form['new_weekoff']
        conn.execute("UPDATE users SET shift = ?, weekoff = ? WHERE username = ?", (new_shift, new_weekoff, target))
        flash(f"Schedule updated for {target}: Shift {new_shift}, Weekoff {new_weekoff}.")

    elif action_type == 'mark_leave':
        target = request.form['target_user']
        status = request.form['status']
        ist_timezone = pytz.timezone('Asia/Kolkata')
        today = datetime.now(ist_timezone).strftime('%Y-%m-%d')
        user = conn.execute('SELECT id FROM users WHERE username = ?', (target,)).fetchone()
        if user:
            record = conn.execute('SELECT id FROM attendance WHERE user_id = ? AND date = ?', (user['id'], today)).fetchone()
            if record: conn.execute('UPDATE attendance SET status = ? WHERE id = ?', (status, record['id']))
            else: conn.execute('INSERT INTO attendance (user_id, date, status) VALUES (?, ?, ?)', (user['id'], today, status))
            flash(f"{target} marked as {status} for today.")

    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/export/<string:report_type>')
def export_excel(report_type):
    if 'user_id' not in session or session['role'] != 'Admin': return redirect(url_for('login'))
    target_user = request.args.get('target_user', 'All')
    conn = get_db_connection()
    query = '''SELECT u.username, u.department, u.shift, u.weekoff, a.date, a.clock_in, a.clock_out, a.status 
               FROM attendance a JOIN users u ON a.user_id = u.id'''
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty: return redirect(url_for('admin_dashboard'))
    df['date'] = pd.to_datetime(df['date'])
    today = datetime.now()
    if report_type == 'weekly': df = df[df['date'] >= (today - timedelta(days=7))]
    elif report_type == 'monthly': df = df[df['date'] >= (today - timedelta(days=30))]
    if target_user != 'All': df = df[df['username'] == target_user]
            
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    output_path = f"/tmp/{report_type}_attendance_{target_user}.xlsx"
    df.to_excel(output_path, index=False)
    return send_file(output_path, as_attachment=True)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/status', methods=['GET'])
def api_status():
    ist_timezone = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist_timezone).strftime('%Y-%m-%d')
    server_time = datetime.now(ist_timezone).strftime('%H:%M:%S')
    conn = get_db_connection()
    total_staff = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'Admin'").fetchone()[0]
    in_office = conn.execute("SELECT COUNT(*) FROM attendance WHERE date = ? AND clock_in IS NOT NULL AND clock_out IS NULL", (today,)).fetchone()[0]
    conn.close()
    return jsonify({"system_status": "Healthy", "current_time": server_time, "metrics": {"total_registered_staff": total_staff, "active_in_office_now": in_office}})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
