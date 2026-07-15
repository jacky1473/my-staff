"""
╔══════════════════════════════════════════════════════════════╗
║          STAFF PORTAL — TERMINAL VOICE ASSISTANT             ║
║          Pure Python | No External API | Offline TTS         ║
║          Speech Recognition via Google's free engine         ║
╚══════════════════════════════════════════════════════════════╝

HOW IT WORKS:
  - Listens to your voice via microphone
  - Recognizes speech using SpeechRecognition (free Google engine)
  - Text-to-Speech using pyttsx3 (100% offline, system TTS)
  - Talks to your Staff Portal Flask server via REST API
  - All processing done locally — no paid API needed

COMMANDS YOU CAN SAY:
  "clock in"           → Clock yourself in
  "clock out"          → Clock yourself out
  "my status"          → Check today's attendance
  "who is in office"   → See who's present today
  "absent count"       → How many are absent
  "present count"      → How many are present
  "announce [message]" → Post announcement (admin only)
  "help"               → List all commands
  "exit" / "quit"      → Exit assistant
"""

import os
import sys
import json
import time
import threading
import requests
import speech_recognition as sr
import pyttsx3
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

# ─────────────────────────────────────────────────────────────
# CONFIG — Edit these before running
# ─────────────────────────────────────────────────────────────
SERVER_URL  = os.environ.get('PORTAL_URL',      'http://192.168.1.100:5000')
USERNAME    = os.environ.get('PORTAL_USERNAME',  'your_username')
PASSWORD    = os.environ.get('PORTAL_PASSWORD',  'your_password')
WAKE_WORD   = os.environ.get('WAKE_WORD',        'hey portal')
MIC_INDEX   = None    # None = default mic. Set to int if you have multiple mics.
LANGUAGE    = 'en-IN' # Indian English. Use 'en-US' for American.
TTS_RATE    = 165     # Words per minute (150-200 is comfortable)
TTS_VOLUME  = 0.9     # 0.0 to 1.0
# ─────────────────────────────────────────────────────────────

console = Console()

# ─────────────────────────────────────────────────────────────
# TEXT TO SPEECH ENGINE
# ─────────────────────────────────────────────────────────────
class Speaker:
    def __init__(self):
        self.engine = None
        self.lock   = threading.Lock()
        self._init_engine()

    def _init_engine(self):
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty('rate',   TTS_RATE)
            self.engine.setProperty('volume', TTS_VOLUME)

            # Try to pick a clear voice
            voices = self.engine.getProperty('voices')
            for v in voices:
                if 'english' in v.name.lower() or 'en' in v.id.lower():
                    self.engine.setProperty('voice', v.id)
                    break

            console.print("[green]✅ Text-to-Speech engine ready[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️  TTS init warning: {e}[/yellow]")
            self.engine = None

    def say(self, text, display=True):
        """Speak text aloud and optionally print it"""
        if display:
            console.print(f"\n[cyan]🔊 Assistant:[/cyan] {text}")

        if self.engine:
            try:
                with self.lock:
                    self.engine.say(text)
                    self.engine.runAndWait()
            except Exception as e:
                console.print(f"[yellow]TTS error: {e}[/yellow]")
        else:
            # Fallback: print only (no audio)
            pass

    def stop(self):
        if self.engine:
            try:
                self.engine.stop()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# SPEECH RECOGNIZER
