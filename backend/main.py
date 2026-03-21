#!/usr/bin/env python3
"""
Live Translation App Backend
Handles WHIP ingestion, WebRTC to VLLM bridge, and WebSocket communication with frontend.
"""
import asyncio
import json
import logging
import base64
import numpy as np
from aiohttp import web, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaRelay, MediaBlackhole
import websockets
import audioop

# Configuration
VLLM_WS_URL = "ws://localhost:8001/v1/realtime"  # Adjust if VLLM runs on different port
WHIP_ENDPOINT = "/whip"
WEBSOCKET_ENDPOINT = "/ws"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state
connected_frontends = set()  # WebSocket connections to frontend
pcs = set()  # PeerConnections for WHIP
vllm_clients = {}  # pc_id -> VLLMRealtimeClient
relays = {}  # pc_id -> MediaRelay

class AudioProcessorTrack(MediaStreamTrack):
    """
    Custom audio track that processes audio frames and sends them to VLLM via WebSocket.
    """
    def __init__(self, track, vllm_client):
        super().__init__()
        self.track = track
        self.vllm_client = vllm_client
        self._queue = asyncio.Queue()
        
    async def recv(self):
        frame = await self.track.recv()
        # Convert audio frame to raw PCM16 bytes
        # For simplicity, we'll extract the audio data and send to VLLM
        # In a full implementation, you'd handle resampling, format conversion, etc.
        try:
            # Get audio data from frame
            audio_data = frame.to_ndarray()
            # Convert to bytes (assuming int16)
            if audio_data.dtype != np.int16:
                # Convert float32 to int16 if needed
                if audio_data.dtype == np.float32:
                    audio_data = (audio_data * 32767).astype(np.int16)
            
            audio_bytes = audio_data.tobytes()
            
            # Send to VLLM if connected
            if self.vllm_client.is_connected:
                asyncio.create_task(self.vllm_client.send_audio(audio_bytes))
                
        except Exception as e:
            logger.error(f"Error processing audio frame: {e}")
        
        # Return a silent frame to keep the track alive
        # In a real implementation, you might want to modify the frame or return None
        return frame

class WHIPHandler:
    def __init__(self):
        self.pcs = set()

    async def whip(self, request):
        """Handle WHIP POST request."""
        # Parse SDP from request body
        body = await request.text()
        offer = RTCSessionDescription(sdp=body, type="offer")

        # Create PeerConnection
        pc = RTCPeerConnection()
        self.pcs.add(pc)
        pc_id = id(pc)
        logger.info(f"Created WHIP PeerConnection {pc_id}")

        # Set up media relay
        relay = MediaRelay()
        relays[pc] = relay

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"WHIP connection state: {pc.connectionState}")
            if pc.connectionState == "failed":
                await pc.close()
                self.pcs.discard(pc)
                # Cleanup
                if pc in relays:
                    del relays[pc]
                if pc_id in vllm_clients:
                    del vllm_clients[pc_id]

        @pc.on("track")
        def on_track(track):
            logger.info(f"Received {track.kind} track")
            if track.kind == "audio":
                # Create VLLM client for this peer connection
                # In a full app, we'd get config from frontend WebSocket
                vllm_client = VLLMRealtimeClient(
                    ws_url=VLLM_WS_URL,
                    source_lang="en",  # Default, will be overridden by frontend
                    target_lang="es",  # Default
                    temperature=0.7,
                    max_tokens=256
                )
                vllm_clients[pc_id] = vllm_client
                
                # Connect to VLLM
                asyncio.create_task(vllm_client.connect())
                
                # Set up callbacks to forward to frontend
                def text_callback(text, is_final=False):
                    # Forward to all connected frontends
                    for ws in connected_frontends:
                        try:
                            ws.send_json({
                                "type": "translation",
                                "text": text,
                                "is_final": is_final,
                                "sourceLang": "en",  # Should get from config
                                "targetLang": "es"
                            })
                        except:
                            pass
                
                def audio_callback(audio_data):
                    # Forward audio to frontend if output mode is audio
                    for ws in connected_frontends:
                        try:
                            ws.send_bytes(audio_data)
                        except:
                            pass
                
                vllm_client.set_audio_callback(audio_callback)
                vllm_client.set_text_callback(text_callback)
                
                # Create processor track and replace the original
                processor = AudioProcessorTrack(track, vllm_client)
                # Replace the track in the peer connection
                # We need to add the processor track and remove the original
                # For simplicity, we'll just process in place
                
            elif track.kind == "video":
                # Ignore video track (black frames) or consume it
                pass

        # Handle offer
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescriptor(answer)

        # Return answer as SDP
        return web.Response(
            content_type="application/sdp",
            text=pc.localDescription.sdp
        )

async def websocket_handler(request):
    """Handle WebSocket connections from frontend."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    connected_frontends.add(ws)
    logger.info("Frontend WebSocket connected")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                logger.info(f"Received from frontend: {data}")
                # Handle configuration messages from frontend
                if data.get("type") == "config":
                    # Store config - in a full app we'd associate with specific PC
                    # For now, we'll broadcast to all VLLM clients
                    config = {
                        "sourceLang": data.get("sourceLang", "en"),
                        "targetLang": data.get("targetLang", "es"),
                        "outputMode": data.get("outputMode", "text"),
                        "temperature": float(data.get("temperature", 0.7)),
                        "maxTokens": int(data.get("maxTokens", 256))
                    }
                    # Update all VLLM clients with new config
                    for vllm_client in vllm_clients.values():
                        # Reconfigure VLLM client (simplified)
                        vllm_client.source_lang = config["sourceLang"]
                        vllm_client.target_lang = config["targetLang"]
                        vllm_client.temperature = config["temperature"]
                        vllm_client.max_tokens = config["maxTokens"]
                        # In a full implementation, we'd renegotiate the session
                        
                    logger.info(f"Updated config for all VLLM clients: {config}")
                        
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        connected_frontends.discard(ws)
        logger.info("Frontend WebSocket disconnected")

    return ws

async def index(request):
    """Serve the frontend HTML."""
    with open('../frontend/dist/index.html', 'r') as f:
        content = f.read()
    return web.Response(text=content, content_type='text/html')
async def static_file(request):
    """Serve static files (JS, CSS, etc.) from the dist directory."""
    path = request.match_info.get('path', '')
    # Prevent directory traversal attacks
    if '..' in path or path.startswith('/'):
        raise web.HTTPNotFound()
    try:
        with open(f'../frontend/dist/{path}', 'r') as f:
            content = f.read()
        if path.endswith('.js'):
            return web.Response(text=content, content_type='application/javascript')
        elif path.endswith('.css'):
            return web.Response(text=content, content_type='text/css')
        elif path.endswith('.html'):
            return web.Response(text=content, content_type='text/html')
        else:
            return web.Response(text=content, content_type='text/plain')
    except FileNotFoundError:
        raise web.HTTPNotFound()
async def init_app():
    """Initialize the aiohttp web application."""
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/{path:.*}', static_file)
    app.router.add_post(WHIP_ENDPOINT, whip_handler.whip)
    app.router.add_get(WEBSOCKET_ENDPOINT, websocket_handler)
    return app

whip_handler = WHIPHandler()

if __name__ == '__main__':
    web.run_app(init_app(), host='0.0.0.0', port=8000)