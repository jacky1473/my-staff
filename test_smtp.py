#!/usr/bin/env python3
"""
SMTP Diagnostic & Test Tool for Staff Attendance Portal.
Validates SMTP credentials and sends a live test verification email.

Usage:
    python3 test_smtp.py [recipient@example.com]
"""

import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Load .env file if available
def load_env():
    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'),
        '/data/.env',
        '.env'
    ]
    for p in env_paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            k, v = line.split('=', 1)
                            k, v = k.strip(), v.strip().strip("'\"")
                            if k and k not in os.environ:
                                os.environ[k] = v
                print(f"[INFO] Loaded environment from: {p}")
                return True
            except Exception as e:
                print(f"[WARN] Failed to read {p}: {e}")
    return False

load_env()

SMTP_HOST = os.environ.get('SMTP_HOST', '').strip()
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587').strip() or 587)
SMTP_USER = os.environ.get('SMTP_USER', '').strip()
SMTP_PASS = os.environ.get('SMTP_PASS', '').strip()
SMTP_FROM = os.environ.get('SMTP_FROM', '').strip() or SMTP_USER or 'noreply@attendance-portal.local'
SMTP_TLS  = os.environ.get('SMTP_TLS', 'true').strip().lower() in ('true', '1', 'yes')

target_email = sys.argv[1].strip() if len(sys.argv) > 1 else (os.environ.get('DEFAULT_TEST_EMAIL', 'ahmedfarman102@gmail.com').strip())

print("=======================================================")
print("📧 SMTP Configuration & Connection Diagnostic")
print("=======================================================")
print(f"Host:       {SMTP_HOST or '[NOT SET]'}")
print(f"Port:       {SMTP_PORT}")
print(f"User:       {SMTP_USER or '[NOT SET]'}")
print(f"Password:   {'*' * len(SMTP_PASS) if SMTP_PASS else '[NOT SET]'}")
print(f"From:       {SMTP_FROM}")
print(f"TLS:        {SMTP_TLS}")
print(f"Recipient:  {target_email}")
print("=======================================================\n")

if not SMTP_HOST or not SMTP_USER or not SMTP_PASS or "xxxx" in SMTP_PASS:
    print("❌ ERROR: SMTP credentials are not configured or still have placeholder values.")
    print("Please open '.env' file, enter your actual SMTP details, and run this script again.")
    sys.exit(1)

test_code = "7821"
subject = f"🔐 [TEST] Verification Code: {test_code} — Staff Attendance Portal"
body = f"""
Hello,

This is a live test email sent from your Staff Attendance Portal SMTP configuration.
Your test verification code is: {test_code}

If you received this email, your SMTP configuration is 100% active and working!
"""

msg = MIMEMultipart()
msg["Subject"] = subject
msg["From"] = SMTP_FROM
msg["To"] = target_email
msg.attach(MIMEText(body, "plain"))

try:
    print(f"[1/3] Connecting to {SMTP_HOST}:{SMTP_PORT}...")
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=12)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12)
        if SMTP_TLS:
            print("[2/3] Initiating STARTTLS encryption...")
            server.starttls()

    print(f"[3/3] Authenticating as '{SMTP_USER}'...")
    server.login(SMTP_USER, SMTP_PASS)

    print(f"Sending test email to '{target_email}'...")
    server.sendmail(SMTP_FROM, [target_email], msg.as_string())
    server.quit()

    print("\n=======================================================")
    print(f"✅ SUCCESS! Test email was successfully delivered to: {target_email}")
    print("Your app is now ready to send live OTPs to employees!")
    print("=======================================================\n")

except smtplib.SMTPAuthenticationError as auth_err:
    print(f"\n❌ AUTHENTICATION FAILED: {auth_err}")
    print("Tip: If using Gmail, make sure you created a 16-character 'App Password' rather than your normal Google login password.")
    sys.exit(1)
except Exception as exc:
    print(f"\n❌ SMTP ERROR: {exc}")
    sys.exit(1)
