#!/usr/bin/env python3
"""
Granite 4.0 1B Speech transcriber.
CPU-based batch transcription using the Hugging Face transformers runtime.
"""

import io
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import librosa
import numpy as np
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoModelForTokenClassification, AutoProcessor, AutoTokenizer
import av

logger = logging.getLogger(__name__)

WORKER_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIRNAME = "granite-4.0-1b-speech"
DEFAULT_MODEL_ID = "ibm-granite/granite-4.0-1b-speech"
DEFAULT_TRANSCRIBE_PROMPT = "can you transcribe the speech into a written format?"

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ja": "Japanese",
}
PUBLIC_MODEL_NAME = "granite-4.0-1b"
ENGLISH_PUNCT_MODEL_ID = os.environ.get("ENGLISH_PUNCT_MODEL_ID", "pcs_en")
MULTILINGUAL_PUNCT_MODEL_ID = os.environ.get(
    "MULTILINGUAL_PUNCT_MODEL_ID",
    "oliverguhr/fullstop-punctuation-multilingual-base",
)
MULTILINGUAL_PUNCT_LABELS = {
    0: "",
    1: ".",
    2: ",",
    3: "?",
    4: "-",
    5: ":",
}
MULTILINGUAL_PUNCT_LANGS = {"de", "fr", "es", "pt"}


def _normalise_language_code(language: Optional[str]) -> str:
    if not language:
        return ""
    return str(language).strip().lower().replace("_", "-").split("-", 1)[0]


def _resolve_model_path(model_path: Optional[str] = None) -> Path:
    """Resolve the preferred local Granite model path across local and container layouts."""
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
        if (candidate / "config.json").exists():
            return candidate

    return candidate_paths[0]


