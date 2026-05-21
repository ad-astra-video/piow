#!/usr/bin/env python3
"""Tests for auto JSON schema generation integration in the worker."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# Must mock ALL heavy dependencies BEFORE any import from app
test_dir = os.path.dirname(os.path.abspath(__file__))
worker_dir = os.path.dirname(test_dir)
sys.path.insert(0, worker_dir)

# Mock heavy deps
sys.modules['av'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['pytrickle'] = MagicMock()
sys.modules['pytrickle.decorators'] = MagicMock()
for decorator_name in ['audio_handler', 'video_handler', 'on_stream_start', 'on_stream_stop', 'model_loader', 'param_updater']:
    setattr(sys.modules['pytrickle.decorators'], decorator_name, lambda f=None, **kw: (lambda fn: fn) if f is None else f)

sys.modules['vllm_client'] = MagicMock()

# Mock aiohttp.web
aiohttp_mod = sys.modules.get('aiohttp', MagicMock())
aiohttp_mod.web = MagicMock()
aiohttp_mod.web.Request = MagicMock
aiohttp_mod.web.Response = MagicMock
aiohttp_mod.web.json_response = lambda *a, **k: MagicMock()
sys.modules['aiohttp'] = aiohttp_mod

# Now safe to import app
from app import LiveTranscriptionWorker


class TestSchemaGenerationIntegration(unittest.IsolatedAsyncioTestCase):
    @patch("app.gemma_translator")
    @patch("app.processor")
    async def test_generate_and_emit_schema_success(self, mock_processor, mock_gemma):
        worker = LiveTranscriptionWorker()
        worker.analysis_enabled = True
        worker.analysis_prompt = "Summarize key decisions"
        worker.analysis_mode = "multimodal"
        worker.analysis_response_format = None

        mock_gemma.generate_analysis_schema = AsyncMock(return_value={
            "schema": {
                "type": "object",
                "title": "DecisionAnalysis",
                "properties": {"summary": {"type": "string"}},
            },
            "model": "test-model",
            "backend": "gemma-4-e4b",
        })
        mock_processor.send_data = AsyncMock()

        await worker._generate_and_emit_schema()

        mock_gemma.generate_analysis_schema.assert_awaited_once_with(
            analysis_prompt="Summarize key decisions",
            mode="multimodal",
            max_tokens=2048,
        )
        self.assertIsNotNone(worker.analysis_response_format)
        self.assertEqual(worker.analysis_response_format["type"], "json_object")
        self.assertEqual(worker.analysis_response_format["schema"]["title"], "DecisionAnalysis")
        mock_processor.send_data.assert_awaited_once()
        import json
        sent = json.loads(mock_processor.send_data.call_args.args[0])
        self.assertEqual(sent["type"], "analysis_response_format")
        self.assertEqual(sent["schema"]["title"], "DecisionAnalysis")

    @patch("app.gemma_translator")
    @patch("app.processor")
    async def test_generate_and_emit_schema_error_response(self, mock_processor, mock_gemma):
        worker = LiveTranscriptionWorker()
        worker.analysis_enabled = True
        worker.analysis_prompt = "Test prompt"
        worker.analysis_mode = "multimodal"
        worker.analysis_response_format = None

        mock_gemma.generate_analysis_schema = AsyncMock(return_value={
            "error": "Model timeout",
            "model": "test-model",
            "backend": "gemma-4-e4b",
        })
        mock_processor.send_data = AsyncMock()

        await worker._generate_and_emit_schema()

        self.assertIsNone(worker.analysis_response_format)
        mock_processor.send_data.assert_awaited_once()
        import json
        sent = json.loads(mock_processor.send_data.call_args.args[0])
        self.assertEqual(sent["type"], "analysis_response_format")
        self.assertIsNone(sent["schema"])
        self.assertEqual(sent["error"], "Model timeout")

    @patch("app.gemma_translator")
    @patch("app.processor")
    async def test_generate_and_emit_schema_skips_when_disabled(self, mock_processor, mock_gemma):
        worker = LiveTranscriptionWorker()
        worker.analysis_enabled = False
        worker.analysis_prompt = "Test prompt"

        mock_gemma.generate_analysis_schema = AsyncMock()

        await worker._generate_and_emit_schema()

        mock_gemma.generate_analysis_schema.assert_not_called()
        mock_processor.send_data.assert_not_called()

    @patch("app.gemma_translator")
    @patch("app.processor")
    async def test_generate_and_emit_schema_skips_when_no_prompt(self, mock_processor, mock_gemma):
        worker = LiveTranscriptionWorker()
        worker.analysis_enabled = True
        worker.analysis_prompt = ""

        mock_gemma.generate_analysis_schema = AsyncMock()

        await worker._generate_and_emit_schema()

        mock_gemma.generate_analysis_schema.assert_not_called()

    @patch("app.gemma_translator")
    @patch("app.processor")
    async def test_generate_and_emit_schema_overwrites_existing_format(self, mock_processor, mock_gemma):
        worker = LiveTranscriptionWorker()
        worker.analysis_enabled = True
        worker.analysis_prompt = "Test prompt"
        worker.analysis_response_format = {"type": "json_object", "schema": {"title": "Old"}}

        mock_gemma.generate_analysis_schema = AsyncMock(return_value={
            "schema": {"type": "object", "title": "New"},
            "model": "test-model",
        })
        mock_processor.send_data = AsyncMock()

        await worker._generate_and_emit_schema()

        mock_gemma.generate_analysis_schema.assert_awaited_once()
        self.assertEqual(worker.analysis_response_format["schema"]["title"], "New")

    @patch("app.gemma_translator")
    @patch("app.processor")
    async def test_generate_and_emit_schema_no_processor(self, mock_processor, mock_gemma):
        worker = LiveTranscriptionWorker()
        worker.analysis_enabled = True
        worker.analysis_prompt = "Test prompt"

        mock_gemma.generate_analysis_schema = AsyncMock(return_value={
            "schema": {"type": "object"},
            "model": "test-model",
        })
        mock_processor.send_data = AsyncMock()

        await worker._generate_and_emit_schema()

        mock_processor.send_data.assert_awaited_once()

    def test_apply_analysis_params_sets_response_format(self):
        worker = LiveTranscriptionWorker()
        params = {
            "analysis_response_format": {"type": "json_object", "schema": {"title": "Custom"}},
        }
        worker._apply_analysis_params(params)
        self.assertIsNotNone(worker.analysis_response_format)
        self.assertEqual(worker.analysis_response_format["schema"]["title"], "Custom")

    def test_apply_analysis_params_clears_response_format_with_empty_string(self):
        worker = LiveTranscriptionWorker()
        worker.analysis_response_format = {"type": "json_object", "schema": {}}
        params = {"analysis_response_format": ""}
        worker._apply_analysis_params(params)
        self.assertIsNone(worker.analysis_response_format)

    def test_apply_analysis_params_preserves_format_when_none_passed(self):
        worker = LiveTranscriptionWorker()
        worker.analysis_response_format = {"type": "json_object", "schema": {"title": "Existing"}}
        params = {"analysis_response_format": None}
        worker._apply_analysis_params(params)
        self.assertEqual(worker.analysis_response_format["schema"]["title"], "Existing")

    def test_apply_analysis_params_preserves_existing_format(self):
        worker = LiveTranscriptionWorker()
        worker.analysis_response_format = {"type": "json_object", "schema": {"title": "Existing"}}
        params = {"analysis_enabled": True}
        worker._apply_analysis_params(params)
        self.assertEqual(worker.analysis_response_format["schema"]["title"], "Existing")


if __name__ == "__main__":
    unittest.main()