# ─────────────────────────────────────────────────────────────
class Listener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold    = 0.8   # seconds of silence = end of phrase
        self.recognizer.phrase_threshold   = 0.3
        self.recognizer.non_speaking_duration = 0.5
        self.mic = None
        self._init_mic()

    def _init_mic(self):
        try:
            if MIC_INDEX is not None:
                self.mic = sr.Microphone(device_index=MIC_INDEX)
            else:
                self.mic = sr.Microphone()
            # Calibrate for ambient noise
            console.print("[dim]Calibrating microphone...[/dim]")
            with self.mic as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1.5)
            console.print("[green]✅ Microphone ready[/green]")
        except Exception as e:
            console.print(f"[red]❌ Microphone error: {e}[/red]")
            console.print("[yellow]Tip: Install PyAudio — pip install pyaudio[/yellow]")
            self.mic = None

    def listen(self, timeout=6, phrase_limit=8):
        """Listen for a voice command. Returns text or None."""
        if not self.mic:
            return None

        try:
            with self.mic as source:
                console.print("[dim]🎙  Listening...[/dim]", end='\r')
                audio = self.recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_limit
                )

            # Use Google's FREE speech recognition (no API key needed)
            text = self.recognizer.recognize_google(audio, language=LANGUAGE)
            return text.lower().strip()

        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            console.print(f"[yellow]Recognition service error: {e}[/yellow]")
            return None
        except Exception as e:
            console.print(f"[yellow]Listen error: {e}[/yellow]")
            return None

    def list_microphones(self):
        """Print all available microphones"""
        console.print("\n[bold]Available Microphones:[/bold]")
        for i, name in enumerate(sr.Microphone.list_microphone_names()):
            console.print(f"  [{i}] {name}")


