#!/usr/bin/env python3
"""
VLLM WebSocket client for real-time translation.
Connects to VLLM's OpenAI-compatible realtime API.
"""
import asyncio
import json
import logging
import base64
import numpy as np
from typing import Optional, Callable
import websockets

logger = logging.getLogger(__name__)

class VLLMRealtimeClient:
    def __init__(self, 
                 ws_url: str = "ws://localhost:8001/v1/realtime",
                 source_lang: str = "en",
                 target_lang: str = "es",
                 temperature: float = 0.7,
                 max_tokens: int = 256):
        self.ws_url = ws_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.audio_callback: Optional[Callable] = None
        self.text_callback: Optional[Callable] = None
        
    async def connect(self):
        """Connect to VLLM WebSocket endpoint."""
        try:
            self.websocket = await websockets.connect(self.ws_url)
            self.is_connected = True
            logger.info(f"Connected to VLLM at {self.ws_url}")
            
            # Send initial configuration
            await self._send_config()
            
            # Start listening for responses
            asyncio.create_task(self._listen())
            
        except Exception as e:
            logger.error(f"Failed to connect to VLLM: {e}")
            self.is_connected = False
            raise
    
    async def _send_config(self):
        """Send session configuration to VLLM."""
        config = {
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "voice": "alloy",  # Will be overridden if using Coqui TTS
                "instructions": f"You are a professional translator. Translate from {self.source_lang} to {self.target_lang}.",
                "temperature": self.temperature,
                "max_response_output_tokens": self.max_tokens,
            }
        }
        await self.websocket.send(json.dumps(config))
        logger.info("Sent session configuration to VLLM")
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to VLLM."""
        if not self.is_connected or not self.websocket:
            return
            
        # Encode audio as base64
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        message = {
            "type": "input_audio_buffer.append",
            "audio": audio_base64
        }
        await self.websocket.send(json.dumps(message))
    
    async def commit_audio(self):
        """Commit audio buffer and request translation."""
        if not self.is_connected or not self.websocket:
            return
            
        await self.websocket.send(json.dumps({
            "type": "input_audio_buffer.commit"
        }))
        
        # Request response
        await self.websocket.send(json.dumps({
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"] if self.output_mode == "audio" else ["text"],
                "temperature": self.temperature,
                "max_output_tokens": self.max_tokens
            }
        }))
    
    async def _listen(self):
        """Listen for messages from VLLM."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._handle_vllm_message(data)
        except websockets.ConnectionClosed:
            logger.info("VLLM WebSocket connection closed")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error in VLLM listener: {e}")
            self.is_connected = False
    
    async def _handle_vllm_message(self, data: dict):
        """Handle incoming messages from VLLM."""
        msg_type = data.get("type")
        
        if msg_type == "response.audio_transcript.delta":
            # Partial transcript
            if self.text_callback:
                self.text_callback(data.get("delta", ""))
        elif msg_type == "response.audio_transcript.done":
            # Final transcript
            transcript = data.get("transcript", "")
            if self.text_callback:
                self.text_callback(transcript, is_final=True)
        elif msg_type == "response.audio.delta":
            # Audio delta
            if self.audio_callback and "delta" in data:
                audio_base64 = data["delta"]
                audio_data = base64.b64decode(audio_base64)
                self.audio_callback(audio_data)
        elif msg_type == "response.audio.done":
            # Audio complete
            pass
        elif msg_type == "error":
            logger.error(f"VLLM error: {data}")
        # Add more message types as needed
    
    def set_audio_callback(self, callback: Callable):
        """Set callback for receiving audio data."""
        self.audio_callback = callback
    
    def set_text_callback(self, callback: Callable):
        """Set callback for receiving text data."""
        self.text_callback = callback
    
    async def close(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.is_connected = False