#!/usr/bin/env python3
"""
Session Management Endpoints
Handles user sessions, transcription sessions, and streaming sessions.

Uses a write-through cache pattern:
- Writes go to Supabase first, then update the in-memory cache
- Reads hit the in-memory cache first, falling back to Supabase on miss
- This provides persistence across backend restarts while keeping hot-path reads fast
"""

from aiohttp import web
import logging
import uuid
from typing import Dict, Any, Optional, List
import time
from typing import Any as TypingAny

from supabase_client import supabase
from payments.payment_strategy import x402_or_subscription

logger = logging.getLogger(__name__)


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

    # ------------------------------------------------------------------
    # User Sessions
    # ------------------------------------------------------------------

    async def create_session(self, user_id: str) -> str:
        """Create a new user session. Persists to Supabase."""
        session_id = str(uuid.uuid4())
        now = time.time()
        session_data = {
            "id": session_id,
            "user_id": user_id,
            "created_at": now,
            "last_activity": now,
            "transcriptions": [],
            "stream_sessions": [],
            "settings": {
                "default_language": "en",
                "translate_to": []
            }
        }

        try:
            supabase.table("user_sessions").insert({
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

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID. Checks cache first, then Supabase."""
        # Cache hit
        if session_id in self._sessions_cache:
            return self._sessions_cache[session_id]

        # Cache miss — try Supabase
        try:
            result = supabase.table("user_sessions").select("*").eq("id", session_id).execute()
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
            supabase.table("user_sessions").update({
                "last_activity": "now()"
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update session activity in Supabase: {e}")

    async def add_transcription_to_session(self, session_id: str, transcription_id: str):
        """Add transcription to session."""
        if session_id in self._sessions_cache:
            self._sessions_cache[session_id]["transcriptions"].append(transcription_id)
            self._sessions_cache[session_id]["last_activity"] = time.time()

        # Persist to Supabase using array append
        try:
            supabase.table("user_sessions").update({
                "transcription_ids": f"array_append(transcription_ids, '{transcription_id}')"
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.warning(f"Failed to add transcription to session in Supabase: {e}")

    async def add_stream_to_session(self, session_id: str, stream_id: str):
        """Add stream session to session."""
        if session_id in self._sessions_cache:
            self._sessions_cache[session_id]["stream_sessions"].append(stream_id)
            self._sessions_cache[session_id]["last_activity"] = time.time()

        # Persist to Supabase using array append
        try:
            supabase.table("user_sessions").update({
                "stream_session_ids": f"array_append(stream_session_ids, '{stream_id}')"
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
        provider_session_data: Any
    ) -> str:
        """
        Create a new stream session with provider data.
        Persists to Supabase stream_sessions table.
        """
        stream_id = str(uuid.uuid4())
        now = time.time()
        stream_data = {
            "id": stream_id,
            "session_id": session_id,
            "language": language,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "provider_session": provider_session_data,
            "total_audio_bytes": 0,
            "transcription_segments": []
        }

        try:
            supabase.table("stream_sessions").insert({
                "id": stream_id,
                "user_session_id": session_id,
                "language": language,
                "status": "active",
                "provider_session": provider_session_data,
                "total_audio_bytes": 0,
                "transcription_segments": [],
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to persist stream session to Supabase, using cache only: {e}")

        # Update cache
        self._stream_sessions_cache[stream_id] = stream_data
        logger.info(f"Created stream session {stream_id} with provider {provider_session_data.get('provider', 'unknown')}")
        return stream_id

    async def get_stream_session(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """Get stream session by ID. Checks cache first, then Supabase."""
        # Cache hit
        if stream_id in self._stream_sessions_cache:
            return self._stream_sessions_cache[stream_id]

        # Cache miss — try Supabase
        try:
            result = supabase.table("stream_sessions").select("*").eq("id", stream_id).execute()
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
            result = supabase.table("stream_sessions").select("id").eq("id", stream_id).execute()
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
        audio_bytes = update_data.get("audio_bytes", 0)

        # Update cache
        if stream_id in self._stream_sessions_cache:
            self._stream_sessions_cache[stream_id]["updated_at"] = now
            if segments_to_append:
                self._stream_sessions_cache[stream_id]["transcription_segments"].append(
                    segments_to_append
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
            supabase.table("stream_sessions").update(db_update).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update stream session in Supabase: {e}")

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
            supabase.table("stream_sessions").update(db_update).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning(f"Failed to close stream session in Supabase: {e}")

        logger.info(f"Stream session {stream_id} closed")

    # ------------------------------------------------------------------
    # Transcription Sessions
    # ------------------------------------------------------------------

    async def create_transcription(self, transcription_id: str, data: Dict[str, Any]):
        """Create a transcription session record. Persists to Supabase."""
        # Update cache
        self._transcriptions_cache[transcription_id] = data

        # Persist to Supabase
        try:
            supabase.table("transcription_sessions").insert({
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

        # Cache miss — try Supabase
        try:
            result = supabase.table("transcription_sessions").select("*").eq("id", transcription_id).execute()
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
            result = supabase.table("transcription_sessions").select("id").eq("id", transcription_id).execute()
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
            supabase.table("transcription_sessions").update(db_update).eq("id", transcription_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update transcription in Supabase: {e}")

    # ------------------------------------------------------------------
    # Row-to-dict converters (Supabase row → in-memory format)
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
        return {
            "id": row["id"],
            "session_id": row.get("user_session_id"),
            "language": row.get("language", "en"),
            "status": row.get("status", "active"),
            "created_at": row.get("created_at", time.time()),
            "updated_at": row.get("updated_at", time.time()),
            "provider_session": row.get("provider_session", {}),
            "total_audio_bytes": row.get("total_audio_bytes", 0),
            "transcription_segments": row.get("transcription_segments", []),
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
    2. Selects an appropriate compute provider
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

        # Import provider manager here to avoid circular imports
        from compute_providers.provider_manager import ComputeProviderManager
        from compute_providers.provider_definitions import PROVIDER_DEFINITIONS

        # Initialize provider manager
        provider_manager = ComputeProviderManager()
        provider_manager.register_providers_from_definitions(PROVIDER_DEFINITIONS)

        # Select compute provider
        provider = provider_manager.select_provider(
            job_type="transcribe_stream",
            requirements={"language": language}
        )

        if not provider:
            return web.json_response(
                {"error": "No compute provider available"},
                status=503
            )

        # Negotiate with provider to create stream session
        provider_session_data = await provider.create_streaming_session(
            session_id=session_id,
            language=language
        )

        # Store in session store with provider data
        stream_id = await session_store.create_stream_session(
            session_id=session_id,
            language=language,
            provider_session_data=provider_session_data
        )

        # Link to user session
        await session_store.add_stream_to_session(session_id, stream_id)

        # Return session info to client.
        # WHIP is now proxied through the backend — clients POST SDP offers
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

        # Get provider URLs
        provider_urls = await session_store.get_provider_urls(stream_id)

        # If provider has an update_url, send the update there
        if provider_urls and provider_urls.get("update_url"):
            import aiohttp
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    provider_urls["update_url"],
                    json={
                        "provider_stream_id": provider_urls.get("provider_stream_id"),
                        "audio_bytes": audio_bytes,
                        "transcription_segment": transcription_segment
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
    """
    try:
        # Get stream_id from path parameter
        stream_id = request.match_info.get('stream_id')
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
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Provider stop failed: {response.status} - {error_text}")
                    return web.json_response({
                        "error": f"Failed to stop stream: HTTP {response.status}",
                        "details": error_text
                    }, status=response.status)

                provider_response = await response.json()

        # Update local session
        await session_store.close_stream_session(stream_id)

        return web.json_response({
            "stream_id": stream_id,
            "status": "stopped",
            "provider_response": provider_response,
            "message": "Stream session stopped via provider"
        })
    except Exception as e:
        logger.error(f"Error stopping stream session: {e}")
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
