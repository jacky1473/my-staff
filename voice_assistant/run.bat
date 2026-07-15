@echo off
:: Quick run script for Windows
:: Edit the values below before running

set PORTAL_URL=http://192.168.1.100:5000
set PORTAL_USERNAME=your_username
set PORTAL_PASSWORD=your_password

python assistant.py %*
