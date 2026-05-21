#!/usr/bin/env python3
"""Shared prompt definitions for Gemma analysis workflows."""

from typing import Dict

ANALYSIS_PROMPTS: Dict[str, str] = {
    "multimodal": (
        "Analyze the latest live conversation window using both audio and video context. "
        "If there is no meaningful update, respond with exactly NO_UPDATE. "
        "Otherwise provide concise markdown with headings Decisions, Actions, and Risks. "
        "Use bullets only for new or changed items, include priority (High/Medium/Low), "
        "and include time references when available. Never output placeholder values like "
        "None, N/A, or unknown as standalone fields."
    ),
    "audio_only": (
        "Analyze only the spoken audio from the latest live conversation window. "
        "If there is no meaningful update, respond with exactly NO_UPDATE. "
        "Otherwise provide concise markdown with headings Decisions, Actions, and Risks. "
        "Use bullets only for new or changed items, include priority (High/Medium/Low), "
        "and include time references when available. Never output placeholder values like "
        "None, N/A, or unknown as standalone fields."
    ),
    "video_only": (
        "Analyze only the visual context from the latest live conversation window. "
        "If there is no meaningful update, respond with exactly NO_UPDATE. "
        "Otherwise provide concise markdown with headings Decisions, Actions, and Risks. "
        "Use bullets only for new or changed items, include priority (High/Medium/Low), "
        "and include time references when available. Never output placeholder values like "
        "None, N/A, or unknown as standalone fields."
    ),
}


def get_analysis_prompt(mode: str) -> str:
    """Return the analysis prompt for a given mode with multimodal fallback."""
    return ANALYSIS_PROMPTS.get(mode, ANALYSIS_PROMPTS["multimodal"])


SCHEMA_GENERATION_PROMPT_TEMPLATE = """You are a JSON Schema designer. Convert the user's natural-language analysis prompt into a valid JSON Schema (draft-07) that structures the expected output of an LLM analysis.

Rules:
- Return ONLY a JSON object representing the schema. No markdown, no explanation, no code fences.
- The schema should capture the key entities, fields, and data types implied by the prompt.
- Use "type": "object" as the root.
- Include descriptive "title" and "description" fields.
- For enumerated categories, use "enum" arrays.
- For timestamps or durations, use "type": "string" with "format": "date-time" or describe the format in "description".
- For confidence scores or priorities, use "type": "number" with "minimum" and "maximum" where appropriate.
- If the prompt asks for a list of items, wrap them in "type": "array" with "items" as an object schema.
- Keep the schema flat when possible; nesting should not exceed 3 levels.
- Ensure all property names are concise, snake_case, and meaningful.

Example:
User prompt: "Analyze the live conversation using both audio and video context. Summarize key actions, decisions, and risks."
Output:
{
  "type": "object",
  "title": "ConversationAnalysis",
  "description": "Structured analysis of a live conversation",
  "properties": {
    "summary": {
      "type": "string",
      "description": "High-level summary of the conversation segment"
    },
    "key_actions": {
      "type": "array",
      "description": "List of actions discussed or decided",
      "items": {
        "type": "object",
        "properties": {
          "action": { "type": "string" },
          "owner": { "type": "string" },
          "deadline": { "type": "string", "description": "ISO 8601 or relative time" }
        },
        "required": ["action"]
      }
    },
    "decisions": {
      "type": "array",
      "description": "Decisions made during the conversation",
      "items": { "type": "string" }
    },
    "risks": {
      "type": "array",
      "description": "Identified risks or concerns",
      "items": {
        "type": "object",
        "properties": {
          "risk": { "type": "string" },
          "severity": { "type": "string", "enum": ["low", "medium", "high", "critical"] },
          "mitigation": { "type": "string" }
        },
        "required": ["risk", "severity"]
      }
    }
  },
  "required": ["summary"]
}"""


def get_schema_generation_prompt(analysis_prompt: str, mode: str = "multimodal") -> str:
    """Build a prompt that instructs Gemma to emit a JSON Schema for the given analysis prompt."""
    return (
        f"{SCHEMA_GENERATION_PROMPT_TEMPLATE}\n\n"
        f"Analysis mode: {mode}\n"
        f"User prompt: {analysis_prompt}\n\n"
        f"Generate the JSON Schema now."
    )
