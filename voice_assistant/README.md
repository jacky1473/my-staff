# 🎙️ Staff Portal Voice Assistant

A terminal-based voice assistant for the Staff Portal.
**No external paid APIs required.** Works 100% offline for TTS.

## Features

| Feature | Technology |
|---------|-----------|
| Speech Recognition | Google Free API (via SpeechRecognition) |
| Text to Speech | pyttsx3 (100% offline, system TTS) |
| Portal Communication | REST API calls to Flask server |
| Terminal UI | Rich (colored, formatted output) |

## Quick Start

### Windows
```batch
1. Edit run.bat — set your server URL, username, password
2. Double-click setup.bat (install dependencies once)
3. Double-click run.bat (or: python assistant.py)
```

### Linux / Mac
```bash
1. Edit run.sh — set your server URL, username, password
2. bash setup.sh        # install dependencies once
3. bash run.sh          # run the assistant
```

## Voice Commands

| Say | Action |
|-----|--------|
| `clock in` | Clock yourself in |
| `clock out` | Clock yourself out |
| `status` | Server & attendance stats |
| `who is in office` | See active staff |
| `absent count` | See offline/absent staff |
| `how many present` | Total present count |
| `time` | Current IST time |
| `today` | Today's date |
| `announce [message]` | Post announcement (admin) |
| `help` | Show all commands |
| `exit` / `quit` | Close assistant |

## Run Modes

```bash
# Voice mode (default) — needs microphone
python assistant.py

# Text mode — type commands instead of speaking
python assistant.py --text

# List available microphones
python assistant.py --list-mics

# Override config via command line
python assistant.py --server http://192.168.1.100:5000 --username john --password pass123
```

## Configuration

Edit the `CONFIG` section at the top of `assistant.py`:

```python
SERVER_URL  = 'http://192.168.1.100:5000'  # Your Flask server IP
USERNAME    = 'your_username'               # Your portal username
PASSWORD    = 'your_password'               # Your portal password
WAKE_WORD   = 'hey portal'                  # Optional wake word
LANGUAGE    = 'en-IN'                       # Speech language
TTS_RATE    = 165                           # Speech speed (words/min)
```

Or use environment variables:
```bash
export PORTAL_URL=http://192.168.1.100:5000
export PORTAL_USERNAME=john
export PORTAL_PASSWORD=pass123
python assistant.py
```

## Troubleshooting

**No microphone / PyAudio error:**
```bash
# Falls back to text mode automatically, or force it:
python assistant.py --text
```

**Windows PyAudio install fails:**
```bash
pip install pipwin
pipwin install pyaudio
```

**Linux PyAudio error:**
```bash
sudo apt-get install portaudio19-dev
pip install pyaudio
```

**No TTS audio on Linux:**
```bash
sudo apt-get install espeak espeak-data
```

**Can't reach server:**
- Make sure Flask app is running: `python app.py`
- Check server IP in config
- Ensure port 5000 is not blocked by firewall

## Next Version Plans

- Wake word detection without internet (using Vosk/Porcupine)
- Offline speech recognition (no Google API needed)
- Attendance report via voice ("read today's report")
- WhatsApp integration
- Multi-language support (Hindi)
