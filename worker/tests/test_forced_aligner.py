#!/usr/bin/env python3
"""Unit tests for forced aligner helper normalization."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forced_aligner import ForcedAlignerService


class _WordObj:
    def __init__(self, text, start_time, end_time):
        self.text = text
        self.start_time = start_time
        self.end_time = end_time


class TestForcedAlignerService(unittest.TestCase):
    def test_normalize_alignment_results_from_dicts(self):
        svc = ForcedAlignerService(model_path="dummy")
        raw = [
            {"text": "hello", "start_time": 0.1, "end_time": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
        ]
        words = svc._normalize_alignment_results(raw)
        self.assertEqual(
            words,
            [
                {"word": "hello", "start": 0.1, "end": 0.4},
                {"word": "world", "start": 0.5, "end": 0.9},
            ],
        )

    def test_normalize_alignment_results_from_objects(self):
        svc = ForcedAlignerService(model_path="dummy")
        words = svc._normalize_alignment_results([_WordObj("hola", 0.0, 0.2)])
        self.assertEqual(words, [{"word": "hola", "start": 0.0, "end": 0.2}])

    def test_align_short_circuits_for_empty_input(self):
        svc = ForcedAlignerService(model_path="dummy")
        self.assertEqual(svc.align(b"", 16000, "hello", "en"), [])
        self.assertEqual(svc.align(b"\x00\x00", 16000, "", "en"), [])


if __name__ == "__main__":
    unittest.main()
