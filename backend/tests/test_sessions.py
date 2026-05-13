#!/usr/bin/env python3
"""Tests for SessionStore persistence behavior."""

import unittest
import json
from typing import Any, cast
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import importlib
import types

import sys
sys.path.insert(0, '.')


class TestSessionStoreStreamPersistence(unittest.IsolatedAsyncioTestCase):
    """Regression tests for stream session persistence and parent session integrity."""

    def _import_sessions_with_stubbed_supabase(self):
        """Import sessions.py with a stubbed supabase_client module."""
        fake_supabase = MagicMock()
        fake_supabase_module = SimpleNamespace(supabase=fake_supabase, async_supabase=fake_supabase)
        fake_payment_strategy_module = types.ModuleType("payments.payment_strategy")

        def _noop_decorator(*args, **kwargs):
            def _inner(func):
                return func
            return _inner

        setattr(fake_payment_strategy_module, "x402_or_subscription", _noop_decorator)

        with patch.dict(sys.modules, {
            "supabase_client": fake_supabase_module,
            "payments.payment_strategy": fake_payment_strategy_module,
        }):
            if "sessions" in sys.modules:
                del sys.modules["sessions"]
            return importlib.import_module("sessions")

    async def test_create_stream_session_ensures_parent_user_session(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        store = sessions.SessionStore()
        session_id = "20f6adaa-38a2-46d1-9dad-876e85b56c11"
        user_id = "73a3bd8f-650f-4960-af4d-2f85059a67f7"

        user_sessions_table = MagicMock()
        user_sessions_table.select.return_value.eq.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[]))
        user_sessions_table.insert.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": session_id}]))

        stream_sessions_table = MagicMock()
        stream_sessions_table.insert.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "stream-1"}]))

        supabase_mock = MagicMock()

        def table_side_effect(table_name):
            if table_name == "user_sessions":
                return user_sessions_table
            if table_name == "stream_sessions":
                return stream_sessions_table
            raise AssertionError(f"Unexpected table: {table_name}")

        supabase_mock.table.side_effect = table_side_effect

        with patch.object(sessions, "supabase", supabase_mock):
            stream_id = await store.create_stream_session(
                session_id=session_id,
                language="en",
                provider_session_data={"provider": "livepeer-primary"},
                user_id=user_id,
            )

        self.assertIsInstance(stream_id, str)
        user_sessions_table.insert.assert_called_once()
        inserted_parent = user_sessions_table.insert.call_args[0][0]
        self.assertEqual(inserted_parent["id"], session_id)
        self.assertEqual(inserted_parent["user_id"], user_id)

        stream_sessions_table.insert.assert_called_once()
        inserted_stream = stream_sessions_table.insert.call_args[0][0]
        self.assertEqual(inserted_stream["user_session_id"], session_id)

    async def test_create_stream_session_rejects_cross_user_parent_session(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        store = sessions.SessionStore()
        session_id = "20f6adaa-38a2-46d1-9dad-876e85b56c11"

        user_sessions_table = MagicMock()
        user_sessions_table.select.return_value.eq.return_value.execute = AsyncMock(return_value=SimpleNamespace(
            data=[{"id": session_id, "user_id": "bd8d8be8-54dd-45d2-b2de-af9247f6f65d"}]
        ))

        stream_sessions_table = MagicMock()

        supabase_mock = MagicMock()

        def table_side_effect(table_name):
            if table_name == "user_sessions":
                return user_sessions_table
            if table_name == "stream_sessions":
                return stream_sessions_table
            raise AssertionError(f"Unexpected table: {table_name}")

        supabase_mock.table.side_effect = table_side_effect

        with patch.object(sessions, "supabase", supabase_mock):
            with self.assertRaises(ValueError):
                await store.create_stream_session(
                    session_id=session_id,
                    language="en",
                    provider_session_data={"provider": "livepeer-primary"},
                    user_id="58db5d03-fec1-4eb3-93dc-a3a8f028f6c6",
                )

        user_sessions_table.insert.assert_not_called()
        stream_sessions_table.insert.assert_not_called()

    async def test_record_stream_usage_records_one_interval(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        store = sessions.SessionStore()
        session_id = "session-1"
        stream_id = "stream-1"

        store._sessions_cache[session_id] = {
            "id": session_id,
            "user_id": "user-1",
            "created_at": 0,
            "last_activity": 0,
            "transcriptions": [],
            "stream_sessions": [stream_id],
            "settings": {"default_language": "en", "translate_to": []},
        }
        store._stream_sessions_cache[stream_id] = {
            "id": stream_id,
            "session_id": session_id,
            "language": "en",
            "status": "active",
            "created_at": 100.0,
            "updated_at": 160.0,
            "provider_session": {"model": "voxtral-realtime", "hardware": "gpu"},
            "total_audio_bytes": 0,
            "transcription_segments": [],
            "final_text": "",
        }

        usage_table = MagicMock()
        usage_table.insert.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[]))

        supabase_mock = MagicMock()

        def table_side_effect(table_name):
            if table_name == "transcription_usage":
                return usage_table
            raise AssertionError(f"Unexpected table: {table_name}")

        supabase_mock.table.side_effect = table_side_effect

        with patch.object(sessions, "supabase", supabase_mock):
            billed = await store._record_stream_usage(
                stream_data=store._stream_sessions_cache[stream_id],
                duration_seconds=60,
                final_text="hello world from stream",
            )

        self.assertTrue(billed)
        usage_table.insert.assert_called_once()
        payload = usage_table.insert.call_args[0][0]
        self.assertEqual(payload["user_id"], "user-1")
        self.assertEqual(payload["duration_seconds"], 60)
        self.assertEqual(payload["word_count"], 4)
        self.assertEqual(payload["source_type"], "stream")
        self.assertEqual(payload["hardware"], "gpu")
        self.assertEqual(payload["model"], "voxtral-realtime")

    async def test_close_stream_session_only_updates_status(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        store = sessions.SessionStore()
        session_id = "session-1"
        stream_id = "stream-2"

        store._sessions_cache[session_id] = {
            "id": session_id,
            "user_id": "user-1",
            "created_at": 0,
            "last_activity": 0,
            "transcriptions": [],
            "stream_sessions": [stream_id],
            "settings": {"default_language": "en", "translate_to": []},
        }
        store._stream_sessions_cache[stream_id] = {
            "id": stream_id,
            "session_id": session_id,
            "language": "en",
            "status": "active",
            "created_at": 100.0,
            "updated_at": 140.0,
            "provider_session": {},
            "total_audio_bytes": 0,
            "transcription_segments": [],
            "final_text": "",
        }

        stream_sessions_table = MagicMock()
        stream_sessions_table.update.return_value.eq.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[]))

        supabase_mock = MagicMock()

        def table_side_effect(table_name):
            if table_name == "stream_sessions":
                return stream_sessions_table
            raise AssertionError(f"Unexpected table: {table_name}")

        supabase_mock.table.side_effect = table_side_effect

        with patch.object(sessions, "supabase", supabase_mock):
            await store.close_stream_session(stream_id, "stream completed")

        self.assertEqual(store._stream_sessions_cache[stream_id]["status"], "completed")
        self.assertEqual(store._stream_sessions_cache[stream_id]["final_text"], "stream completed")

    async def test_update_stream_translation_config_persists_provider_metadata(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        store = sessions.SessionStore()
        stream_id = "stream-translation"
        store._stream_sessions_cache[stream_id] = {
            "id": stream_id,
            "session_id": "session-1",
            "language": "en",
            "source_language": "en",
            "target_language": None,
            "status": "active",
            "created_at": 100.0,
            "updated_at": 140.0,
            "provider_session": {
                "provider": "livepeer",
                "provider_stream_id": "provider-1",
            },
            "total_audio_bytes": 0,
            "transcription_segments": [],
            "final_text": "",
        }

        stream_sessions_table = MagicMock()
        stream_sessions_table.update.return_value.eq.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[]))

        supabase_mock = MagicMock()
        supabase_mock.table.side_effect = lambda table_name: stream_sessions_table if table_name == "stream_sessions" else (_ for _ in ()).throw(AssertionError(f"Unexpected table: {table_name}"))

        with patch.object(sessions, "supabase", supabase_mock):
            result = await store.update_stream_translation_config(stream_id, "en", "es")

        self.assertIsNotNone(result)
        self.assertEqual(result["source_language"], "en")
        self.assertEqual(result["target_language"], "es")
        self.assertEqual(result["provider_session"]["metadata"]["target_language"], "es")
        stream_sessions_table.update.assert_called_once()

    async def test_stream_payload_indicates_running(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        self.assertTrue(sessions._stream_payload_indicates_running({"status": "running"}))
        self.assertTrue(sessions._stream_payload_indicates_running({"live": True}))
        self.assertFalse(sessions._stream_payload_indicates_running({"status": "stopped"}))
        self.assertFalse(sessions._stream_payload_indicates_running({"is_running": False}))

    async def test_update_stream_session_persists_timestamp_segments(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        store = sessions.SessionStore()
        stream_id = "stream-3"
        store._stream_sessions_cache[stream_id] = {
            "id": stream_id,
            "session_id": "session-1",
            "language": "en",
            "status": "active",
            "created_at": 100.0,
            "updated_at": 140.0,
            "provider_session": {},
            "total_audio_bytes": 0,
            "transcription_segments": [],
            "text_timestamps": [],
            "transcription_id": "tx-1",
        }

        stream_sessions_table = MagicMock()
        stream_sessions_table.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])

        transcriptions_table = MagicMock()
        transcriptions_table.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])

        supabase_mock = MagicMock()

        def table_side_effect(table_name):
            if table_name == "stream_sessions":
                return stream_sessions_table
            if table_name == "transcriptions":
                return transcriptions_table
            raise AssertionError(f"Unexpected table: {table_name}")

        supabase_mock.table.side_effect = table_side_effect

        payload = {
            "type": "text_timestamps",
            "window_id": 2,
            "transcript": "hello world",
            "words": [{"word": "hello", "start": 0.0, "end": 0.4}],
        }

        with patch.object(sessions, "supabase", supabase_mock):
            await store.update_stream_session(stream_id, {"timestamp_segment": payload})

        self.assertEqual(store._stream_sessions_cache[stream_id]["text_timestamps"], [payload])
        transcriptions_table.update.assert_called_once()
        update_payload = transcriptions_table.update.call_args[0][0]
        self.assertIn("segments", update_payload)
        self.assertEqual(update_payload["segments"], [payload])

    async def test_compute_provider_manager_resolves_legacy_provider_alias(self):
        from compute_providers.provider_manager import ComputeProviderManager

        manager = ComputeProviderManager()
        provider = SimpleNamespace(enabled=True, provider_name="livepeer-primary")
        manager.providers["livepeer-primary"] = cast(Any, provider)
        manager.default_provider = "livepeer-primary"

        self.assertIs(manager.get_provider("livepeer"), provider)

    async def test_update_stream_session_uses_provider_update_method(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        provider = MagicMock()
        provider.update_streaming_session = AsyncMock()
        provider_manager = MagicMock()
        provider_manager.get_provider.return_value = provider

        stream_id = "stream-legacy-provider"
        stream_session = {
            "id": stream_id,
            "status": "active",
            "total_audio_bytes": 512,
            "provider_session": {
                "provider": "livepeer",
                "provider_stream_id": "provider-stream-1",
                "update_url": "http://provider.example/update",
            },
        }

        request = SimpleNamespace(
            match_info={"stream_id": stream_id},
            json=AsyncMock(return_value={
                "audio_bytes": 512,
                "transcription_segment": {"text": "hello"},
            }),
        )

        session_store_mock = MagicMock()
        session_store_mock.has_stream_session = AsyncMock(return_value=True)
        session_store_mock.get_stream_session = AsyncMock(side_effect=[stream_session, stream_session])
        session_store_mock.get_provider_urls = AsyncMock(return_value={
            "update_url": "http://provider.example/update",
            "provider_stream_id": "provider-stream-1",
        })
        session_store_mock.update_stream_session = AsyncMock()

        with patch.object(sessions, "session_store", session_store_mock), \
             patch.object(sessions, "_verify_stream_ownership", AsyncMock(return_value=True)), \
             patch.object(sessions, "_build_compute_provider_manager", return_value=provider_manager):
            response = await sessions.update_stream_session(request)

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["stream_id"], stream_id)
        provider_manager.get_provider.assert_called_once_with("livepeer")
        provider.update_streaming_session.assert_awaited_once_with(
            provider_stream_id="provider-stream-1",
            params={
                "audio_bytes": 512,
                "transcription_segment": {"text": "hello"},
            },
            capability="live-transcription",
        )
        session_store_mock.update_stream_session.assert_awaited_once_with(stream_id, {
            "audio_bytes": 512,
            "transcription_segment": {"text": "hello"},
        })

    async def test_bill_active_stream_minutes_skips_streams_without_activity(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        stream_row = {
            "id": "stream-idle",
            "user_session_id": "session-1",
            "language": "en",
            "provider_session": {"status_url": "https://provider.example/status"},
            "status": "active",
            "created_at": "2026-05-13T00:00:00Z",
            "updated_at": "2026-05-13T00:00:10Z",
            "total_audio_bytes": 0,
            "transcription_segments": [],
            "final_text": "",
        }

        stream_sessions_table = MagicMock()
        stream_sessions_table.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=SimpleNamespace(data=[stream_row])
        )

        supabase_mock = MagicMock()
        supabase_mock.table.side_effect = lambda table_name: stream_sessions_table if table_name == "stream_sessions" else (_ for _ in ()).throw(AssertionError(f"Unexpected table: {table_name}"))

        record_usage = AsyncMock(return_value=True)
        row_to_stream_session = MagicMock(return_value={"id": "stream-idle"})

        with patch.object(sessions, "supabase", supabase_mock), \
             patch.object(sessions, "_provider_stream_is_running", AsyncMock(return_value=True)), \
             patch.object(sessions.session_store, "_record_stream_usage", record_usage), \
             patch.object(sessions.session_store, "_row_to_stream_session", row_to_stream_session):
            sessions._stream_usage_billed_minute.clear()
            await sessions._bill_active_stream_minutes()

        record_usage.assert_not_awaited()
        row_to_stream_session.assert_not_called()

    async def test_bill_active_stream_minutes_bills_streams_with_activity(self):
        sessions = self._import_sessions_with_stubbed_supabase()

        stream_row = {
            "id": "stream-active",
            "user_session_id": "session-1",
            "language": "en",
            "provider_session": {"status_url": "https://provider.example/status"},
            "status": "active",
            "created_at": "2026-05-13T00:00:00Z",
            "updated_at": "2026-05-13T00:00:10Z",
            "total_audio_bytes": 256,
            "transcription_segments": [],
            "final_text": "",
        }

        stream_sessions_table = MagicMock()
        stream_sessions_table.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=SimpleNamespace(data=[stream_row])
        )

        supabase_mock = MagicMock()
        supabase_mock.table.side_effect = lambda table_name: stream_sessions_table if table_name == "stream_sessions" else (_ for _ in ()).throw(AssertionError(f"Unexpected table: {table_name}"))

        record_usage = AsyncMock(return_value=True)
        row_to_stream_session = MagicMock(return_value={"id": "stream-active"})

        with patch.object(sessions, "supabase", supabase_mock), \
             patch.object(sessions, "_provider_stream_is_running", AsyncMock(return_value=True)), \
             patch.object(sessions.session_store, "_record_stream_usage", record_usage), \
             patch.object(sessions.session_store, "_row_to_stream_session", row_to_stream_session):
            sessions._stream_usage_billed_minute.clear()
            await sessions._bill_active_stream_minutes()

        row_to_stream_session.assert_called_once_with(stream_row)
        record_usage.assert_awaited_once_with({"id": "stream-active"}, duration_seconds=60)


if __name__ == "__main__":
    unittest.main()
