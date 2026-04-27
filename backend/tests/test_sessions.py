#!/usr/bin/env python3
"""Tests for SessionStore persistence behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import importlib
import types

import sys
sys.path.insert(0, '.')


class TestSessionStoreStreamPersistence(unittest.IsolatedAsyncioTestCase):
    """Regression tests for stream session persistence and parent session integrity."""

    def _import_sessions_with_stubbed_supabase(self):
        """Import sessions.py with a stubbed supabase_client module."""
        fake_supabase_module = SimpleNamespace(supabase=MagicMock())
        fake_payment_strategy_module = types.ModuleType("payments.payment_strategy")

        def _noop_decorator(*args, **kwargs):
            def _inner(func):
                return func
            return _inner

        fake_payment_strategy_module.x402_or_subscription = _noop_decorator

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
        user_sessions_table.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
        user_sessions_table.insert.return_value.execute.return_value = SimpleNamespace(data=[{"id": session_id}])

        stream_sessions_table = MagicMock()
        stream_sessions_table.insert.return_value.execute.return_value = SimpleNamespace(data=[{"id": "stream-1"}])

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
        user_sessions_table.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": session_id, "user_id": "bd8d8be8-54dd-45d2-b2de-af9247f6f65d"}]
        )

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


if __name__ == "__main__":
    unittest.main()
