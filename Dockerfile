FROM python:3.11-slim
WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend source
COPY backend/ ./backend/

# Copy the pre-built frontend dist directory
COPY frontend/dist/ ./frontend/dist/

# Expose the port the backend runs on
EXPOSE 8000

# Command to run the backend
CMD ["python", "backend/main.py"]