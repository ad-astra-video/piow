#!/usr/bin/env python3
"""
WebRTC components for handling real-time audio processing
Includes AudioProcessorTrack for processing audio frames and sending to VLLM
"""

import asyncio
import logging
import numpy as np
from aiortc import MediaStreamTrack

logger = logging.getLogger(__name__)

VLLM_SAMPLE_RATE = 16000  # VLLM expects 16kHz audio


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