@echo off
:: StaffPortal Agent Installer for Windows
:: Run this as Administrator on each staff PC

title StaffPortal Agent Installer
color 0A

echo.
echo  ============================================
echo   StaffPortal Agent - Windows Installer
echo  ============================================
echo.

:: Check Python installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Installing Python...
    echo Please install Python 3.8+ from https://python.org
    echo Make sure to check "Add Python to PATH" during install
    pause
    exit /b 1
)

echo [OK] Python found
python --version

:: Create install directory
set INSTALL_DIR=%APPDATA%\StaffAgent
echo.
echo [INFO] Installing to: %INSTALL_DIR%
mkdir "%INSTALL_DIR%" 2>nul

:: Copy agent files
copy /y "%~dp0agent.py" "%INSTALL_DIR%\agent.py" >nul
echo [OK] Agent files copied

:: Create VBS launcher (runs agent completely hidden - no window)
echo Set objShell = WScript.CreateObject("WScript.Shell") > "%INSTALL_DIR%\launch.vbs"
echo objShell.Run "pythonw.exe """ ^& "%INSTALL_DIR%\agent.py" ^& """", 0, False >> "%INSTALL_DIR%\launch.vbs"
echo [OK] Silent launcher created

:: Add to Windows Startup folder (auto-start with Windows)
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
copy /y "%INSTALL_DIR%\launch.vbs" "%STARTUP%\StaffPortalAgent.vbs" >nul
echo [OK] Added to Windows Startup

:: Start agent immediately
echo.
echo [INFO] Starting agent now...
start "" "%INSTALL_DIR%\launch.vbs"
timeout /t 2 /nobreak >nul

:: Verify running
tasklist /fi "imagename eq pythonw.exe" | find "pythonw.exe" >nul
if errorlevel 1 (
    echo [WARN] Agent may not have started yet, check log file
) else (
    echo [OK] Agent is running silently in background
)

echo.
echo  ============================================
echo   Installation Complete!
echo  ============================================
echo.
echo  Agent will now:
echo  - Run silently (no window visible)
echo  - Start automatically when Windows boots
echo  - Track activity and report to server
echo.
echo  Log file: %INSTALL_DIR%\agent.log
echo.
pause
