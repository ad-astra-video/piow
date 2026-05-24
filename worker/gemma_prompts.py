#!/usr/bin/env python3
"""Shared prompt definitions for Gemma analysis workflows."""

import json
from typing import Dict, Any, Optional

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


def _summarize_schema(schema: Dict[str, Any], indent: int = 0) -> str:
    """Produce a human-readable summary of a JSON Schema object's properties."""
    if not isinstance(schema, dict):
        return ""

    lines = []
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    for key, prop in props.items():
        if not isinstance(prop, dict):
            continue
        prefix = "  " * indent
        req_flag = " (required)" if key in required else ""
        ptype = prop.get("type", "any")
        desc = prop.get("description", "")
        enum_vals = prop.get("enum")
        title = prop.get("title", "")

        label_parts = [ptype]
        if enum_vals:
            label_parts.append(f"enum: {json.dumps(enum_vals)}")
        label = " | ".join(label_parts)

        info_parts = []
        if title:
            info_parts.append(title)
        if desc:
            info_parts.append(desc)
        info = " — ".join(info_parts)

        lines.append(f"{prefix}- {key}{req_flag}: {label}{(' ' + info) if info else ''}")

        if ptype == "object" and prop.get("properties"):
            lines.append(_summarize_schema(prop, indent + 1))
        elif ptype == "array":
            items = prop.get("items") or {}
            if items.get("properties"):
                lines.append(f"{prefix}  items:")
                lines.append(_summarize_schema(items, indent + 2))
            elif items.get("type"):
                lines.append(f"{prefix}  items: {items.get('type')}")

    return "\n".join(lines)


def get_analysis_prompt_with_schema(
    base_prompt: str,
    schema: Optional[Dict[str, Any]],
) -> str:
    """Return an analysis prompt that instructs the model to output JSON matching the schema.

    When *schema* is None or empty, the original *base_prompt* is returned unchanged.
    When a schema is provided, the prompt is rewritten to demand strict JSON output that
    conforms to the schema, while preserving the NO_UPDATE suppression contract.
    """
    if not schema or not isinstance(schema, dict):
        return base_prompt

    inner_schema = schema.get("schema") if schema.get("type") == "json_object" else schema
    if not inner_schema or not isinstance(inner_schema, dict):
        return base_prompt

    schema_summary = _summarize_schema(inner_schema)
    title = inner_schema.get("title", "AnalysisResult")
    description = inner_schema.get("description", "Structured analysis output")

    return (
        f"You are a structured analysis engine. Analyze the provided context and produce a "
        f"JSON object that strictly conforms to the schema below.\n\n"
        f"Schema: {title}\n"
        f"{description}\n\n"
        f"Fields:\n{schema_summary}\n\n"
        f"Rules:\n"
        f"- Output ONLY a single valid JSON object. No markdown, no code fences, no explanations.\n"
        f"- Every required field must be present.\n"
        f"- Use the exact property names shown above.\n"
        f"- For array fields, always return a JSON array (use [] when there are no items).\n"
        f"- Never use placeholder values like None, N/A, null, or unknown as standalone values.\n"
        f"- If there is no meaningful update since the last analysis, respond with exactly NO_UPDATE.\n"
        f"- The JSON must be parseable without any surrounding text.\n\n"
        f"Original task: {base_prompt.strip()}\n\n"
        f"Generate the JSON object now."
    )


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
