@echo off
title Staff Portal Voice Assistant - Setup
color 0A

echo.
echo  ================================================
echo   Staff Portal Voice Assistant - Windows Setup
echo  ================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from python.org
    pause & exit /b 1
)
echo [OK] Python found

:: Install dependencies
echo.
echo Installing dependencies...
pip install SpeechRecognition pyttsx3 requests rich

:: PyAudio (needs special handling on Windows)
echo.
echo Installing PyAudio for microphone support...
pip install pyaudio
if errorlevel 1 (
    echo [WARN] PyAudio failed. Trying pipwin method...
    pip install pipwin
    pipwin install pyaudio
    if errorlevel 1 (
        echo [WARN] PyAudio install failed. Will run in text mode.
    )
)

echo.
echo  ================================================
echo   Setup Complete!
echo  ================================================
echo.
echo  TO RUN:
echo    python assistant.py                   (voice mode)
echo    python assistant.py --text            (text mode - no mic needed)
echo    python assistant.py --list-mics       (show microphones)
echo.
echo  TO CONFIGURE:
echo    Edit the CONFIG section in assistant.py
echo    Set SERVER_URL, USERNAME, PASSWORD
echo.
pause
