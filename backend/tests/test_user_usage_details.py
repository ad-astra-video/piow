#!/usr/bin/env python3
"""Regression tests for user usage-details pagination behavior."""

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
    def __init__(self, pages):
        self.pages = pages
        self._start = 0

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        if _args:
            self._start = int(_args[0])
        return self

    async def execute(self):
        page_index = self._start // 1000
        if page_index < len(self.pages):
            page = self.pages[page_index]
            return SimpleNamespace(data=page)
        return SimpleNamespace(data=[])


class _SupabaseStub:
    def __init__(self, table_pages):
        self.table_pages = table_pages

    def table(self, table_name):
        pages = self.table_pages.get(table_name)
        if pages is None:
            return _QueryStub([[]])
        return _QueryStub(pages)


class TestUserUsageDetailsPagination(unittest.IsolatedAsyncioTestCase):
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

    async def test_usage_details_sums_all_transcription_pages(self):
        user_routes = self._import_user_routes()

        # 1000 rows on first page + 50 rows on second page = 1050 minutes
        first_page = [{"duration_seconds": 60, "word_count": 1, "created_at": "2026-05-01T00:00:00Z", "source_type": "stream"}] * 1000
        second_page = [{"duration_seconds": 60, "word_count": 1, "created_at": "2026-05-01T01:00:00Z", "source_type": "stream"}] * 50

        supabase_stub = _SupabaseStub({
            "transcription_usage": [first_page, second_page],
            "translation_usage": [],
            "transcriptions": [],
            "translations": [],
        })

        class _Request:
            query = {"days": "30"}

            def get(self, key):
                if key == "user":
                    return SimpleNamespace(id="user-1")
                return None

        with patch.object(user_routes, "supabase", supabase_stub):
            response = await user_routes.get_usage_details(_Request())

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["transcription"]["total_seconds"], 63000)

    async def test_usage_details_sums_all_translation_pages(self):
        user_routes = self._import_user_routes()

        first_page = [{"characters_translated": 2000, "created_at": "2026-05-01T00:00:00Z"}] * 1000
        second_page = [{"characters_translated": 2000, "created_at": "2026-05-01T00:10:00Z"}] * 10

        supabase_stub = _SupabaseStub({
            "transcription_usage": [],
            "translation_usage": [first_page, second_page],
            "transcriptions": [],
            "translations": [],
        })

        class _Request:
            query = {"days": "30"}

            def get(self, key):
                if key == "user":
                    return SimpleNamespace(id="user-1")
                return None

        with patch.object(user_routes, "supabase", supabase_stub):
            response = await user_routes.get_usage_details(_Request())

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["translation"]["total_characters"], 2020000)


if __name__ == "__main__":
    unittest.main()
