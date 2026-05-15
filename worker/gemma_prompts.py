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
