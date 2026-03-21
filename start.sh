#!/bin/bash
# Start script for Live Translation App

echo "Starting Live Translation App..."
echo "Make sure you have:"
echo "1. VLLM server running with mistralai/Voxtral-Mini-4B-Realtime-2602 on port 8001"
echo "2. Coqui TTS installed (if using audio output)"
echo ""
echo "Starting backend server on http://localhost:8000"
echo "Open your browser to http://localhost:8000 to use the app"
echo ""

# Change to backend directory and start the server
cd /projects/live-translation-app/backend
python main.py