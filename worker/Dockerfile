# Production stage
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for audio processing, WebRTC, and git (for pytrickle)
RUN apt-get update && apt-get install -y \
    libavcodec-dev \
    libavformat-dev \
    libavfilter-dev \
    libavdevice-dev \
    libswresample-dev \
    libswscale-dev \
    libportaudio2 \
    portaudio19-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create a virtual environment
RUN uv venv /app/.venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Python dependencies using uv
COPY requirements.txt .
RUN uv pip install -r requirements.txt

# Copy the worker source
COPY . .

# Create models directory for an optional local Granite model snapshot
RUN mkdir -p /models

# Expose the port the worker runs on
EXPOSE 9935

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HOST_IP=0.0.0.0
ENV WORKER_PORT=9935
ENV HF_HOME=/models
ENV HUGGINGFACE_HUB_CACHE=/models

# Command to run the worker
CMD ["python", "app.py"]