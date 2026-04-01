#!/usr/bin/env python3
"""
Live Translation App Backend
Handles WHIP ingestion, WebRTC to VLLM bridge, and WebSocket communication with frontend.
"""
import asyncio
import contextlib
import json
import logging
import os
import wave
import numpy as np
import av
from aiohttp import web, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

# Import transcription and translation endpoints
from transcribe import setup_routes as setup_transcribe_routes
from sessions import setup_routes as setup_sessions_routes

from vllm_client import VLLMRealtimeClient
from supabase_client import supabase

# Configuration
VLLM_WS_URL = "ws://vllm:6000/v1/realtime"  # Adjust if VLLM runs on different port
WHIP_ENDPOINT = "/whip"
WEBSOCKET_ENDPOINT = "/ws"

# Host IP for SDP munging (Docker container IP replacement)
# This is critical for WebRTC to work when backend runs in Docker
# The container's internal IP (172.x.x.x) must be replaced with host IP for browser connectivity
HOST_IP = os.environ.get("HOST_IP", "127.0.0.1")

# TURN Server Configuration (optional, for complex NAT scenarios)
TURN_SERVER = os.environ.get("TURN_SERVER", "")
TURN_USERNAME = os.environ.get("TURN_USERNAME", "")
TURN_PASSWORD = os.environ.get("TURN_PASSWORD", "")

# ICE Server Configuration
# Get ICE servers from environment variables for NAT traversal
# Default includes Google STUN server and a public TURN server
DEFAULT_ICE_SERVERS = ["stun:stun.l.google.com:19302"]
ICE_SERVERS_CONFIG = os.environ.get("ICE_SERVERS", ",".join(DEFAULT_ICE_SERVERS))

def parse_ice_servers(config_string):
    """Parse ICE servers from comma-separated string into RTCIceServer list."""
    servers = []
    for url in config_string.split(","):
        url = url.strip()
        if url:
            # Check if this is a TURN server that needs credentials
            if url.startswith("turn:") and TURN_SERVER and url in TURN_SERVER:
                servers.append(RTCIceServer(
                    urls=url,
                    username=TURN_USERNAME,
                    credential=TURN_PASSWORD
                ))
            else:
                servers.append(RTCIceServer(urls=url))
    return servers

ICE_SERVERS = parse_ice_servers(ICE_SERVERS_CONFIG)
ICE_CONFIGURATION = RTCConfiguration(iceServers=ICE_SERVERS)

def munge_sdp(sdp_text, host_ip):
    """
    Replace Docker internal IPs with host IP in SDP.
    
    This is necessary because aiortc generates candidates with the container's
    internal IP (e.g., 172.18.0.4), which browsers cannot reach directly.
    
    This function handles:
    1. c= lines (connection data)
    2. a=candidate lines with typ host
    3. a=rtcp lines
    
    Args:
        sdp_text: The SDP text to modify
        host_ip: The host machine's IP address to use in candidates
    
    Returns:
        Modified SDP text with host IP instead of container IP
    """
    import re
    
    def replace_ip(match):
        prefix = match.group(1)
        old_ip = match.group(2)
        suffix = match.group(3) if match.group(3) else ""
        # Skip localhost and already correct IPs
        if old_ip == host_ip or old_ip == "127.0.0.1":
            return match.group(0)
        logger.info(f"SDP munging: replacing {old_ip} with {host_ip}")
        return f"{prefix}{host_ip}{suffix}"
    
    # Pattern 1: c= lines (connection data)
    # Matches: c=IN IP4 172.18.0.4
    c_line_pattern = r'(c=IN IP4 )(\d+\.\d+\.\d+\.\d+)(\r?\n)'
    sdp_text = re.sub(c_line_pattern, replace_ip, sdp_text)
    
    # Pattern 2: a=candidate lines with typ host
    # Matches: a=candidate:... 172.18.0.4 40045 typ host
    # We need to replace the IP but keep the rest of the candidate line intact
    candidate_pattern = r'(a=candidate:[^\s]+ \d+ udp \d+ )(\d+\.\d+\.\d+\.\d+)( \d+ typ host)'
    sdp_text = re.sub(candidate_pattern, replace_ip, sdp_text)
    
    # Pattern 3: a=rtcp lines
    # Matches: a=rtcp:9 IN IP4 0.0.0.0 or a=rtcp:40045 IN IP4 172.18.0.4
    rtcp_pattern = r'(a=rtcp:\d+ IN IP4 )(\d+\.\d+\.\d+\.\d+)(\r?\n)'
    sdp_text = re.sub(rtcp_pattern, replace_ip, sdp_text)
    
    return sdp_text

