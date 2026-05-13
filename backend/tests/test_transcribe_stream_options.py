#!/usr/bin/env python3
"""Regression tests for stream transcription options and quota behavior."""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _identity_decorator(*_args, **_kwargs):
    if _args and callable(_args[0]) and len(_args) == 1 and not _kwargs:
        return _args[0]

    def decorator(func):
        return func

    return decorator


class _ProviderManagerStub:
    def register_providers_from_definitions(self, _definitions):
        return None

    def select_providers(self, *args, **kwargs):
        return []


def _install_transcribe_stubs():
    auth_module = types.ModuleType("auth")
    setattr(auth_module, "no_auth", _identity_decorator)
    setattr(auth_module, "require_user_auth", _identity_decorator)
    setattr(auth_module, "track_usage", _identity_decorator)
    sys.modules["auth"] = auth_module

    payments_package = types.ModuleType("payments")
    setattr(payments_package, "__path__", [])
    payment_strategy_module = types.ModuleType("payments.payment_strategy")
    setattr(payment_strategy_module, "x402_or_subscription", _identity_decorator)
    quotas_module = types.ModuleType("payments.quotas")

    async def _default_check_quota(*_args, **_kwargs):
        return True, {"remaining": -1, "limit": -1, "used": 0, "unlimited": True}

    setattr(quotas_module, "check_quota", _default_check_quota)
    sys.modules["payments"] = payments_package
    sys.modules["payments.payment_strategy"] = payment_strategy_module
    sys.modules["payments.quotas"] = quotas_module

    supabase_module = types.ModuleType("supabase_client")
    setattr(supabase_module, "supabase", MagicMock())
    setattr(supabase_module, "async_supabase", MagicMock())
    sys.modules["supabase_client"] = supabase_module

    provider_manager_module = types.ModuleType("compute_providers.provider_manager")
    setattr(provider_manager_module, "ComputeProviderManager", _ProviderManagerStub)
    sys.modules["compute_providers.provider_manager"] = provider_manager_module

    livepeer_module = types.ModuleType("compute_providers.livepeer.livepeer")
    setattr(livepeer_module, "LivepeerComputeProvider", object)
    sys.modules["compute_providers.livepeer.livepeer"] = livepeer_module

    definitions_module = types.ModuleType("compute_providers.provider_definitions")
    setattr(definitions_module, "PROVIDER_DEFINITIONS", [])
    sys.modules["compute_providers.provider_definitions"] = definitions_module

    agents_module = types.ModuleType("agents")
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
        setattr(agents_module, name, MagicMock())
    sys.modules["agents"] = agents_module

    languages_module = types.ModuleType("languages")
    setattr(languages_module, "get_languages", MagicMock(return_value={"languages": []}))
    sys.modules["languages"] = languages_module

    translate_module = types.ModuleType("translate")
    setattr(translate_module, "translate_text", MagicMock())
    setattr(translate_module, "translate_transcription", MagicMock())
    sys.modules["translate"] = translate_module

    sessions_module = types.ModuleType("sessions")
    setattr(sessions_module, "session_store", MagicMock())

    async def _verify_stream_ownership(_request, _stream_id):
        return True

    setattr(sessions_module, "_verify_stream_ownership", _verify_stream_ownership)
    sys.modules["sessions"] = sessions_module

    sse_relay_module = types.ModuleType("sse_relay")
    setattr(sse_relay_module, "get_relay", MagicMock(return_value=None))
    sys.modules["sse_relay"] = sse_relay_module


class TestTranscribeStreamOptions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _install_transcribe_stubs()
        sys.modules.pop("transcribe", None)
        self.transcribe = importlib.import_module("transcribe")

        self.provider = MagicMock()
        self.provider.provider_name = "test-provider"
        self.provider.update_streaming_session = AsyncMock()
        self.transcribe.compute_provider_manager.select_providers = MagicMock(return_value=[self.provider])
        self.transcribe.compute_provider_manager.get_provider = MagicMock(return_value=self.provider)

        application = web.Application()
        application.router.add_put("/stream/{stream_id}/translation", self.transcribe.update_stream_translation)
        self.server = TestServer(application)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for name in (
            "transcribe",
            "auth",
            "payments",
            "payments.payment_strategy",
            "payments.quotas",
            "supabase_client",
            "compute_providers.provider_manager",
            "compute_providers.livepeer.livepeer",
            "compute_providers.provider_definitions",
            "agents",
            "languages",
            "translate",
            "sessions",
            "sse_relay",
        ):
            sys.modules.pop(name, None)

    async def test_update_stream_translation_registers_relay_callback(self):
        sessions_module = sys.modules["sessions"]
        sse_relay_module = sys.modules["sse_relay"]

        sessions_module.session_store.get_stream_session = AsyncMock(return_value={
            "id": "stream-1",
            "language": "en",
            "source_language": "en",
            "target_language": None,
            "provider_session": {
                "provider": "test-provider",
                "provider_stream_id": "provider-stream-1",
            },
        })
        sessions_module.session_store.update_stream_translation_config = AsyncMock(return_value={
            "id": "stream-1",
            "language": "en",
            "source_language": "en",
            "target_language": "es",
            "provider_session": {
                "provider": "test-provider",
                "provider_stream_id": "provider-stream-1",
                "metadata": {
                    "source_language": "en",
                    "target_language": "es",
                },
            },
        })

        relay = MagicMock()
        relay.set_translation_callback = MagicMock()
        sse_relay_module.get_relay.return_value = relay

        response = await self.client.put(
            "/stream/stream-1/translation",
            json={"source_language": "en", "target_language": "es"},
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["translation_enabled"])
        sessions_module.session_store.update_stream_translation_config.assert_awaited_once_with(
            stream_id="stream-1",
            source_language="en",
            target_language="es",
        )
        callback = relay.set_translation_callback.call_args_list[-1][0][0]
        self.assertTrue(callable(callback))

        await callback("Hello world.")

        self.provider.update_streaming_session.assert_awaited_once_with(
            provider_stream_id="provider-stream-1",
            params={
                "translate_sentence": "Hello world.",
                "source_language": "en",
                "target_language": "es",
            },
            capability="live-transcription",
            timeout_seconds=30,
        )

    async def test_transcribe_stream_blocks_when_quota_exceeded(self):
        self.transcribe.compute_provider_manager.select_providers = MagicMock(return_value=[])

        class _MockSupabaseTable:
            def __init__(self):
                self._select = self
                self._eq = self

            def select(self, *_args, **_kwargs):
                return self._select

            def eq(self, *_args, **_kwargs):
                return self._eq

            async def execute(self):
                return types.SimpleNamespace(data=[{"plan": "free", "status": "active"}])

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = _MockSupabaseTable()

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"language": "en"})
        mock_request.get.side_effect = lambda key, default=None: {
            "user": types.SimpleNamespace(id="user-1"),
            "agent": None,
        }.get(key, default)

        with patch.object(self.transcribe, "supabase", mock_supabase), \
             patch("payments.quotas.check_quota", AsyncMock(return_value=(False, {
                 "remaining": 0,
                 "limit": 1800,
                 "used": 1800,
                 "unlimited": False,
             }))):
            response = await self.transcribe.transcribe_stream(mock_request)

        self.assertEqual(response.status, 402)
        payload = json.loads(response.text)
        self.assertEqual(payload["code"], "quota_exceeded")
        self.assertEqual(payload["service_type"], "transcribe_gpu")


if __name__ == "__main__":
    unittest.main()
