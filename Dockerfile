FROM python:3.9-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 5000

# Use environment variable for secret key in production
ENV SECRET_KEY=change-this-in-production
ENV FLASK_DEBUG=false

CMD ["python", "app.py"]
