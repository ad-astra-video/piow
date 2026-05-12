#!/usr/bin/env python3
"""
Session Management Endpoints
Handles user sessions, transcription sessions, and streaming sessions.

Uses a write-through cache pattern:
- Writes go to Supabase first, then update the in-memory cache
- Reads hit the in-memory cache first, falling back to Supabase on miss
- This provides persistence across backend restarts while keeping hot-path reads fast
"""

import asyncio
import contextlib
from aiohttp import web
import aiohttp
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Awaitable, Callable
import time
from typing import Any as TypingAny

import supabase_client as _supabase_client
from payments.payment_strategy import x402_or_subscription

logger = logging.getLogger(__name__)

STREAM_USAGE_POLL_SECONDS = 60
STREAM_STATUS_TIMEOUT_SECONDS = 10

_stream_usage_monitor_task: Optional[asyncio.Task] = None
_stream_usage_billed_minute: Dict[str, int] = {}


def _resolve_supabase_client():
    try:
        return getattr(_supabase_client, "async_supabase")
    except Exception:
        return getattr(_supabase_client, "supabase")


class _SupabaseProxy:
    def __getattr__(self, name: str):
        return getattr(_resolve_supabase_client(), name)


supabase = _SupabaseProxy()


def _build_compute_provider_manager():
    """Build a provider manager with the configured provider definitions."""
    from compute_providers.provider_manager import ComputeProviderManager
    from compute_providers.provider_definitions import PROVIDER_DEFINITIONS

    provider_manager = ComputeProviderManager()
    provider_manager.register_providers_from_definitions(PROVIDER_DEFINITIONS)
    return provider_manager


