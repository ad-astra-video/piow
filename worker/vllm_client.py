#!/usr/bin/env python3
"""
VLLM WebSocket client for real-time translation.
Connects to VLLM's OpenAI-compatible realtime API.
"""
import os
import asyncio
import inspect
import json
import logging
import base64
from typing import Optional, Callable, Any, Dict, Union

import numpy as np
import websockets

logger = logging.getLogger(__name__)

# Type alias for text callback - accepts text and optional kwargs
TextCallback = Callable[..., Union[None, Any]]
# Type alias for audio callback
AudioCallback = Callable[[bytes], None]


class VLLMRealtimeClient:
    """Client for VLLM's OpenAI-compatible realtime API."""
    
    def __init__(
        self,
        ws_url: Optional[str] = None,
        source_lang: str = "en",
        target_lang: str = "es",
        temperature: float = 0.0,
        max_tokens: int = 256,
    ):
        """
        Initialize VLLM realtime client.
        
        Args:
            ws_url: WebSocket URL for VLLM realtime API (default: from VLLM_WS_URL env var or ws://localhost:6000/v1/realtime)
            source_lang: Source language code (e.g., 'en')
            target_lang: Target language code (e.g., 'es')
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens in response
        """
        # Get ws_url from environment variable if not provided
        if ws_url is None:
            ws_url = os.environ.get("VLLM_WS_URL", "ws://localhost:6000/v1/realtime")
        """
        Initialize VLLM realtime client.
        
        Args:
            ws_url: WebSocket URL for VLLM realtime API
            source_lang: Source language code (e.g., 'en')
            target_lang: Target language code (e.g., 'es')
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens in response
        """
        # Validate parameters
        if not isinstance(ws_url, str) or not ws_url:
            raise ValueError("ws_url must be a non-empty string")
        if not isinstance(source_lang, str) or not source_lang:
            raise ValueError("source_lang must be a non-empty string")
        if not isinstance(target_lang, str) or not target_lang:
            raise ValueError("target_lang must be a non-empty string")
        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
            raise ValueError("temperature must be a number between 0 and 2")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        
        self.ws_url = ws_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.websocket: Optional[Any] = None
        self.is_connected = False
        self.audio_callback: Optional[AudioCallback] = None
        self.text_callback: Optional[TextCallback] = None
        self.listener_task: Optional[asyncio.Task] = None
        self._closing = False
        self._audio_frame_count: int = 0
        self._transcription_completed = asyncio.Event()

    async def connect(self, max_retries=30, retry_delay=5):
        """Connect to VLLM WebSocket endpoint with retry logic."""
        self._closing = False
        for attempt in range(max_retries):
            if self._closing:
                logger.info("VLLM client connect aborted during shutdown")
                return

            try:
                self.websocket = await websockets.connect(self.ws_url, additional_headers={})
                self.is_connected = True
                logger.info(f"Connected to VLLM at {self.ws_url}")
                
                # Wait for session.created event
                response = json.loads(await self.websocket.recv())
                if response["type"] == "session.created":
                    logger.info(f"Session created: {response.get('id', 'unknown')}")
                else:
                    logger.warning(f"Unexpected response: {response}")
                
                # Configure session for Voxtral realtime model
                # Following the reference implementation pattern
                # Voxtral is a translation model, so we send minimal config first
                session_update = {
                    "type": "session.update",
                    "model": "mistralai/Voxtral-Mini-4B-Realtime-2602",
                }
                logger.info(f"Sending session update: {session_update}")
                await self.websocket.send(json.dumps(session_update))

                # Signal ready - one initial commit before audio starts flowing.
                # This is the correct vLLM Voxtral realtime API handshake: the server
                # enables turn detection / VAD upon receiving this first commit.
                # After this, audio is only appended; the server VAD drives generation.
                logger.info("Sending initial input_audio_buffer.commit (signal ready)")
                await self.websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                
                # Log all incoming messages for debugging
                logger.info("Session configured, waiting for events from VLLM...")
                
                # Start listening for responses
                self.listener_task = asyncio.create_task(self._listen(), name="vllm-listener")
                return
                
            except Exception as e:
                self.is_connected = False
                if self._closing:
                    logger.info("VLLM client connect interrupted by shutdown")
                    return
                if attempt < max_retries - 1:
                    logger.warning(f"Failed to connect to VLLM (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to connect to VLLM after {max_retries} attempts: {e}")
                    raise

    async def send_audio(self, audio_data: bytes, commit: bool = False) -> None:
        """
        Send audio data to VLLM.
        
        Args:
            audio_data: Raw PCM16 audio bytes
            
        Raises:
            TypeError: If audio_data is not bytes
        """
        if not isinstance(audio_data, bytes):
            raise TypeError(f"audio_data must be bytes, got {type(audio_data).__name__}")
            
        if not self.is_connected or not self.websocket:
            logger.warning("VLLM not connected, dropping audio data")
            return

        # Encode audio as base64 PCM16
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        # Log occasionally for debugging
        if not hasattr(self, '_audio_frame_count'):
            self._audio_frame_count = 0
        self._audio_frame_count += 1
        
        if self._audio_frame_count % 100 == 0:
            logger.info(f"send_audio: frame={self._audio_frame_count}, bytes={len(audio_data)}, base64_len={len(audio_base64)}")
        
        await self.websocket.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": audio_base64
        }))

        if commit:
            logger.info("send_audio: committing buffered audio after append")
            await self.commit_audio()
        
        # NOTE: We do NOT auto-commit here
        # The VAD (Voice Activity Detection) on the server handles turn detection
        # Commits should only be sent when we detect speech has ended
        # For continuous streaming, let the server VAD handle it

    async def commit_audio(self, final: bool = False) -> None:
        """
        Commit audio buffer and request translation.
        
        Args:
            final: Whether this is the final commit (end of stream)
        """
        if not isinstance(final, bool):
            logger.warning(f"final must be bool, got {type(final).__name__}, converting")
            final = bool(final)
            
        if not self.is_connected or not self.websocket:
            logger.warning("VLLM not connected, cannot commit audio")
            return

        self._transcription_completed.clear()
        logger.info("VLLMClient: Committing audio buffer")
        # VLLM's realtime API automatically triggers generation on commit
        # when turn_detection is enabled. We don't send response.create
        # as it's not supported by VLLM (it's OpenAI-specific)
        commit_msg: Dict[str, Any] = {"type": "input_audio_buffer.commit"}
        if final:
            commit_msg["final"] = True
        await self.websocket.send(json.dumps(commit_msg))

    async def wait_for_transcription_completion(self, timeout: float = 3.0) -> bool:
        """Wait briefly for a final transcription event after committing audio."""
        try:
            await asyncio.wait_for(self._transcription_completed.wait(), timeout=timeout)
            logger.info("VLLMClient: transcription completion observed after commit")
            return True
        except asyncio.TimeoutError:
            logger.warning("VLLMClient: timed out waiting for transcription completion")
            return False

    async def _listen(self):
        """Listen for messages from VLLM."""
        if not self.websocket:
            logger.error("VLLMClient _listen called with no websocket")
            return
            
        try:
            logger.info("VLLMClient: Started listening for messages")
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    # Only log at DEBUG level — result logs are emitted in _handle_vllm_message
                    logger.debug(f"VLLM raw message: {json.dumps(data, indent=2)[:500]}")
                    await self._handle_vllm_message(data)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse VLLM message: {e}, raw: {message[:200]}")
        except asyncio.CancelledError:
            logger.info("VLLM listener task cancelled")
            raise
        except websockets.ConnectionClosed:
            logger.info("VLLM WebSocket connection closed")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error in VLLM listener: {e}")
            self.is_connected = False

    async def _handle_vllm_message(self, data: dict):
        """Handle incoming messages from VLLM."""
        msg_type = data.get("type")
        
        # VLLM uses OpenAI-compatible realtime API event types
        # Handle transcription events - log ALL delta events even if empty
        if msg_type == "transcription.delta":
            delta = data.get("delta", "")
            logger.debug(f"VLLM transcription.delta: delta='{delta}' (len={len(delta)})")
            if delta and self.text_callback:
                result = self.text_callback(delta)
                if inspect.isawaitable(result):
                    await result
                
        elif msg_type == "transcription.done":
            transcript = data.get("transcript", "")
            logger.info(f"RESULT transcription.done: '{transcript}'")
            self._transcription_completed.set()
            if transcript and self.text_callback:
                usage = data.get("usage", {})
                result = self.text_callback(transcript, is_final=True, usage=usage)
                if inspect.isawaitable(result):
                    await result
        
        elif msg_type == "response.audio_transcript.delta":
            delta = data.get("delta", "")
            if delta and self.text_callback:
                result = self.text_callback(delta)
                if inspect.isawaitable(result):
                    await result

        elif msg_type == "response.audio_transcript.done":
            transcript = data.get("transcript", "")
            logger.info(f"RESULT response.audio_transcript.done: '{transcript}'")
            self._transcription_completed.set()
            if transcript and self.text_callback:
                result = self.text_callback(transcript, is_final=True)
                if inspect.isawaitable(result):
                    await result

        elif msg_type == "response.text.delta":
            delta = data.get("delta", "")
            if delta and self.text_callback:
                result = self.text_callback(delta)
                if inspect.isawaitable(result):
                    await result

        elif msg_type == "response.text.done":
            text = data.get("text", "")
            logger.info(f"RESULT response.text.done: '{text}'")
            self._transcription_completed.set()
            if text and self.text_callback:
                result = self.text_callback(text, is_final=True)
                if inspect.isawaitable(result):
                    await result

        elif msg_type == "response.done":
            logger.info(f"RESULT response.done: {data}")
            self._transcription_completed.set()
            response = data.get("response", {})
            output = response.get("output", [])
            for item in output:
                if item.get("type") == "message":
                    for content in item.get("content", []):
                        if content.get("type") == "text":
                            text = content.get("text", "")
                            if text and self.text_callback:
                                logger.info(f"RESULT response.done message text: '{text[:200]}'")
                                result = self.text_callback(text, is_final=True)
                                if inspect.isawaitable(result):
                                    await result
                
        elif msg_type == "error":
            logger.error(f"VLLM error: {data}")
            self._transcription_completed.set()
            # Send error to frontend if possible
            if self.text_callback:
                result = self.text_callback(f"Error: {data.get('error', 'Unknown error')}", is_final=True)
                if inspect.isawaitable(result):
                    await result
                
        elif msg_type in ["session.created", "session.updated", "input_audio_buffer.committed"]:
            logger.debug(f"VLLM {msg_type}")

        else:
            logger.debug(f"VLLM unhandled message type: {msg_type}")

    async def update_session_config(
        self,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> None:
        """
        Update session configuration dynamically.
        
        Args:
            source_lang: New source language code
            target_lang: New target language code
            temperature: New temperature value (0.0-2.0)
            max_tokens: New max tokens value
            
        Raises:
            ValueError: If parameters are invalid
        """
        # Validate parameters
        if source_lang is not None:
            if not isinstance(source_lang, str) or not source_lang:
                raise ValueError("source_lang must be a non-empty string")
        if target_lang is not None:
            if not isinstance(target_lang, str) or not target_lang:
                raise ValueError("target_lang must be a non-empty string")
        if temperature is not None:
            if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
                raise ValueError("temperature must be a number between 0 and 2")
        if max_tokens is not None:
            if not isinstance(max_tokens, int) or max_tokens <= 0:
                raise ValueError("max_tokens must be a positive integer")
        
        if not self.is_connected or not self.websocket:
            logger.warning("VLLM not connected, cannot update session config")
            return
            
        # Update local config
        if source_lang:
            self.source_lang = source_lang
        if target_lang:
            self.target_lang = target_lang
        if temperature is not None:
            self.temperature = float(temperature)
        if max_tokens is not None:
            self.max_tokens = int(max_tokens)
            
        logger.info(
            "Updated local transcription config: temperature=%s, max_tokens=%s, source_lang=%s",
            self.temperature,
            self.max_tokens,
            self.source_lang,
        )

    def set_audio_callback(self, callback: Callable):
        """Set callback for receiving audio data."""
        self.audio_callback = callback

    def set_text_callback(self, callback: Callable):
        """Set callback for receiving text data."""
        self.text_callback = callback

    async def close(self):
        """Close the WebSocket connection."""
        self._closing = True
        if self.listener_task and not self.listener_task.done():
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
        self.listener_task = None

        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        self.is_connected = False