# ─────────────────────────────────────────────────────────────
# PORTAL API CLIENT
# ─────────────────────────────────────────────────────────────
class PortalClient:
    def __init__(self):
        self.session     = requests.Session()
        self.session.headers.update({'User-Agent': 'StaffPortalVoiceAssistant/1.0'})
        self.logged_in   = False
        self.role        = None

    def login(self):
        """Login to staff portal"""
        try:
            # Step 1: POST credentials
            role_type = 'admin' if self._check_if_admin() else 'staff'
            r = self.session.post(
                f"{SERVER_URL}/api/agent-login",
                json={'username': USERNAME, 'password': PASSWORD},
                timeout=8
            )
            if r.status_code == 200 and r.json().get('ok'):
                self.logged_in = True
                self.role      = r.json().get('role', 'Staff')
                console.print(f"[green]✅ Logged in as {USERNAME} ({self.role})[/green]")
                return True
            else:
                console.print(f"[red]Login failed: {r.json().get('error','Unknown error')}[/red]")
                return False
        except Exception as e:
            console.print(f"[red]Cannot reach server: {e}[/red]")
            return False

    def _check_if_admin(self):
        """Quick check — will be determined after login"""
        return False

    def clock_in(self):
        try:
            r = self.session.post(
                f"{SERVER_URL}/clock",
                data={'action': 'in'},
                allow_redirects=True,
                timeout=8
            )
            return r.status_code in (200, 302)
        except Exception:
            return False

    def clock_out(self):
        try:
            r = self.session.post(
                f"{SERVER_URL}/clock",
                data={'action': 'out'},
                allow_redirects=True,
                timeout=8
            )
            return r.status_code in (200, 302)
        except Exception:
            return False

    def get_status(self):
        """Get current attendance status"""
        try:
            r = self.session.get(f"{SERVER_URL}/api/status", timeout=8)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def get_activity(self):
        """Get live activity data (admin)"""
        try:
            r = self.session.get(f"{SERVER_URL}/api/activity", timeout=8)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def post_announcement(self, title, body, priority='normal'):
        """Post announcement (admin)"""
        try:
            r = self.session.post(
                f"{SERVER_URL}/admin_action",
                data={
                    'action_type':  'post_announcement',
                    'ann_title':    title,
                    'ann_body':     body,
                    'ann_priority': priority,
                },
                allow_redirects=True,
                timeout=8
            )
            return r.status_code in (200, 302)
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────
# COMMAND PROCESSOR
# ─────────────────────────────────────────────────────────────
class CommandProcessor:
    def __init__(self, speaker: Speaker, client: PortalClient):
        self.speaker = speaker
        self.client  = client

    def process(self, text: str) -> bool:
        """
        Process a voice command.
        Returns False to exit, True to continue.
        """
        text = text.lower().strip()
        console.print(f"\n[bold yellow]🎤 You said:[/bold yellow] {text}")

        # ── EXIT ──────────────────────────────────────────────
        if any(w in text for w in ['exit', 'quit', 'goodbye', 'bye', 'stop']):
            self.speaker.say("Goodbye! Have a great day.")
            return False

        # ── HELP ──────────────────────────────────────────────
        elif any(w in text for w in ['help', 'commands', 'what can you do']):
            self._show_help()

        # ── CLOCK IN ──────────────────────────────────────────
        elif any(p in text for p in ['clock in', 'check in', 'mark in', 'clocked in', 'i am in', "i'm in", 'start shift']):
            self.speaker.say("Clocking you in now...")
            if self.client.clock_in():
                now = datetime.now().strftime('%I:%M %p')
                self.speaker.say(f"Done! You are clocked in at {now}.")
            else:
                self.speaker.say("Sorry, I could not clock you in. You may already be clocked in, or there is a server issue.")

        # ── CLOCK OUT ─────────────────────────────────────────
        elif any(p in text for p in ['clock out', 'check out', 'mark out', 'i am out', "i'm out", 'end shift', 'leaving']):
            self.speaker.say("Clocking you out now...")
            if self.client.clock_out():
                now = datetime.now().strftime('%I:%M %p')
                self.speaker.say(f"Done! You are clocked out at {now}. See you tomorrow!")
            else:
                self.speaker.say("Sorry, I could not clock you out. Please make sure you are clocked in first.")

        # ── SERVER / SYSTEM STATUS ────────────────────────────
        elif any(p in text for p in ['status', 'server status', 'system status', 'how many', 'how is it']):
            self.speaker.say("Checking server status...")
            data = self.client.get_status()
            if data:
                metrics = data.get('metrics', {})
                total   = metrics.get('total_registered_staff', 0)
                in_off  = metrics.get('active_in_office_now', 0)
                t       = data.get('current_time_ist', 'unknown')
                self.speaker.say(
                    f"Server is healthy. Current time is {t}. "
                    f"There are {total} registered staff members. "
                    f"{in_off} are currently in office."
                )
            else:
                self.speaker.say("Could not reach the server. Please check your network connection.")

        # ── WHO IS IN OFFICE ──────────────────────────────────
        elif any(p in text for p in ['who is in', 'who is present', 'who came', 'present today', 'in office']):
            self.speaker.say("Checking who is in office...")
            data = self.client.get_activity()
            if data:
                active = [s['username'] for s in data.get('staff', []) if s['status'] == 'active']
                idle   = [s['username'] for s in data.get('staff', []) if s['status'] == 'idle']
                if active:
                    names = ', '.join(active[:5])
                    more  = f" and {len(active)-5} more" if len(active) > 5 else ""
                    self.speaker.say(f"{len(active)} staff active right now: {names}{more}.")
                elif idle:
                    self.speaker.say(f"No one is actively working. {len(idle)} staff are idle.")
                else:
                    self.speaker.say("No staff are currently active in the portal.")
                self._show_activity_table(data)
            else:
                self.speaker.say("Could not fetch activity data. Admin access may be required.")

        # ── ABSENT COUNT ──────────────────────────────────────
        elif any(p in text for p in ['absent', 'who is absent', 'not in office', 'missing']):
            self.speaker.say("Checking absent staff...")
            data = self.client.get_activity()
            if data:
                offline = [s for s in data.get('staff', []) if s['status'] == 'offline']
                summary = data.get('summary', {})
                count   = summary.get('offline', len(offline))
                if count == 0:
                    self.speaker.say("Great news! Everyone is present today.")
                elif count == 1:
                    self.speaker.say(f"1 staff member is offline or absent today.")
                else:
                    self.speaker.say(f"{count} staff members are offline or absent today.")
            else:
                self.speaker.say("Could not fetch data right now.")

        # ── PRESENT COUNT ─────────────────────────────────────
        elif any(p in text for p in ['how many present', 'total present', 'attendance count', 'count']):
            data = self.client.get_status()
            if data:
                m = data.get('metrics', {})
                self.speaker.say(
                    f"Out of {m.get('total_registered_staff', 0)} staff, "
                    f"{m.get('active_in_office_now', 0)} are currently in office."
                )
            else:
                self.speaker.say("Could not fetch attendance data.")

        # ── TIME ──────────────────────────────────────────────
        elif any(p in text for p in ['time', 'what time', 'current time']):
            now = datetime.now().strftime('%I:%M %p')
            self.speaker.say(f"The current time is {now} Indian Standard Time.")

        # ── DATE ──────────────────────────────────────────────
        elif any(p in text for p in ['date', 'today', 'what day']):
            today = datetime.now().strftime('%A, %d %B %Y')
            self.speaker.say(f"Today is {today}.")

        # ── ANNOUNCE ──────────────────────────────────────────
        elif text.startswith('announce') or 'announcement' in text or 'notify staff' in text:
            # Extract message after "announce"
            msg = text.replace('announce', '').replace('announcement', '').strip()
            if not msg:
                self.speaker.say("What would you like to announce? Please say the message after the word announce.")
            else:
                self.speaker.say(f"Posting announcement: {msg}")
                if self.client.post_announcement("Voice Announcement", msg):
                    self.speaker.say("Announcement posted successfully to all staff.")
                else:
                    self.speaker.say("Failed to post announcement. You may need admin access.")

        # ── GREETING ──────────────────────────────────────────
        elif any(p in text for p in ['hello', 'hi', 'hey', 'good morning', 'good afternoon']):
            hour = datetime.now().hour
            greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
            self.speaker.say(f"{greeting}, {USERNAME}! How can I help you today?")

        # ── UNKNOWN ───────────────────────────────────────────
        else:
            self.speaker.say(
                f"I didn't understand that. You said: {text}. "
                "Try saying help to see available commands."
            )

        return True

    def _show_help(self):
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("Say This",   style="yellow", min_width=22)
        table.add_column("What It Does")

        commands = [
            ("clock in",          "Mark yourself as clocked in"),
            ("clock out",         "Mark yourself as clocked out"),
            ("status",            "Check server & attendance stats"),
            ("who is in office",  "See who's active right now"),
            ("absent count",      "See who's not in"),
            ("how many present",  "Total present count"),
            ("time",              "Current IST time"),
            ("today",             "Today's date"),
            ("announce [msg]",    "Post announcement to all staff"),
            ("help",              "Show this list"),
            ("exit / quit",       "Close the assistant"),
        ]
        for cmd, desc in commands:
            table.add_row(cmd, desc)

        console.print(Panel(table, title="[bold]Voice Commands[/bold]", border_style="cyan"))
        self.speaker.say(
            "You can say: clock in, clock out, status, who is in office, "
            "absent count, time, today, announce, help, or exit."
        )

    def _show_activity_table(self, data):
        if not data or not data.get('staff'):
            return
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Name",   style="bold")
        table.add_column("Dept")
        table.add_column("Status")
        table.add_column("Last Seen")

        status_colors = {'active': 'green', 'idle': 'yellow', 'offline': 'dim'}
        for s in data['staff'][:10]:  # Show max 10
            color  = status_colors.get(s['status'], 'white')
            table.add_row(
                s['username'],
                s.get('department', '—'),
                f"[{color}]{s['status'].upper()}[/{color}]",
                s.get('last_seen', '—'),
            )
        console.print(table)


