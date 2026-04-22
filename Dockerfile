# Build stage for frontend
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