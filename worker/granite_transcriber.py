#!/usr/bin/env python3
"""
Granite 4.0 1B Speech ONNX Transcriber
CPU-based batch transcription and translation using Granite 4.0 model
"""

import os
import numpy as np
import librosa
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import time

logger = logging.getLogger(__name__)

class Granite4Transcriber:
    """
    Granite 4.0 1B Speech ONNX transcriber for CPU-based batch processing.
    Handles both transcription and translation tasks.
    """
    
    def __init__(self, model_path: str = "models/granite-4.0-1b-speech-onnx"):
        """
        Initialize the Granite 4.0 transcriber.
        
        Args:
            model_path: Path to the ONNX model directory
        """
        self.model_path = Path(model_path)
        self.session = None
        self.tokenizer = None
        self.sample_rate = 16000
        self.is_loaded = False
        
        # Try to load the model
        self._load_model()
    
    def _load_model(self):
        """Load the ONNX model and tokenizer."""
        try:
            import onnxruntime as rt
            from transformers import AutoTokenizer
            
            logger.info(f"Loading Granite 4.0 model from {self.model_path}")
            
            # Check if model exists
            model_onnx_path = self.model_path / "model.onnx"
            if not model_onnx_path.exists():
                logger.warning(f"Model file not found at {model_onnx_path}")
                logger.warning("Granite 4.0 transcriber will operate in mock mode")
                self.is_loaded = False
                return
            
            # Initialize ONNX runtime session
            session_options = rt.SessionOptions()
            session_options.intra_op_num_threads = 4
            session_options.inter_op_num_threads = 2
            
            self.session = rt.InferenceSession(
                str(model_onnx_path),
                sess_options=session_options,
                providers=["CPUExecutionProvider"]
            )
            
            # Load tokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            
            self.is_loaded = True
            logger.info("Granite 4.0 model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load Granite 4.0 model: {e}")
            logger.warning("Granite 4.0 transcriber will operate in mock mode")
            self.is_loaded = False
    
    def is_available(self) -> bool:
        """Check if the transcriber is available and loaded."""
        return self.is_loaded
    
    def transcribe(self, audio_path: str, language: str = "en") -> Dict[str, Any]:
        """
        Transcribe audio file using Granite 4.0 on CPU.
        
        Args:
            audio_path: Path to audio file (mp3, wav, etc.)
            language: Language code for transcription
            
        Returns:
            Dictionary with transcription results
        """
        start_time = time.time()
        
        if not self.is_loaded:
            # Return mock result for development/testing
            logger.warning("Granite 4.0 not loaded, returning mock transcription")
            return self._mock_transcription(audio_path, language)
        
        try:
            logger.info(f"Transcribing {audio_path} with Granite 4.0 (language: {language})")
            
            # Load and preprocess audio
            audio, sr = librosa.load(audio_path, sr=self.sample_rate)
            
            # Process in chunks for long audio
            chunk_duration = 30  # seconds
            chunk_samples = chunk_duration * self.sample_rate
            
            transcriptions = []
            segments = []
            total_samples = len(audio)
            
            for i in range(0, total_samples, chunk_samples):
                chunk = audio[i:i + chunk_samples]
                
                # Pad if necessary
                if len(chunk) < chunk_samples:
                    chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
                
                # Run inference
                inputs = self._prepare_inputs(chunk, language)
                outputs = self.session.run(None, inputs)
                
                # Decode output
                text = self.tokenizer.decode(outputs[0][0], skip_special_tokens=True)
                transcriptions.append(text)
                
                # Calculate segment timestamps
                start_time_sec = i / self.sample_rate
                end_time_sec = min((i + chunk_samples) / self.sample_rate, total_samples / self.sample_rate)
                segments.append({
                    "start": start_time_sec,
                    "end": end_time_sec,
                    "text": text
                })
                
                logger.debug(f"Processed chunk {i//chunk_samples + 1}: {text[:50]}...")
            
            processing_time = time.time() - start_time
            audio_duration = total_samples / self.sample_rate
            
            result = {
                "text": " ".join(transcriptions),
                "segments": segments,
                "language": language,
                "duration": audio_duration,
                "processing_time": processing_time,
                "real_time_factor": processing_time / audio_duration if audio_duration > 0 else 0,
                "model": "granite-4.0-1b",
                "hardware": "cpu"
            }
            
            logger.info(f"Transcription completed in {processing_time:.2f}s (RTF: {result['real_time_factor']:.2f})")
            return result
            
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return {
                "error": str(e),
                "text": "",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": "granite-4.0-1b",
                "hardware": "cpu"
            }
    
    def translate(self, text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """
        Translate text using Granite 4.0.
        
        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            
        Returns:
            Dictionary with translation results
        """
        start_time = time.time()
        
        if not self.is_loaded:
            logger.warning("Granite 4.0 not loaded, returning mock translation")
            return self._mock_translation(text, source_lang, target_lang)
        
        try:
            logger.info(f"Translating text from {source_lang} to {target_lang}")
            
            # Prepare input
            input_text = f"<|translate|> from {source_lang} to {target_lang}: {text}"
            inputs = self.tokenizer(input_text, return_tensors="np")
            
            # Run inference
            outputs = self.session.run(None, {
                "input_ids": inputs["input_ids"].astype(np.int64),
                "attention_mask": inputs["attention_mask"].astype(np.int64)
            })
            
            # Decode output
            translated = self.tokenizer.decode(outputs[0][0], skip_special_tokens=True)
            
            processing_time = time.time() - start_time
            
            result = {
                "original_text": text,
                "translated_text": translated,
                "source_language": source_lang,
                "target_language": target_lang,
                "processing_time": processing_time,
                "model": "granite-4.0-1b",
                "hardware": "cpu"
            }
            
            logger.info(f"Translation completed in {processing_time:.2f}s")
            return result
            
        except Exception as e:
            logger.error(f"Error during translation: {e}")
            return {
                "error": str(e),
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": "granite-4.0-1b",
                "hardware": "cpu"
            }
    
    def _prepare_inputs(self, audio: np.ndarray, language: str) -> dict:
        """
        Prepare audio input for model inference.
        
        Args:
            audio: Audio samples as numpy array
            language: Language code
            
        Returns:
            Dictionary of model inputs
        """
        # Convert to mel spectrogram
        mel = librosa.feature.melspectrogram(
            y=audio,
            sr=self.sample_rate,
            n_mels=128,
            hop_length=160,
            n_fft=400
        )
        mel = librosa.power_to_db(mel, ref=np.max)
        
        # Normalize
        mel = (mel - mel.mean()) / (mel.std() + 1e-8)
        
        # Add batch dimension
        mel = np.expand_dims(mel, 0).astype(np.float32)
        
        # Prepare language token
        lang_token = self.tokenizer.encode(f"<|{language}|>", add_special_tokens=False)[0]
        lang_tokens = np.array([[lang_token]], dtype=np.int64)
        
        return {
            "input_features": mel,
            "decoder_input_ids": lang_tokens
        }
    
    def _mock_transcription(self, audio_path: str, language: str) -> Dict[str, Any]:
        """Return mock transcription for development/testing."""
        # Get file info for mock duration
        try:
            import soundfile as sf
            with sf.SoundFile(audio_path) as f:
                duration = len(f) / f.samplerate
        except:
            duration = 10.0  # Default 10 seconds
        
        mock_text = f"This is a mock transcription of the audio file {os.path.basename(audio_path)}. "
        mock_text += "In a real implementation, this would contain the actual transcribed text from the Granite 4.0 model. "
        mock_text += f"The detected language is {language}."
        
        return {
            "text": mock_text,
            "segments": [{
                "start": 0.0,
                "end": duration,
                "text": mock_text
            }],
            "language": language,
            "duration": duration,
            "processing_time": duration * 0.5,  # Mock 0.5x real-time
            "real_time_factor": 0.5,
            "model": "granite-4.0-1b-mock",
            "hardware": "cpu"
        }
    
    def _mock_translation(self, text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """Return mock translation for development/testing."""
        # Simple mock translation
        mock_translated = f"[Translated from {source_lang} to {target_lang}] {text}"
        
        return {
            "original_text": text,
            "translated_text": mock_translated,
            "source_language": source_lang,
            "target_language": target_lang,
            "processing_time": 0.1,  # Fast mock
            "model": "granite-4.0-1b-mock",
            "hardware": "cpu"
        }

# Factory function for easy instantiation
def create_granite_transcriber(model_path: str = None) -> Granite4Transcriber:
    """
    Factory function to create a Granite4Transcriber instance.
    
    Args:
        model_path: Optional path to model directory
        
    Returns:
        Granite4Transcriber instance
    """
    if model_path is None:
        # Use default path from environment or relative path
        model_path = os.environ.get("GRANITE_MODEL_PATH", "models/granite-4.0-1b-speech-onnx")
    
    return Granite4Transcriber(model_path)

# Health check function
def granite_health_check() -> Dict[str, Any]:
    """Check if Granite transcriber is working correctly."""
    transcriber = Granite4Transcriber()
    return {
        "status": "healthy" if transcriber.is_available() else "degraded",
        "module": "granite_transcriber",
        "model_loaded": transcriber.is_available(),
        "model_path": str(transcriber.model_path),
        "timestamp": time.time()
    }
