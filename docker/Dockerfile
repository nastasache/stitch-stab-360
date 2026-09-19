FROM python:3.12-slim

# Install system dependencies: FFmpeg with vidstab, ExifTool, and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libimage-exiftool-perl \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application source
COPY . /app/

# Prevent Python bytecode generation
ENV PYTHONDONTWRITEBYTECODE=1

# Ensure workflow and cache directories exist with appropriate permissions
RUN mkdir -p \
    data/input/videos \
    data/input/nadir \
    data/input/gps \
    data/input/presets \
    data/input/masks \
    data/output \
    data/runtime/work \
    data/runtime/temp \
    data/runtime/logs \
    scratch \
    && chmod -R 777 data scratch

EXPOSE 80

CMD ["python3", "-B", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "80", "--workers", "4"]
