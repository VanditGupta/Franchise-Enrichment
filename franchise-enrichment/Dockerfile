# Use official Python image
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl unzip wget gnupg ca-certificates fonts-liberation \
    libnss3 libatk-bridge2.0-0 libgtk-3-0 libxss1 libasound2 libxshmfence-dev libgbm-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependencies and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install --with-deps

# Copy project files
COPY . .

# Set the entrypoint
CMD ["python", "main.py"]
