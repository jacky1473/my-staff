FROM python:3.9-slim

WORKDIR /app

# Install system utilities (curl for container healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies first for efficient layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure data and backup directories exist
RUN mkdir -p /data /app/backups

EXPOSE 5000 5001

ENV PORT=5000
ENV SECRET_KEY=change-this-to-a-secure-secret-key
ENV FLASK_DEBUG=false
ENV PYTHONUNBUFFERED=1

# Run with Gunicorn WSGI server (2 workers, 4 threads each for high concurrency)
CMD ["sh", "-c", "exec gunicorn -w 2 --threads 4 -b 0.0.0.0:${PORT:-5000} --timeout 60 app:app"]
