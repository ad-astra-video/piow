#!/usr/bin/env python3
"""
Test suite for Granite 4.0 transcriber
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

from granite_transcriber import Granite4Transcriber, create_granite_transcriber, granite_health_check, _resolve_model_path

class TestGranite4Transcriber(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_audio_path = ""
        self.create_test_audio_file()
        
    def tearDown(self):
        """Clean up test fixtures"""
        if self.test_audio_path and os.path.exists(self.test_audio_path):
            os.unlink(self.test_audio_path)
    
    def create_test_audio_file(self):
        """Create a temporary audio file for testing"""
        # Generate a simple sine wave for testing
        sample_rate = 16000
        duration = 2.0  # 2 seconds
        frequency = 440.0  # A4 note
        
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        audio = np.sin(frequency * 2 * np.pi * t)
        
        # Normalize to 16-bit range
        audio = audio * 0.3  # Reduce volume to avoid clipping
        
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        sf.write(temp_file.name, audio, sample_rate)
        self.test_audio_path = temp_file.name
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_init(self, mock_load_model):
        """Test transcriber initialization"""
        transcriber = Granite4Transcriber()
        
        self.assertIsInstance(transcriber, Granite4Transcriber)
        self.assertEqual(transcriber.sample_rate, 16000)
        self.assertEqual(transcriber.model_ref, 'ibm-granite/granite-4.0-1b-speech')
        self.assertFalse(transcriber.is_loaded)  # Should be False when _load_model is mocked
    
    def test_is_available(self):
        """Test is_available method"""
        transcriber = Granite4Transcriber()
        # Should return boolean
        result = transcriber.is_available()
        self.assertIsInstance(result, bool)
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_not_loaded(self, mock_load_model):
        """Test transcription when model is not loaded"""
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = False  # Simulate not loaded
        transcriber.load_error = 'Granite runtime unavailable'
        
        result = transcriber.transcribe(self.test_audio_path)
        
        self.assertIn('error', result)
        self.assertIn('segments', result)
        self.assertIn('language', result)
        self.assertIn('duration', result)
        self.assertEqual(result['model'], 'granite-4.0-1b')
        self.assertEqual(result['hardware'], 'cpu')
        self.assertEqual(result['error'], 'Granite runtime unavailable')
        self.assertEqual(result['text'], '')
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_loaded_mock_inference(self, mock_load_model):
        """Test transcription with mocked transformers inference"""
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True

        with patch.object(transcriber, '_decode_audio_to_array', return_value=np.array([0.1, 0.2, 0.3], dtype=np.float32)), \
             patch.object(transcriber, '_run_transcription', return_value='mock transcription'):
            result = transcriber.transcribe(self.test_audio_path)

        # Check result structure
        self.assertIn('text', result)
        self.assertIn('segments', result)
        self.assertEqual(result['text'], "mock transcription")
        self.assertEqual(result['model'], 'granite-4.0-1b')
        self.assertEqual(result['hardware'], 'cpu')
        self.assertGreaterEqual(result['processing_time'], 0)
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_translate_not_loaded(self, mock_load_model):
        """Test translation when model is not loaded"""
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = False
        
        result = transcriber.translate("Hello world", "en", "es")
        
        # Should return explicit not-implemented result
        self.assertIn('original_text', result)
        self.assertIn('translated_text', result)
        self.assertIn('source_language', result)
        self.assertIn('target_language', result)
        self.assertIn('error', result)
        self.assertEqual(result['original_text'], "Hello world")
        self.assertEqual(result['source_language'], "en")
        self.assertEqual(result['target_language'], "es")
        self.assertEqual(result['model'], 'granite-4.0-1b')
        self.assertEqual(result['hardware'], 'cpu')
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_translate_loaded_mock_inference(self, mock_load_model):
        """Test translation with mocked model inference"""
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True
        
        result = transcriber.translate("Hello world", "en", "es")
        
        # Check result
        self.assertEqual(result['original_text'], "Hello world")
        self.assertEqual(result['translated_text'], "")
        self.assertEqual(result['source_language'], "en")
        self.assertEqual(result['target_language'], "es")
        self.assertIn('error', result)
        self.assertEqual(result['model'], 'granite-4.0-1b')
        self.assertEqual(result['hardware'], 'cpu')
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_build_prompt(self, mock_load_model):
        """Test the chat prompt format used for speech transcription."""
        transcriber = Granite4Transcriber()
        transcriber.tokenizer = MagicMock()
        transcriber.tokenizer.apply_chat_template.return_value = 'PROMPT'

        prompt = transcriber._build_prompt()

        self.assertEqual(prompt, 'PROMPT')
        transcriber.tokenizer.apply_chat_template.assert_called_once()

    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_transcribe_returns_error_when_granite_unavailable(self, mock_load_model):
        """Test unavailable Granite backend returns an explicit error."""
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = False
        transcriber.load_error = 'Granite runtime unavailable'

        result = transcriber.transcribe(self.test_audio_path)

        self.assertEqual(result['error'], 'Granite runtime unavailable')
        self.assertEqual(result['text'], '')
        self.assertEqual(result['model'], 'granite-4.0-1b')
    
    def test_create_granite_transcriber(self):
        """Test factory function"""
        transcriber = create_granite_transcriber()
        self.assertIsInstance(transcriber, Granite4Transcriber)
        
        # Test with custom path
        transcriber2 = create_granite_transcriber("/custom/path")
        self.assertIsInstance(transcriber2, Granite4Transcriber)

    def test_resolve_model_path_prefers_worker_models_dir(self):
        """Test default model path resolution prefers the worker-local model directory."""
        resolved = _resolve_model_path()
        self.assertIsInstance(resolved, Path)
        self.assertTrue(str(resolved).endswith("worker\\models\\granite-4.0-1b-speech"))
    
    def test_granite_health_check(self):
        """Test health check function"""
        health = granite_health_check()
        
        self.assertIn('status', health)
        self.assertIn('module', health)
        self.assertIn('model_loaded', health)
        self.assertIn('model_path', health)
        self.assertIn('model_ref', health)
        self.assertIn('timestamp', health)
        self.assertEqual(health['module'], 'granite_transcriber')

if __name__ == '__main__':
    unittest.main()