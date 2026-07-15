#!/bin/bash
# Staff Portal Voice Assistant - Linux/Mac Setup

echo ""
echo "================================================"
echo " Staff Portal Voice Assistant - Linux/Mac Setup"
echo "================================================"
echo ""

# Check Python
python3 --version || { echo "Python3 not found"; exit 1; }

# System dependencies for PyAudio (Linux)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Installing system audio libraries..."
    sudo apt-get install -y portaudio19-dev python3-pyaudio 2>/dev/null || \
    sudo yum install -y portaudio-devel 2>/dev/null || \
    echo "Could not install portaudio via package manager"
fi

# Python packages
echo "Installing Python packages..."
pip3 install SpeechRecognition pyttsx3 requests rich PyAudio

# Linux TTS engine
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Installing espeak (TTS for Linux)..."
    sudo apt-get install -y espeak espeak-data 2>/dev/null || true
fi

echo ""
echo "================================================"
echo " Setup Complete!"
echo "================================================"
echo ""
echo " TO RUN:"
echo "   python3 assistant.py              (voice mode)"
echo "   python3 assistant.py --text       (text mode)"
echo "   python3 assistant.py --list-mics  (show mics)"
echo ""
echo " TO CONFIGURE:"
echo "   Edit CONFIG section in assistant.py"
echo "   Set SERVER_URL, USERNAME, PASSWORD"
echo ""
