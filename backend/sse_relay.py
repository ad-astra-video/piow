#!/usr/bin/env python3
"""
SSE Relay Module

Connects to compute provider SSE data_url endpoints and relays transcription
events to subscribed WebSocket clients. This bridges the gap between the
provider's SSE transcription output and the frontend's WebSocket connection.

Architecture:
    GPU Worker (SSE)  ──data_url──>  SSERelay  ──WebSocket──>  Frontend
    (produces transcription)        (backend)              (displays text)
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, Set, List

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)


class SSERelay:
    """
    Connects to a compute provider's SSE data_url and relays transcription
    events to subscribed WebSocket clients.

    Usage:
        relay = SSERelay(data_url="http://worker:9935/stream/data", stream_id="abc123")
        relay.add_client(websocket)
        await relay.start()
        # ... later ...
        relay.remove_client(websocket)
        if not relay.has_clients:
            await relay.stop()
    """

    # How often (seconds) to flush buffered transcription text to the DB
    FLUSH_INTERVAL_SECONDS = 10

    def __init__(self, data_url: str, stream_id: str, session_store=None):
        """
        Initialize the SSE relay.

        Args:
            data_url: The SSE endpoint URL from the compute provider
            stream_id: The stream session ID for logging and tracking
            session_store: Optional SessionStore instance used to persist
                           transcription segments every FLUSH_INTERVAL_SECONDS.
        """
        self.data_url = data_url
        self.stream_id = stream_id
        self.clients: Set[web.WebSocketResponse] = set()
        self._task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._base_reconnect_delay = 1.0  # seconds
        self._retry_delay: Optional[float] = None  # Set by SSE retry: field
        self._last_event_id: Optional[str] = None
        # DB persistence
        self._session_store = session_store
        self._pending_segments: List[str] = []  # final segments not yet flushed
        self._pending_timestamps: List[Dict[str, Any]] = []

    async def start(self):
        """Start the SSE relay task."""
        if self._running:
            logger.warning(f"SSE relay already running for stream {self.stream_id}")
            return
        self._running = True
        self._task = asyncio.create_task(
            self._relay_loop(),
            name=f"sse-relay-{self.stream_id}"
        )
        if self._session_store is not None:
            self._flush_task = asyncio.create_task(
                self._flush_loop(),
                name=f"sse-flush-{self.stream_id}"
            )
        logger.info(f"Started SSE relay for stream {self.stream_id} -> {self.data_url}")

    async def stop(self):
        """Stop the SSE relay and clean up resources."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Final flush of any remaining buffered segments
        await self._flush_pending_segments()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
        self.clients.clear()
        logger.info(f"Stopped SSE relay for stream {self.stream_id}")

    def add_client(self, ws: web.WebSocketResponse):
        """Add a WebSocket client to receive relayed events."""
        self.clients.add(ws)
        logger.info(
            f"Added WebSocket client to SSE relay for stream {self.stream_id} "
            f"(total clients: {len(self.clients)})"
        )

    def remove_client(self, ws: web.WebSocketResponse):
        """Remove a WebSocket client from the relay."""
        self.clients.discard(ws)
        logger.info(
            f"Removed WebSocket client from SSE relay for stream {self.stream_id} "
            f"(remaining clients: {len(self.clients)})"
        )

    @property
    def has_clients(self) -> bool:
        """Check if the relay has any subscribed clients."""
        return len(self.clients) > 0

    # ------------------------------------------------------------------
    # Internal relay logic
    # ------------------------------------------------------------------

    async def _relay_loop(self):
        """Main relay loop with reconnection logic."""
        while self._running:
            try:
                await self._connect_and_relay()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SSE relay error for stream {self.stream_id}: {e}")
                if self._running and self._reconnect_attempts < self._max_reconnect_attempts:
                    self._reconnect_attempts += 1
                    # Use retry delay from SSE server if available, else exponential backoff
                    delay = self._retry_delay if self._retry_delay is not None else (
                        self._base_reconnect_delay * (2 ** (self._reconnect_attempts - 1))
                    )
                    logger.info(
                        f"Reconnecting SSE relay in {delay:.1f}s "
                        f"(attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})"
                    )
                    await self._broadcast({
                        "type": "status",
                        "text": f"Reconnecting to stream... (attempt {self._reconnect_attempts})"
                    })
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        break
                else:
                    logger.error(
                        f"SSE relay giving up for stream {self.stream_id} "
                        f"after {self._reconnect_attempts} attempts"
                    )
                    await self._broadcast({
                        "type": "status",
                        "text": "Stream connection lost. Please try again."
                    })
                    break

        self._running = False

    async def _connect_and_relay(self):
        """Connect to the SSE endpoint and relay events to clients."""
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        # Support SSE reconnection with Last-Event-ID
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id

        self._http_session = aiohttp.ClientSession()
        try:
            async with self._http_session.get(
                self.data_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=60)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(
                        f"SSE connection failed: HTTP {response.status} - {error_text[:200]}"
                    )

                logger.info(
                    f"SSE relay connected to {self.data_url} for stream {self.stream_id}"
                )
                self._reconnect_attempts = 0  # Reset on successful connection
                await self._broadcast({
                    "type": "status",
                    "text": "Connected to transcription stream."
                })

                # Parse SSE events from the streaming response
                buffer = ""
                async for chunk in response.content.iter_any():
                    if not self._running:
                        break

                    buffer += chunk.decode("utf-8", errors="replace")

                    # Normalize \r\n to \n for consistent parsing
                    buffer = buffer.replace("\r\n", "\n")

                    # SSE events are separated by blank lines (\n\n)
                    while "\n\n" in buffer:
                        event_text, buffer = buffer.split("\n\n", 1)
                        event = self._parse_sse_event(event_text)
                        if event:
                            if event.get("id"):
                                self._last_event_id = event["id"]
                            await self._handle_event(event)

                # If we exit the loop without _running being False, the connection dropped
                if self._running:
                    raise Exception("SSE connection closed unexpectedly")

        finally:
            if self._http_session and not self._http_session.closed:
                await self._http_session.close()
                self._http_session = None

    @staticmethod
    def _parse_field(line: str):
        """
        Parse a single SSE field line into (field_name, value).

        Per the SSE spec (HTML Living Standard):
        - If the line contains a colon, the field name is before the first colon
          and the value is after it.
        - If the value starts with a U+0020 SPACE, that single space is removed.
        - If the line has no colon, the entire line is the field name with empty value.
        """
        colon_pos = line.find(":")
        if colon_pos == -1:
            return line, ""

        field = line[:colon_pos]
        value = line[colon_pos + 1:]
        # Per SSE spec: remove one leading space from the value if present
        if value.startswith(" "):
            value = value[1:]
        return field, value

    def _parse_sse_event(self, event_text: str) -> Optional[Dict[str, Any]]:
        """
        Parse an SSE event from raw text.

        SSE format example:
            event: transcription
            data: {"type": "transcription", "text": "Hello", "is_final": false}
            id: 42

        Multiple data lines are joined with newlines per the SSE spec.
        Lines starting with : are comments and are ignored.
        """
        event_type = "message"
        data_lines = []
        event_id = None

        for line in event_text.split("\n"):
            # Skip empty lines and comments
            if not line or line.startswith(":"):
                continue

            field, value = self._parse_field(line)

            if field == "event":
                event_type = value
            elif field == "data":
                data_lines.append(value)
            elif field == "id":
                event_id = value
            elif field == "retry":
                # The retry field tells the client how long to wait before reconnecting
                try:
                    retry_ms = int(value)
                    if retry_ms >= 0:
                        self._retry_delay = retry_ms / 1000.0
                except ValueError:
                    pass  # Ignore invalid retry values

        if not data_lines:
            return None

        # Join multiple data lines with newlines (per SSE spec)
        data_str = "\n".join(data_lines)

        # Try to parse as JSON
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            # If not JSON, wrap as plain text transcription
            data = {"text": data_str}

        return {
            "event": event_type,
            "data": data,
            "id": event_id
        }

    async def _handle_event(self, event: Dict[str, Any]):
        """Handle a parsed SSE event and relay to clients if appropriate."""
        messages = self._normalize_messages(event)
        for message in messages:
            await self._broadcast(message)
            # Buffer transcription text for periodic DB persistence.
            # Buffer ALL transcription messages (delta and final) because the
            # worker cycles the VLLM WebSocket every 15 minutes, so
            # transcription.done events may never arrive before the connection
            # is torn down.
            if self._session_store is not None and message.get("type") in (
                "transcription",
                "transcription.delta",
            ):
                text = (message.get("text") or message.get("delta") or "").strip()
                if text:
                    self._pending_segments.append(text)
            if (
                self._session_store is not None
                and message.get("type") == "text_timestamps"
                and isinstance(message.get("words"), list)
            ):
                self._pending_timestamps.append(message)

    async def _flush_loop(self):
        """Periodically flush buffered transcription segments to the database."""
        while self._running:
            try:
                await asyncio.sleep(self.FLUSH_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            await self._flush_pending_segments()

    async def _flush_pending_segments(self):
        """Write any buffered segments to the database and clear the buffer."""
        if self._session_store is None:
            return
        if not self._pending_segments and not self._pending_timestamps:
            return
        segments, self._pending_segments = self._pending_segments, []
        timestamp_segments, self._pending_timestamps = self._pending_timestamps, []

        # Combine all segment text into a single string and do one DB update
        combined_text = " ".join(seg for seg in segments if seg)
        if combined_text:
            try:
                await self._session_store.update_stream_session(
                    self.stream_id, {"transcription_segment": combined_text}
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist transcription segment for stream %s: %s",
                    self.stream_id, exc
                )

        # Incrementally build the transcriptions row for history/recents
        if segments:
            try:
                await self._session_store.upsert_stream_transcription(self.stream_id, segments)
            except Exception as exc:
                logger.warning(
                    "Failed to upsert live transcription for stream %s: %s",
                    self.stream_id, exc
                )

        for ts_payload in timestamp_segments:
            try:
                await self._session_store.update_stream_session(
                    self.stream_id, {"timestamp_segment": ts_payload}
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist timestamp segment for stream %s: %s",
                    self.stream_id,
                    exc,
                )

    @staticmethod
    def _decode_possible_json(value: Any) -> Any:
        """Decode JSON-like string payloads commonly nested in SSE envelopes."""
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed and trimmed[0] in "[{\"":
                try:
                    return json.loads(trimmed)
                except (json.JSONDecodeError, ValueError):
                    return value
        return value

    def _to_transcription(self, payload: Dict[str, Any], is_final: Optional[bool] = None) -> Dict[str, Any]:
        """Convert provider payload variants into canonical transcription messages."""
        text = payload.get("text")
        if text is None:
            text = payload.get("transcript")
        if text is None:
            text = payload.get("delta")

        final_flag = is_final
        if final_flag is None:
            final_flag = bool(payload.get("is_final") or payload.get("final") or payload.get("done"))

        return {
            "type": "transcription",
            "text": text or "",
            "is_final": final_flag,
        }

    def _extract_messages(self, payload: Any, event_type: str) -> List[Dict[str, Any]]:
        """Extract one or more frontend-compatible messages from provider payloads."""
        payload = self._decode_possible_json(payload)
        messages: List[Dict[str, Any]] = []

        if payload is None:
            return messages

        if isinstance(payload, list):
            for item in payload:
                messages.extend(self._extract_messages(item, event_type))
            return messages

        if isinstance(payload, str):
            if payload.strip().upper() == "[DONE]":
                return messages
            return [{"type": "transcription", "text": payload, "is_final": False}]

        if not isinstance(payload, dict):
            return messages

        msg_type = payload.get("type")
        if isinstance(msg_type, str):
            if msg_type in ("transcription", "status", "error", "translation"):
                return [payload]

            if msg_type in ("text_timestamps", "text_timestamps.error"):
                return [payload]

            if msg_type == "transcription.delta":
                return [payload]

            # Normalize common realtime delta/done event variants.
            if msg_type in (
                "response.audio_transcript.delta",
                "response.text.delta",
            ):
                text = payload.get("delta") or payload.get("text") or ""
                if text:
                    return [{"type": "transcription", "text": text, "is_final": False}]
                return messages

            if msg_type in (
                "transcription.done",
                "response.audio_transcript.done",
                "response.text.done",
            ):
                msg = self._to_transcription(payload, is_final=True)
                if msg.get("text"):
                    return [msg]
                return messages

            # Unwrap provider envelopes that carry nested payloads/items.
            if msg_type in ("data", "data_item", "data-item", "event", "stream_event", "message"):
                for key in ("data", "payload", "message", "item", "items"):
                    if key in payload:
                        messages.extend(self._extract_messages(payload.get(key), event_type))
                if messages:
                    return messages

        # Batch item envelopes are common in provider stream payloads.
        if isinstance(payload.get("items"), list):
            for item in payload["items"]:
                messages.extend(self._extract_messages(item, event_type))
            if messages:
                return messages

        # Dive into common nested objects before falling back to legacy behavior.
        for key in ("data", "payload", "message", "item", "response"):
            if key in payload and isinstance(payload[key], (dict, list, str)):
                messages.extend(self._extract_messages(payload[key], event_type))
        if messages:
            return messages

        # Legacy behavior: untyped dict payloads are treated as transcription data.
        if any(k in payload for k in ("text", "transcript", "delta", "is_final", "final", "done")):
            return [self._to_transcription(payload)]

        return [{"type": "transcription", **payload}]

    def _normalize_messages(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize provider SSE events into frontend-consumable websocket messages."""
        data = event.get("data")
        if data is None:
            return []

        messages = self._extract_messages(data, event.get("event", "message"))
        if not messages:
            logger.debug(f"No relayable messages extracted for stream {self.stream_id}")
        return messages

    async def _broadcast(self, message: Dict[str, Any]):
        """Broadcast a message to all connected WebSocket clients."""
        if not self.clients:
            return

        # Iterate over a copy to avoid RuntimeError if the set changes during iteration
        disconnected = set()
        for ws in list(self.clients):
            try:
                if ws.closed:
                    # Already closed — remove from clients
                    disconnected.add(ws)
                    continue
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket client: {e}")
                disconnected.add(ws)

        if disconnected:
            self.clients -= disconnected


# ----------------------------------------------------------------------
# Global relay registry
# ----------------------------------------------------------------------

# Map of stream_id -> SSERelay instance
_active_relays: Dict[str, SSERelay] = {}


async def get_or_create_relay(stream_id: str, data_url: str, session_store=None) -> SSERelay:
    """
    Get an existing SSE relay for a stream, or create and start a new one.

    If a relay already exists for the stream_id but with a different data_url,
    the old relay is stopped and a new one is created.

    Args:
        stream_id: The stream session ID
        data_url: The SSE endpoint URL from the compute provider
        session_store: Optional SessionStore used to persist transcription segments.

    Returns:
        The SSERelay instance for this stream
    """
    if stream_id in _active_relays:
        relay = _active_relays[stream_id]
        if relay.data_url != data_url:
            logger.info(
                f"data_url changed for stream {stream_id}, restarting relay"
            )
            await relay.stop()
            del _active_relays[stream_id]
        else:
            return relay

    relay = SSERelay(data_url=data_url, stream_id=stream_id, session_store=session_store)
    _active_relays[stream_id] = relay
    await relay.start()
    return relay


async def stop_relay(stream_id: str):
    """Stop and remove an SSE relay for a stream."""
    if stream_id in _active_relays:
        relay = _active_relays.pop(stream_id)
        await relay.stop()


async def stop_all_relays():
    """Stop all active SSE relays (for application shutdown)."""
    relay_ids = list(_active_relays.keys())
    for stream_id in relay_ids:
        await stop_relay(stream_id)


def get_relay(stream_id: str) -> Optional[SSERelay]:
    """Get the SSE relay for a stream, if it exists."""
    return _active_relays.get(stream_id)


def get_active_relay_count() -> int:
    """Return the number of currently active SSE relays."""
    return len(_active_relays)