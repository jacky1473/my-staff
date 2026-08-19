FROM python:3.9-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 5000

ENV FLASK_DEBUG=false

# SECRET_KEY is intentionally NOT set here — pass it at `docker run` time
# (e.g. via Jenkins credentials) so it's never baked into the image or git
# history. If it's left unset, app.py generates and persists one under
# /data on first boot so sessions still survive container restarts.

# Single worker with multiple threads: the background scheduler/auto-backup
# threads in app.py are only meant to run once per container, and SQLite
# doesn't benefit from multiple worker processes anyway.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "60", "app:app"]
