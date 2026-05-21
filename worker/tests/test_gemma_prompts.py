#!/usr/bin/env python3
"""Tests for gemma_prompts schema generation utilities."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gemma_prompts import (
    get_schema_generation_prompt,
    SCHEMA_GENERATION_PROMPT_TEMPLATE,
)


class TestSchemaGenerationPrompt(unittest.TestCase):
    def test_prompt_includes_template(self):
        prompt = get_schema_generation_prompt("Summarize decisions and risks")
        self.assertIn(SCHEMA_GENERATION_PROMPT_TEMPLATE, prompt)

    def test_prompt_includes_analysis_prompt(self):
        user_prompt = "Extract action items and deadlines from the conversation"
        prompt = get_schema_generation_prompt(user_prompt)
        self.assertIn(f"User prompt: {user_prompt}", prompt)

    def test_prompt_includes_mode(self):
        prompt = get_schema_generation_prompt("Test prompt", mode="audio_only")
        self.assertIn("Analysis mode: audio_only", prompt)

    def test_prompt_default_mode_is_multimodal(self):
        prompt = get_schema_generation_prompt("Test prompt")
        self.assertIn("Analysis mode: multimodal", prompt)

    def test_prompt_ends_with_generation_instruction(self):
        prompt = get_schema_generation_prompt("Test prompt")
        self.assertTrue(prompt.endswith("Generate the JSON Schema now."))

    def test_prompt_contains_rules(self):
        prompt = get_schema_generation_prompt("Test prompt")
        self.assertIn('"type": "object"', prompt)
        self.assertIn("snake_case", prompt)
        self.assertIn("enum", prompt)


if __name__ == "__main__":
    unittest.main()
