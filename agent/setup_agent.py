"""
StaffPortal Agent Config Generator
=====================================
Run this once to generate a customized agent.py
for each staff member with their credentials.

Usage:
  python setup_agent.py
"""
import os
import shutil

SERVER_URL = input("Enter server IP (e.g. http://192.168.1.100:5000): ").strip()
USERNAME   = input("Enter staff username: ").strip()
PASSWORD   = input("Enter staff password: ").strip()

# Read template
with open('agent.py', 'r') as f:
    content = f.read()

# Replace config values
content = content.replace('http://192.168.1.100:5000', SERVER_URL)
content = content.replace('"john"', f'"{USERNAME}"')
content = content.replace('"pass123"', f'"{PASSWORD}"')

# Write personalized agent
out_file = f'agent_{USERNAME}.py'
with open(out_file, 'w') as f:
    f.write(content)

# Copy installer
shutil.copy('install.bat', f'install_{USERNAME}.bat')

# Update install.bat to use personalized agent
with open(f'install_{USERNAME}.bat', 'r') as f:
    bat = f.read()
bat = bat.replace('agent.py', out_file)
with open(f'install_{USERNAME}.bat', 'w') as f:
    f.write(bat)

print(f"\n✅ Generated: {out_file}")
print(f"✅ Generated: install_{USERNAME}.bat")
print(f"\n📦 Give these 2 files to {USERNAME}'s PC and run install_{USERNAME}.bat")