# ─────────────────────────────────────────────────────────────
# MAIN ASSISTANT
# ─────────────────────────────────────────────────────────────
class VoiceAssistant:
    def __init__(self):
        self.speaker   = Speaker()
        self.listener  = Listener()
        self.client    = PortalClient()
        self.processor = CommandProcessor(self.speaker, self.client)
        self.running   = False

    def start(self):
        """Start the voice assistant"""
        self._print_banner()

        # Login to portal
        self.speaker.say("Connecting to Staff Portal...")
        if not self.client.login():
            self.speaker.say("Could not login. Please check your server URL and credentials in the config section.")
            sys.exit(1)

        self.speaker.say(f"Hello {USERNAME}! I am your Staff Portal Voice Assistant. Say {WAKE_WORD} followed by a command, or just speak a command directly. Say help to see all commands.")

        self.running = True
        self._main_loop()

    def _main_loop(self):
        """Main voice command loop"""
        console.print(f"\n[bold green]🎙  Listening for commands... (say 'exit' to quit)[/bold green]")
        console.print(f"[dim]Wake word: '{WAKE_WORD}' (optional)[/dim]\n")

        while self.running:
            try:
                text = self.listener.listen(timeout=8, phrase_limit=10)

                if text is None:
                    continue  # No speech detected, keep listening

                # Strip wake word if present
                if WAKE_WORD in text:
                    text = text.replace(WAKE_WORD, '').strip()
                    if not text:
                        self.speaker.say("Yes? How can I help?")
                        continue

                # Process command
                if text:
                    should_continue = self.processor.process(text)
                    if not should_continue:
                        self.running = False

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted by user.[/yellow]")
                self.running = False
            except Exception as e:
                console.print(f"[red]Error in main loop: {e}[/red]")
                time.sleep(1)

        self.speaker.say("Shutting down. Goodbye!")
        console.print("[bold]Voice Assistant stopped.[/bold]")

    def text_mode(self):
        """
        Fallback text-input mode (if no microphone available)
        Type commands instead of speaking them
        """
        self._print_banner()
        console.print("[yellow]⚠️  Running in TEXT MODE (no microphone)[/yellow]")
        console.print("[dim]Type commands below. Press Enter to send.[/dim]\n")

        self.speaker.say("Connecting to Staff Portal...")
        if not self.client.login():
            console.print("[red]Login failed. Check config.[/red]")
            sys.exit(1)

        self.speaker.say(f"Hello {USERNAME}! Text mode active. Type help to see commands.")

        while True:
            try:
                text = input("\n[You]: ").strip().lower()
                if not text:
                    continue
                should_continue = self.processor.process(text)
                if not should_continue:
                    break
            except KeyboardInterrupt:
                console.print("\n[yellow]Goodbye![/yellow]")
                break
            except EOFError:
                break

    def _print_banner(self):
        banner = Text()
        banner.append("STAFF PORTAL", style="bold white")
        banner.append("  •  ", style="dim")
        banner.append("VOICE ASSISTANT", style="bold cyan")
        banner.append("  •  ", style="dim")
        banner.append("v1.0", style="dim")

        console.print(Panel(
            banner,
            subtitle=f"[dim]Server: {SERVER_URL}  |  User: {USERNAME}[/dim]",
            border_style="cyan",
            padding=(0, 2),
        ))
        console.print(f"[dim]Wake word: '{WAKE_WORD}'  |  Language: {LANGUAGE}[/dim]\n")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Staff Portal Voice Assistant')
    parser.add_argument('--text',       action='store_true',  help='Use text input instead of microphone')
    parser.add_argument('--list-mics',  action='store_true',  help='List available microphones and exit')
    parser.add_argument('--server',     type=str,             help='Override server URL')
    parser.add_argument('--username',   type=str,             help='Override username')
    parser.add_argument('--password',   type=str,             help='Override password')
    args = parser.parse_args()

    # Override config from CLI args
    global SERVER_URL, USERNAME, PASSWORD
    if args.server:   SERVER_URL = args.server
    if args.username: USERNAME   = args.username
    if args.password: PASSWORD   = args.password

    if args.list_mics:
        Listener().list_microphones()
        return

    assistant = VoiceAssistant()

    if args.text or not sr.Microphone.list_microphone_names():
        assistant.text_mode()
    else:
        try:
            assistant.start()
        except Exception as e:
            console.print(f"[yellow]Voice mode failed ({e}), switching to text mode...[/yellow]")
            assistant.text_mode()


if __name__ == '__main__':
    main()
