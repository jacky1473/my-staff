# 1. Use a lightweight Python base image
FROM python:3.9-slim

# 2. Set the working directory inside the container
WORKDIR /app

# 3. Copy your requirements file first (saves time on rebuilds)
COPY requirements.txt .

# 4. Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy your application files (app.py and the templates folder)
COPY . .

# 6. Expose the port Flask runs on
EXPOSE 5000

# 7. The final command to start your app
CMD ["python", "app.py"]
