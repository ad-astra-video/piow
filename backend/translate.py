#!/usr/bin/env python3
"""
Translation API Endpoints
This is an alias for the transcription endpoints since translation 
is handled by the same transcribe.py file.
"""

# Import and re-export the functions from transcribe.py for clarity
from transcribe import translate_text, get_languages

# Re-export for clarity in imports
__all__ = ['translate_text', 'get_languages']
