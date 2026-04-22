# Build stage for frontend (if needed for static assets)
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

# Copy frontend package files
COPY frontend/package*.json ./
RUN npm ci

# Copy frontend source and build
COPY frontend/ ./
RUN npm run build

# Production stage
FROM python:3.12-slim
WORKDIR /app

# Install system dependencies for audio processing and WebRTC
RUN apt-get update && apt-get install -y \
    libavcodec-dev \
    libavformat-dev \
    libavfilter-dev \
    libavdevice-dev \
    libswresample-dev \
    libswscale-dev \
    libportaudio2 \
    portaudio19-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install uv for faster package management
RUN pip install --no-cache-dir uv

# Install additional worker-specific dependencies
RUN uv pip install --system soxr

# Copy the worker source
COPY worker/ ./worker/

# Copy the built frontend dist directory from builder stage (if needed)
COPY --from=frontend-builder /app/frontend/dist/ ./frontend/dist/

# Create models directory for Granite ONNX model
RUN mkdir -p /models

# Expose the port the worker runs on
EXPOSE 8001

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HOST_IP=0.0.0.0

# Command to run the worker
CMD ["python", "worker/app.py"]