# Logging — only show INFO+ for own modules; suppress noisy third-party libs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

# Log HOST_IP configuration after logger is initialized
logger.info(f"Using HOST_IP for SDP munging: {HOST_IP}")

# Global state
connected_frontends = set()  # WebSocket connections to frontend
pcs = set()  # PeerConnections for WHIP
vllm_clients = {}  # pc_id -> VLLMRealtimeClient
relays = {}  # pc_id -> MediaRelay
peer_contexts = {}  # pc -> tracked resources for cleanup


def create_peer_context(pc, pc_id):
    context = {
        "pc": pc,
        "pc_id": pc_id,
        "tasks": set(),
        "tracks": set(),
        "closing": False,
    }
    peer_contexts[pc] = context
    return context


def track_task(context, coro, name):
    task = asyncio.create_task(coro, name=name)
    context["tasks"].add(task)

    def _cleanup(done_task):
        context["tasks"].discard(done_task)
        with contextlib.suppress(asyncio.CancelledError):
            exception = done_task.exception()
            if exception is not None:
                logger.error("Task %s failed: %s", name, exception)

    task.add_done_callback(_cleanup)
    return task


async def cleanup_peer_connection(pc, reason="cleanup"):
    context = peer_contexts.get(pc)
    if context and context["closing"]:
        return
    if context:
        context["closing"] = True

    pc_id = id(pc)
    logger.info("Cleaning up peer connection %s (%s)", pc_id, reason)

    if context:
        for track in list(context["tracks"]):
            with contextlib.suppress(Exception):
                track.stop()

        tasks = list(context["tasks"])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    vllm_client = vllm_clients.pop(pc_id, None)
    if vllm_client:
        with contextlib.suppress(Exception):
            await vllm_client.close()

    relays.pop(pc, None)
    pcs.discard(pc)
    whip_handler.pcs.discard(pc)
    peer_contexts.pop(pc, None)

    if getattr(pc, "connectionState", None) != "closed":
        with contextlib.suppress(Exception):
            await pc.close()


async def shutdown_app(app):
    logger.info("Application shutdown started")

    frontend_close_tasks = []
    for ws in list(connected_frontends):
        frontend_close_tasks.append(ws.close())
        connected_frontends.discard(ws)

    if frontend_close_tasks:
        await asyncio.gather(*frontend_close_tasks, return_exceptions=True)

    await asyncio.gather(
        *(cleanup_peer_connection(pc, reason="application shutdown") for pc in list(peer_contexts)),
        return_exceptions=True,
    )

    logger.info("Application shutdown finished")

# VLLM requires 16kHz mono audio
VLLM_SAMPLE_RATE = 16000
CHUNK_DURATION_MS = 1000
TARGET_SAMPLES_PER_CHUNK = int(VLLM_SAMPLE_RATE * CHUNK_DURATION_MS / 1000)


def audio_frames_to_pcm16(frames):
    """Convert a list of av.AudioFrame (already resampled to s16 mono 16kHz) to a flat int16 array."""
    chunks = []
    for f in frames:
        arr = f.to_ndarray()  # shape (1, n) for s16 mono
        chunks.append(arr.reshape(-1))
    return np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)


