#!/usr/bin/env python3
"""Tests for user_routes stream sentence responses."""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _identity_decorator(func):
    return func


class _QueryStub:
    def __init__(self, table_name, responses):
        self.table_name = table_name
        self.responses = responses

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def execute(self):
        return SimpleNamespace(data=self.responses.get(self.table_name, []))


class _SupabaseStub:
    def __init__(self, responses):
        self.responses = responses

    def table(self, table_name):
        return _QueryStub(table_name, self.responses)


class TestUserRoutesStreamSentences(unittest.IsolatedAsyncioTestCase):
    def _import_user_routes(self):
        fake_auth = types.ModuleType("auth")
        setattr(fake_auth, "require_user_auth", _identity_decorator)

        fake_supabase_module = types.ModuleType("supabase_client")
        setattr(fake_supabase_module, "async_supabase", MagicMock())

        with patch.dict(sys.modules, {
            "auth": fake_auth,
            "supabase_client": fake_supabase_module,
        }):
            if "user_routes" in sys.modules:
                del sys.modules["user_routes"]
            return importlib.import_module("user_routes")

    async def test_get_stream_sentences_prefers_sentence_level_translations(self):
        user_routes = self._import_user_routes()

        supabase_stub = _SupabaseStub({
            "stream_sessions": [{
                "id": "stream-1",
                "user_id": "user-1",
                "stream_settings": {
                    "translation": {
                        "target_language": "es",
                    }
                },
            }],
            "transcription_sentences": [
                {"sentence_index": 0, "text": "Hello", "translated_text": "Hola", "timestamp": "00:00:01"},
                {"sentence_index": 1, "text": "World", "translated_text": "Mundo", "timestamp": "00:00:02"},
            ],
            "translations": [
                {"id": "tr-1", "target_language": "es", "translated_text": "Hola\nMundo", "sentence_index": None, "original_text": "Hello\nWorld", "created_at": "2026-05-20T00:00:00Z"},
            ],
        })

        class _Request:
            match_info = {"id": "stream-1"}

            @property
            def query(self):
                return {}

            def get(self, key):
                if key == "user":
                    return SimpleNamespace(id="user-1")
                return None

        with patch.object(user_routes, "supabase", supabase_stub):
            response = await user_routes.get_stream_sentences(_Request())

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["translated_languages"], ["es"])
        self.assertEqual(
            payload["translations_by_language"]["es"],
            [
                {"sentence_index": 0, "text": "Hello", "translated_text": "Hola", "timestamp": "00:00:01"},
                {"sentence_index": 1, "text": "World", "translated_text": "Mundo", "timestamp": "00:00:02"},
            ],
        )


if __name__ == "__main__":
    unittest.main()