#!/usr/bin/env python3
"""
Granite 4.0 1B Speech ONNX Transcriber
CPU-based batch transcription and translation using Granite 4.0 model
"""

import os
import json
import subprocess
import numpy as np
import librosa
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import time

logger = logging.getLogger(__name__)

WORKER_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIRNAME = "granite-4.0-1b-speech-onnx"


def _resolve_model_path(model_path: Optional[str] = None) -> Path:
    """Resolve the Granite model path across local and container layouts."""
    if model_path:
        return Path(model_path)

    env_model_path = os.environ.get("GRANITE_MODEL_PATH")
    if env_model_path:
        return Path(env_model_path)

    candidate_paths = [
        WORKER_DIR / "models" / DEFAULT_MODEL_DIRNAME,
        Path("/models") / DEFAULT_MODEL_DIRNAME,
        WORKER_DIR.parent / "models" / DEFAULT_MODEL_DIRNAME,
    ]

    for candidate in candidate_paths:
        if (candidate / "onnx" / "decoder_model_merged.onnx").exists():
            return candidate

    return candidate_paths[0]

class Granite4Transcriber:
    """
    Granite 4.0 1B Speech ONNX transcriber for CPU-based batch processing.
    Handles both transcription and translation tasks.
    """
    
    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize the Granite 4.0 transcriber.
        
        Args:
            model_path: Path to the ONNX model directory
        """
        self.model_path = _resolve_model_path(model_path)
        self.onnx_dir = self.model_path / "onnx"
        self.session: Any = None
        self.audio_encoder_session: Any = None
        self.embed_tokens_session: Any = None
        self.decoder_session: Any = None
        self.tokenizer: Any = None
        self.model_config: Dict[str, Any] = {}
        self.generation_config: Dict[str, Any] = {}
        self.audio_token_index = 100352
        self.num_layers = 40
        self.num_kv_heads = 4
        self.head_dim = 128
        self.max_new_tokens = int(os.environ.get("GRANITE_MAX_NEW_TOKENS", "256"))
        self.sample_rate = 16000
        self.is_loaded = False
        self.active_backend = "mock"
        self.load_error: Optional[str] = None
        
        # Try to load the model
        self._load_model()
    
    def _download_model(self):
        """Download the Granite 4.0 1B Speech ONNX model from Hugging Face."""
        try:
            logger.info("Downloading Granite 4.0 1B Speech ONNX model from Hugging Face...")
            
            # Ensure the model directory exists
            self.model_path.mkdir(parents=True, exist_ok=True)
            
            # Download using hf CLI
            result = subprocess.run(
                [
                    "hf", "download",
                    "onnx-community/granite-4.0-1b-speech-ONNX",
                    "--local-dir", str(self.model_path)
                ],
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout for large downloads
            )
            
            if result.returncode != 0:
                logger.error(f"Failed to download model. stderr: {result.stderr}")
                return False
            
            logger.info("Model downloaded successfully")
            logger.info(f"Download output: {result.stdout}")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Model download timed out after 1 hour")
            return False
        except FileNotFoundError:
            logger.error("hf CLI not found. Install with: pip install hf")
            return False
        except Exception as e:
            logger.error(f"Failed to download model: {e}")
            return False
    
    def _load_model(self):
        """Load the ONNX model and tokenizer."""
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            logger.info(f"Loading Granite 4.0 model from {self.model_path}")
            
            # Check if model exists
            model_onnx_path = self.onnx_dir / "decoder_model_merged.onnx"
            if not model_onnx_path.exists():
                logger.warning(f"Model file not found at {model_onnx_path}")
                logger.info("Attempting to download model from Hugging Face...")
                
                # Try to download the model
                if not self._download_model():
                    logger.warning("Failed to download model. Granite 4.0 transcriber will operate in mock mode")
                    self.is_loaded = False
                    return
                
                # Check again after download
                if not model_onnx_path.exists():
                    logger.warning("Model file still not found after download attempt")
                    logger.warning("Granite 4.0 transcriber will operate in mock mode")
                    self.is_loaded = False
                    self.load_error = f"Granite model file not found: {model_onnx_path}"
                    return

            config_path = self.model_path / "config.json"
            if config_path.exists():
                self.model_config = json.loads(config_path.read_text(encoding="utf-8"))
                self.audio_token_index = int(self.model_config.get("audio_token_index", self.audio_token_index))
                text_cfg = self.model_config.get("text_config", {})
                self.num_layers = int(text_cfg.get("num_hidden_layers", self.num_layers))
                self.num_kv_heads = int(text_cfg.get("num_key_value_heads", self.num_kv_heads))
                hidden_size = int(text_cfg.get("hidden_size", self.num_kv_heads * self.head_dim))
                if self.num_kv_heads > 0:
                    self.head_dim = hidden_size // self.num_kv_heads

            gen_path = self.model_path / "generation_config.json"
            if gen_path.exists():
                self.generation_config = json.loads(gen_path.read_text(encoding="utf-8"))

            session_options = ort.SessionOptions()
            session_options.intra_op_num_threads = int(os.environ.get("ORT_INTRA_OP_THREADS", "4"))
            session_options.inter_op_num_threads = int(os.environ.get("ORT_INTER_OP_THREADS", "2"))

            providers = ["CPUExecutionProvider"]
            self.audio_encoder_session = ort.InferenceSession(
                str(self.onnx_dir / "audio_encoder.onnx"),
                sess_options=session_options,
                providers=providers,
            )
            self.embed_tokens_session = ort.InferenceSession(
                str(self.onnx_dir / "embed_tokens.onnx"),
                sess_options=session_options,
                providers=providers,
            )
            self.decoder_session = ort.InferenceSession(
                str(self.onnx_dir / "decoder_model_merged.onnx"),
                sess_options=session_options,
                providers=providers,
            )

            # Keep compatibility with older tests that reference self.session.
            self.session = self.decoder_session
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)

            self.is_loaded = True
            self.active_backend = "onnxruntime"
            self.load_error = None
            logger.info("Granite 4.0 ONNX runtime initialized successfully")
            
        except Exception as e:
            logger.exception("Failed to load Granite 4.0 model")
            logger.warning("Granite 4.0 transcriber will operate in mock mode")
            self.is_loaded = False
            self.active_backend = "unavailable"
            self.load_error = str(e)
    
    def is_available(self) -> bool:
        """Check if the Granite backend is available and loaded."""
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
            logger.error("Granite 4.0 not available for transcription: %s", self.load_error or "unknown error")
            return {
                "error": self.load_error or "Granite 4.0 transcription backend is unavailable",
                "text": "",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": "granite-4.0-1b",
                "hardware": "cpu",
            }
        
        try:
            logger.info(f"Transcribing {audio_path} with Granite 4.0 (language: {language})")

            audio = self._decode_audio_to_array(audio_path)
            inputs = self._prepare_inputs(audio)

            audio_features = self.audio_encoder_session.run(
                ["audio_features"],
                {"input_features": inputs["input_features"]},
            )[0].astype(np.float32)

            prompt_ids = self._build_prompt_input_ids(language)
            prompt_embeds = self.embed_tokens_session.run(
                ["inputs_embeds"],
                {"input_ids": prompt_ids},
            )[0].astype(np.float32)

            combined_embeds = self._merge_audio_into_prompt(prompt_ids, prompt_embeds, audio_features)
            generated_ids = self._generate_greedy(combined_embeds)

            decoded_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

            processing_time = time.time() - start_time
            duration = len(audio) / self.sample_rate
            result = {
                "text": decoded_text,
                "segments": [{"start": 0.0, "end": duration, "text": decoded_text}],
                "language": language,
                "duration": duration,
                "processing_time": processing_time,
                "real_time_factor": processing_time / duration if duration > 0 else 0,
                "model": "granite-4.0-1b",
                "hardware": "cpu",
            }

            logger.info(f"Transcription completed in {processing_time:.2f}s (RTF: {result['real_time_factor']:.2f})")
            return result
            
        except Exception as e:
            logger.exception("Error during Granite transcription")
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
        logger.warning("Granite ONNX translation path is not implemented yet")
        return {
            "error": "Granite ONNX translation path is not implemented yet",
            "original_text": text,
            "translated_text": "",
            "source_language": source_lang,
            "target_language": target_lang,
            "model": "granite-4.0-1b",
            "hardware": "cpu",
        }
    
    def _decode_audio_to_array(self, input_path: str) -> np.ndarray:
        """
        Decode any audio format to a 16 kHz mono float32 numpy array in memory
        using PyAV.  No intermediate files are written to disk.
        """
        import av

        resampler = av.AudioResampler(
            format="fltp",
            layout="mono",
            rate=self.sample_rate,
        )

        chunks: list[np.ndarray] = []
        try:
            with av.open(input_path) as container:
                for frame in container.decode(audio=0):
                    for out_frame in resampler.resample(frame):
                        chunks.append(out_frame.to_ndarray()[0])  # mono → shape (n,)
            # Flush resampler
            for out_frame in resampler.resample(None):
                chunks.append(out_frame.to_ndarray()[0])
        except Exception as exc:
            raise RuntimeError(
                f"PyAV could not decode audio from '{input_path}': {exc}"
            ) from exc

        if not chunks:
            raise RuntimeError(f"No audio frames decoded from '{input_path}'")

        audio_data = np.concatenate(chunks).astype(np.float32)
        logger.debug(
            "Decoded '%s' → %d samples @ %d Hz (in memory)",
            input_path, len(audio_data), self.sample_rate,
        )
        return audio_data

    def _prepare_inputs(self, audio: np.ndarray) -> dict:
        """
        Prepare audio input for model inference.
        
        Args:
            audio: Audio samples as numpy array
            
        Returns:
            Dictionary of model inputs
        """
        # Granite expects 160-dim features (80 log-mels + 80 deltas) per frame.
        mel = librosa.feature.melspectrogram(
            y=audio,
            sr=self.sample_rate,
            n_mels=80,
            hop_length=160,
            n_fft=512,
            win_length=400,
        )
        mel = librosa.power_to_db(mel, ref=np.max)
        delta = librosa.feature.delta(mel)
        features = np.concatenate([mel, delta], axis=0).T
        features = np.expand_dims(features, 0).astype(np.float32)
        
        return {
            "input_features": features,
        }

    def _build_prompt_input_ids(self, language: str) -> np.ndarray:
        messages = [
            {
                "role": "user",
                "content": "<|audio|>can you transcribe the speech into a written format?",
            }
        ]
        if hasattr(self.tokenizer, "apply_chat_template"):
            prompt = self.tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=False,
                tokenize=False,
            )
        else:
            prompt = "USER: <|audio|>can you transcribe the speech into a written format?\n ASSISTANT:"

        tokenized = self.tokenizer(prompt, return_tensors="np", add_special_tokens=False)
        return tokenized["input_ids"].astype(np.int64)

    def _merge_audio_into_prompt(
        self,
        prompt_ids: np.ndarray,
        prompt_embeds: np.ndarray,
        audio_features: np.ndarray,
    ) -> np.ndarray:
        audio_positions = np.where(prompt_ids[0] == self.audio_token_index)[0]
        if audio_positions.size == 0:
            raise RuntimeError("Audio token not found in Granite prompt")

        audio_idx = int(audio_positions[0])
        prefix = prompt_embeds[:, :audio_idx, :]
        suffix = prompt_embeds[:, audio_idx + 1 :, :]
        return np.concatenate([prefix, audio_features, suffix], axis=1).astype(np.float32)

    def _initial_past_key_values(self) -> Dict[str, np.ndarray]:
        empty = {}
        shape = (1, self.num_kv_heads, 0, self.head_dim)
        for layer in range(self.num_layers):
            empty[f"past_key_values.{layer}.key"] = np.zeros(shape, dtype=np.float32)
            empty[f"past_key_values.{layer}.value"] = np.zeros(shape, dtype=np.float32)
        return empty

    def _present_to_past(self, outputs: list[Any]) -> Dict[str, np.ndarray]:
        past = {}
        offset = 1
        for layer in range(self.num_layers):
            past[f"past_key_values.{layer}.key"] = outputs[offset + (2 * layer)]
            past[f"past_key_values.{layer}.value"] = outputs[offset + (2 * layer) + 1]
        return past

    def _generate_greedy(self, initial_embeds: np.ndarray) -> list[int]:
        eos_token_id = int(self.generation_config.get("eos_token_id", 100257))
        attention_mask = np.ones((1, initial_embeds.shape[1]), dtype=np.int64)
        past = self._initial_past_key_values()

        outputs = self.decoder_session.run(
            None,
            {
                "inputs_embeds": initial_embeds,
                "attention_mask": attention_mask,
                **past,
            },
        )
        logits = outputs[0]
        generated: list[int] = []
        next_id = int(np.argmax(logits[0, -1, :]))
        generated.append(next_id)
        past = self._present_to_past(outputs)

        for _ in range(self.max_new_tokens - 1):
            if next_id == eos_token_id:
                break

            next_token = np.array([[next_id]], dtype=np.int64)
            token_embed = self.embed_tokens_session.run(
                ["inputs_embeds"],
                {"input_ids": next_token},
            )[0].astype(np.float32)

            seq_len = past["past_key_values.0.key"].shape[2] + 1
            attention_mask = np.ones((1, seq_len), dtype=np.int64)

            outputs = self.decoder_session.run(
                None,
                {
                    "inputs_embeds": token_embed,
                    "attention_mask": attention_mask,
                    **past,
                },
            )

            logits = outputs[0]
            next_id = int(np.argmax(logits[0, -1, :]))
            generated.append(next_id)
            past = self._present_to_past(outputs)

        return generated
    
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
def create_granite_transcriber(model_path: Optional[str] = None) -> Granite4Transcriber:
    """
    Factory function to create a Granite4Transcriber instance.
    
    Args:
        model_path: Optional path to model directory
        
    Returns:
        Granite4Transcriber instance
    """
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
        "active_backend": transcriber.active_backend,
        "load_error": transcriber.load_error,
        "timestamp": time.time()
    }
