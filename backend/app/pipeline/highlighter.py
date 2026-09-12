"""
Multi-provider orchestration: Three LLMs collaborate on highlight selection.

1. OpenAI (gpt-4o-mini):      Fast moment detection from transcript
2. Anthropic (claude-sonnet):  Rich title & hook generation via reasoning
3. Gemini (flash):             Final validation, scoring, and ranking

Returns a plain list of dicts:
    [{"start": 12.3, "end": 54.1, "title": "...", "hook": "...", "score": 87}, ...]
"""
import json
import re
from typing import List, Dict

from app.config import settings

# Stage 1: OpenAI detects moment candidates (timestamps + raw content)
DETECT_MOMENTS_PROMPT = """You are a video editor scanning a transcript for viral-worthy moments.

Identify the {max_clips} best standalone moments that:
- are between {min_sec} and {max_sec} seconds long
- work without needing earlier context
- have a strong hook in the first 3 seconds

Return ONLY JSON, no prose, in this exact shape:
[
  {{"start": <float>, "end": <float>, "transcript_excerpt": "<key quote>"}}
]

TRANSCRIPT:
{transcript}
"""

# Stage 2: Anthropic crafts titles and hooks for each moment
CRAFT_HOOKS_PROMPT = """You are a viral short-form video expert.

For each moment below, write:
- A catchy, concise title (5-8 words)
- A compelling hook (the first sentence that grabs viewers)

Moments:
{moments}

Return ONLY JSON:
[
  {{"start": <float>, "end": <float>, "title": "<title>", "hook": "<hook>"}}
]
"""

# Stage 3: Gemini validates and scores the final clips
VALIDATE_AND_SCORE_PROMPT = """You are a TikTok/Reels algorithm expert scoring viral potential.

Review these clips and:
1. Validate each has a proper hook and title
2. Assign a virality score (0-100) based on hook strength, pacing, and engagement potential

Clips:
{clips}

Return ONLY JSON (fix any formatting issues):
[
  {{"start": <float>, "end": <float>, "title": "<title>", "hook": "<hook>", "score": <0-100>}}
]
"""


def _extract_json(text: str) -> str:
    """LLMs sometimes wrap JSON in prose or code fences despite instructions."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group(0) if match else text


def _require_key(env_var: str, key: str) -> None:
    """Raise a clear error if an API key is missing/empty, so we never send
    an invalid 'Bearer ' header that surfaces as a cryptic connection error."""
    if not key:
        raise ValueError(
            f"{env_var} is not set. Add your API key to .env (see .env.example)."
        )


def _call_openai(prompt: str) -> str:
    """Stage 1: Fast moment detection."""
    _require_key("OPENAI_API_KEY", settings.openai_api_key)
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,  # lower temp for consistent moment detection
    )
    return resp.choices[0].message.content


def _call_anthropic(prompt: str) -> str:
    """Stage 2: Rich title & hook generation using deep reasoning."""
    _require_key("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_gemini(prompt: str) -> str:
    """Stage 3: Final validation, scoring, and ranking."""
    _require_key("GEMINI_API_KEY", settings.gemini_api_key)
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    resp = model.generate_content(prompt)
    return resp.text


def select_highlights(transcript_text: str) -> List[Dict]:
    """
    Multi-provider orchestration with intelligent fallback:
    1. OpenAI: Detect moment candidates (fast)
    2. Anthropic: Craft compelling titles & hooks (reasoning-heavy)
    3. Gemini: Validate, score, and rank (expert scoring)
    
    If any stage fails (quota exhausted, rate limit, etc.), the system
    falls back to the next available provider for that stage.
    """
    detect_prompt = DETECT_MOMENTS_PROMPT.format(
        max_clips=settings.max_clips_per_job,
        min_sec=settings.clip_min_seconds,
        max_sec=settings.clip_max_seconds,
        transcript=transcript_text,
    )
    
    # Stage 1: Try OpenAI → Anthropic → Gemini for moment detection
    print(f"Stage 1/3: Detecting moments...")
    moments = []
    for provider_name, provider_func in [
        ("OpenAI", _call_openai),
        ("Anthropic", _call_anthropic),
        ("Gemini", _call_gemini),
    ]:
        try:
            print(f"  Trying {provider_name}...")
            raw_moments = provider_func(detect_prompt)
            json_str = _extract_json(raw_moments)
            moments = json.loads(json_str)
            moments = [m for m in moments if "start" in m and "end" in m and float(m["end"]) > float(m["start"])]
            if moments:
                print(f"  ✓ {provider_name} found {len(moments)} moments")
                break
        except Exception as e:
            print(f"  ✗ {provider_name} failed: {str(e)[:80]}...")
            continue
    
    if not moments:
        print("Warning: No moments detected by any provider, returning empty list")
        return []
    
    print(f"Stage 2/3: Crafting titles & hooks for {len(moments)} moments...")
    moments_json = json.dumps(moments, indent=2)
    hook_prompt = CRAFT_HOOKS_PROMPT.format(moments=moments_json)
    
    # Stage 2: Try Anthropic → OpenAI → Gemini for hook generation
    enhanced_moments = []
    for provider_name, provider_func in [
        ("Anthropic", _call_anthropic),
        ("OpenAI", _call_openai),
        ("Gemini", _call_gemini),
    ]:
        try:
            print(f"  Trying {provider_name}...")
            raw_hooks = provider_func(hook_prompt)
            json_str = _extract_json(raw_hooks)
            enhanced_moments = json.loads(json_str)
            print(f"  ✓ {provider_name} crafted hooks")
            break
        except Exception as e:
            print(f"  ✗ {provider_name} failed: {str(e)[:80]}...")
            continue
    
    # Merge with defaults if all providers failed
    if not enhanced_moments:
        enhanced_moments = moments
    
    for em in enhanced_moments:
        em.setdefault("title", f"Clip {len(moments)}")
        em.setdefault("hook", "")
        em.setdefault("score", 50)  # default score before Gemini validation
    
    print(f"Stage 3/3: Validating and scoring...")
    clips_json = json.dumps(enhanced_moments, indent=2)
    score_prompt = VALIDATE_AND_SCORE_PROMPT.format(clips=clips_json)
    
    # Stage 3: Try Gemini → OpenAI → Anthropic for final scoring
    final_clips = []
    for provider_name, provider_func in [
        ("Gemini", _call_gemini),
        ("OpenAI", _call_openai),
        ("Anthropic", _call_anthropic),
    ]:
        try:
            print(f"  Trying {provider_name}...")
            raw_scores = provider_func(score_prompt)
            json_str = _extract_json(raw_scores)
            final_clips = json.loads(json_str)
            print(f"  ✓ {provider_name} scored clips")
            break
        except Exception as e:
            print(f"  ✗ {provider_name} failed: {str(e)[:80]}...")
            continue
    
    # Use enhanced_moments as fallback if scoring failed
    if not final_clips:
        final_clips = enhanced_moments
    
    # Final sanity check
    clean = []
    for clip in final_clips:
        try:
            if float(clip["end"]) > float(clip["start"]):
                clean.append(clip)
        except (KeyError, TypeError, ValueError):
            continue
    
    print(f"✓ Pipeline complete: {len(clean)} clips ready")
    return clean
