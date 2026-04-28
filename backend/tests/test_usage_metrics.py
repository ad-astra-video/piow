#!/usr/bin/env python3
"""Tests for usage metric helper functions."""

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


class TestUsageMetricHelpers(unittest.TestCase):
    """Validate helper logic for transcription/translation usage metrics."""

    def _stub_common_modules(self):
        fake_auth_module = types.ModuleType("auth")

        def _noop_decorator(*args, **kwargs):
            def _inner(func):
                return func
            return _inner

        fake_auth_module.no_auth = _noop_decorator
        fake_auth_module.require_user_auth = _noop_decorator

        fake_payments_module = types.ModuleType("payments.payment_strategy")
        fake_payments_module.x402_or_subscription = _noop_decorator

        fake_supabase_module = types.ModuleType("supabase_client")
        fake_supabase_module.supabase = MagicMock()

        fake_provider_manager_module = types.ModuleType("compute_providers.provider_manager")

        class _FakeProviderManager:
            def register_providers_from_definitions(self, _defs):
                return None

            def select_providers(self, *args, **kwargs):
                return []

        fake_provider_manager_module.ComputeProviderManager = _FakeProviderManager

        fake_livepeer_module = types.ModuleType("compute_providers.livepeer.livepeer")
        fake_livepeer_module.LivepeerComputeProvider = object

        fake_provider_defs_module = types.ModuleType("compute_providers.provider_definitions")
        fake_provider_defs_module.PROVIDER_DEFINITIONS = []

        return {
            "auth": fake_auth_module,
            "payments.payment_strategy": fake_payments_module,
            "supabase_client": fake_supabase_module,
            "compute_providers.provider_manager": fake_provider_manager_module,
            "compute_providers.livepeer.livepeer": fake_livepeer_module,
            "compute_providers.provider_definitions": fake_provider_defs_module,
        }

    def _import_translate(self):
        stubs = self._stub_common_modules()
        with patch.dict(sys.modules, stubs):
            if "translate" in sys.modules:
                del sys.modules["translate"]
            return importlib.import_module("translate")

    def _import_transcribe(self):
        stubs = self._stub_common_modules()
        fake_translate_module = types.ModuleType("translate")
        fake_translate_module.translate_text = lambda request: None
        fake_translate_module.translate_transcription = lambda request: None

        fake_agents_module = types.ModuleType("agents")
        for name in (
            "agent_register",
            "agent_get_usage",
            "agent_list_keys",
            "agent_create_key",
            "agent_revoke_key",
            "agent_get_subscription",
            "agent_create_subscription",
            "agent_delete_subscription",
            "agent_reactivate_subscription",
        ):
            setattr(fake_agents_module, name, lambda *args, **kwargs: None)

        fake_languages_module = types.ModuleType("languages")
        fake_languages_module.get_languages = lambda request: None

        stubs.update({
            "translate": fake_translate_module,
            "agents": fake_agents_module,
            "languages": fake_languages_module,
        })

        with patch.dict(sys.modules, stubs):
            if "transcribe" in sys.modules:
                del sys.modules["transcribe"]
            return importlib.import_module("transcribe")

    def test_translation_total_text_sent_chars(self):
        translate = self._import_translate()

        self.assertEqual(translate._get_total_text_sent_chars("hello"), 5)
        self.assertEqual(translate._get_total_text_sent_chars(["ab", "cde"]), 5)
        self.assertEqual(
            translate._get_total_text_sent_chars({"a": "abc", "b": ["de", "f"]}),
            6,
        )
        self.assertEqual(translate._get_total_text_sent_chars(None), 0)

    def test_translation_success_result_helper(self):
        translate = self._import_translate()

        self.assertTrue(translate._is_successful_translation_result({"status": "completed"}))
        self.assertTrue(translate._is_successful_translation_result({"status": "succeeded"}))
        self.assertTrue(translate._is_successful_translation_result({}))
        self.assertFalse(translate._is_successful_translation_result({"status": "failed"}))
        self.assertFalse(translate._is_successful_translation_result({"status": "error"}))

    def test_audio_duration_seconds_uses_container_duration(self):
        transcribe = self._import_transcribe()

        fake_container = MagicMock()
        fake_container.duration = 3_000_000
        fake_container.streams.audio = []

        class _FakeOpenContext:
            def __enter__(self):
                return fake_container

            def __exit__(self, exc_type, exc, tb):
                return False

        fake_av = types.SimpleNamespace(
            time_base=1_000_000,
            open=lambda _path: _FakeOpenContext(),
        )

        with patch.object(transcribe, "av", fake_av):
            self.assertEqual(transcribe._get_audio_duration_seconds("dummy.wav"), 3)

    def test_transcription_success_result_helper(self):
        transcribe = self._import_transcribe()

        self.assertTrue(transcribe._is_successful_transcription_result({"status": "completed"}))
        self.assertTrue(transcribe._is_successful_transcription_result({}))
        self.assertFalse(transcribe._is_successful_transcription_result({"status": "failed"}))
        self.assertFalse(transcribe._is_successful_transcription_result({"status": "error"}))


if __name__ == "__main__":
    unittest.main()
