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


class _NestedResultObj:
    def __init__(self, entries):
        self.time_stamps = entries


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

    def test_normalize_alignment_results_from_nested_dict(self):
        svc = ForcedAlignerService(model_path="dummy")
        raw = {
            "time_stamps": [
                {"text": "foo", "start_time": 0.1, "end_time": 0.2},
                {"text": "bar", "startTime": 0.25, "endTime": 0.5},
            ]
        }
        words = svc._normalize_alignment_results(raw)
        self.assertEqual(
            words,
            [
                {"word": "foo", "start": 0.1, "end": 0.2},
                {"word": "bar", "start": 0.25, "end": 0.5},
            ],
        )

    def test_normalize_alignment_results_from_nested_object(self):
        svc = ForcedAlignerService(model_path="dummy")
        raw = [_NestedResultObj([_WordObj("alpha", 1.0, 1.2), _WordObj("beta", 1.3, 1.7)])]
        words = svc._normalize_alignment_results(raw)
        self.assertEqual(
            words,
            [
                {"word": "alpha", "start": 1.0, "end": 1.2},
                {"word": "beta", "start": 1.3, "end": 1.7},
            ],
        )


if __name__ == "__main__":
    unittest.main()
