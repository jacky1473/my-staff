FROM python:3.9-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Persistent data (sqlite DB, backups) lives here — mount a host volume at
# this path so data survives container recreation/upgrades.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 5000

ENV FLASK_DEBUG=false
# SECRET_KEY has no default on purpose — app.py refuses to start without it
# outside debug mode. Pass it at `docker run` time (see deploy notes),
# never bake a real secret into the image.

# Single worker + multiple threads: the in-process attendance scheduler
# thread (see app.py) assumes exactly one process. Scale by giving the
# container more CPU/threads, not more gunicorn workers, unless the
# scheduler is moved to an external cron/leader-elected job first.
CMD ["gunicorn", "--workers", "1", "--threads", "4", \
     "--bind", "0.0.0.0:5000", "--access-logfile", "-", \
     "--error-logfile", "-", "app:app"]
