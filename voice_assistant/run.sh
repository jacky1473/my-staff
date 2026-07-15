#!/bin/bash
# Quick run script for Linux/Mac

export PORTAL_URL="http://192.168.1.100:5000"
export PORTAL_USERNAME="your_username"
export PORTAL_PASSWORD="your_password"

python3 assistant.py "$@"
