#!/usr/bin/env python3
"""Regression tests for batch transcription option forwarding."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import FormData, web
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
    payment_strategy_module = types.ModuleType("payments.payment_strategy")
    setattr(payment_strategy_module, "x402_or_subscription", _identity_decorator)
    sys.modules["payments"] = payments_package
    sys.modules["payments.payment_strategy"] = payment_strategy_module

    supabase_module = types.ModuleType("supabase_client")
    setattr(supabase_module, "supabase", MagicMock())
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


class TestTranscribeBatchOptions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _install_transcribe_stubs()
        sys.modules.pop("transcribe", None)
        self.transcribe = importlib.import_module("transcribe")

        self.provider = MagicMock()
        self.provider.provider_name = "test-provider"
        self.provider.create_transcription_job = AsyncMock(return_value={
            "job_id": "job-123",
            "status": "completed",
            "text": "hello",
            "language": "en",
            "segments": [{"text": "hello", "start": 0.0, "end": 1.0}],
            "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "speakers": [{"speaker": 1, "text": "hello"}],
            "word_count": 1,
            "provider": "test-provider",
        })
        self.transcribe.compute_provider_manager.select_providers = MagicMock(return_value=[self.provider])
        self.store_patch = patch.object(self.transcribe, "_store_transcription_result", AsyncMock(return_value="tx-123"))
        self.store_patch.start()
        self.duration_patch = patch.object(self.transcribe, "_probe_duration_from_bytes", return_value=None)
        self.duration_patch.start()

        application = web.Application()
        application.router.add_post("/file", self.transcribe.transcribe_file)
        application.router.add_post("/url", self.transcribe.transcribe_url)
        self.server = TestServer(application)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.duration_patch.stop()
        self.store_patch.stop()
        sys.modules.pop("transcribe", None)

    async def test_transcribe_file_forwards_speaker_and_timestamp_flags(self):
        form = FormData()
        form.add_field("file", b"RIFFdata", filename="sample.wav", content_type="audio/wav")
        form.add_field("language", "en")
        form.add_field("with_speakers", "true")
        form.add_field("with_word_timestamps", "true")

        response = await self.client.post("/file", data=form)

        self.assertEqual(response.status, 200)
        payload = await response.json()
        call = self.provider.create_transcription_job.await_args
        self.assertTrue(call.kwargs["with_speakers"])
        self.assertTrue(call.kwargs["with_word_timestamps"])
        self.assertEqual(payload["words"], [{"word": "hello", "start": 0.0, "end": 0.5}])
        self.assertEqual(payload["speakers"], [{"speaker": 1, "text": "hello"}])

    async def test_transcribe_url_forwards_speaker_and_timestamp_flags(self):
        with patch.object(
            self.transcribe,
            "_safe_download_audio_bytes",
            AsyncMock(return_value=(b"RIFFdata", "remote.wav")),
        ):
            response = await self.client.post(
                "/url",
                json={
                    "audio_url": "https://example.com/audio.wav",
                    "language": "en",
                    "with_speakers": True,
                    "with_word_timestamps": True,
                },
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        call = self.provider.create_transcription_job.await_args
        self.assertTrue(call.kwargs["with_speakers"])
        self.assertTrue(call.kwargs["with_word_timestamps"])
        self.assertEqual(payload["words"], [{"word": "hello", "start": 0.0, "end": 0.5}])
        self.assertEqual(payload["speakers"], [{"speaker": 1, "text": "hello"}])