class AudioProcessorTrack(MediaStreamTrack):
    """
    Custom audio track that processes audio frames and sends them to VLLM via WebSocket.
    Properly handles resampling to 16kHz mono for VLLM compatibility.
    """
    def __init__(self, track, vllm_client):
        super().__init__()
        self.track = track
        self.vllm_client = vllm_client
        self._queue = asyncio.Queue()
        self.frame_count = 0
        self.last_log_time = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0

    def _process_audio_frame(self, frame) -> bytes:
        """
        Process audio frame to 16kHz mono PCM16 for VLLM.
        
        Args:
            frame: WebRTC audio frame
            
        Returns:
            PCM16 bytes at 16kHz mono
        """
        pcm16, _, _ = audio_frame_to_pcm16(frame)
        return pcm16.tobytes()

    async def recv(self):
        frame = await self.track.recv()
        self.frame_count += 1

        # Log every 100 frames to avoid spamming
        current_time = asyncio.get_event_loop().time()
        if self.frame_count % 100 == 0:
            logger.info(f"AudioProcessorTrack: Received {self.frame_count} audio frames")

        try:
            # Process audio frame to 16kHz mono PCM16
            audio_bytes = self._process_audio_frame(frame)

            # Log audio level occasionally
            if self.frame_count % 500 == 0 and len(audio_bytes) > 0:
                # Calculate RMS for logging
                pcm16 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
                rms = np.sqrt(np.mean(np.square(pcm16)))
                logger.info(f"AudioProcessorTrack: Audio level RMS={rms:.2f}, frame size={len(audio_bytes)} bytes, sample_rate={VLLM_SAMPLE_RATE}Hz")

            # Send to VLLM if connected
            if self.vllm_client.is_connected:
                logger.debug(f"AudioProcessorTrack: Sending {len(audio_bytes)} bytes to VLLM")
                await self.vllm_client.send_audio(audio_bytes)
            else:
                logger.debug(f"AudioProcessorTrack: VLLM not connected, dropping audio frame")

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
        logger.info("Received WHIP request")
        # Parse SDP from request body
        body = await request.text()
        offer = RTCSessionDescription(sdp=body, type="offer")

        # Create PeerConnection with ICE servers for NAT traversal
        pc = RTCPeerConnection(configuration=ICE_CONFIGURATION)
        self.pcs.add(pc)
        pcs.add(pc)
        pc_id = id(pc)
        context = create_peer_context(pc, pc_id)
        logger.info(f"Created WHIP PeerConnection {pc_id} with ICE servers: {[s.urls for s in ICE_SERVERS]}")

        # Log ICE connection state changes
        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info(f"ICE connection state: {pc.iceConnectionState}")

        @pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            logger.info(f"ICE gathering state: {pc.iceGatheringState}")

        @pc.on("icecandidate")
        def on_icecandidate(candidate):
            if candidate:
                logger.info(f"New ICE candidate: {candidate.candidate}")
            else:
                logger.info("ICE gathering complete")

        # Set up media relay
        relay = MediaRelay()
        relays[pc] = relay

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"WHIP connection state: {pc.connectionState}")
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await cleanup_peer_connection(pc, reason=f"connection state {pc.connectionState}")

        @pc.on("track")
        def on_track(track):
            logger.info(f"Received {track.kind} track")
            context["tracks"].add(track)
            if track.kind == "audio":
                # Create VLLM client for this peer connection
                # In a full app, we'd get config from frontend WebSocket
                vllm_client = VLLMRealtimeClient(
                    ws_url=VLLM_WS_URL,
                    source_lang="en",  # Default, will be overridden by frontend
                    target_lang="en",  # Unused in transcription-only mode
                    temperature=0.0,
                    max_tokens=256,
                )
                vllm_clients[pc_id] = vllm_client

                # Set up callbacks to forward to frontend
                async def text_callback(text, is_final=False, usage=None):
                    logger.info(f"Sending transcription to frontend (is_final={is_final}): {text[:100]}{'...' if len(text) > 100 else ''}")
                    # Forward to all connected frontends
                    stale_frontends = []
                    for ws in tuple(connected_frontends):
                        if ws.closed:
                            stale_frontends.append(ws)
                            continue
                        try:
                            await ws.send_json({
                                "type": "transcription",
                                "text": text,
                                "is_final": is_final,
                                "language": vllm_client.source_lang,
                                "usage": usage or {},
                            })
                        except Exception:
                            stale_frontends.append(ws)

                    for ws in stale_frontends:
                        connected_frontends.discard(ws)

                vllm_client.set_text_callback(text_callback)

                # Create background task to process audio frames from this track
                async def process_audio_track():
                    try:
                        await vllm_client.connect()
                        logger.info(f"Starting audio track processing for {track.kind} track")
                        frame_count = 0
                        total_audio_bytes = 0

                        # PyAV resampler: handles format/layout/rate conversion via libav swresample.
                        # Created once per connection so it maintains state across frames.
                        resampler = av.AudioResampler(
                            format='s16',
                            layout='mono',
                            rate=VLLM_SAMPLE_RATE,
                        )

                        # Buffer for accumulating fixed 1-second PCM chunks.
                        audio_buffer = np.array([], dtype=np.int16)
                        sent_any_audio = False

                        # Debug: accumulate 5s of audio and write to WAV for inspection
                        # COMMENTED OUT: Audio file saving disabled
                        # DEBUG_AUDIO = os.environ.get("DEBUG_AUDIO_WAV", "0") == "1"
                        # DEBUG_WAV_SAMPLES = VLLM_SAMPLE_RATE * 5  # 5 seconds
                        # debug_audio_buf = np.array([], dtype=np.int16)
                        # debug_wav_index = 0
                        
                        while not context["closing"]:
                            try:
                                frame = await asyncio.wait_for(track.recv(), timeout=1.0)
                                frame_count += 1

                                # Convert audio frame to raw PCM16 bytes
                                try:
                                    resampled = resampler.resample(frame)
                                    pcm16 = audio_frames_to_pcm16(resampled)
                                    if len(pcm16) == 0:
                                        continue

                                    # Log first frame details
                                    if frame_count == 1:
                                        logger.info(
                                            "First audio frame: fmt=%s, channels=%s, sample_rate=%s -> %d pcm16 samples",
                                            frame.format.name,
                                            len(frame.layout.channels),
                                            frame.sample_rate,
                                            len(pcm16),
                                        )
                                    
                                    # Add to buffer
                                    audio_buffer = np.concatenate([audio_buffer, pcm16])

                                    # Debug WAV dump every 5 seconds
                                    # COMMENTED OUT: Audio file saving disabled
                                    # if DEBUG_AUDIO:
                                    #     debug_audio_buf = np.concatenate([debug_audio_buf, pcm16])
                                    #     if len(debug_audio_buf) >= DEBUG_WAV_SAMPLES:
                                    #         os.makedirs("/models/audio", exist_ok=True)
                                    #         wav_path = f"/models/audio/debug_audio_{pc_id}_{debug_wav_index:04d}.wav"
                                    #         with wave.open(wav_path, "wb") as wf:
                                    #             wf.setnchannels(1)
                                    #             wf.setsampwidth(2)  # int16 = 2 bytes
                                    #             wf.setframerate(VLLM_SAMPLE_RATE)
                                    #             wf.writeframes(debug_audio_buf[:DEBUG_WAV_SAMPLES].tobytes())
                                    #         logger.info("Debug WAV written: %s", wav_path)
                                    #         debug_audio_buf = debug_audio_buf[DEBUG_WAV_SAMPLES:]
                                    #         debug_wav_index += 1
                                    
                                    while len(audio_buffer) >= TARGET_SAMPLES_PER_CHUNK:
                                        chunk = audio_buffer[:TARGET_SAMPLES_PER_CHUNK]
                                        audio_buffer = audio_buffer[TARGET_SAMPLES_PER_CHUNK:]

                                        audio_bytes = chunk.tobytes()
                                        rms = np.sqrt(np.mean(np.square(chunk.astype(np.float32))))

                                        total_audio_bytes += len(audio_bytes)
                                        logger.debug(
                                            "Sending %sms chunk: %s bytes, RMS=%.2f, total=%s bytes",
                                            CHUNK_DURATION_MS,
                                            len(audio_bytes),
                                            rms,
                                            total_audio_bytes,
                                        )

                                        # Send to VLLM if connected.
                                        # No manual commit here — the server's VAD detects
                                        # speech boundaries and triggers generation automatically.
                                        if vllm_client.is_connected:
                                            await vllm_client.send_audio(audio_bytes, commit=False)
                                            sent_any_audio = True
                                        else:
                                            logger.debug(f"AudioProcessorTrack: VLLM not connected, dropping audio chunk")

                                except Exception as e:
                                    logger.error(f"Error processing audio frame: {e}")

                            except asyncio.TimeoutError:
                                if getattr(track, "readyState", None) == "ended":
                                    logger.info("Audio track ended for peer %s", pc_id)
                                    break
                            except Exception as e:
                                logger.error(f"Error receiving audio frame: {e}")
                                break
                        
                        # Flush any samples buffered inside the resampler
                        try:
                            flushed = resampler.resample(None)
                            flush_pcm = audio_frames_to_pcm16(flushed)
                            if len(flush_pcm) > 0:
                                audio_buffer = np.concatenate([audio_buffer, flush_pcm])
                        except Exception:
                            pass

                        # Send any remaining audio in the buffer
                        if vllm_client.is_connected:
                            if len(audio_buffer) > 0:
                                logger.info(f"Sending final chunk: {len(audio_buffer)} samples")
                                audio_bytes = audio_buffer.tobytes()
                                await vllm_client.send_audio(audio_bytes, commit=True)
                                sent_any_audio = True
                            if sent_any_audio:
                                await vllm_client.commit_audio(final=True)
                                await vllm_client.wait_for_transcription_completion(timeout=3.0)
                                
                    except asyncio.CancelledError:
                        logger.info("Audio processing task cancelled for peer %s", pc_id)
                        raise
                    except Exception as e:
                        logger.error(f"Error in audio track processing task: {e}")
                    finally:
                        logger.info(f"Audio track processing ended for track {track.id}")
                        await vllm_client.close()

                # Start the audio processing task
                track_task(context, process_audio_track(), name=f"process-audio-{pc_id}")

            elif track.kind == "video":
                # Ignore video track (black frames) or consume it
                pass

        # Handle offer
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        # Munge SDP to replace Docker internal IP with host IP
        # This is critical for browser connectivity when running in Docker
        munged_sdp = munge_sdp(pc.localDescription.sdp, HOST_IP)
        
        # Return answer as SDP
        return web.Response(
            content_type="application/sdp",
            text=munged_sdp
        )

