#!/usr/bin/env python3
"""
Test suite for the Granite-Speech-4.1-2B-Plus transcriber.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf

# Add the worker directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite_transcriber import (
    Granite4Transcriber,
    create_granite_transcriber,
    granite_health_check,
    _resolve_model_path,
    _parse_word_timestamps,
    _segments_from_speakers,
    DEFAULT_MODEL_ID,
    PUBLIC_MODEL_NAME,
)


class TestGranite4Transcriber(unittest.TestCase):

    def setUp(self):
        self.test_audio_path = ""
        self.create_test_audio_file()

    def tearDown(self):
        if self.test_audio_path and os.path.exists(self.test_audio_path):
            os.unlink(self.test_audio_path)

    def create_test_audio_file(self):
        sample_rate = 16000
        duration = 2.0
        frequency = 440.0
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        audio = np.sin(frequency * 2 * np.pi * t) * 0.3
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        sf.write(temp_file.name, audio, sample_rate)
        self.test_audio_path = temp_file.name

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_init(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        self.assertIsInstance(transcriber, Granite4Transcriber)
        self.assertEqual(transcriber.sample_rate, 16000)
        self.assertEqual(transcriber.model_ref, DEFAULT_MODEL_ID)
        self.assertFalse(transcriber.is_loaded)

    def test_is_available(self):
        transcriber = Granite4Transcriber()
        self.assertIsInstance(transcriber.is_available(), bool)

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_not_loaded(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = False
        transcriber.load_error = 'Granite runtime unavailable'

        result = transcriber.transcribe(self.test_audio_path)

        self.assertIn('error', result)
        self.assertIn('segments', result)
        self.assertEqual(result['model'], PUBLIC_MODEL_NAME)
        self.assertEqual(result['error'], 'Granite runtime unavailable')
        self.assertEqual(result['text'], '')

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_loaded_mock_inference(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True

        with patch.object(transcriber, '_decode_audio_to_array', return_value=np.array([0.1, 0.2, 0.3], dtype=np.float32)), \
             patch.object(transcriber, '_run_generation', return_value='mock transcription'), \
             patch.object(transcriber, '_build_prompt', return_value='PROMPT'):
            result = transcriber.transcribe(self.test_audio_path)

        self.assertEqual(result['text'], 'mock transcription')
        self.assertEqual(result['model'], PUBLIC_MODEL_NAME)
        self.assertGreaterEqual(result['processing_time'], 0)
        self.assertNotIn('words', result)
        self.assertNotIn('speakers', result)

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_with_word_timestamps(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True

        ts_text = "hello [T:45] world [T:82]"
        with patch.object(transcriber, '_decode_audio_to_array', return_value=np.zeros(16000, dtype=np.float32)), \
             patch.object(transcriber, '_run_generation', return_value=ts_text), \
             patch.object(transcriber, '_build_prompt', return_value='PROMPT'):
            result = transcriber.transcribe(self.test_audio_path, with_word_timestamps=True)

        self.assertIn('words', result)
        self.assertEqual(len(result['words']), 2)
        self.assertEqual(result['words'][0], {'word': 'hello', 'end': 0.45})
        self.assertEqual(result['words'][1], {'word': 'world', 'end': 0.82})
        self.assertEqual(result['text'], 'hello world')

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_with_speakers(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True

        saa_text = "[Speaker 1]: hello there [Speaker 2]: hi back"
        with patch.object(transcriber, '_decode_audio_to_array', return_value=np.zeros(16000, dtype=np.float32)), \
             patch.object(transcriber, '_run_generation', return_value=saa_text), \
             patch.object(transcriber, '_build_prompt', return_value='PROMPT'):
            result = transcriber.transcribe(self.test_audio_path, with_speakers=True)

        self.assertIn('speakers', result)
        self.assertEqual(len(result['speakers']), 2)
        self.assertEqual(result['speakers'][0]['speaker'], 1)
        self.assertEqual(result['speakers'][0]['text'], 'hello there')
        self.assertEqual(result['speakers'][1]['speaker'], 2)
        self.assertEqual(result['speakers'][1]['text'], 'hi back')
        self.assertEqual(result['text'], saa_text)

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_translate_not_loaded(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = False

        result = transcriber.translate("Hello world", "en", "es")

        self.assertEqual(result['original_text'], "Hello world")
        self.assertEqual(result['source_language'], "en")
        self.assertEqual(result['target_language'], "es")
        self.assertIn('error', result)
        self.assertEqual(result['model'], PUBLIC_MODEL_NAME)

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_build_prompt(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.tokenizer = MagicMock()
        transcriber.tokenizer.apply_chat_template.return_value = 'PROMPT'

        prompt = transcriber._build_prompt()

        self.assertEqual(prompt, 'PROMPT')
        transcriber.tokenizer.apply_chat_template.assert_called_once()
        # System + user messages were passed
        chat = transcriber.tokenizer.apply_chat_template.call_args[0][0]
        self.assertEqual(chat[0]['role'], 'system')
        self.assertEqual(chat[1]['role'], 'user')

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_build_prompt_modes(self, _mock_load_model):
        transcriber = Granite4Transcriber()
        transcriber.tokenizer = MagicMock()
        transcriber.tokenizer.apply_chat_template.side_effect = lambda chat, **kw: chat[1]['content']

        self.assertIn('transcribe', transcriber._build_prompt(mode='asr').lower())
        self.assertIn('Speaker', transcriber._build_prompt(mode='speakers'))
        self.assertIn('[T:', transcriber._build_prompt(mode='timestamps'))
        ast = transcriber._build_prompt(source_language='en', target_language='fr')
        self.assertIn('translate from English to French', ast)

    def test_create_granite_transcriber(self):
        transcriber = create_granite_transcriber()
        self.assertIsInstance(transcriber, Granite4Transcriber)
        transcriber2 = create_granite_transcriber("/custom/path")
        self.assertIsInstance(transcriber2, Granite4Transcriber)

    def test_resolve_model_path_prefers_worker_models_dir(self):
        resolved = _resolve_model_path()
        self.assertIsInstance(resolved, Path)
        self.assertTrue(
            str(resolved).endswith(os.path.join("worker", "models", "granite-speech-4.1-2b-plus")),
            f"unexpected path: {resolved}",
        )

    def test_granite_health_check(self):
        health = granite_health_check()
        self.assertIn('status', health)
        self.assertIn('module', health)
        self.assertIn('model_loaded', health)
        self.assertIn('model_path', health)
        self.assertIn('model_ref', health)
        self.assertIn('timestamp', health)


class TestTimestampParser(unittest.TestCase):
    def test_parse_simple(self):
        words = _parse_word_timestamps("hello [T:45] world [T:82]")
        self.assertEqual(words, [{'word': 'hello', 'end': 0.45}, {'word': 'world', 'end': 0.82}])

    def test_skip_silence_token(self):
        words = _parse_word_timestamps("hello [T:45] _ [T:60] world [T:82]")
        self.assertEqual([w['word'] for w in words], ['hello', 'world'])

    def test_rollover(self):
        # Tag is centiseconds mod 1000 (rollover every 10s). When the next
        # tag value is smaller than the last unwrapped end, add 10s.
        # Here: 8.50s, then a tag of 10cs -> must unwrap to 10.10s.
        words = _parse_word_timestamps("a [T:850] b [T:10]")
        self.assertAlmostEqual(words[0]['end'], 8.50, places=3)
        self.assertAlmostEqual(words[1]['end'], 10.10, places=3)

    def test_empty(self):
        self.assertEqual(_parse_word_timestamps(""), [])
        self.assertEqual(_parse_word_timestamps("no tags here"), [])


class TestSpeakerSegmentParser(unittest.TestCase):
    def test_two_speakers(self):
        segs = _segments_from_speakers("[Speaker 1]: hi [Speaker 2]: hey")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0], {'speaker': 1, 'text': 'hi'})
        self.assertEqual(segs[1], {'speaker': 2, 'text': 'hey'})

    def test_with_word_timings(self):
        words = [
            {'word': 'hello', 'end': 0.5},
            {'word': 'there', 'end': 1.0},
            {'word': 'hi', 'end': 1.5},
            {'word': 'back', 'end': 2.0},
        ]
        segs = _segments_from_speakers(
            "[Speaker 1]: hello there [Speaker 2]: hi back",
            words=words,
        )
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]['start'], 0.0)
        self.assertEqual(segs[0]['end'], 1.0)
        self.assertEqual(segs[1]['start'], 1.0)
        self.assertEqual(segs[1]['end'], 2.0)

    def test_no_tags_returns_empty(self):
        self.assertEqual(_segments_from_speakers("just plain text"), [])


if __name__ == '__main__':
    unittest.main()
