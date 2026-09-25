@echo off
title MyStaff Softphone - Windows Build Tool
color 0b

echo ========================================================
echo       MyStaff Softphone - 1-Click Windows Executable Builder
echo ========================================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.9+ from https://www.python.org/
    pause
    exit /b 1
)

echo [1/3] Installing required packages...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo [2/3] Building standalone executable with PyInstaller...
pyinstaller --onefile --noconsole --name "MyStaff-Softphone" softphone.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed! Check the log messages above.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo [3/3] BUILD SUCCESSFUL!
echo ========================================================
echo Executable location: dist\MyStaff-Softphone.exe
echo You can copy this .exe to any Windows PC and launch it directly.
echo.
pause
