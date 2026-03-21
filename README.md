# Live Translation App

A real-time translation application using WebRTC, WHIP, and VLLM with Mistral's Voxtral model.

## Architecture

```
Frontend (Browser) <--WebSocket--> Backend (Python/aiohttp)
     |                               |
     |--WHIP--->                     |--WebSocket--> VLLM Server
     |                               |
     |<--WebSocket (audio/text)------|<-WebSocket-- VLLM Responses
```

## Components

1. **Frontend**: HTML/JavaScript app that:
   - Captures microphone audio
   - Creates 240p black video frames using canvas
   - Sends media via WHIP to backend
   - Receives translations via WebSocket (text or audio)
   - Provides UI for language selection and model tuning

2. **Backend**: Python/aiohttp server that:
   - Handles WHIP ingestion (WebRTC over HTTP)
   - Manages WebSocket connections to frontend
   - Bridges audio to VLLM via WebSocket
   - Relays VLLM responses back to frontend

3. **VLLM Server**: Running locally with Mistral's Voxtral-Mini-4B-Realtime-2602 model
   - Provides OpenAI-compatible realtime API at `/v1/realtime`
   - Handles speech-to-text, translation, and text-to-speech

4. **Coqui TTS**: Used for voice cloning when audio output is requested

## Setup

### Prerequisites
- Python 3.8+
- Node.js (for frontend dependencies, though we're using vanilla JS)
- VLLM server running with Voxtral model
- Coqui TTS installed

### Backend Setup
```bash
cd /projects/live-translation-app/backend
pip install -r requirements.txt
```

### VLLM Server
Start VLLM with the Voxtral model:
```bash
vllm serve mistralai/Voxtral-Mini-4B-Realtime-2602 \
    --host localhost --port 8001 \
    --api-key token-abc123 \
    --enable-realtime-mode
```

### Run the Application
```bash
cd /projects/live-translation-app/backend
python main.py
```

Then open `http://localhost:8000` in your browser.

## Usage
1. Select source and target languages
2. Choose output mode (Text or Audio)
3. Adjust temperature and max tokens as needed
4. Click "Start Translation"
5. Speak into your microphone
6. View translations in real-time

## Notes
- The frontend creates 240p black video frames to satisfy WebRTC requirements while focusing on audio translation
- WHIP is used for WebRTC ingestion over standard HTTP requests
- The backend acts as a signaling and media bridge between frontend and VLLM
- When audio output is selected, Coqui TTS with voice cloning is used to generate speech in the target language