class Granite4Transcriber:
    """Granite 4.0 1B Speech transcriber for CPU-based batch processing."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = _resolve_model_path(model_path)
        self.model_ref = str(self.model_path) if self.model_path.exists() else DEFAULT_MODEL_ID
        self.sample_rate = 16000
        self.max_new_tokens = int(os.environ.get("GRANITE_MAX_NEW_TOKENS", "131072"))
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor: Any = None
        self.tokenizer: Any = None
        self.model: Any = None
        self.is_loaded = False
        self.active_backend = "unavailable"
        self.load_error: Optional[str] = None
        self.english_punctuator: Any = None
        self.multilingual_punct_tokenizer: Any = None
        self.multilingual_punct_model: Any = None
        self.multilingual_punct_max_length = 512

        self._load_model()

    def _select_torch_dtype(self, torch_module: Any) -> Any:
        if self.device == "cuda" and hasattr(torch_module, "bfloat16"):
            return torch_module.bfloat16
        return torch_module.float32

    def _load_model(self):
        """Load the Granite processor and model via transformers."""
        try:
            logger.info("Loading Granite 4.0 speech model from %s", self.model_ref)

            self.processor = AutoProcessor.from_pretrained(self.model_ref)
            self.tokenizer = self.processor.tokenizer
            self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.model_ref,
                torch_dtype=self._select_torch_dtype(torch),
            )
            self.model.to(self.device)
            self.model.eval()

            self.is_loaded = True
            self.active_backend = "transformers"
            self.load_error = None
            logger.info("Granite 4.0 transformers runtime initialized successfully on %s", self.device)
        except Exception as exc:
            logger.exception("Failed to load Granite 4.0 speech model")
            self.is_loaded = False
            self.active_backend = "unavailable"
            self.load_error = str(exc)

    def is_available(self) -> bool:
        """Check if the Granite backend is available and loaded."""
        return self.is_loaded

    def transcribe(self, audio_source: "str | bytes", language: str = "en", punctuation_pass: bool = False, source_language: Optional[str] = None, target_language: Optional[str] = None) -> Dict[str, Any]:
        """Transcribe audio using Granite 4.0 on CPU."""
        start_time = time.time()

        if not self.is_loaded:
            logger.error("Granite 4.0 not available for transcription: %s", self.load_error or "unknown error")
            return {
                "error": self.load_error or "Granite 4.0 transcription backend is unavailable",
                "text": "",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": PUBLIC_MODEL_NAME,
                "hardware": "cpu",
            }

        try:
            label = f"<{len(audio_source)} bytes>" if isinstance(audio_source, (bytes, bytearray)) else audio_source
            logger.info("Transcribing %s with Granite 4.0 (language: %s)", label, language)

            audio = self._decode_audio_to_array(audio_source)
            logger.info("Audio converted to array with shape %s", audio.shape)
            output_text = self._run_transcription(audio, source_language=source_language, target_language=target_language)
            logger.info("Raw transcription result: %s", output_text)
            if punctuation_pass and output_text:
                output_text = self._apply_punctuation(output_text, language)
                logger.info("Punctuated transcription result: %s", output_text)

            processing_time = time.time() - start_time
            duration = len(audio) / self.sample_rate
            result = {
                "text": output_text,
                "segments": [{"start": 0.0, "end": duration, "text": output_text}],
                "language": language,
                "duration": duration,
                "processing_time": processing_time,
                "real_time_factor": processing_time / duration if duration > 0 else 0,
                "model": PUBLIC_MODEL_NAME,
                "hardware": "cpu",
            }

            logger.info(
                "Transcription completed in %.2fs (RTF: %.2f)",
                processing_time,
                result["real_time_factor"],
            )
            return result
        except Exception as exc:
            logger.exception("Error during Granite transcription")
            return {
                "error": str(exc),
                "text": "",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": PUBLIC_MODEL_NAME,
                "hardware": "cpu",
            }

    def translate(self, text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """Translate text using Granite 4.0's LLM backbone (text-only, no audio token)."""
        start_time = time.time()

        if not self.is_loaded:
            logger.error("Granite 4.0 not available for translation: %s", self.load_error or "unknown error")
            return {
                "error": self.load_error or "Granite 4.0 translation backend is unavailable",
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": PUBLIC_MODEL_NAME,
                "hardware": "cpu",
            }

        try:
            logger.info("Translating from %s to %s with Granite 4.0", source_lang, target_lang)

            chat = [
                {
                    "role": "user",
                    "content": f"Translate the following text from {source_lang} to {target_lang}:\n\n{text}",
                }
            ]
            prompt = self.tokenizer.apply_chat_template(
                chat,
                tokenize=False,
                add_generation_prompt=True,
            )

            with torch.inference_mode():
                model_inputs = self.tokenizer(prompt, return_tensors="pt")
                model_inputs = {name: tensor.to(self.device) for name, tensor in model_inputs.items()}
                model_outputs = self.model.generate(
                    **model_inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                    num_beams=1,
                )

            num_input_tokens = model_inputs["input_ids"].shape[-1]
            new_tokens = model_outputs[:, num_input_tokens:]
            decoded = self.tokenizer.batch_decode(
                new_tokens,
                add_special_tokens=False,
                skip_special_tokens=True,
            )
            translated_text = decoded[0].strip() if decoded else ""

            processing_time = time.time() - start_time
            logger.info("Translation completed in %.2fs", processing_time)

            return {
                "original_text": text,
                "translated_text": translated_text,
                "source_language": source_lang,
                "target_language": target_lang,
                "processing_time": processing_time,
                "model": PUBLIC_MODEL_NAME,
                "hardware": "cpu",
            }
        except Exception as exc:
            logger.exception("Error during Granite translation")
            return {
                "error": str(exc),
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": PUBLIC_MODEL_NAME,
                "hardware": "cpu",
            }

    def _run_transcription(self, audio: np.ndarray, source_language: Optional[str] = None, target_language: Optional[str] = None) -> str:
        prompt = self._build_prompt(source_language=source_language, target_language=target_language)

        with torch.inference_mode():
            model_inputs = self.processor(
                prompt,
                audio,
                sampling_rate=self.sample_rate,
                return_tensors="pt",
            )
            model_inputs = {name: tensor.to(self.device) for name, tensor in model_inputs.items()}
            model_outputs = self.model.generate(
                **model_inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                num_beams=1,
            )

        num_input_tokens = model_inputs["input_ids"].shape[-1]
        new_tokens = model_outputs[:, num_input_tokens:]
        decoded = self.tokenizer.batch_decode(
            new_tokens,
            add_special_tokens=False,
            skip_special_tokens=True,
        )
        return decoded[0].strip() if decoded else ""

    def _apply_punctuation(self, text: str, language: Optional[str]) -> str:
        """Apply language-specific punctuation restoration when supported."""
        cleaned_text = " ".join(text.split())
        if not cleaned_text:
            return text

        normalised_language = _normalise_language_code(language)
        try:
            if normalised_language == "en":
                return self._apply_english_punctuation(cleaned_text)
            if normalised_language in MULTILINGUAL_PUNCT_LANGS:
                return self._apply_multilingual_punctuation(cleaned_text)
        except Exception:
            logger.exception(
                "Punctuation pass failed for language=%s; returning original text",
                normalised_language or "unknown",
            )
        return cleaned_text

    def _load_english_punctuator(self) -> Any:
        if self.english_punctuator is not None:
            return self.english_punctuator

        from punctuators.models import PunctCapSegModelONNX

        logger.info("Loading English punctuator model %s", ENGLISH_PUNCT_MODEL_ID)
        self.english_punctuator = PunctCapSegModelONNX.from_pretrained(ENGLISH_PUNCT_MODEL_ID)
        return self.english_punctuator

    def _apply_english_punctuation(self, text: str) -> str:
        punctuator = self._load_english_punctuator()
        outputs = punctuator.infer([text])
        if not outputs or not outputs[0]:
            return text
        punctuated = " ".join(segment.strip() for segment in outputs[0] if segment and segment.strip()).strip()
        return punctuated or text

    def _load_multilingual_punctuator(self) -> tuple[Any, Any]:
        if self.multilingual_punct_tokenizer is not None and self.multilingual_punct_model is not None:
            return self.multilingual_punct_tokenizer, self.multilingual_punct_model

        logger.info("Loading multilingual punctuator model %s", MULTILINGUAL_PUNCT_MODEL_ID)
        self.multilingual_punct_tokenizer = AutoTokenizer.from_pretrained(MULTILINGUAL_PUNCT_MODEL_ID)
        self.multilingual_punct_model = AutoModelForTokenClassification.from_pretrained(MULTILINGUAL_PUNCT_MODEL_ID)
        self.multilingual_punct_model.to(self.device)
        self.multilingual_punct_model.eval()
        self.multilingual_punct_max_length = min(
            getattr(self.multilingual_punct_tokenizer, "model_max_length", 512),
            512,
        )
        return self.multilingual_punct_tokenizer, self.multilingual_punct_model

    def _apply_multilingual_punctuation(self, text: str) -> str:
        tokenizer, model = self._load_multilingual_punctuator()
        chunks = self._chunk_text_for_multilingual_punctuation(text, tokenizer)
        punctuated_chunks = []
        for chunk in chunks:
            inputs = tokenizer(
                chunk,
                return_tensors="pt",
                truncation=True,
                max_length=self.multilingual_punct_max_length,
            )
            input_ids = inputs["input_ids"].to(self.device)
            attention_mask = inputs["attention_mask"].to(self.device)
            with torch.inference_mode():
                logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
            pred_ids = logits.argmax(dim=-1)[0].tolist()
            tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"][0].tolist())
            punctuated_chunk = self._reconstruct_multilingual_text(tokens, pred_ids)
            punctuated_chunks.append(punctuated_chunk)
        return " ".join(chunk for chunk in punctuated_chunks if chunk).strip() or text

    def _chunk_text_for_multilingual_punctuation(self, text: str, tokenizer: Any) -> list[str]:
        words = text.split()
        if not words:
            return []

        chunks: list[str] = []
        current_words: list[str] = []
        max_length = self.multilingual_punct_max_length

        for word in words:
            candidate_words = current_words + [word]
            candidate_text = " ".join(candidate_words)
            token_count = len(tokenizer(candidate_text, truncation=False)["input_ids"])
            if token_count <= max_length or not current_words:
                current_words = candidate_words
                continue

            chunks.append(" ".join(current_words))
            current_words = [word]

        if current_words:
            chunks.append(" ".join(current_words))
        return chunks

    def _reconstruct_multilingual_text(self, tokens: list[str], pred_ids: list[int]) -> str:
        pieces: list[str] = []
        special_tokens = {"<s>", "</s>", "<pad>"}

        for token, pred_id in zip(tokens, pred_ids):
            if token in special_tokens:
                continue

            if token.startswith("▁"):
                if pieces:
                    pieces.append(" ")
                pieces.append(token[1:])
            else:
                pieces.append(token)

            punctuation = MULTILINGUAL_PUNCT_LABELS.get(pred_id, "")
            if punctuation:
                pieces.append(punctuation)

        return "".join(pieces).strip()

    def _build_prompt(self, source_language: Optional[str] = None, target_language: Optional[str] = None) -> str:
        if source_language and target_language and source_language.lower() != target_language.lower():
            src_name = LANGUAGE_NAMES.get(source_language.lower(), source_language)
            tgt_name = LANGUAGE_NAMES.get(target_language.lower(), target_language)
            content = f"<|audio|>translate from {src_name} to {tgt_name}"
        else:
            content = f"<|audio|>{DEFAULT_TRANSCRIBE_PROMPT}"
        chat = [
            {
                "role": "user",
                "content": content,
            }
        ]
        return self.tokenizer.apply_chat_template(
            chat,
            tokenize=False,
            add_generation_prompt=True,
        )

    def _decode_audio_to_array(self, source: "str | bytes | io.IOBase") -> np.ndarray:
        """
        Decode any audio format to a 16 kHz mono float32 numpy array in memory.
        Uses PyAV first and falls back to librosa when necessary.
        """
        resampler = av.AudioResampler(
            format="fltp",
            layout="mono",
            rate=self.sample_rate,
        )

        if isinstance(source, (bytes, bytearray)):
            av_input = io.BytesIO(source)
            label = f"<{len(source)} bytes>"
        elif isinstance(source, io.IOBase):
            av_input = source
            label = repr(source)
        else:
            av_input = source
            label = source

        file_size = len(source) if isinstance(source, (bytes, bytearray)) else (
            os.path.getsize(source) if isinstance(source, str) and os.path.exists(source) else -1
        )
        logger.debug("Decoding '%s' (%d bytes) with PyAV", label, file_size)

        chunks: list[np.ndarray] = []
        try:
            with av.open(av_input) as container:
                for frame in container.decode(audio=0):
                    for out_frame in resampler.resample(frame):
                        chunks.append(out_frame.to_ndarray()[0])
            for out_frame in resampler.resample(None):
                chunks.append(out_frame.to_ndarray()[0])
        except Exception as av_exc:
            logger.warning(
                "PyAV failed on '%s' (%d bytes): %s; falling back to librosa",
                label,
                file_size,
                av_exc,
            )
            try:
                if isinstance(source, (bytes, bytearray)):
                    audio_data, _ = librosa.load(io.BytesIO(source), sr=self.sample_rate, mono=True)
                else:
                    audio_data, _ = librosa.load(source, sr=self.sample_rate, mono=True)
                return audio_data.astype(np.float32)
            except Exception as librosa_exc:
                raise RuntimeError(
                    f"All decoders failed for '{label}': PyAV={av_exc!r}, librosa={librosa_exc!r}"
                ) from librosa_exc

        if not chunks:
            raise RuntimeError(f"No audio frames decoded from '{label}'")

        audio_data = np.concatenate(chunks).astype(np.float32)
        logger.debug("Decoded '%s' -> %d samples @ %d Hz", label, len(audio_data), self.sample_rate)
        return audio_data


def create_granite_transcriber(model_path: Optional[str] = None) -> Granite4Transcriber:
    """Factory function to create a Granite4Transcriber instance."""
    return Granite4Transcriber(model_path)


def granite_health_check() -> Dict[str, Any]:
    """Check if Granite transcriber is working correctly."""
    transcriber = Granite4Transcriber()
    return {
        "status": "healthy" if transcriber.is_available() else "degraded",
        "module": "granite_transcriber",
        "model_loaded": transcriber.is_available(),
        "model_path": str(transcriber.model_path),
        "model_ref": transcriber.model_ref,
        "active_backend": transcriber.active_backend,
        "load_error": transcriber.load_error,
        "timestamp": time.time(),
    }
