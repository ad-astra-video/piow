#!/usr/bin/env python3
"""
Test suite for Granite 4.0 transcriber
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import numpy as np
import tempfile
import soundfile as sf

# Add the worker directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite_transcriber import Granite4Transcriber, create_granite_transcriber, granite_health_check

class TestGranite4Transcriber(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_audio_path = None
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
        
        result = transcriber.transcribe(self.test_audio_path)
        
        # Should return mock result
        self.assertIn('text', result)
        self.assertIn('segments', result)
        self.assertIn('language', result)
        self.assertIn('duration', result)
        self.assertEqual(result['model'], 'granite-4.0-1b-mock')
        self.assertEqual(result['hardware'], 'cpu')
        self.assertTrue(len(result['text']) > 0)
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    @patch('granite_transcriber.librosa.load')
    @patch('numpy.pad')
    def test_transcribe_loaded_mock_inference(self, mock_pad, mock_load, mock_load_model):
        """Test transcription with mocked model inference"""
        # Setup mocks
        mock_load.return_value = (np.array([0.1, 0.2, 0.3]), 16000)  # fake audio, sr
        mock_pad.return_value = np.array([0.1, 0.2, 0.3])  # padded audio
        
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True
        
        # Mock the session and tokenizer
        mock_session = MagicMock()
        mock_session.run.return_value = [np.array([[1, 2, 3]])]  # fake output
        transcriber.session = mock_session
        
        mock_tokenizer = MagicMock()
        mock_tokenizer.decode.return_value = "mock transcription"
        transcriber.tokenizer = mock_tokenizer
        
        # Mock _prepare_inputs
        with patch.object(transcriber, '_prepare_inputs') as mock_prepare:
            mock_prepare.return_value = {'input_features': np.array([]), 'decoder_input_ids': np.array([])}
            
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
        
        # Should return mock result
        self.assertIn('original_text', result)
        self.assertIn('translated_text', result)
        self.assertIn('source_language', result)
        self.assertIn('target_language', result)
        self.assertEqual(result['original_text'], "Hello world")
        self.assertEqual(result['source_language'], "en")
        self.assertEqual(result['target_language'], "es")
        self.assertEqual(result['model'], 'granite-4.0-1b-mock')
        self.assertEqual(result['hardware'], 'cpu')
    
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    @patch('transformers.AutoTokenizer')
    def test_translate_loaded_mock_inference(self, mock_tokenizer_class, mock_load_model):
        """Test translation with mocked model inference"""
        # Setup mocks
        mock_tokenizer = MagicMock()
        mock_tokenizer_class.from_pretrained.return_value = mock_tokenizer
        mock_tokenizer.return_value = {
            'input_ids': np.array([[1, 2, 3]]),
            'attention_mask': np.array([[1, 1, 1]])
        }
        mock_tokenizer.decode.return_value = "Hola mundo"
        
        transcriber = Granite4Transcriber()
        transcriber.is_loaded = True
        transcriber.tokenizer = mock_tokenizer
        
        # Mock the session
        mock_session = MagicMock()
        mock_session.run.return_value = [np.array([[4, 5, 6]])]  # fake output
        transcriber.session = mock_session
        
        result = transcriber.translate("Hello world", "en", "es")
        
        # Check result
        self.assertEqual(result['original_text'], "Hello world")
        self.assertEqual(result['translated_text'], "Hola mundo")
        self.assertEqual(result['source_language'], "en")
        self.assertEqual(result['target_language'], "es")
        self.assertEqual(result['model'], 'granite-4.0-1b')
        self.assertEqual(result['hardware'], 'cpu')
        self.assertGreaterEqual(result['processing_time'], 0)
    
    @patch('granite_transcriber.librosa.feature.melspectrogram')
    @patch('granite_transcriber.librosa.power_to_db')
    @patch('transformers.AutoTokenizer')
    def test_prepare_inputs(self, mock_tokenizer_class, mock_db, mock_mel):
        """Test _prepare_inputs method"""
        transcriber = Granite4Transcriber()
        
        # Create fake audio data
        audio = np.random.randn(16000).astype(np.float32)  # 1 second of audio
        language = "en"
        
        # Setup mocks
        mock_mel.return_value = np.random.randn(128, 100)  # mel spec
        mock_db.return_value = np.random.randn(128, 100)  # db mel
        
        mock_tokenizer = MagicMock()
        mock_tokenizer_class.from_pretrained.return_value = mock_tokenizer
        mock_tokenizer.return_value = {
            'input_ids': np.array([[1, 2, 3]]),
            'attention_mask': np.array([[1, 1, 1]])
        }
        
        result = transcriber._prepare_inputs(audio, language)
        
        self.assertIn('input_features', result)
        self.assertIn('decoder_input_ids', result)
        self.assertIsInstance(result['input_features'], np.ndarray)
        self.assertIsInstance(result['decoder_input_ids'], np.ndarray)
    
    def test_mock_transcription(self):
        """Test _mock_transcription method"""
        transcriber = Granite4Transcriber()
        
        result = transcriber._mock_transcription(self.test_audio_path, "en")
        
        self.assertIn('text', result)
        self.assertIn('segments', result)
        self.assertIn('language', result)
        self.assertIn('duration', result)
        self.assertEqual(result['language'], "en")
        self.assertEqual(result['model'], 'granite-4.0-1b-mock')
        self.assertEqual(result['hardware'], 'cpu')
        self.assertTrue(len(result['text']) > 0)
        self.assertEqual(len(result['segments']), 1)
        self.assertEqual(result['segments'][0]['text'], result['text'])
    
    def test_mock_translation(self):
        """Test _mock_translation method"""
        transcriber = Granite4Transcriber()
        
        result = transcriber._mock_translation("Hello world", "en", "es")
        
        self.assertEqual(result['original_text'], "Hello world")
        self.assertEqual(result['source_language'], "en")
        self.assertEqual(result['target_language'], "es")
        self.assertEqual(result['model'], 'granite-4.0-1b-mock')
        self.assertEqual(result['hardware'], 'cpu')
        self.assertIn('[Translated from en to es]', result['translated_text'])
    
    def test_create_granite_transcriber(self):
        """Test factory function"""
        transcriber = create_granite_transcriber()
        self.assertIsInstance(transcriber, Granite4Transcriber)
        
        # Test with custom path
        transcriber2 = create_granite_transcriber("/custom/path")
        self.assertIsInstance(transcriber2, Granite4Transcriber)
    
    def test_granite_health_check(self):
        """Test health check function"""
        health = granite_health_check()
        
        self.assertIn('status', health)
        self.assertIn('module', health)
        self.assertIn('model_loaded', health)
        self.assertIn('model_path', health)
        self.assertIn('timestamp', health)
        self.assertEqual(health['module'], 'granite_transcriber')

if __name__ == '__main__':
    unittest.main()