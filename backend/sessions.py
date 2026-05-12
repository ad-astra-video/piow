     1|#!/usr/bin/env python3
     2|"""
     3|Session Management Endpoints
     4|Handles user sessions, transcription sessions, and streaming sessions.
     5|
     6|Uses a write-through cache pattern:
     7|- Writes go to Supabase first, then update the in-memory cache
     8|- Reads hit the in-memory cache first, falling back to Supabase on miss
     9|- This provides persistence across backend restarts while keeping hot-path reads fast
    10|"""
    11|
    12|import asyncio
    13|import contextlib
    14|from aiohttp import web
    15|import aiohttp
    16|import logging
    17|import uuid
    18|from datetime import datetime
    19|from typing import Dict, Any, Optional, List
    20|import time
    21|from typing import Any as TypingAny
    22|
    23|from supabase_client import async_supabase as supabase
    24|from payments.payment_strategy import x402_or_subscription
    25|
    26|logger = logging.getLogger(__name__)
    27|
    28|STREAM_USAGE_POLL_SECONDS = 60
    29|STREAM_STATUS_TIMEOUT_SECONDS = 10
    30|
    31|_stream_usage_monitor_task: Optional[asyncio.Task] = None
    32|_stream_usage_billed_minute: Dict[str, int] = {}
    33|
    34|
    35|class SessionStore:
    36|    """
    37|    Database-backed session store with in-memory cache.
    38|
    39|    All write operations persist to Supabase tables:
    40|    - user_sessions: user session tracking
    41|    - stream_sessions: live streaming session data
    42|    - transcription_sessions: batch transcription job tracking
    43|
    44|    The in-memory cache provides fast reads for hot-path operations
    45|    (e.g., WebSocket relay looking up stream data_url).
    46|    """
    47|
    48|    def __init__(self):
    49|        # In-memory cache layers
    50|        self._sessions_cache: Dict[str, Dict[str, Any]] = {}  # session_id -> session_data
    51|        self._transcriptions_cache: Dict[str, Dict[str, Any]] = {}  # transcription_id -> data
    52|        self._stream_sessions_cache: Dict[str, Dict[str, Any]] = {}  # stream_id -> data
    53|
    54|    def _build_session_data(self, session_id: str, user_id: str, now: Optional[float] = None) -> Dict[str, Any]:
    55|        """Build in-memory session data with defaults."""
    56|        ts = now if now is not None else time.time()
    57|        return {
    58|            "id": session_id,
    59|            "user_id": user_id,
    60|            "created_at": ts,
    61|            "last_activity": ts,
    62|            "transcriptions": [],
    63|            "stream_sessions": [],
    64|            "settings": {
    65|                "default_language": "en",
    66|                "translate_to": []
    67|            }
    68|        }
    69|
    70|    def _coerce_timestamp(self, value: Any) -> float:
    71|        """Convert supported timestamp types into epoch seconds."""
    72|        if value is None:
    73|            return time.time()
    74|        if isinstance(value, (int, float)):
    75|            return float(value)
    76|        if isinstance(value, datetime):
    77|            return value.timestamp()
    78|        if isinstance(value, str):
    79|            try:
    80|                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    81|            except ValueError:
    82|                return time.time()
    83|        return time.time()
    84|
    85|    def _extract_stream_text(self, stream_data: Dict[str, Any], final_text: str) -> str:
    86|        """Build best-effort text for usage word counts from final text or segments."""
    87|        text = (final_text or "").strip()
    88|        if text:
    89|            return text
    90|
    91|        cached_final = (stream_data.get("final_text") or "").strip()
    92|        if cached_final:
    93|            return cached_final
    94|
    95|        segments = stream_data.get("transcription_segments") or []
    96|        collected: List[str] = []
    97|        for segment in segments:
    98|            if isinstance(segment, str):
    99|                segment_text = segment.strip()
   100|            elif isinstance(segment, dict):
   101|                segment_text = str(segment.get("text") or segment.get("transcript") or "").strip()
   102|            else:
   103|                segment_text = ""
   104|            if segment_text:
   105|                collected.append(segment_text)
   106|
   107|        return " ".join(collected).strip()
   108|
   109|    async def _record_stream_usage(self, stream_data: Dict[str, Any], duration_seconds: int, final_text: str = "") -> bool:
   110|        """Persist a transcription_usage row for a live stream interval."""
   111|        if not stream_data:
   112|            return False
   113|
   114|        if duration_seconds <= 0:
   115|            return False
   116|
   117|        session_id = stream_data.get("session_id")
   118|        if not session_id:
   119|            logger.warning("Skipping stream usage log: stream has no parent session")
   120|            return False
   121|
   122|        parent_session = await self.get_session(session_id)
   123|        user_id = str(parent_session.get("user_id")) if parent_session and parent_session.get("user_id") else None
   124|        if not user_id:
   125|            logger.warning("Skipping stream usage log: missing user_id for stream %s", stream_data.get("id"))
   126|            return False
   127|
   128|        text = self._extract_stream_text(stream_data, final_text)
   129|        word_count = len(text.split()) if text else 0
   130|
   131|        provider_session = stream_data.get("provider_session") or {}
   132|        model = provider_session.get("model") or "voxtral-realtime"
   133|        hardware = provider_session.get("hardware") or "gpu"
   134|        source_language = stream_data.get("language") or "en"
   135|
   136|        try:
   137|            await supabase.table("transcription_usage").insert({
   138|                "user_id": user_id,
   139|                "duration_seconds": duration_seconds,
   140|                "word_count": word_count,
   141|                "source_language": source_language,
   142|                "model": model,
   143|                "hardware": hardware,
   144|                "source_type": "stream",
   145|            }).execute()
   146|            return True
   147|        except Exception as e:
   148|            logger.warning("Failed to record stream usage for %s: %s", stream_data.get("id"), e)
   149|            return False
   150|
   151|    # ------------------------------------------------------------------
   152|    # User Sessions
   153|    # ------------------------------------------------------------------
   154|
   155|    async def create_session(self, user_id: str) -> str:
   156|        """Create a new user session. Persists to Supabase."""
   157|        session_id = str(uuid.uuid4())
   158|        session_data = self._build_session_data(session_id=session_id, user_id=user_id)
   159|
   160|        try:
   161|            await supabase.table("user_sessions").insert({
   162|                "id": session_id,
   163|                "user_id": user_id,
   164|                "settings": session_data["settings"],
   165|            }).execute()
   166|        except Exception as e:
   167|            logger.warning(f"Failed to persist session to Supabase, using cache only: {e}")
   168|
   169|        # Update cache
   170|        self._sessions_cache[session_id] = session_data
   171|        logger.info(f"Created session {session_id}")
   172|        return session_id
   173|
   174|    async def ensure_session(self, session_id: str, user_id: Optional[str]) -> bool:
   175|        """
   176|        Ensure a user session exists for the given session_id.
   177|
   178|        Returns True when the session exists or is created.
   179|        Returns False if the session does not exist and user_id is unavailable.
   180|        Raises ValueError if session_id already exists but belongs to another user.
   181|        """
   182|        existing_session = await self.get_session(session_id)
   183|        if existing_session:
   184|            existing_user_id = existing_session.get("user_id")
   185|            if user_id and existing_user_id and str(existing_user_id) != str(user_id):
   186|                raise ValueError(
   187|                    f"Session {session_id} belongs to a different user"
   188|                )
   189|            return True
   190|
   191|        if not user_id:
   192|            logger.warning(
   193|                "Cannot ensure missing user session without user_id: session_id=%s",
   194|                session_id,
   195|            )
   196|            return False
   197|
   198|        session_data = self._build_session_data(session_id=session_id, user_id=user_id)
   199|        try:
   200|            await supabase.table("user_sessions").insert({
   201|                "id": session_id,
   202|                "user_id": user_id,
   203|                "settings": session_data["settings"],
   204|            }).execute()
   205|            self._sessions_cache[session_id] = session_data
   206|            logger.info("Ensured user session exists: session_id=%s", session_id)
   207|            return True
   208|        except Exception as e:
   209|            # Handle a concurrent insert race by re-reading the row.
   210|            if "23505" in str(e):
   211|                refreshed = await self.get_session(session_id)
   212|                if refreshed:
   213|                    existing_user_id = refreshed.get("user_id")
   214|                    if user_id and existing_user_id and str(existing_user_id) != str(user_id):
   215|                        raise ValueError(
   216|                            f"Session {session_id} belongs to a different user"
   217|                        )
   218|                    return True
   219|            raise
   220|
   221|    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
   222|        """Get session by ID. Checks cache first, then Supabase."""
   223|        # Cache hit
   224|        if session_id in self._sessions_cache:
   225|            return self._sessions_cache[session_id]
   226|
   227|        # Cache miss — try Supabase
   228|        try:
   229|            result = await supabase.table("user_sessions").select("*").eq("id", session_id).execute()
   230|            if result.data:
   231|                row = result.data[0]
   232|                session_data = self._row_to_session(row)
   233|                self._sessions_cache[session_id] = session_data
   234|                return session_data
   235|        except Exception as e:
   236|            logger.warning(f"Failed to load session from Supabase: {e}")
   237|
   238|        return None
   239|
   240|    async def update_session_activity(self, session_id: str):
   241|        """Update session last activity time."""
   242|        if session_id in self._sessions_cache:
   243|            self._sessions_cache[session_id]["last_activity"] = time.time()
   244|
   245|        # Fire-and-forget Supabase update
   246|        try:
   247|            await supabase.table("user_sessions").update({
   248|                "last_activity": "now()"
   249|            }).eq("id", session_id).execute()
   250|        except Exception as e:
   251|            logger.warning(f"Failed to update session activity in Supabase: {e}")
   252|
   253|    async def add_transcription_to_session(self, session_id: str, transcription_id: str):
   254|        """Add transcription to session."""
   255|        if session_id in self._sessions_cache:
   256|            self._sessions_cache[session_id]["transcriptions"].append(transcription_id)
   257|            self._sessions_cache[session_id]["last_activity"] = time.time()
   258|
   259|        # Persist to Supabase by reading current array and writing the updated one
   260|        try:
   261|            transcription_ids = []
   262|            if session_id in self._sessions_cache:
   263|                transcription_ids = list(self._sessions_cache[session_id]["transcriptions"])
   264|            else:
   265|                result = await supabase.table("user_sessions").select("transcription_ids").eq("id", session_id).execute()
   266|                if result.data:
   267|                    transcription_ids = list(result.data[0].get("transcription_ids") or [])
   268|                transcription_ids.append(transcription_id)
   269|
   270|            await supabase.table("user_sessions").update({
   271|                "transcription_ids": transcription_ids
   272|            }).eq("id", session_id).execute()
   273|        except Exception as e:
   274|            logger.warning(f"Failed to add transcription to session in Supabase: {e}")
   275|
   276|    async def add_stream_to_session(self, session_id: str, stream_id: str):
   277|        """Add stream session to session."""
   278|        if session_id in self._sessions_cache:
   279|            self._sessions_cache[session_id]["stream_sessions"].append(stream_id)
   280|            self._sessions_cache[session_id]["last_activity"] = time.time()
   281|
   282|        # Persist to Supabase by reading current array and writing the updated one
   283|        try:
   284|            stream_session_ids = []
   285|            if session_id in self._sessions_cache:
   286|                stream_session_ids = list(self._sessions_cache[session_id]["stream_sessions"])
   287|            else:
   288|                result = await supabase.table("user_sessions").select("stream_session_ids").eq("id", session_id).execute()
   289|                if result.data:
   290|                    stream_session_ids = list(result.data[0].get("stream_session_ids") or [])
   291|                stream_session_ids.append(stream_id)
   292|
   293|            await supabase.table("user_sessions").update({
   294|                "stream_session_ids": stream_session_ids
   295|            }).eq("id", session_id).execute()
   296|        except Exception as e:
   297|            logger.warning(f"Failed to add stream to session in Supabase: {e}")
   298|
   299|    # ------------------------------------------------------------------
   300|    # Stream Sessions
   301|    # ------------------------------------------------------------------
   302|
    async def create_stream_session(
        self,
        session_id: str,
        language: str,
        provider_session_data: Any,
        user_id: Optional[str] = None,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> str:
   310|        """
   311|        Create a new stream session with provider data.
   312|        Persists to Supabase stream_sessions table.
   313|        """
   314|        stream_id = str(uuid.uuid4())
   315|        now = time.time()
   316|        stream_data = {
   317|            "id": stream_id,
   318|            "session_id": session_id,
   319|            "language": language,
   320|            "status": "active",
   321|            "created_at": now,
   322|            "updated_at": now,
   323|            "provider_session": provider_session_data,
   324|            "total_audio_bytes": 0,
   325|            "transcription_segments": [],
   326|            "text_timestamps": [],
   327|        }
   328|
   329|        try:
   330|            await self.ensure_session(session_id=session_id, user_id=user_id)
   331|        except ValueError:
   332|            # Surface ownership mismatches to callers so they can return 403.
   333|            raise
   334|        except Exception as e:
   335|            logger.warning(
   336|                "Failed to ensure user session before stream creation: session_id=%s error=%s",
   337|                session_id,
   338|                e,
   339|            )
   340|
   341|        try:
   342|            await supabase.table("stream_sessions").insert({
   343|                "id": stream_id,
   344|                "user_session_id": session_id,
   345|                "language": language,
   346|                "status": "active",
   347|                "provider_session": provider_session_data,
   348|                "total_audio_bytes": 0,
   349|                "transcription_segments": [],
   350|                "text_timestamps": [],
   351|            }).execute()
   352|        except Exception as e:
   353|            logger.warning(f"Failed to persist stream session to Supabase, using cache only: {e}")
   354|
   355|        # Update cache
   356|        self._stream_sessions_cache[stream_id] = stream_data
   357|        logger.info(f"Created stream session {stream_id} with provider {provider_session_data.get('provider', 'unknown')}")
   358|        return stream_id
   359|
   360|    async def get_stream_session(self, stream_id: str) -> Optional[Dict[str, Any]]:
   361|        """Get stream session by ID. Checks cache first, then Supabase."""
   362|        # Cache hit
   363|        if stream_id in self._stream_sessions_cache:
   364|            return self._stream_sessions_cache[stream_id]
   365|
   366|        # Cache miss — try Supabase
   367|        try:
   368|            result = await supabase.table("stream_sessions").select("*").eq("id", stream_id).execute()
   369|            if result.data:
   370|                row = result.data[0]
   371|                stream_data = self._row_to_stream_session(row)
   372|                self._stream_sessions_cache[stream_id] = stream_data
   373|                return stream_data
   374|        except Exception as e:
   375|            logger.warning(f"Failed to load stream session from Supabase: {e}")
   376|
   377|        return None
   378|
   379|    async def has_stream_session(self, stream_id: str) -> bool:
   380|        """Check if a stream session exists."""
   381|        if stream_id in self._stream_sessions_cache:
   382|            return True
   383|        # Try Supabase
   384|        try:
   385|            result = await supabase.table("stream_sessions").select("id").eq("id", stream_id).execute()
   386|            return len(result.data) > 0
   387|        except Exception as e:
   388|            logger.warning(f"Failed to check stream session in Supabase: {e}")
   389|            return False
   390|
   391|    async def get_provider_urls(self, stream_id: str) -> Optional[Dict[str, str]]:
   392|        """
   393|        Get provider management URLs for a stream session.
   394|        """
   395|        session = await self.get_stream_session(stream_id)
   396|        if session:
   397|            provider_session = session.get("provider_session", {})
   398|            return {
   399|                "update_url": provider_session.get("update_url"),
   400|                "stop_url": provider_session.get("stop_url"),
   401|                "data_url": provider_session.get("data_url"),
   402|                "whip_url": provider_session.get("whip_url"),
   403|                "provider_stream_id": provider_session.get("provider_stream_id")
   404|            }
   405|        return None
   406|
   407|    async def update_stream_session(self, stream_id: str, update_data: Dict[str, Any]):
   408|        """Update stream session with new data."""
   409|        now = time.time()
   410|        segments_to_append = update_data.get("transcription_segment")
   411|        timestamp_segment = update_data.get("timestamp_segment")
   412|        audio_bytes = update_data.get("audio_bytes", 0)
   413|
   414|        # Update cache
   415|        if stream_id in self._stream_sessions_cache:
   416|            self._stream_sessions_cache[stream_id]["updated_at"] = now
   417|            if segments_to_append:
   418|                self._stream_sessions_cache[stream_id]["transcription_segments"].append(
   419|                    segments_to_append
   420|                )
   421|            if isinstance(timestamp_segment, dict):
   422|                self._stream_sessions_cache[stream_id].setdefault("text_timestamps", []).append(
   423|                    timestamp_segment
   424|                )
   425|            if audio_bytes:
   426|                self._stream_sessions_cache[stream_id]["total_audio_bytes"] += audio_bytes
   427|
   428|        # Persist to Supabase
   429|        try:
   430|            db_update = {"updated_at": "now()"}
   431|            if audio_bytes:
   432|                db_update["total_audio_bytes"] = self._stream_sessions_cache.get(stream_id, {}).get("total_audio_bytes", audio_bytes)
   433|            if segments_to_append:
   434|                db_update["transcription_segments"] = self._stream_sessions_cache.get(stream_id, {}).get("transcription_segments", [])
   435|            if isinstance(timestamp_segment, dict):
   436|                db_update["text_timestamps"] = self._stream_sessions_cache.get(stream_id, {}).get("text_timestamps", [])
   437|            await supabase.table("stream_sessions").update(db_update).eq("id", stream_id).execute()
   438|        except Exception as e:
   439|            logger.warning(f"Failed to update stream session in Supabase: {e}")
   440|
   441|        if isinstance(timestamp_segment, dict):
   442|            transcription_id = self._stream_sessions_cache.get(stream_id, {}).get("transcription_id")
   443|            if transcription_id:
   444|                try:
   445|                    await supabase.table("transcriptions").update({
   446|                        "segments": self._stream_sessions_cache.get(stream_id, {}).get("text_timestamps", []),
   447|                    }).eq("id", transcription_id).execute()
   448|                except Exception as e:
   449|                    logger.warning(
   450|                        "Failed to persist text_timestamps into transcriptions for stream %s: %s",
   451|                        stream_id,
   452|                        e,
   453|                    )
   454|
   455|    async def update_stream_translation(
        self,
        stream_id: str,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> None:
        """Update translation config for an active stream session."""
        update_data = {}
        if source_language is not None:
            update_data["source_language"] = source_language
        if target_language is not None:
            update_data["target_language"] = target_language

        if not update_data:
            return

        # Update cache
        if stream_id in self._stream_sessions_cache:
            self._stream_sessions_cache[stream_id].update(update_data)

        # Persist to Supabase
        try:
            await supabase.table("stream_sessions").update(update_data).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning("Failed to update stream translation config in Supabase: %s", e)

    async def update_stream_translation(
        self,
        stream_id: str,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> None:
        """Update translation config for an active stream session."""
        update_data = {}
        if source_language is not None:
            update_data["source_language"] = source_language
        if target_language is not None:
            update_data["target_language"] = target_language

        if not update_data:
            return

        # Update cache
        if stream_id in self._stream_sessions_cache:
            self._stream_sessions_cache[stream_id].update(update_data)

        # Persist to Supabase
        try:
            await supabase.table("stream_sessions").update(update_data).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning("Failed to update stream translation config in Supabase: %s", e)

    async def update_stream_translation(
        self,
        stream_id: str,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> None:
        """Update translation config for an active stream session."""
        update_data = {}
        if source_language is not None:
            update_data["source_language"] = source_language
        if target_language is not None:
            update_data["target_language"] = target_language

        if not update_data:
            return

        # Update cache
        if stream_id in self._stream_sessions_cache:
            self._stream_sessions_cache[stream_id].update(update_data)

        # Persist to Supabase
        try:
            await supabase.table("stream_sessions").update(update_data).eq("id", stream_id).execute()
        except Exception as e:
            logger.warning("Failed to update stream translation config in Supabase: %s", e)

    async def close_stream_session(self, stream_id: str, final_text: str = ""):
   456|        """Close a stream session."""
   457|        now = time.time()
   458|
   459|        # Update cache
   460|        if stream_id in self._stream_sessions_cache:
   461|            self._stream_sessions_cache[stream_id]["status"] = "completed"
   462|            self._stream_sessions_cache[stream_id]["final_text"] = final_text
   463|            self._stream_sessions_cache[stream_id]["updated_at"] = now
   464|
   465|        # Persist to Supabase
   466|        try:
   467|            db_update = {
   468|                "status": "completed",
   469|                "updated_at": "now()",
   470|            }
   471|            if final_text:
   472|                db_update["final_text"] = final_text
   473|            await supabase.table("stream_sessions").update(db_update).eq("id", stream_id).execute()
   474|        except Exception as e:
   475|            logger.warning(f"Failed to close stream session in Supabase: {e}")
   476|
   477|        logger.info(f"Stream session {stream_id} closed")
   478|
   479|    # ------------------------------------------------------------------
   480|    # Live-stream transcription upsert
   481|    # ------------------------------------------------------------------
   482|
   483|    async def upsert_stream_transcription(self, stream_id: str, new_segments: List[str]) -> Optional[str]:
   484|        """Create or incrementally update the transcriptions row for a live stream.
   485|
   486|        Called by the SSE relay flush loop each time a batch of final segments
   487|        is ready.  On the first call an in-progress row is inserted; subsequent
   488|        calls append the new text and update word_count.
   489|
   490|        The transcription_id is cached on the stream session so
   491|        stop_stream_session can finalize the row without a DB lookup.
   492|
   493|        Returns the transcription_id on success, None on failure.
   494|        """
   495|        if not new_segments:
   496|            return (self._stream_sessions_cache.get(stream_id) or {}).get("transcription_id")
   497|
   498|        # Load stream data (cache-first)
   499|        stream_data = self._stream_sessions_cache.get(stream_id) or await self.get_stream_session(stream_id)
   500|        if not stream_data:
   501|