@echo off
title StaffPortal Agent Uninstaller
color 0C

echo.
echo  ============================================
echo   StaffPortal Agent - Uninstaller
echo  ============================================
echo.

:: Kill running agent
taskkill /f /im pythonw.exe /fi "windowtitle eq agent*" >nul 2>&1
echo [OK] Stopped running agent

:: Remove from startup
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
del /f /q "%STARTUP%\StaffPortalAgent.vbs" >nul 2>&1
echo [OK] Removed from Windows Startup

:: Remove install directory
rmdir /s /q "%APPDATA%\StaffAgent" >nul 2>&1
echo [OK] Removed agent files

echo.
echo  ============================================
echo   Uninstall Complete! Agent removed.
echo  ============================================
echo.
pause
