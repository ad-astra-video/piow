# Build stage for frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

# Copy frontend package files
COPY frontend/package*.json ./
RUN npm ci

# Build-time environment variables for Vite
# These must be passed as --build-arg when building the image
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
ARG VITE_STRIPE_PUBLIC_KEY
ARG VITE_FACILITATOR_URL

ENV VITE_SUPABASE_URL=${VITE_SUPABASE_URL}
ENV VITE_SUPABASE_PUBLISHABLE_KEY=${VITE_SUPABASE_PUBLISHABLE_KEY}
ENV VITE_STRIPE_PUBLIC_KEY=${VITE_STRIPE_PUBLIC_KEY}
ENV VITE_FACILITATOR_URL=${VITE_FACILITATOR_URL}

# Copy frontend source and build
COPY frontend/ ./
RUN npm run build

# Production stage
FROM python:3.12-slim
WORKDIR /app

# Install system dependencies required for aiortc and other packages with native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libopus-dev \
    libvpx-dev \
    libffi-dev \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager
RUN pip install --no-cache-dir uv

# Install Python dependencies using UV
COPY backend/requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt --system

# Copy the backend source
COPY backend/ ./backend/

# Copy the built frontend dist directory from builder stage
COPY --from=frontend-builder /app/frontend/dist/ ./frontend/dist/

# Expose the port the backend runs on
EXPOSE 8000

# Command to run the backend
CMD ["python", "backend/main.py"]