class SessionStore:
    """
    Database-backed session store with in-memory cache.

    All write operations persist to Supabase tables:
    - user_sessions: user session tracking
    - stream_sessions: live streaming session data
    - transcription_sessions: batch transcription job tracking

    The in-memory cache provides fast reads for hot-path operations
    (e.g., WebSocket relay looking up stream data_url).
    """

    def __init__(self):
        # In-memory cache layers
        self._sessions_cache: Dict[str, Dict[str, Any]] = {}  # session_id -> session_data
        self._transcriptions_cache: Dict[str, Dict[str, Any]] = {}  # transcription_id -> data
        self._stream_sessions_cache: Dict[str, Dict[str, Any]] = {}  # stream_id -> data

    def _build_session_data(self, session_id: str, user_id: str, now: Optional[float] = None) -> Dict[str, Any]:
        """Build in-memory session data with defaults."""
        ts = now if now is not None else time.time()
        return {
            "id": session_id,
            "user_id": user_id,
            "created_at": ts,
            "last_activity": ts,
            "transcriptions": [],
            "stream_sessions": [],
            "settings": {
                "default_language": "en",
                "translate_to": []
            }
        }

    def _coerce_timestamp(self, value: Any) -> float:
        """Convert supported timestamp types into epoch seconds."""
        if value is None:
            return time.time()
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return time.time()
        return time.time()

    def _extract_stream_text(self, stream_data: Dict[str, Any], final_text: str) -> str:
        """Build best-effort text for usage word counts from final text or segments."""
        text = (final_text or "").strip()
        if text:
            return text

        cached_final = (stream_data.get("final_text") or "").strip()
        if cached_final:
            return cached_final

        segments = stream_data.get("transcription_segments") or []
        collected: List[str] = []
        for segment in segments:
            if isinstance(segment, str):
                segment_text = segment.strip()
            elif isinstance(segment, dict):
                segment_text = str(segment.get("text") or segment.get("transcript") or "").strip()
            else:
                segment_text = ""
            if segment_text:
                collected.append(segment_text)

        return " ".join(collected).strip()

    async def _record_stream_usage(self, stream_data: Dict[str, Any], duration_seconds: int, final_text: str = "") -> bool:
        """Persist a transcription_usage row for a live stream interval."""
        if not stream_data:
            return False

        if duration_seconds <= 0:
            return False

        session_id = stream_data.get("session_id")
        if not session_id:
            logger.warning("Skipping stream usage log: stream has no parent session")
            return False

        parent_session = await self.get_session(session_id)
        user_id = str(parent_session.get("user_id")) if parent_session and parent_session.get("user_id") else None
        if not user_id:
            logger.warning("Skipping stream usage log: missing user_id for stream %s", stream_data.get("id"))
            return False

        text = self._extract_stream_text(stream_data, final_text)
        word_count = len(text.split()) if text else 0

        provider_session = stream_data.get("provider_session") or {}
        model = provider_session.get("model") or "voxtral-realtime"
        hardware = provider_session.get("hardware") or "gpu"
        source_language = stream_data.get("language") or "en"

        try:
            await supabase.table("transcription_usage").insert({
                "user_id": user_id,
                "duration_seconds": duration_seconds,
                "word_count": word_count,
                "source_language": source_language,
                "model": model,
                "hardware": hardware,
                "source_type": "stream",
            }).execute()
            return True
        except Exception as e:
            logger.warning("Failed to record stream usage for %s: %s", stream_data.get("id"), e)
            return False

    # ------------------------------------------------------------------
    # User Sessions
    # ------------------------------------------------------------------

    async def create_session(self, user_id: str) -> str:
        """Create a new user session. Persists to Supabase."""
        session_id = str(uuid.uuid4())
        session_data = self._build_session_data(session_id=session_id, user_id=user_id)

        try:
            await supabase.table("user_sessions").insert({
                "id": session_id,
                "user_id": user_id,
                "settings": session_data["settings"],
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to persist session to Supabase, using cache only: {e}")

        # Update cache
        self._sessions_cache[session_id] = session_data
        logger.info(f"Created session {session_id}")
        return session_id

    async def ensure_session(self, session_id: str, user_id: Optional[str]) -> bool:
        """
        Ensure a user session exists for the given session_id.

        Returns True when the session exists or is created.
        Returns False if the session does not exist and user_id is unavailable.
        Raises ValueError if session_id already exists but belongs to another user.
        """
        existing_session = await self.get_session(session_id)
        if existing_session:
            existing_user_id = existing_session.get("user_id")
            if user_id and existing_user_id and str(existing_user_id) != str(user_id):
                raise ValueError(
                    f"Session {session_id} belongs to a different user"
                )
            return True

        if not user_id:
            logger.warning(
                "Cannot ensure missing user session without user_id: session_id=%s",
                session_id,
            )
            return False

        session_data = self._build_session_data(session_id=session_id, user_id=user_id)
        try:
            await supabase.table("user_sessions").insert({
                "id": session_id,
                "user_id": user_id,
                "settings": session_data["settings"],
            }).execute()
            self._sessions_cache[session_id] = session_data
            logger.info("Ensured user session exists: session_id=%s", session_id)
            return True
        except Exception as e:
            # Handle a concurrent insert race by re-reading the row.
            if "23505" in str(e):
                refreshed = await self.get_session(session_id)
                if refreshed:
                    existing_user_id = refreshed.get("user_id")
                    if user_id and existing_user_id and str(existing_user_id) != str(user_id):
                        raise ValueError(
                            f"Session {session_id} belongs to a different user"
                        )
                    return True
            raise

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID. Checks cache first, then Supabase."""
        # Cache hit
        if session_id in self._sessions_cache:
            return self._sessions_cache[session_id]

        # Cache miss G�� try Supabase
        try:
            result = await supabase.table("user_sessions").select("*").eq("id", session_id).execute()
            if result.data:
                row = result.data[0]
                session_data = self._row_to_session(row)
                self._sessions_cache[session_id] = session_data
                return session_data
        except Exception as e:
            logger.warning(f"Failed to load session from Supabase: {e}")

        return None

    async def update_session_activity(self, session_id: str):
        """Update session last activity time."""
        if session_id in self._sessions_cache:
            self._sessions_cache[session_id]["last_activity"] = time.time()

        # Fire-and-forget Supabase update
        try:
            await supabase.table("user_sessions").update({
                "last_activity": "now()"
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update session activity in Supabase: {e}")

    async def add_transcription_to_session(self, session_id: str, transcription_id: str):
        """Add transcription to session."""
        if session_id in self._sessions_cache:
            self._sessions_cache[session_id]["transcriptions"].append(transcription_id)
            self._sessions_cache[session_id]["last_activity"] = time.time()

        # Persist to Supabase by reading current array and writing the updated one
        try:
            transcription_ids = []
            if session_id in self._sessions_cache:
                transcription_ids = list(self._sessions_cache[session_id]["transcriptions"])
            else:
                result = await supabase.table("user_sessions").select("transcription_ids").eq("id", session_id).execute()
                if result.data:
                    transcription_ids = list(result.data[0].get("transcription_ids") or [])
                transcription_ids.append(transcription_id)

            await supabase.table("user_sessions").update({
                "transcription_ids": transcription_ids
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.warning(f"Failed to add transcription to session in Supabase: {e}")

    async def add_stream_to_session(self, session_id: str, stream_id: str):
        """Add stream session to session."""
        if session_id in self._sessions_cache:
            self._sessions_cache[session_id]["stream_sessions"].append(stream_id)
            self._sessions_cache[session_id]["last_activity"] = time.time()

        # Persist to Supabase by reading current array and writing the updated one
        try:
            stream_session_ids = []
            if session_id in self._sessions_cache:
                stream_session_ids = list(self._sessions_cache[session_id]["stream_sessions"])
            else:
                result = await supabase.table("user_sessions").select("stream_session_ids").eq("id", session_id).execute()
                if result.data:
                    stream_session_ids = list(result.data[0].get("stream_session_ids") or [])
                stream_session_ids.append(stream_id)

            await supabase.table("user_sessions").update({
                "stream_session_ids": stream_session_ids
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.warning(f"Failed to add stream to session in Supabase: {e}")

    # ------------------------------------------------------------------
    # Stream Sessions
    # ------------------------------------------------------------------

    async def create_stream_session(
        self,
        session_id: str,
        language: str,
        provider_session_data: Any,
        user_id: Optional[str] = None,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> str:
        """
        Create a new stream session with provider data.
        Persists to Supabase stream_sessions table.
        """
        stream_id = str(uuid.uuid4())
        now = time.time()
        effective_source_language = source_language or language
        provider_session_payload = dict(provider_session_data or {})
        metadata = provider_session_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update({
            "source_language": effective_source_language,
            "target_language": target_language,
        })
        provider_session_payload["metadata"] = metadata
        provider_session_payload.setdefault("source_language", effective_source_language)
        provider_session_payload.setdefault("target_language", target_language)
        stream_data = {
            "id": stream_id,
            "session_id": session_id,
            "language": language,
            "source_language": effective_source_language,
            "target_language": target_language,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "provider_session": provider_session_payload,
            "total_audio_bytes": 0,
            "transcription_segments": [],
            "text_timestamps": [],
        }

        try:
            await self.ensure_session(session_id=session_id, user_id=user_id)
        except ValueError:
            # Surface ownership mismatches to callers so they can return 403.
            raise
        except Exception as e:
            logger.warning(
                "Failed to ensure user session before stream creation: session_id=%s error=%s",
                session_id,
                e,
            )

        try:
            await supabase.table("stream_sessions").insert({
                "id": stream_id,
                "user_session_id": session_id,
                "language": language,
                "status": "active",
                "provider_session": provider_session_payload,
                "total_audio_bytes": 0,
                "transcription_segments": [],
                "text_timestamps": [],
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to persist stream session to Supabase, using cache only: {e}")

        # Update cache
        self._stream_sessions_cache[stream_id] = stream_data
        logger.info(f"Created stream session {stream_id} with provider {provider_session_payload.get('provider', 'unknown')}")
        return stream_id

    async def get_stream_session(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """Get stream session by ID. Checks cache first, then Supabase."""
        # Cache hit
        if stream_id in self._stream_sessions_cache:
            return self._stream_sessions_cache[stream_id]

        # Cache miss G�� try Supabase
        try:
            result = await supabase.table("stream_sessions").select("*").eq("id", stream_id).execute()
            if result.data:
                row = result.data[0]
                stream_data = self._row_to_stream_session(row)
                self._stream_sessions_cache[stream_id] = stream_data
                return stream_data
        except Exception as e:
            logger.warning(f"Failed to load stream session from Supabase: {e}")

        return None

    async def has_stream_session(self, stream_id: str) -> bool:
        """Check if a stream session exists."""
        if stream_id in self._stream_sessions_cache:
            return True
        # Try Supabase
        try:
            result = await supabase.table("stream_sessions").select("id").eq("id", stream_id).execute()
            return len(result.data) > 0
        except Exception as e:
            logger.warning(f"Failed to check stream session in Supabase: {e}")
            return False

    async def get_provider_urls(self, stream_id: str) -> Optional[Dict[str, str]]:
        """
        Get provider management URLs for a stream session.
        """
        session = await self.get_stream_session(stream_id)
        if session:
            provider_session = session.get("provider_session", {})
            return {
                "update_url": provider_session.get("update_url"),
                "stop_url": provider_session.get("stop_url"),
                "data_url": provider_session.get("data_url"),
                "whip_url": provider_session.get("whip_url"),
                "provider_stream_id": provider_session.get("provider_stream_id")
            }
        return None

    async def update_stream_session(self, stream_id: str, update_data: Dict[str, Any]):
        """Update stream session with new data."""
        now = time.time()
        segments_to_append = update_data.get("transcription_segment")
        timestamp_segment = update_data.get("timestamp_segment")
        audio_bytes = update_data.get("audio_bytes", 0)

        # Update cache
        if stream_id in self._stream_sessions_cache:
            self._stream_sessions_cache[stream_id]["updated_at"] = now
            if segments_to_append:
                self._stream_sessions_cache[stream_id]["transcription_segments"].append(
                    segments_to_append
                )
            if isinstance(timestamp_segment, dict):
                self._stream_sessions_cache[stream_id].setdefault("text_timestamps", []).append(
                    timestamp_segment
                )
            if audio_bytes:
                self._stream_sessions_cache[stream_id]["total_audio_bytes"] += audio_bytes

        # Persist to Supabase
        try:
            db_update = {"updated_at": "now()"}
            if audio_bytes:
                db_update["total_audio_bytes"] = self._stream_sessions_cache.get(stream_id, {}).get("total_audio_bytes", audio_bytes)
            if segments_to_append:
                db_update["transcription_segments"] = self._stream_sessions_cache.get(stream_id, {}).get("transcription_segments", [])
            if isinstance(timestamp_segment, dict):
                db_update["text_timestamps"] = self._stream_sessions_cache.get(stream_id, {}).get("text_timestamps", [])
            await supabase.table("stream_sessions").update(db_update).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update stream session in Supabase: {e}")

        if isinstance(timestamp_segment, dict):
            transcription_id = self._stream_sessions_cache.get(stream_id, {}).get("transcription_id")
            if transcription_id:
                try:
                    await supabase.table("transcriptions").update({
                        "segments": self._stream_sessions_cache.get(stream_id, {}).get("text_timestamps", []),
                    }).eq("id", transcription_id).execute()
                except Exception as e:
                    logger.warning(
                        "Failed to persist text_timestamps into transcriptions for stream %s: %s",
                        stream_id,
                        e,
                    )

    async def update_stream_translation_config(
        self,
        stream_id: str,
        source_language: str,
        target_language: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Persist live translation configuration on the stream session."""
        stream_session = self._stream_sessions_cache.get(stream_id) or await self.get_stream_session(stream_id)
        if not stream_session:
            return None

        now = time.time()
        provider_session = dict(stream_session.get("provider_session") or {})
        metadata = provider_session.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        metadata.update({
            "source_language": source_language,
            "target_language": target_language,
        })
        provider_session["metadata"] = metadata
        provider_session["source_language"] = source_language
        provider_session["target_language"] = target_language

        stream_session["source_language"] = source_language
        stream_session["target_language"] = target_language
        stream_session["provider_session"] = provider_session
        stream_session["updated_at"] = now
        self._stream_sessions_cache[stream_id] = stream_session

        try:
            await supabase.table("stream_sessions").update({
                "provider_session": provider_session,
                "updated_at": "now()",
            }).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning("Failed to persist stream translation config for %s: %s", stream_id, e)

        return stream_session

    async def close_stream_session(self, stream_id: str, final_text: str = ""):
        """Close a stream session."""
        now = time.time()

        # Update cache
        if stream_id in self._stream_sessions_cache:
            self._stream_sessions_cache[stream_id]["status"] = "completed"
            self._stream_sessions_cache[stream_id]["final_text"] = final_text
            self._stream_sessions_cache[stream_id]["updated_at"] = now

        # Persist to Supabase
        try:
            db_update = {
                "status": "completed",
                "updated_at": "now()",
            }
            if final_text:
                db_update["final_text"] = final_text
            await supabase.table("stream_sessions").update(db_update).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning(f"Failed to close stream session in Supabase: {e}")

        logger.info(f"Stream session {stream_id} closed")

    # ------------------------------------------------------------------
    # Live-stream transcription upsert
    # ------------------------------------------------------------------

    async def upsert_stream_transcription(self, stream_id: str, new_segments: List[str]) -> Optional[str]:
        """Create or incrementally update the transcriptions row for a live stream.

        Called by the SSE relay flush loop each time a batch of final segments
        is ready.  On the first call an in-progress row is inserted; subsequent
        calls append the new text and update word_count.

        The transcription_id is cached on the stream session so
        stop_stream_session can finalize the row without a DB lookup.

        Returns the transcription_id on success, None on failure.
        """
        if not new_segments:
            return (self._stream_sessions_cache.get(stream_id) or {}).get("transcription_id")

        # Load stream data (cache-first)
        stream_data = self._stream_sessions_cache.get(stream_id) or await self.get_stream_session(stream_id)
        if not stream_data:
            logger.warning("upsert_stream_transcription: stream %s not found", stream_id)
            return None

        # Resolve user_id via parent session (same pattern as _record_stream_usage)
        session_id = stream_data.get("session_id")
        user_id: Optional[str] = None
        if session_id:
            parent = await self.get_session(session_id)
            user_id = str(parent.get("user_id")) if parent and parent.get("user_id") else None
        if not user_id:
            logger.warning("upsert_stream_transcription: missing user_id for stream %s", stream_id)
            return None

        language = stream_data.get("language", "en")
        provider_session = stream_data.get("provider_session") or {}
        model = provider_session.get("model")
        hardware = provider_session.get("hardware")

        new_text = "\n".join(seg for seg in new_segments if seg)
        existing_id: Optional[str] = stream_data.get("transcription_id")
        existing_segments = stream_data.get("text_timestamps") or []

        if existing_id:
            # Append new text to the existing row
            try:
                rows = await supabase.table("transcriptions").select("text").eq("id", existing_id).execute()
                prev_text = rows.data[0].get("text", "") if rows.data else ""
                full_text = (prev_text + "\n" + new_text).strip()
                await supabase.table("transcriptions").update({
                    "text": full_text,
                    "word_count": len(full_text.split()),
                    "segments": existing_segments,
                }).eq("id", existing_id).execute()
                if stream_id in self._stream_sessions_cache:
                    self._stream_sessions_cache[stream_id]["_live_text"] = full_text
            except Exception as e:
                logger.warning("Failed to append to stream transcription %s: %s", existing_id, e)
            return existing_id

        # First flush G�� create the initial row
        try:
            word_count = len(new_text.split())
            insert_payload: Dict[str, Any] = {
                "user_id": user_id,
                "audio_url": f"stream://{stream_id}",
                "text": new_text,
                "language": language,
                "duration": 0,
                "word_count": word_count,
                "segments": existing_segments,
                "status": "processing",
                "source_type": "stream",
            }
            # Only include constrained columns when we have DB-valid values
            if model in ("granite-4.0-1b", "voxtral-realtime"):
                insert_payload["model_used"] = model
            if hardware in ("cpu", "gpu"):
                insert_payload["hardware"] = hardware

            rec = await supabase.table("transcriptions").insert(insert_payload).execute()
            transcription_id = rec.data[0]["id"] if rec.data else None
            if transcription_id:
                if stream_id in self._stream_sessions_cache:
                    self._stream_sessions_cache[stream_id]["transcription_id"] = transcription_id
                    self._stream_sessions_cache[stream_id]["_live_text"] = new_text
                logger.info(
                    "Created live transcription record: stream_id=%s transcription_id=%s",
                    stream_id,
                    transcription_id,
                )
            return transcription_id
        except Exception as e:
            logger.warning("Failed to create stream transcription for stream %s: %s", stream_id, e)
            return None

    # ------------------------------------------------------------------
    # Transcription Sessions
    # ------------------------------------------------------------------

    async def create_transcription(self, transcription_id: str, data: Dict[str, Any]):
        """Create a transcription session record. Persists to Supabase."""
        # Update cache
        self._transcriptions_cache[transcription_id] = data

        # Persist to Supabase
        try:
            await supabase.table("transcription_sessions").insert({
                "id": transcription_id,
                "user_session_id": data.get("session_id"),
                "filename": data.get("filename", "unknown"),
                "duration": data.get("duration", 0),
                "language": data.get("language", "en"),
                "status": data.get("status", "processing"),
                "result": data.get("result"),
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to persist transcription to Supabase, using cache only: {e}")

    async def get_transcription(self, transcription_id: str) -> Optional[Dict[str, Any]]:
        """Get transcription by ID. Checks cache first, then Supabase."""
        # Cache hit
        if transcription_id in self._transcriptions_cache:
            return self._transcriptions_cache[transcription_id]

        # Cache miss G�� try Supabase
        try:
            result = await supabase.table("transcription_sessions").select("*").eq("id", transcription_id).execute()
            if result.data:
                row = result.data[0]
                data = self._row_to_transcription(row)
                self._transcriptions_cache[transcription_id] = data
                return data
        except Exception as e:
            logger.warning(f"Failed to load transcription from Supabase: {e}")

        return None

    async def has_transcription(self, transcription_id: str) -> bool:
        """Check if a transcription exists."""
        if transcription_id in self._transcriptions_cache:
            return True
        try:
            result = await supabase.table("transcription_sessions").select("id").eq("id", transcription_id).execute()
            return len(result.data) > 0
        except Exception as e:
            logger.warning(f"Failed to check transcription in Supabase: {e}")
            return False

    async def update_transcription(self, transcription_id: str, updates: Dict[str, Any]):
        """Update transcription with new data."""
        now = time.time()

        # Update cache
        if transcription_id in self._transcriptions_cache:
            self._transcriptions_cache[transcription_id].update(updates)
            self._transcriptions_cache[transcription_id]["updated_at"] = now

        # Persist to Supabase
        try:
            db_update = {k: v for k, v in updates.items() if k in ("result", "status")}
            db_update["updated_at"] = "now()"
            await supabase.table("transcription_sessions").update(db_update).eq("id", transcription_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update transcription in Supabase: {e}")

    # ------------------------------------------------------------------
    # Row-to-dict converters (Supabase row G�� in-memory format)
    # ------------------------------------------------------------------

    def _row_to_session(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a Supabase user_sessions row to in-memory session format."""
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "created_at": row.get("created_at", time.time()),
            "last_activity": row.get("last_activity", row.get("updated_at", time.time())),
            "transcriptions": row.get("transcription_ids", []),
            "stream_sessions": row.get("stream_session_ids", []),
            "settings": row.get("settings", {"default_language": "en", "translate_to": []}),
        }

    def _row_to_stream_session(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a Supabase stream_sessions row to in-memory format."""
        provider_session = row.get("provider_session", {})
        metadata = provider_session.get("metadata") if isinstance(provider_session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        source_language = (
            row.get("source_language")
            or (provider_session.get("source_language") if isinstance(provider_session, dict) else None)
            or metadata.get("source_language")
            or row.get("language", "en")
        )
        target_language = (
            row.get("target_language")
            or (provider_session.get("target_language") if isinstance(provider_session, dict) else None)
            or metadata.get("target_language")
        )
        return {
            "id": row["id"],
            "session_id": row.get("user_session_id"),
            "language": row.get("language", "en"),
            "source_language": source_language,
            "target_language": target_language,
            "status": row.get("status", "active"),
            "created_at": row.get("created_at", time.time()),
            "updated_at": row.get("updated_at", time.time()),
            "provider_session": provider_session,
            "total_audio_bytes": row.get("total_audio_bytes", 0),
            "transcription_segments": row.get("transcription_segments", []),
            "text_timestamps": row.get("text_timestamps", []),
            "final_text": row.get("final_text"),
        }

    def _row_to_transcription(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a Supabase transcription_sessions row to in-memory format."""
        return {
            "id": row["id"],
            "session_id": row.get("user_session_id"),
            "filename": row.get("filename", "unknown"),
            "duration": row.get("duration", 0),
            "language": row.get("language", "en"),
            "status": row.get("status", "processing"),
            "created_at": row.get("created_at", time.time()),
            "updated_at": row.get("updated_at", time.time()),
            "result": row.get("result"),
        }


# Global session store
session_store = SessionStore()


def _stream_payload_indicates_running(payload: Any) -> bool:
    """Interpret provider status payloads in a tolerant way."""
    if not isinstance(payload, dict):
        return True

    bool_flags = ["running", "is_running", "active", "is_active", "live", "is_live"]
    for key in bool_flags:
        value = payload.get(key)
        if isinstance(value, bool):
            return value

    state_keys = ["status", "state", "phase", "lifecycle", "lifecycle_phase"]
    for key in state_keys:
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in {"running", "active", "live", "started", "ready", "connected", "ok"}:
            return True
        if normalized in {"stopped", "stopping", "completed", "ended", "failed", "error", "terminated", "offline", "inactive"}:
            return False

    return True


async def _provider_stream_is_running(provider_session: Dict[str, Any]) -> bool:
    """Ping provider status_url and infer whether stream is still running."""
    status_url = provider_session.get("status_url")
    if not status_url:
        return False

    params = {}
    provider_stream_id = provider_session.get("provider_stream_id")
    if provider_stream_id:
        params["provider_stream_id"] = provider_stream_id

    timeout = aiohttp.ClientTimeout(total=STREAM_STATUS_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.get(status_url, params=params) as response:
                if response.status != 200:
                    return False
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type:
                    payload = await response.json(content_type=None)
                    return _stream_payload_indicates_running(payload)
                return True
    except Exception as e:
        logger.warning("Failed stream status check: status_url=%s error=%s", status_url, e)
        return False


async def _bill_active_stream_minutes() -> None:
    """Bill one usage minute for each active stream confirmed running by provider status endpoint."""
    try:
        stream_result = await supabase.table("stream_sessions").select(
            "id,user_session_id,language,provider_session,status,created_at,updated_at,total_audio_bytes,transcription_segments,final_text"
        ).eq("status", "active").execute()
    except Exception as e:
        logger.warning("Failed to query active stream sessions for usage monitor: %s", e)
        return

    active_streams = stream_result.data or []
    now_minute = int(time.time() // 60)
    active_stream_ids = set()

    for row in active_streams:
        stream_id = str(row.get("id") or "")
        if not stream_id:
            continue
        active_stream_ids.add(stream_id)

        if _stream_usage_billed_minute.get(stream_id) == now_minute:
            continue

        provider_session = row.get("provider_session") or {}
        if not provider_session.get("status_url"):
            continue

        is_running = await _provider_stream_is_running(provider_session)
        if not is_running:
            continue

        stream_data = session_store._row_to_stream_session(row)
        billed = await session_store._record_stream_usage(stream_data, duration_seconds=60)
        if billed:
            _stream_usage_billed_minute[stream_id] = now_minute

    stale_ids = [sid for sid in list(_stream_usage_billed_minute.keys()) if sid not in active_stream_ids]
    for stale_id in stale_ids:
        _stream_usage_billed_minute.pop(stale_id, None)


async def _stream_usage_monitor_loop() -> None:
    """Background loop to enforce minute-based stream usage billing."""
    while True:
        try:
            await _bill_active_stream_minutes()
        except Exception as e:
            logger.warning("Stream usage monitor iteration failed: %s", e)
        await asyncio.sleep(STREAM_USAGE_POLL_SECONDS)


async def start_stream_usage_monitor(_app: web.Application) -> None:
    """Start background stream usage monitor task."""
    global _stream_usage_monitor_task
    if _stream_usage_monitor_task and not _stream_usage_monitor_task.done():
        return
    _stream_usage_monitor_task = asyncio.create_task(_stream_usage_monitor_loop())
    logger.info("Started stream usage monitor")


async def stop_stream_usage_monitor(_app: web.Application) -> None:
    """Stop background stream usage monitor task."""
    global _stream_usage_monitor_task
    if not _stream_usage_monitor_task:
        return

    _stream_usage_monitor_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _stream_usage_monitor_task
    _stream_usage_monitor_task = None
    logger.info("Stopped stream usage monitor")


# ======================================================================
# Auth Helpers
# ======================================================================

def _get_authenticated_entity_id(request):
    """Extract the authenticated entity (user or agent) ID from the request.

    Returns (entity_id, entity_type) tuple where entity_type is 'user' or 'agent'.
    Returns (None, None) if no authenticated entity found (should not happen after auth_middleware).
    """
    user = request.get('user')
    agent = request.get('agent')
    if user:
        return str(user.id), 'user'
    if agent:
        return str(agent.get('id')), 'agent'
    return None, None


async def _verify_session_ownership(request, session_id):
    """Verify that the authenticated entity owns the given session.

    Returns True if ownership is verified, False otherwise.
    """
    entity_id, entity_type = _get_authenticated_entity_id(request)
    if not entity_id:
        return False

    session = await session_store.get_session(session_id)
    if not session:
        return False

    session_user_id = session.get('user_id')
    if not session_user_id:
        # Session has no owner (legacy data), allow access
        return True

    return str(session_user_id) == str(entity_id)


async def _verify_stream_ownership(request, stream_id):
    """Verify that the authenticated entity owns the given stream session.

    Checks via the stream session's parent user session.
    Returns True if ownership is verified, False otherwise.
    """
    entity_id, entity_type = _get_authenticated_entity_id(request)
    if not entity_id:
        return False

    stream_session = await session_store.get_stream_session(stream_id)
    if not stream_session:
        return False

    # Check if the stream session's parent session belongs to the user
    parent_session_id = stream_session.get('session_id')
    if parent_session_id:
        return await _verify_session_ownership(request, parent_session_id)

    # Stream session has no parent session, allow access (legacy data)
    return True


async def _verify_transcription_ownership(request, transcription_id):
    """Verify that the authenticated entity owns the given transcription.

    Checks via the transcription's parent user session.
    Returns True if ownership is verified, False otherwise.
    """
    entity_id, entity_type = _get_authenticated_entity_id(request)
    if not entity_id:
        return False

    transcription = await session_store.get_transcription(transcription_id)
    if not transcription:
        return False

    # Check if the transcription's parent session belongs to the user
    parent_session_id = transcription.get('session_id')
    if parent_session_id:
        return await _verify_session_ownership(request, parent_session_id)

    # Transcription has no parent session, allow access (legacy data)
    return True


# ======================================================================
# HTTP Endpoint Handlers
# ======================================================================

async def create_session(request):
    """Create a new user session. User ID is derived from authentication."""
    try:
        # Derive user_id from authenticated entity instead of trusting request body
        entity_id, entity_type = _get_authenticated_entity_id(request)
        if not entity_id:
            return web.json_response({"error": "Authentication required"}, status=401)

        user_id = entity_id

        session_id = await session_store.create_session(user_id)

        return web.json_response({
            "session_id": session_id,
            "user_id": user_id,
            "message": "Session created successfully"
        })
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def get_session(request):
    """Get session information. Only the session owner can access it."""
    try:
        session_id = request.match_info.get('session_id')
        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)

        session = await session_store.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)

        # Verify ownership
        if not await _verify_session_ownership(request, session_id):
            return web.json_response({"error": "Access denied"}, status=403)

        return web.json_response(session)
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def get_user_transcriptions(request):
    """Get transcriptions for a user/session. Only the session owner can access them."""
    try:
        session_id = request.match_info.get('session_id')
        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)

        session = await session_store.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)

        # Verify ownership
        if not await _verify_session_ownership(request, session_id):
            return web.json_response({"error": "Access denied"}, status=403)

        # Get transcription details
        transcriptions = []
        for tid in session.get("transcriptions", []):
            transcription = await session_store.get_transcription(tid)
            if transcription:
                transcriptions.append(transcription)

        return web.json_response({
            "session_id": session_id,
            "transcriptions": transcriptions,
            "count": len(transcriptions)
        })
    except Exception as e:
        logger.error(f"Error getting user transcriptions: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def create_transcription_session(request):
    """Create a transcription session record. Only the session owner can create transcriptions."""
    try:
        data = await request.json()
        session_id = data.get('session_id')
        filename = data.get('filename', 'unknown')
        duration = data.get('duration', 0)
        language = data.get('language', 'en')

        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)

        # Verify ownership of the parent session
        if not await _verify_session_ownership(request, session_id):
            return web.json_response({"error": "Access denied: session does not belong to authenticated user"}, status=403)

        transcription_id = str(uuid.uuid4())
        now = time.time()
        transcription_data = {
            "id": transcription_id,
            "session_id": session_id,
            "filename": filename,
            "duration": duration,
            "language": language,
            "status": "processing",
            "created_at": now,
            "updated_at": now,
            "result": None
        }

        await session_store.create_transcription(transcription_id, transcription_data)

        # Link to user session
        await session_store.add_transcription_to_session(session_id, transcription_id)

        return web.json_response({
            "transcription_id": transcription_id,
            "message": "Transcription session created",
            "status": "processing"
        })
    except Exception as e:
        logger.error(f"Error creating transcription session: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def update_transcription_result(request):
    """Update transcription with results. Only the owner can update their transcriptions."""
    try:
        # Get transcription_id from path parameter
        transcription_id = request.match_info.get('transcription_id')
        if not transcription_id:
            return web.json_response({"error": "Transcription ID required"}, status=400)

        data = await request.json()
        result = data.get('result')
        status = data.get('status', 'completed')

        if not await session_store.has_transcription(transcription_id):
            return web.json_response({"error": "Transcription not found"}, status=404)

        # Verify ownership
        if not await _verify_transcription_ownership(request, transcription_id):
            return web.json_response({"error": "Access denied"}, status=403)

        await session_store.update_transcription(transcription_id, {
            "result": result,
            "status": status,
        })

        if status == "completed":
            logger.info(f"Transcription {transcription_id} completed")

        return web.json_response({
            "transcription_id": transcription_id,
            "status": status,
            "message": f"Transcription updated to {status}"
        })
    except Exception as e:
        logger.error(f"Error updating transcription result: {e}")
        return web.json_response({"error": str(e)}, status=500)


@x402_or_subscription(service_type='transcribe_gpu')
async def create_stream_session(request):
    """
    Create a streaming session with provider negotiation.

    This endpoint:
    1. Verifies the user owns the parent session
    2. Selects an appropriate compute provider (with failover)
    3. Negotiates a stream session with the provider
    4. Stores the provider's response (URLs) for later use
    5. Returns the stream URLs to the client
    """
    try:
        data = await request.json()
        session_id = data.get('session_id')
        language = data.get('language', 'en')

        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)

        # Verify ownership of the parent session
        if not await _verify_session_ownership(request, session_id):
            return web.json_response({"error": "Access denied: session does not belong to authenticated user"}, status=403)

        provider_manager = _build_compute_provider_manager()

        def _is_valid_streaming_session(session_result):
            """Check if a provider returned a usable streaming session with a WHIP URL."""
            if not session_result:
                return False
            whip_url = session_result.get("whip_url")
            return bool(whip_url and str(whip_url).strip())

        # Get ranked list of providers for failover
        stream_request_id = uuid.uuid4().hex[:12]
        ranked_providers = provider_manager.select_providers(
            job_type="transcribe_stream",
            requirements={"language": language}
        )
        logger.info(
            "Stream provider selection: request_id=%s session_id=%s language=%s providers=%s",
            stream_request_id,
            session_id,
            language,
            [provider.provider_name for provider in ranked_providers],
        )

        if not ranked_providers:
            return web.json_response(
                {"error": "No compute provider available"},
                status=503
            )

        # Try providers in order until one returns a valid session with whip_url
        provider_session_data = None
        last_error = None
        for provider in ranked_providers:
            try:
                logger.info(
                    "Starting provider stream session request: request_id=%s provider=%s session_id=%s language=%s",
                    stream_request_id,
                    provider.provider_name,
                    session_id,
                    language,
                )
                provider_session_data = await provider.create_streaming_session(
                    session_id=session_id,
                    language=language,
                    stream_request_id=stream_request_id,
                )
                if _is_valid_streaming_session(provider_session_data):
                    logger.info(
                        "Provider stream session ready: request_id=%s provider=%s session_id=%s provider_stream_id=%s",
                        stream_request_id,
                        provider.provider_name,
                        session_id,
                        provider_session_data.get("provider_stream_id"),
                    )
                    break
                else:
                    logger.warning(
                        "Provider stream session missing whip_url: request_id=%s provider=%s session_id=%s response_keys=%s",
                        stream_request_id,
                        provider.provider_name,
                        session_id,
                        sorted(list(provider_session_data.keys())),
                    )
                    provider_session_data = None
            except Exception as e:
                logger.warning(
                    "Provider stream session request failed: request_id=%s provider=%s session_id=%s language=%s error=%s",
                    stream_request_id,
                    provider.provider_name,
                    session_id,
                    language,
                    e,
                )
                last_error = e
                provider_session_data = None

        if not provider_session_data:
            error_msg = (
                f"All providers failed to return a valid streaming session. Last error: {last_error}"
                if last_error
                else "All providers returned invalid streaming sessions (missing whip_url)"
            )
            logger.error(error_msg)
            return web.json_response({"error": error_msg}, status=503)

        # Store in session store with provider data
        entity_id, _ = _get_authenticated_entity_id(request)
        stream_id = await session_store.create_stream_session(
            session_id=session_id,
            language=language,
            provider_session_data=provider_session_data,
            user_id=entity_id,
        )

        # Link to user session
        await session_store.add_stream_to_session(session_id, stream_id)

        # Return session info to client.
        # WHIP is now proxied through the backend G�� clients POST SDP offers
        # to /api/v1/transcribe/stream/{stream_id}/whip instead of connecting
        # directly to the provider. The provider's whip_url is stored server-side.
        return web.json_response({
            "stream_id": stream_id,
            "session_id": session_id,
            "status": "active",
            "data_url": provider_session_data.get("data_url"),
            "update_url": provider_session_data.get("update_url"),
            "stop_url": provider_session_data.get("stop_url"),
            "provider_stream_id": provider_session_data.get("provider_stream_id"),
            "provider": provider_session_data.get("provider")
        })
    except Exception as e:
        logger.error(f"Error creating stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def update_stream_session(request):
    """
    Update streaming session with new data.

    This endpoint sends updates to the provider's update_url.
    Only the stream owner can update it.
    """
    try:
        # Get stream_id from path parameter
        stream_id = request.match_info.get('stream_id')
        if not stream_id:
            return web.json_response({"error": "Stream ID required"}, status=400)

        data = await request.json()
        audio_bytes = data.get('audio_bytes', 0)
        transcription_segment = data.get('transcription_segment')

        if not await session_store.has_stream_session(stream_id):
            return web.json_response({"error": "Stream session not found"}, status=404)

        # Verify ownership
        if not await _verify_stream_ownership(request, stream_id):
            return web.json_response({"error": "Access denied"}, status=403)

        stream_session = await session_store.get_stream_session(stream_id)
        provider_session = (stream_session or {}).get("provider_session") or {}
        provider_name = provider_session.get("provider")
        provider_stream_id = provider_session.get("provider_stream_id")
        update_payload = {
            "audio_bytes": audio_bytes,
            "transcription_segment": transcription_segment,
        }

        provider_urls = await session_store.get_provider_urls(stream_id)
        provider_update_url = (provider_urls or {}).get("update_url")

        if provider_name and provider_stream_id:
            provider_manager = _build_compute_provider_manager()
            provider = provider_manager.get_provider(provider_name)
            update_streaming_session: Optional[Callable[..., Awaitable[Any]]] = (
                getattr(provider, "update_streaming_session", None) if provider else None
            )

            if callable(update_streaming_session):
                try:
                    await update_streaming_session(
                        provider_stream_id=provider_stream_id,
                        params=update_payload,
                        capability="live-transcription",
                    )
                except Exception as exc:
                    logger.warning(
                        "Provider update failed for stream %s via provider '%s': %s",
                        stream_id,
                        provider_name,
                        exc,
                    )
            elif provider_update_url:
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        provider_update_url,
                        json={
                            "provider_stream_id": provider_stream_id,
                            **update_payload,
                        }
                    ) as response:
                        if response.status != 200:
                            logger.warning(f"Provider update returned status {response.status}")
        elif provider_update_url:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    provider_update_url,
                    json={
                        "provider_stream_id": provider_stream_id,
                        **update_payload,
                    }
                ) as response:
                    if response.status != 200:
                        logger.warning(f"Provider update returned status {response.status}")

        # Update local session
        await session_store.update_stream_session(stream_id, {
            "audio_bytes": audio_bytes,
            "transcription_segment": transcription_segment
        })

        stream = await session_store.get_stream_session(stream_id)

        return web.json_response({
            "stream_id": stream_id,
            "status": stream["status"] if stream else "updated",
            "total_audio_bytes": stream["total_audio_bytes"] if stream else audio_bytes,
            "message": "Stream session updated"
        })
    except Exception as e:
        logger.error(f"Error updating stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def close_stream_session(request):
    """
    Close a streaming session.

    This endpoint calls the provider's stop_url to terminate the stream.
    Only the stream owner can close it.
    """
    try:
        # Get stream_id from path parameter
        stream_id = request.match_info.get('stream_id')
        if not stream_id:
            return web.json_response({"error": "Stream ID required"}, status=400)

        data = await request.json()
        final_text = data.get('final_text', '')

        if not await session_store.has_stream_session(stream_id):
            return web.json_response({"error": "Stream session not found"}, status=404)

        # Verify ownership
        if not await _verify_stream_ownership(request, stream_id):
            return web.json_response({"error": "Access denied"}, status=403)

        # Get provider URLs
        provider_urls = await session_store.get_provider_urls(stream_id)

        # If provider has a stop_url, call it to terminate the stream
        if provider_urls and provider_urls.get("stop_url"):
            import aiohttp
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    provider_urls["stop_url"],
                    json={"provider_stream_id": provider_urls.get("provider_stream_id")}
                ) as response:
                    if response.status != 200:
                        logger.warning(f"Provider stop returned status {response.status}")
                    else:
                        logger.info(f"Provider stream stopped successfully")

        # Update local session
        await session_store.close_stream_session(stream_id, final_text)

        stream = await session_store.get_stream_session(stream_id)

        return web.json_response({
            "stream_id": stream_id,
            "status": "completed",
            "final_text": final_text,
            "total_audio_bytes": stream["total_audio_bytes"] if stream else 0,
            "message": "Stream session closed"
        })
    except Exception as e:
        logger.error(f"Error closing stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def stop_stream_session(request):
    """
    Stop a streaming session via provider's stop_url.

    This is an alternative endpoint specifically for stopping streams
    through the provider's API. Only the stream owner can stop it.

    The transcription text is built incrementally by the SSE relay flush loop
    and only needs to be marked 'completed' here.
    """
    try:
        # Get stream_id from path parameter
        stream_id = request.match_info.get('stream_id')
        logger.info("Stop stream request received: stream_id=%s", stream_id)
        if not stream_id:
            return web.json_response({"error": "Stream ID required"}, status=400)

        if not await session_store.has_stream_session(stream_id):
            return web.json_response({"error": "Stream session not found"}, status=404)

        # Verify ownership
        if not await _verify_stream_ownership(request, stream_id):
            return web.json_response({"error": "Access denied"}, status=403)

        # Get provider URLs
        provider_urls = await session_store.get_provider_urls(stream_id)

        if not provider_urls or not provider_urls.get("stop_url"):
            return web.json_response({
                "error": "Stream session not found or no stop URL available"
            }, status=404)

        # Call provider's stop URL
        import aiohttp
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                provider_urls["stop_url"],
                json={"provider_stream_id": provider_urls.get("provider_stream_id")}
            ) as response:
                response_text = await response.text()
                logger.info(
                    "Stop stream provider response: stream_id=%s provider_stream_id=%s http_status=%s",
                    stream_id,
                    provider_urls.get("provider_stream_id"),
                    response.status,
                )

                if response.status == 404:
                    logger.warning(
                        "Provider stop returned 404; treating as successful stop: stream_id=%s provider_stream_id=%s response_text=%s",
                        stream_id,
                        provider_urls.get("provider_stream_id"),
                        response_text[:1000],
                    )
                    provider_response = {
                        "status": "already_stopped",
                        "provider_status": 404,
                        "details": response_text,
                    }
                elif response.status not in (200, 204):
                    logger.error(f"Provider stop failed: {response.status} - {response_text}")
                    return web.json_response({
                        "error": f"Failed to stop stream: HTTP {response.status}",
                        "details": response_text
                    }, status=response.status)
                else:
                    provider_response = {
                        "provider_status": response.status,
                        "details": response_text,
                    }

        # Update local session
        await session_store.close_stream_session(stream_id)

        # Finalize the transcriptions row built incrementally by the SSE relay flush loop.
        transcription_id = None
        try:
            stream_session = await session_store.get_stream_session(stream_id)
            transcription_id = (stream_session or {}).get("transcription_id")
            if transcription_id:
                await supabase.table("transcriptions").update({"status": "completed"}).eq("id", transcription_id).execute()
                logger.info(
                    "Finalized live transcription record: stream_id=%s transcription_id=%s",
                    stream_id,
                    transcription_id,
                )
            else:
                logger.info("Stop stream: no incremental transcription row to finalize for stream %s", stream_id)
        except Exception as save_err:
            logger.warning("Failed to finalize transcription record for stream %s: %s", stream_id, save_err)

        logger.info("Stop stream request completed: stream_id=%s", stream_id)

        return web.json_response({
            "stream_id": stream_id,
            "status": "stopped",
            "transcription_id": transcription_id,
            "provider_response": provider_response,
            "message": "Stream session stopped via provider"
        })
    except Exception as e:
        logger.exception(f"Error stopping stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)


def setup_routes(app: web.Application):
    """Setup session-related routes."""
    app.router.add_post('/api/v1/sessions', create_session)
    app.router.add_get('/api/v1/sessions/{session_id}', get_session)
    app.router.add_get('/api/v1/sessions/{session_id}/transcriptions', get_user_transcriptions)
    app.router.add_post('/api/v1/transcriptions/session', create_transcription_session)
    app.router.add_post('/api/v1/transcriptions/{transcription_id}/result', update_transcription_result)
    app.router.add_post('/api/v1/stream/session', create_stream_session)
    app.router.add_post('/api/v1/stream/{stream_id}/update', update_stream_session)
    app.router.add_post('/api/v1/stream/{stream_id}/close', close_stream_session)
    app.router.add_post('/api/v1/stream/{stream_id}/stop', stop_stream_session)

    logger.info("Session routes configured")
