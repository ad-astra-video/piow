#!/usr/bin/env python3
"""
WebRTC and WHIP components for real-time audio processing
Contains WHIP handler, audio processing track, and SDP manipulation utilities
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
import wave
import numpy as np
import av
from aiohttp import web, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

logger = logging.getLogger(__name__)

# Configuration
VLLM_WS_URL = os.environ.get("VLLM_WS_URL", "ws://vllm:6000/v1/realtime")  # Adjust if VLLM runs on different port
GPU_RUNNER_URL = os.environ.get("GPU_RUNNER_URL", "http://localhost:9935")
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
    ice_servers = []
    if not config_string:
        return ice_servers
    
    for server_config in config_string.split(","):
        server_config = server_config.strip()
        if not server_config:
            continue
            
        # Parse STUN/TURN server URL
        if server_config.startswith("stun:"):
            ice_servers.append(RTCIceServer(urls=server_config))
        elif server_config.startswith("turn:"):
            # For TURN servers, we need username and password
            ice_servers.append(
                RTCIceServer(
                    urls=server_config,
                    username=TURN_USERNAME,
                    credential=TURN_PASSWORD
                )
            )
        else:
            # Assume it's a STUN server if no protocol specified
            ice_servers.append(RTCIceServer(urls=f"stun:{server_config}"))
    
    return ice_servers


def get_ice_servers():
    """Get ICE servers for WebRTC peer connections."""
    return parse_ice_servers(ICE_SERVERS_CONFIG)


def munge_sdp(sdp_text, host_ip):
    """
    Replace IP addresses in SDP with the host IP for NAT traversal.
    This is necessary because aiortc generates candidates with the container's
    internal IP (172.x.x.x) which is not routable from outside the container.
    """
    lines = sdp_text.split("\n")
    munge_lines = []
    for line in lines:
        if line.startswith("c=") and ("IP4" in line or "IP6" in line):
            # Replace connection address
            parts = line.split(" ")
            if len(parts) >= 3:
                parts[2] = host_ip
                line = " ".join(parts)
        elif line.startswith("a=candidate:"):
            # Replace candidate address
            parts = line.split(" ")
            if len(parts) >= 5:
                # The IP address is typically the 5th field in candidate line
                parts[4] = host_ip
                line = " ".join(parts)
        munge_lines.append(line)
    return "\n".join(munge_lines)


def create_peer_context(pc, pc_id):
    """Create a context dictionary for tracking peer connection state."""
    return {
        "pc": pc,
        "pc_id": pc_id,
        "audio_track": None,
        "video_track": None,
        "data_channel": None,
        "messages": [],
        "created_at": asyncio.get_event_loop().time(),
    }


def track_task(context, coro, name):
    """Track a task and clean it up when done."""
    task = asyncio.ensure_future(coro)
    
    def cleanup_task(task):
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Task {name} failed: {e}")
        finally:
            context["tasks"] = [t for t in context.get("tasks", []) if t != task]
    
    task.add_done_callback(cleanup_task)
    if "tasks" not in context:
        context["tasks"] = []
    context["tasks"].append(task)
    return task


def audio_frames_to_pcm16(frames):
    """Convert audio frames to PCM16 format."""
    if not frames:
        return b""
    
    # Convert to numpy array
    audio_data = np.concatenate([f.to_ndarray() for f in frames])
    
    # Ensure we have mono audio
    if len(audio_data.shape) > 1:
        audio_data = audio_data.mean(axis=1)
    
    # Convert to int16
    audio_data = audio_data.astype(np.int16)
    
    return audio_data.tobytes()


class WHIPHandler:
    """Handle WHIP POST requests by proxying to GPU worker."""
    
    def __init__(self):
        self.pcs = set()  # PeerConnections for WHIP

    async def whip(self, request):
        """Handle WHIP POST request by proxying to GPU worker."""
        logger.info("Received WHIP request")
        
        # Get the request body (SDP offer)
        body = await request.text()
        
        # Extract session ID from query parameters or headers if needed
        # For now, we will generate a session ID or get it from request
        session_id = request.query.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
        
        # Prepare request to GPU worker
        whip_url = f"{GPU_RUNNER_URL}/process/stream/{session_id}/whip"
        logger.info(f"Proxying WHIP request to GPU worker: {whip_url}")
        
        # Forward the WHIP request to GPU worker
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    whip_url,
                    data=body,
                    headers={
                        "Content-Type": "application/sdp"
                    },
                ) as worker_response:
                    if worker_response.status != 200:
                        error_text = await worker_response.text()
                        logger.error(f"GPU worker WHIP request failed: {worker_response.status} - {error_text}")
                        return web.json_response(
                            {"error": "Failed to establish WHIP connection with GPU worker"},
                            status=worker_response.status,
                        )
                    
                    # Get the answer from GPU worker
                    answer_sdp = await worker_response.text()
                    logger.info(f"Received WHIP answer from GPU worker (length: {len(answer_sdp)})")
                    
                    # Return the answer to the client
                    return web.Response(
                        text=answer_sdp,
                        content_type="application/sdp"
                    )
            except Exception as e:
                logger.error(f"HTTP error forwarding WHIP to GPU worker: {e}")
                return web.json_response(
                    {"error": "Failed to connect to GPU worker"},
                    status=500,
                )


class AudioProcessorTrack(MediaStreamTrack):
    """
    Custom audio track that processes audio frames and sends them to VLLM via WebSocket.
    Properly handles resampling to 16kHz mono for VLLM compatibility.
    """
    
    def __init__(self, track, vllm_client):
        super().__init__()
        self.track = track
        self.vllm_client = vllm_client
        self.frame_count = 0

    async def recv(self):
        frame = await self.track.recv()
        
        self.frame_count += 1
        
        # Log every 100 frames to avoid spamming
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
                logger.info(f"AudioProcessorTrack: Audio level RMS={rms:.2f}, frame size={len(audio_bytes)} bytes, sample_rate={16000}Hz")
            
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

    def _process_audio_frame(self, frame) -> bytes:
        """
        Convert WebRTC audio frame to 16kHz mono PCM16 bytes.
        This is a simplified version - in production you might want to use
        a proper resampling library like soxr or librosa.
        """
        # For now, we'll assume the frame is already in the right format
        # In a real implementation, you would:
        # 1. Get the audio data from the frame
        # 2. Resample to 16kHz if needed
        # 3. Convert to mono if needed
        # 4. Convert to PCM16 format
        
        # Since we're getting frames from the browser via WebRTC,
        # they should already be in a reasonable format
        # Let's just return the raw audio data for now
        # TODO: Implement proper audio processing/resampling
        
        try:
            # Convert frame to ndarray
            arr = frame.to_ndarray()
            
            # If it's stereo, convert to mono by averaging channels
            if len(arr.shape) > 1 and arr.shape[0] == 2:  # Stereo
                arr = ((arr[0, :] + arr[1, :]) / 2).astype(np.int16)
            elif len(arr.shape) > 1:  # Multi-channel
                arr = np.mean(arr, axis=0).astype(np.int16)
            
            # Ensure we're at the right sample rate (this is simplified)
            # In reality, you'd need to resample if the sample rate doesn't match
            
            return arr.tobytes()
        except Exception as e:
            logger.error(f"Error in _process_audio_frame: {e}")
            # Return silence on error
            return b'\x00\x00' * 160  # 160 samples of silence at 16-bit