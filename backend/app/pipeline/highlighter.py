"""
Sends the timestamped transcript to an LLM and asks it to pick the best
short-form moments. Returns a plain list of dicts:

    [{"start": 12.3, "end": 54.1, "title": "...", "hook": "...", "score": 87}, ...]

Swap providers by changing LLM_PROVIDER in .env — no other code changes needed.
"""
import json
import re
from typing import List, Dict

from app.config import settings

PROMPT_TEMPLATE = """You are an expert short-form video editor who finds viral-worthy \
moments in long videos for TikTok/Reels/Shorts.

Below is a timestamped transcript. Pick the {max_clips} best standalone moments. \
Each moment must:
- be between {min_sec} and {max_sec} seconds long
- work on its own without needing earlier context
- have a strong hook in the first 3 seconds (a question, bold claim, or surprising statement)

Return ONLY valid JSON, no markdown fences, no commentary, in this exact shape:
[
  {{"start": <seconds float>, "end": <seconds float>, "title": "<short catchy title>", \
"hook": "<the hook line/idea>", "score": <0-100 virality score>}}
]

TRANSCRIPT:
{transcript}
"""


def _extract_json(text: str) -> str:
    """LLMs sometimes wrap JSON in prose or code fences despite instructions."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group(0) if match else text


def _call_openai(prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content


def _call_anthropic(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    resp = model.generate_content(prompt)
    return resp.text


def select_highlights(transcript_text: str) -> List[Dict]:
    prompt = PROMPT_TEMPLATE.format(
        max_clips=settings.max_clips_per_job,
        min_sec=settings.clip_min_seconds,
        max_sec=settings.clip_max_seconds,
        transcript=transcript_text,
    )

    provider = settings.llm_provider.lower()
    if provider == "openai":
        raw = _call_openai(prompt)
    elif provider == "anthropic":
        raw = _call_anthropic(prompt)
    elif provider == "gemini":
        raw = _call_gemini(prompt)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")

    json_str = _extract_json(raw)
    highlights = json.loads(json_str)

    # basic sanity filtering
    clean = []
    for h in highlights:
        try:
            if float(h["end"]) > float(h["start"]):
                clean.append(h)
        except (KeyError, TypeError, ValueError):
            continue
    return clean
