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
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            department TEXT NOT NULL,
            role TEXT NOT NULL
        )
    ''')
    # Create Attendance Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT NOT NULL,
            clock_in TEXT,
            clock_out TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Create a default admin if table is empty
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username='admin'")
    if not cursor.fetchone():
        hashed_pw = generate_password_hash('admin123')
        conn.execute("INSERT INTO users (username, password, department, role) VALUES (?, ?, ?, ?)",
                     ('admin', hashed_pw, 'IT', 'Admin'))
        # Create a sample support staff user
        hashed_user_pw = generate_password_hash('staff123')
        conn.execute("INSERT INTO users (username, password, department, role) VALUES (?, ?, ?, ?)",
                     ('staff_user', hashed_user_pw, 'QA', 'Staff'))
        conn.commit()
    conn.close()

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session['role'] == 'Admin':
        return redirect(url_for('admin_dashboard'))
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
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    action = request.form['action']
    user_id = session['user_id']
    
    # Get exact local time
    ist_timezone = pytz.timezone('Asia/Kolkata')
    local_time = datetime.now(ist_timezone)
    
    today = local_time.strftime('%Y-%m-%d')
    now_time = local_time.strftime('%H:%M:%S')
    
    conn = get_db_connection()
    record = conn.execute('SELECT * FROM attendance WHERE user_id = ? AND date = ?', (user_id, today)).fetchone()
    
    if action == 'in':
        if not record:
            conn.execute('INSERT INTO attendance (user_id, date, clock_in) VALUES (?, ?, ?)', (user_id, today, now_time))
            flash('Clocked In successfully!')
        else:
            flash('You have already Clocked In today.')
    elif action == 'out':
        if record and not record['clock_out']:
            conn.execute('UPDATE attendance SET clock_out = ? WHERE id = ?', (now_time, record['id']))
            flash('Clocked Out successfully!')
        elif record and record['clock_out']:
            flash('You have already Clocked Out today.')
        else:
            flash('You must Clock In first!')
            
    conn.commit()
    conn.close()
    return redirect(url_for('staff_dashboard'))

@app.route('/staff')
def staff_dashboard():
    if 'user_id' not in session or session['role'] != 'Staff':
        return redirect(url_for('login'))
    conn = get_db_connection()
    logs = conn.execute('SELECT * FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 10', (session['user_id'],)).fetchall()
    conn.close()
    return render_template('staff.html', logs=logs)

@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))
    return render_template('admin.html')

@app.route('/export/<string:report_type>')
def export_excel(report_type):
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    query = '''
        SELECT u.username, u.department, a.date, a.clock_in, a.clock_out 
        FROM attendance a 
        JOIN users u ON a.user_id = u.id
    '''
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        flash('No data available to export.')
        return redirect(url_for('admin_dashboard'))
        
    df['date'] = pd.to_datetime(df['date'])
    today = datetime.now()
    
    if report_type == 'weekly':
        start_date = today - timedelta(days=7)
        df = df[df['date'] >= start_date]
        filename = "weekly_attendance.xlsx"
    elif report_type == 'monthly':
        start_date = today - timedelta(days=30)
        df = df[df['date'] >= start_date]
        filename = "monthly_attendance.xlsx"
        
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    output_path = f"/tmp/{filename}"
    df.to_excel(output_path, index=False)
    
    return send_file(output_path, as_attachment=True)

@app.route('/add_user', methods=['POST'])
def add_user():
    if 'user_id' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    new_username = request.form['new_username']
    new_password = generate_password_hash(request.form['new_password'])
    department = request.form['department']

    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (username, password, department, role) VALUES (?, ?, ?, ?)",
                     (new_username, new_password, department, 'Staff'))
        conn.commit()
        flash(f"User '{new_username}' added successfully to {department}!")
    except sqlite3.IntegrityError:
        flash("Error: That username already exists.")
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
