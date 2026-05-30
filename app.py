import os
import sqlite3
import pytz
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

app = Flask(__name__)
app.secret_key = 'super_secret_office_key'
DB_PATH = '/data/attendance.db' if os.path.exists('/data') else 'attendance.db'

ALLOWED_DEPARTMENTS = ['IT', 'MIS', 'QA', 'TL', 'Manager']

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Create Users Table
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, department TEXT NOT NULL, role TEXT NOT NULL)''')
            
    # Create Attendance Table
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT NOT NULL,
            clock_in TEXT, clock_out TEXT, FOREIGN KEY (user_id) REFERENCES users (id))''')
            
    # Safely upgrade existing database with a status column
    try:
        conn.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    except sqlite3.OperationalError:
        pass # Column already exists
    
    # Create a default admin if table is empty
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username='admin'")
    if not cursor.fetchone():
        hashed_pw = generate_password_hash('admin123')
        conn.execute("INSERT INTO users (username, password, department, role) VALUES (?, ?, ?, ?)",
                     ('admin', hashed_pw, 'IT', 'Admin'))
    conn.commit()
    conn.close()

# Helper function to get today's live presence
def get_todays_roster():
    ist_timezone = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist_timezone).strftime('%Y-%m-%d')
    conn = get_db_connection()
    # Fetch all staff and their attendance for today (if any)
    query = '''
        SELECT u.username, u.department, a.clock_in, a.clock_out, a.status 
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
                flash('Access Denied: Your department is not authorized.')
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
            conn.execute('INSERT INTO attendance (user_id, date, clock_in, status) VALUES (?, ?, ?, ?)', 
                         (user_id, today, now_time, 'Present'))
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
    conn = get_db_connection()
    logs = conn.execute('SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 10', (session['user_id'],)).fetchall()
    conn.close()
    roster = get_todays_roster()
    return render_template('staff.html', logs=logs, roster=roster)

@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'Admin': return redirect(url_for('login'))
    conn = get_db_connection()
    # Get user list for the dropdowns
    users = conn.execute("SELECT username FROM users WHERE role != 'Admin' ORDER BY username").fetchall()
    conn.close()
    roster = get_todays_roster()
    return render_template('admin.html', users=users, roster=roster)

@app.route('/admin_action', methods=['POST'])
def admin_action():
    if 'user_id' not in session or session['role'] != 'Admin': return redirect(url_for('login'))
        
    action_type = request.form.get('action_type')
    conn = get_db_connection()
    
    # 1. Add New User
    if action_type == 'add_user':
        new_user = request.form['new_username']
        new_pass = generate_password_hash(request.form['new_password'])
        dept = request.form['department']
        try:
            conn.execute("INSERT INTO users (username, password, department, role) VALUES (?, ?, ?, ?)", (new_user, new_pass, dept, 'Staff'))
            flash(f"User '{new_user}' created.")
        except sqlite3.IntegrityError:
            flash("Error: Username exists.")
            
    # 2. Reset Password
    elif action_type == 'reset_password':
        target = request.form['target_user']
        new_pass = generate_password_hash(request.form['new_password'])
        conn.execute("UPDATE users SET password = ? WHERE username = ?", (new_pass, target))
        flash(f"Password reset for {target}.")
        
    # 3. Mark Leave or Absent
    elif action_type == 'mark_leave':
        target = request.form['target_user']
        status = request.form['status']
        ist_timezone = pytz.timezone('Asia/Kolkata')
        today = datetime.now(ist_timezone).strftime('%Y-%m-%d')
        
        user = conn.execute('SELECT id FROM users WHERE username = ?', (target,)).fetchone()
        if user:
            record = conn.execute('SELECT id FROM attendance WHERE user_id = ? AND date = ?', (user['id'], today)).fetchone()
            if record:
                conn.execute('UPDATE attendance SET status = ? WHERE id = ?', (status, record['id']))
            else:
                conn.execute('INSERT INTO attendance (user_id, date, status) VALUES (?, ?, ?)', (user['id'], today, status))
            flash(f"{target} marked as {status} for today.")

    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/export/<string:report_type>')
def export_excel(report_type):
    if 'user_id' not in session or session['role'] != 'Admin': return redirect(url_for('login'))
    conn = get_db_connection()
    query = '''SELECT u.username, u.department, a.date, a.clock_in, a.clock_out, a.status 
               FROM attendance a JOIN users u ON a.user_id = u.id'''
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        flash('No data available to export.')
        return redirect(url_for('admin_dashboard'))
        
    df['date'] = pd.to_datetime(df['date'])
    today = datetime.now()
    if report_type == 'weekly': df = df[df['date'] >= (today - timedelta(days=7))]
    elif report_type == 'monthly': df = df[df['date'] >= (today - timedelta(days=30))]
        
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    output_path = f"/tmp/{report_type}_attendance.xlsx"
    df.to_excel(output_path, index=False)
    return send_file(output_path, as_attachment=True)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)

