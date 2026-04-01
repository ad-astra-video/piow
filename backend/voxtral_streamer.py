#!/usr/bin/env python3
"""
VLLM Voxtral Realtime Streamer
GPU-based real-time streaming transcription using VLLM with Voxtral model
"""

import os
import asyncio
import json
import logging
import websockets
import numpy as np
from typing import AsyncGenerator, Optional, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

class VoxtralStreamer:
    """
    VLLM Voxtral Realtime streamer for GPU-based real-time transcription.
    Connects to VLLM server via WebSocket for low-latency streaming.
    """
    
    def __init__(self, 
                 ws_url: str = "ws://localhost:6000/v1/realtime",
                 source_lang: str = "en",
                 target_lang: str = "en",
                 temperature: float = 0.0,
                 max_tokens: int = 256):
        """
        Initialize the Voxtral streamer.
        
        Args:
            ws_url: WebSocket URL for VLLM server
            source_lang: Source language for transcription
            target_lang: Target language (unused in transcription-only mode)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
        """
        self.ws_url = ws_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        self.websocket = None
        self.is_connected = False
        self.session_id = None
        
        # Callbacks
        self.text_callback: Optional[Callable[[str, bool, Optional[dict]], None]] = None
        self.error_callback: Optional[Callable[[str], None]] = None
        self.close_callback: Optional[Callable[[], None]] = None
        
        # Audio configuration
        self.sample_rate = 16000
        self.channels = 1
        self.sample_width = 2  # 16-bit
        
        logger.info(f"VoxtralStreamer initialized for {ws_url}")
    
    def set_text_callback(self, callback: Callable[[str, bool, Optional[dict]], None]):
        """Set callback for receiving transcribed text."""
        self.text_callback = callback
    
    def set_error_callback(self, callback: Callable[[str], None]):
        """Set callback for receiving errors."""
        self.error_callback = callback
    
    def set_close_callback(self, callback: Callable[[], None]):
        """Set callback for connection close."""
        self.close_callback = callback
    
    async def connect(self) -> bool:
        """
        Connect to the VLLM WebSocket server.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to VLLM at {self.ws_url}")
            
            self.websocket = await websockets.connect(self.ws_url)
            self.is_connected = True
            
            # Initialize session
            init_message = {
                "session_id": f"voxtral_{int(datetime.now().timestamp())}",
                "modalities": ["audio", "text"],
                "audio_format": {
                    "sample_rate": self.sample_rate,
                    "channels": self.channels,
                    "sample_width": self.sample_width
                },
                "text_configuration": {
                    "language": self.source_lang,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens
                }
            }
            
            await self.websocket.send(json.dumps(init_message))
            response = await self.websocket.recv()
            response_data = json.loads(response)
            
            if response_data.get("type") == "session.created":
                self.session_id = response_data.get("session_id")
                logger.info(f"Voxtral session created: {self.session_id}")
                
                # Start listening for messages
                asyncio.create_task(self._listen_for_messages())
                return True
            else:
                logger.error(f"Failed to create session: {response_data}")
                await self.close()
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to VLLM: {e}")
            self.is_connected = False
            if self.error_callback:
                self.error_callback(str(e))
            return False
    
    async def close(self):
        """Close the WebSocket connection."""
        if self.websocket and self.is_connected:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket: {e}")
            finally:
                self.is_connected = False
                self.websocket = None
                if self.close_callback:
                    self.close_callback()
    
    async def send_audio(self, audio_bytes: bytes, commit: bool = False):
        """
        Send audio data to VLLM for transcription.
        
        Args:
            audio_bytes: PCM16 audio data as bytes
            commit: Whether to commit the audio buffer (end of utterance)
        """
        if not self.is_connected or not self.websocket:
            logger.warning("Cannot send audio: not connected to VLLM")
            return False
        
        try:
            message = {
                "type": "input_audio_buffer.append",
                "audio": audio_bytes.hex() if isinstance(audio_bytes, bytes) else str(audio_bytes)
            }
            
            if commit:
                message["type"] = "input_audio_buffer.commit"
            
            await self.websocket.send(json.dumps(message))
            logger.debug(f"Sent {len(audio_bytes)} bytes to VLLM (commit={commit})")
            return True
            
        except Exception as e:
            logger.error(f"Error sending audio to VLLM: {e}")
            if self.error_callback:
                self.error_callback(str(e))
            return False
    
    async def _listen_for_messages(self):
        """Listen for messages from VLLM WebSocket."""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode VLLM message: {e}")
                except Exception as e:
                    logger.error(f"Error handling VLLM message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("VLLM WebSocket connection closed")
            self.is_connected = False
            if self.close_callback:
                self.close_callback()
        except Exception as e:
            logger.error(f"Error in VLLM message listener: {e}")
            self.is_connected = False
            if self.error_callback:
                self.error_callback(str(e))
    
    async def _handle_message(self, data: dict):
        """
        Handle incoming message from VLLM.
        
        Args:
            data: Parsed JSON message from VLLM
        """
        msg_type = data.get("type")
        
        if msg_type == "conversation.item.created":
            logger.debug("Conversation item created")
            
        elif msg_type == "response.audio_transcript.delta":
            # Streaming transcript delta
            delta = data.get("delta", "")
            if delta and self.text_callback:
                self.text_callback(delta, False, None)
                
        elif msg_type == "response.audio_transcript.done":
            # Final transcript
            transcript = data.get("transcript", "")
            if transcript and self.text_callback:
                self.text_callback(transcript, True, data.get("usage"))
                
        elif msg_type == "response.audio.done":
            logger.debug("Audio generation done")
            
        elif msg_type == "error":
            error_msg = data.get("message", "Unknown error")
            logger.error(f"VLLM error: {error_msg}")
            if self.error_callback:
                self.error_callback(error_msg)
                
        else:
            logger.debug(f"Received VLLM message: {msg_type}")
    
    async def update_session_config(self, source_lang: str = None, target_lang: str = None):
        """
        Update the session configuration.
        
        Args:
            source_lang: New source language (optional)
            target_lang: New target language (optional)
        """
        if not self.is_connected:
            logger.warning("Cannot update session: not connected")
            return False
        
        try:
            if source_lang is not None:
                self.source_lang = source_lang
            if target_lang is not None:
                self.target_lang = target_lang
            
            config_message = {
                "type": "session.update",
                "session": {
                    "modalities": ["audio", "text"],
                    "audio_format": {
                        "sample_rate": self.sample_rate,
                        "channels": self.channels,
                        "sample_width": self.sample_width
                    },
                    "text_configuration": {
                        "language": self.source_lang,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens
                    }
                }
            }
            
            await self.websocket.send(json.dumps(config_message))
            logger.info(f"Updated session config: lang={self.source_lang}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating session config: {e}")
            if self.error_callback:
                self.error_callback(str(e))
            return False

# Factory function
def create_voxtral_streamer(ws_url: str = None, **kwargs) -> VoxtralStreamer:
    """
    Factory function to create a VoxtralStreamer instance.
    
    Args:
        ws_url: WebSocket URL (optional, uses env or default)
        **kwargs: Additional arguments for VoxtralStreamer
        
    Returns:
        VoxtralStreamer instance
    """
    if ws_url is None:
        ws_url = os.environ.get("VLLM_WS_URL", "ws://localhost:6000/v1/realtime")
    
    return VoxtralStreamer(ws_url=ws_url, **kwargs)

# Health check function
async def voxtral_health_check(ws_url: str = None) -> Dict[str, Any]:
    """Check if Voxtral streamer can connect to VLLM."""
    if ws_url is None:
        ws_url = os.environ.get("VLLM_WS_URL", "ws://localhost:6000/v1/realtime")
    
    streamer = VoxtralStreamer(ws_url=ws_url)
    connected = await streamer.connect()
    
    if connected:
        await streamer.close()
        status = "healthy"
    else:
        status = "unhealthy"
    
    return {
        "status": status,
        "module": "voxtral_streamer",
        "ws_url": ws_url,
        "connected": connected,
        "timestamp": datetime.utcnow().isoformat()
    }
