#!/usr/bin/env python3
"""
Comprehensive End-to-End Test for:
1. REST API for Windows Softphone Client
2. Staff Leave Application (No leave type selector)
3. Admin Leave Classification & Approval (Admin assigns PL/UL/LWP)
4. Admin Staff Leave Tracker View
"""

import sqlite3
import requests
import json
import sys

BASE_URL = "http://127.0.0.1:5001"
DB_PATH = "/data/attendance_staging.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def run_tests():
    print("=" * 65)
    print("🚀 RUNNING END-TO-END VERIFICATION FOR SOFTPHONE & LEAVE SYSTEM")
    print("=" * 65)

    # 1. API Status Check
    print("\n[STEP 1] Testing /api/status...")
    r = requests.get(f"{BASE_URL}/api/status", timeout=5)
    assert r.status_code == 200, f"Status check failed: {r.status_code}"
    status_data = r.json()
    print("  ✅ /api/status response:", status_data)

    # Ensure a test staff member exists
    conn = get_db()
    staff_user = conn.execute("SELECT * FROM users WHERE role = 'Staff' AND (is_active = 1 OR is_active IS NULL)").fetchone()
    if not staff_user:
        from werkzeug.security import generate_password_hash
        conn.execute("INSERT INTO users (username, password, department, role, email, is_active) VALUES ('teststaff', ?, 'Engineering', 'Staff', 'ahmedfarman102@gmail.com', 1)", (generate_password_hash("staff@123"),))
        conn.commit()
        staff_user = conn.execute("SELECT * FROM users WHERE username = 'teststaff'").fetchone()
    staff_username = staff_user['username']
    conn.close()
    print(f"  Target staff user for test: '{staff_username}' (ID: {staff_user['id']})")

    # Ensure test staff has secret_code
    conn = get_db()
    staff_user = conn.execute("SELECT * FROM users WHERE id = ?", (staff_user['id'],)).fetchone()
    staff_secret = staff_user['secret_code']
    if not staff_secret:
        staff_secret = "654321"
        conn.execute("UPDATE users SET secret_code = ? WHERE id = ?", (staff_secret, staff_user['id']))
        conn.commit()
    conn.close()

    # 1.5 Test First-time Device Setup & Secret Number Verification
    print("\n[STEP 1.5] Testing First-Time Device Setup & Secret Number Verification...")
    r_bad_secret = requests.post(f"{BASE_URL}/api/auth/verify-secret", json={"username": staff_username, "secret_code": "000000"})
    assert r_bad_secret.status_code == 401, f"Expected 401 for wrong secret, got {r_bad_secret.status_code}"
    print("  ✅ Negative secret verification correctly rejected with 401")

    r_ok_secret = requests.post(f"{BASE_URL}/api/auth/verify-secret", json={"username": staff_username, "secret_code": staff_secret})
    assert r_ok_secret.status_code == 200, f"Expected 200 for valid secret, got {r_ok_secret.status_code}"
    print(f"  ✅ Device paired successfully with Admin Secret Code '{staff_secret}': {r_ok_secret.json().get('message')}")

    # 2. Test Softphone Login (Invalid credentials test)
    print("\n[STEP 2] Testing Softphone Login Negative Case...")
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": staff_username, "password": "wrongpassword"})
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    print("  ✅ Negative login correctly rejected with 401")

    # 3. Test Softphone Login (Valid credentials)
    print("\n[STEP 3] Testing Softphone Login (Valid)...")
    import subprocess
    test_pass = "TestPass123!"
    hashed_pass = subprocess.check_output([
        "docker", "exec", "attendance-app-staging", "python3", "-c",
        f"from werkzeug.security import generate_password_hash; print(generate_password_hash('{test_pass}'))"
    ]).decode().strip()

    conn = get_db()
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pass, staff_user['id']))
    conn.commit()
    conn.close()

    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": staff_username, "password": test_pass})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    login_res = r.json()
    assert login_res.get("status") == "otp_required", f"Expected otp_required, got: {login_res}"
    print(f"  ✅ OTP requested: {login_res.get('message')}")

    # 4. Retrieve OTP from login_pins table
    conn = get_db()
    pin_row = conn.execute("SELECT pin FROM login_pins WHERE username = ? COLLATE NOCASE", (staff_username,)).fetchone()
    assert pin_row, "PIN not found in login_pins!"
    active_pin = pin_row["pin"]
    conn.close()
    print(f"  ✅ Retrieved generated PIN: {active_pin}")

    # 5. Verify PIN and obtain Bearer token
    print("\n[STEP 4] Verifying PIN via /api/auth/verify-pin...")
    r = requests.post(f"{BASE_URL}/api/auth/verify-pin", json={"username": staff_username, "pin": active_pin, "device_info": "Test Softphone"})
    assert r.status_code == 200, f"Verify PIN failed: {r.status_code}: {r.text}"
    verify_res = r.json()
    assert verify_res.get("status") == "success", f"Expected success: {verify_res}"
    auth_token = verify_res.get("token")
    assert auth_token, "Token not returned!"
    print(f"  ✅ Authentication successful! Bearer Token received: {auth_token[:10]}...")

    headers = {"Authorization": f"Bearer {auth_token}"}

    # 6. Test GET /api/staff/status
    print("\n[STEP 5] Testing GET /api/staff/status with Bearer token...")
    r = requests.get(f"{BASE_URL}/api/staff/status", headers=headers)
    assert r.status_code == 200, f"Status failed: {r.status_code}: {r.text}"
    staff_status = r.json()
    print("  ✅ Staff Softphone Status received:")
    print("     User:", staff_status.get("user"))
    print("     Attendance:", staff_status.get("attendance"))

    # 7. Test Softphone Clock In
    print("\n[STEP 6] Testing Softphone Clock IN via /api/staff/clock...")
    # Clean today's attendance for clean test
    conn = get_db()
    from datetime import date
    today_s = date.today().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM attendance WHERE user_id = ? AND date = ?", (staff_user['id'], today_s))
    conn.commit()
    conn.close()

    r = requests.post(f"{BASE_URL}/api/staff/clock", headers=headers, json={"action": "in"})
    assert r.status_code == 200, f"Clock in failed: {r.status_code}: {r.text}"
    clock_res = r.json()
    assert clock_res.get("attendance", {}).get("is_clocked_in") == True
    print(f"  ✅ Clock IN successful: {clock_res.get('message')}")

    # 8. Test Softphone Clock Out
    print("\n[STEP 7] Testing Softphone Clock OUT via /api/staff/clock...")
    r = requests.post(f"{BASE_URL}/api/staff/clock", headers=headers, json={"action": "out"})
    assert r.status_code == 200, f"Clock out failed: {r.status_code}: {r.text}"
    clock_out_res = r.json()
    assert clock_out_res.get("attendance", {}).get("is_completed") == True
    print(f"  ✅ Clock OUT successful: {clock_out_res.get('message')}")

    # 9. Test Staff Apply Leave (WITHOUT leave type!)
    print("\n[STEP 8] Testing Staff Leave Application (No leave type parameter)...")
    leave_payload = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "reason": "Family event"
    }
    r = requests.post(f"{BASE_URL}/api/staff/leave/apply", headers=headers, json=leave_payload)
    assert r.status_code == 200, f"Apply leave failed: {r.status_code}: {r.text}"
    print("  ✅ Leave applied successfully:", r.json().get("message"))

    # Check leave record in DB: must be status='Pending' and leave_type='Pending'
    conn = get_db()
    leave_rec = conn.execute(
        "SELECT * FROM leaves WHERE user_id = ? AND start_date = '2026-10-01' ORDER BY id DESC LIMIT 1",
        (staff_user['id'],)
    ).fetchone()
    assert leave_rec, "Leave application not saved in DB!"
    assert leave_rec["status"] == "Pending", f"Expected Pending, got {leave_rec['status']}"
    assert leave_rec["leave_type"] == "Pending", f"Expected initial leave_type Pending, got {leave_rec['leave_type']}"
    leave_id = leave_rec["id"]
    conn.close()
    print(f"  ✅ Leave #{leave_id} correctly saved as Pending without staff selecting type!")

    # 10. Admin approves leave and ASSIGNS LEAVE TYPE ('PL')
    print("\n[STEP 9] Testing Admin Leave Approval & Classification (Admin assigns PL)...")
    # Perform admin action via web session
    web_session = requests.Session()
    # Log in as admin via web
    admin_login_res = web_session.post(f"{BASE_URL}/login", data={"login_type": "admin", "username": "admin", "password": "admin@123"}, allow_redirects=True)
    # Get OTP for admin
    conn = get_db()
    admin_pin = conn.execute("SELECT pin FROM login_pins WHERE username = 'admin' COLLATE NOCASE").fetchone()["pin"]
    conn.close()
    # Verify pin
    verify_web = web_session.post(f"{BASE_URL}/verify-pin", data={"username": "admin", "pin": admin_pin}, allow_redirects=True)
    assert verify_web.status_code == 200

    # Admin approves leave #{leave_id} and assigns type 'PL'
    action_res = web_session.post(f"{BASE_URL}/admin_action", data={
        "action_type": "approve_leave",
        "leave_id": str(leave_id),
        "leave_type": "PL",
        "admin_remark": "Approved as Paid Leave"
    }, allow_redirects=True)
    assert action_res.status_code == 200

    # Verify DB: leave record must be Approved with leave_type='PL'
    conn = get_db()
    updated_leave = conn.execute("SELECT * FROM leaves WHERE id = ?", (leave_id,)).fetchone()
    assert updated_leave["status"] == "Approved", f"Expected Approved, got {updated_leave['status']}"
    assert updated_leave["leave_type"] == "PL", f"Expected PL assigned by Admin, got {updated_leave['leave_type']}"
    
    # Verify attendance records generated for the leave dates with status 'PL'
    att_leave_1 = conn.execute("SELECT status FROM attendance WHERE user_id = ? AND date = '2026-10-01'", (staff_user['id'],)).fetchone()
    att_leave_2 = conn.execute("SELECT status FROM attendance WHERE user_id = ? AND date = '2026-10-02'", (staff_user['id'],)).fetchone()
    assert att_leave_1 and att_leave_1["status"] == "PL", f"Attendance day 1 status mismatch: {att_leave_1}"
    assert att_leave_2 and att_leave_2["status"] == "PL", f"Attendance day 2 status mismatch: {att_leave_2}"
    conn.close()
    print(f"  ✅ Admin successfully classified and approved Leave #{leave_id} as 'PL'!")
    print("  ✅ Attendance records for 2026-10-01 and 2026-10-02 correctly marked as 'PL'!")

    # 11. Check Admin Leave Tracker Table in Admin Portal HTML
    print("\n[STEP 10] Verifying Staff Leave Tracker on Admin Dashboard HTML...")
    admin_dash = web_session.get(f"{BASE_URL}/admin")
    assert "Staff Leave Tracker" in admin_dash.text, "Staff Leave Tracker heading missing from admin.html!"
    assert staff_username in admin_dash.text, "Staff member not listed in admin leave tracker!"
    print("  ✅ Staff Leave Tracker table successfully rendered on Admin Portal!")

    # 12. Check Staff Dashboard HTML: Verify Leave Tracker cards and Leave Type dropdown are REMOVED
    print("\n[STEP 11] Verifying Staff Portal HTML (No leave tracker, no leave type select)...")
    staff_session = requests.Session()
    staff_login_res = staff_session.post(f"{BASE_URL}/login", data={"login_type": "staff", "username": staff_username, "password": test_pass}, allow_redirects=True)
    conn = get_db()
    staff_pin = conn.execute("SELECT pin FROM login_pins WHERE username = ? COLLATE NOCASE", (staff_username,)).fetchone()["pin"]
    conn.close()
    staff_session.post(f"{BASE_URL}/verify-pin", data={"username": staff_username, "pin": staff_pin}, allow_redirects=True)
    staff_dash = staff_session.get(f"{BASE_URL}/staff")

    assert "stat-grid-leaves" not in staff_dash.text, "Leave tracker cards should NOT be on staff portal!"
    assert "name=\"leave_type\"" not in staff_dash.text, "Leave type select dropdown should NOT be on staff portal!"
    assert "Approved (PL)" in staff_dash.text, "Staff should see approved status with Admin's assigned leave type!"
    print("  ✅ Staff portal verified: Leave tracker cards and leave_type dropdown are completely removed!")
    print("  ✅ Staff sees approved status with Admin-assigned type 'Approved (PL)'!")

    # 13. Test Softphone Logout
    print("\n[STEP 12] Testing Softphone Logout via /api/auth/logout...")
    r = requests.post(f"{BASE_URL}/api/auth/logout", headers=headers)
    assert r.status_code == 200
    r_check = requests.get(f"{BASE_URL}/api/staff/status", headers=headers)
    assert r_check.status_code == 401, "Token should be invalid after logout!"
    print("  ✅ Token successfully invalidated on logout!")

    print("\n" + "=" * 65)
    print("🎉 ALL TESTS PASSED! SOFTPHONE & LEAVE SYSTEM FULLY VERIFIED!")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