async def websocket_handler(request):
    """Handle WebSocket connections from frontend."""
    logger.info("New WebSocket connection attempt")
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
                    for vllm_client in vllm_clients.values():
                        await vllm_client.update_session_config()

                    await ws.send_json({
                        "type": "status",
                        "text": "Transcription session configured.",
                    })
                    logger.info("Updated transcription config for all VLLM clients")

            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        connected_frontends.discard(ws)
        logger.info("Frontend WebSocket disconnected")

    return ws

async def index(request):
    """Serve the frontend HTML."""
    logger.info("Serving index.html")
    with open('./frontend/dist/index.html', 'r') as f:
        content = f.read()
    return web.Response(text=content, content_type='text/html')

async def static_file(request):
    """Serve static files (JS, CSS, etc.) from the dist directory."""
    logger.info(f"Serving static file: {request.match_info.get('path', '')}")
    path = request.match_info.get('path', '')
    # Prevent directory traversal attacks
    if '..' in path or path.startswith('/'):
        raise web.HTTPNotFound()
    try:
        with open(f'./frontend/dist/{path}', 'r') as f:
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
    app.on_shutdown.append(shutdown_app)
    app.router.add_get('/', index)
    app.router.add_get('/{path:.*}', static_file)
    app.router.add_post(WHIP_ENDPOINT, whip_handler.whip)
    app.router.add_get(WEBSOCKET_ENDPOINT, websocket_handler)
    app.router.add_get("/health/supabase", supabase_health_check)
    return app

whip_handler = WHIPHandler()


async def supabase_health_check(request):
    """Health check endpoint for Supabase connection."""
    try:
        # Check if we can get the supabase client
        client = supabase
        # We can do a simple query to check the connection, but let's just check if the client is initialized.
        # To actually test the connection, we can try to fetch one row from a table (but we don't want to expose data).
        # We'll try to select from the users table with limit 0.
        result = client.table('users').select('id').limit(0).execute()
        return web.json_response({'status': 'ok', 'message': 'Supabase connection successful'})
    except Exception as e:
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)
if __name__ == '__main__':
    web.run_app(init_app(), host='0.0.0.0', port=8000)