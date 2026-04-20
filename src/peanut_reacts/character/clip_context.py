"""Context-aware reaction pipeline: vision describe + LLM verdict.

Replaces the old "random outcome + static line arrays" approach. For each
clip, we now:

1. Extract 3 frames (20%, 50%, 80% through the clip) as base64 JPEGs
2. Send them to Groq Llama 3.2 Vision with a description prompt
3. Feed that description to DeepSeek to generate a persona-appropriate
   Peanut verdict (line + outcome + emotion + intensity)
4. Keep a rolling history of previous lines so DeepSeek avoids repeats

Cost per 18-clip episode:
  - Groq vision (free tier OK for one ep worth): ~$0
  - DeepSeek chat: ~$0.001 per verdict x 18 = negligible
  - Total LLM cost: <$0.05/ep

This is the piece that gives Peanut actual CONTEXT awareness — he reacts
to what was in the clip, not random pseudo-reactions.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


# ── Vision: Groq Llama 3.2 Vision ──────────────────────────────────────────

GROQ_VISION_URL = "https://api.groq.com/openai/v1/chat/completions"
# 2026 current Groq vision models. Maverick is Scout's larger sibling —
# slightly slower but higher quality and often available when Scout is
# rate-limited. The old llama-3.2-vision-preview models are deprecated
# (they return 400 Bad Request as of early 2026).
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_VISION_FALLBACKS = [
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]


def _extract_frame_as_b64(clip_path: Path, at_seconds: float) -> Optional[str]:
    """Extract one frame as base64-encoded JPEG data URL."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{at_seconds:.2f}", "-i", str(clip_path),
             "-frames:v", "1", "-q:v", "4", "-f", "image2pipe",
             "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        b64 = base64.b64encode(result.stdout).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        log.warning("Frame extract failed at %.2fs: %s", at_seconds, e)
        return None


def _probe_duration(clip_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def describe_clip(clip_path: Path, num_frames: int = 3) -> str:
    """Return a short description of what's in the clip, via Groq vision.

    Falls back to a generic description if the API is unreachable — the
    downstream LLM still gets *something* to work with.
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        log.warning("No GROQ_API_KEY; skipping vision description")
        return "A funny clip (no vision API available)"

    duration = _probe_duration(clip_path)
    if duration < 1.0:
        return "A very short clip"

    # Sample at 20%, 50%, 80% of clip duration
    sample_points = [duration * f for f in (0.2, 0.5, 0.8)][:num_frames]
    frames = []
    for t in sample_points:
        b64 = _extract_frame_as_b64(clip_path, t)
        if b64:
            frames.append(b64)

    if not frames:
        return "A clip we couldn't see"

    # Build multi-image message
    content = [
        {"type": "text", "text":
         "These are 3 frames from a short funny video clip (in time order). "
         "Describe in ONE sentence (max 25 words) what happens: subject, "
         "action, punchline. Be specific about what's visually funny. Don't "
         "use phrases like 'this video' or 'this clip', just describe the "
         "content directly."},
    ]
    for b64 in frames:
        content.append({
            "type": "image_url",
            "image_url": {"url": b64},
        })

    import time as _time

    models_to_try = [GROQ_VISION_MODEL] + GROQ_VISION_FALLBACKS
    for model in models_to_try:
        # Up to 3 attempts per model with exponential backoff on 429.
        # Groq free tier RPM limits hit fast when generating ~18 clips in a
        # loop — the backoff turns "18 calls in 30 seconds = one rate limit"
        # into "18 calls paced over 60 seconds = zero rate limits".
        for attempt in range(3):
            try:
                resp = httpx.post(
                    GROQ_VISION_URL,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": content}],
                        "temperature": 0.3,
                        "max_tokens": 120,
                    },
                    timeout=60,
                )
                if resp.status_code == 429:
                    # Rate-limited — back off and retry the same model
                    wait = 2 ** attempt * 3  # 3s, 6s, 12s
                    log.info("Groq 429 on %s, retry in %ds (attempt %d/3)",
                             model, wait, attempt + 1)
                    _time.sleep(wait)
                    continue
                if resp.status_code in (400, 404) or "model_not_found" in resp.text.lower():
                    # Model-level failure (deprecated/unknown) — try the next
                    # model, not the same one again
                    log.debug("Groq %s returned %d, trying next model",
                              model, resp.status_code)
                    break
                resp.raise_for_status()
                desc = resp.json()["choices"][0]["message"]["content"].strip()
                desc = desc.strip('"\'')
                log.info("[CTX] Clip described (%s): %s", model, desc[:100])
                return desc
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 2:
                    continue  # backoff already handled above
                log.warning("Groq vision %s failed: %s", model, str(e)[:200])
                break
            except Exception as e:
                log.warning("Groq vision %s failed: %s", model, str(e)[:200])
                break

    return "A funny clip (vision API unreachable)"


# ── LLM verdict generation: DeepSeek chat ──────────────────────────────────

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# Richard Sales voiced Peanut persona. Kept as a constant so the TNTL
# pipeline passes a consistent system prompt every turn.
PEANUT_TNTL_PERSONA = """You are Peanut — a posh British peanut character
watching a Try Not To Laugh compilation. Your voice is a calm, London-accented
Victorian butler. You treat ordinary footage with absurd gravitas, and you are
SUPPOSED to stay stone-faced but sometimes crack.

Style rules:
- Speak in short bursts (3-10 words, never longer).
- ALL CAPS for emphasis on key beats.
- British "me" tic is welcome ("I do love a good laugh, me").
- Signature verdicts you can draw from when apt: "NO GAPS", "TO THE BRIM",
  "BRIM ME BABY", "VERY IMPRESSIVE", "I NEED ANSWERS IMMEDIATELY",
  "WE ARE SO BEHIND TECHNOLOGY WISE".
- Never cruel, never mean-spirited — sincere-ironic awe of nonsense.
- Never say "this clip" or "this video" — react to what happened.

You will be shown a one-sentence description of a clip. Output ONE short
verdict (3-10 words) reacting specifically to what happened, plus whether
you laughed (the outcome) and which emotion to animate.
"""


@dataclass
class Verdict:
    text: str           # the line Peanut says
    outcome: str        # "survive" (didn't laugh) or "fail" (laughed)
    emotion: str        # idle|laughing|shocked|confused|sarcastic|facepalm|...
    intensity: float    # 0-1, drives TTS expressiveness


VALID_EMOTIONS = {
    # Standard emotion set (mapped 1:1 to clips in data/ai_peanut_clips)
    "idle", "laughing", "shocked", "confused", "sarcastic", "facepalm",
    "scared", "excited", "celebrating",
    # Phase 2 "special" animations — Peanut-specific visual gags that
    # escalate beyond standard emotions. Use sparingly, for peak-moment
    # verdicts only (DeepSeek should treat these as the "nuclear option").
    "shell_crack",   # shell visibly cracks -> tension/about-to-break
    "mouth_clamp",   # hands over mouth, fighting the laugh
    "spit_take",     # peanut butter spray -> shock/disbelief
    "explode",       # shell splits + kernel pops -> total defeat
}


def _parse_verdict_json(raw: str) -> Optional[dict]:
    """Extract a JSON object from the LLM response. Tolerant of code fences."""
    # Strip code fences ```json ... ```
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Grab the first {...} block
    match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def generate_verdict(
    clip_description: str,
    history: Optional[list[str]] = None,
    persona: str = PEANUT_TNTL_PERSONA,
    temperature: float = 0.85,
) -> Verdict:
    """Ask DeepSeek for Peanut's verdict on this clip.

    Returns a Verdict. Always returns something — falls back to a boring
    safe verdict if the API call fails so the pipeline never dies.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return Verdict(text="VERY IMPRESSIVE.", outcome="survive",
                       emotion="idle", intensity=0.3)

    history = history or []
    recent = "\n".join(f"- {h}" for h in history[-5:])  # last 5 only
    avoid_block = f"\nRecent lines you've said (avoid repeating or using the same structure):\n{recent}\n" if recent else ""

    user_prompt = (
        f"The clip: {clip_description}\n"
        f"{avoid_block}\n"
        "Output ONLY a JSON object with keys:\n"
        "- text: your verdict (3-10 words, ALL CAPS for emphasis, British voice)\n"
        "- outcome: \"survive\" or \"fail\" (did the clip make you crack up?)\n"
        "- emotion: one of:\n"
        "    Standard: idle, laughing, shocked, confused, sarcastic, facepalm, scared, excited, celebrating\n"
        "    Escalated (use for biggest peak moments only, roughly 1 in 5 clips):\n"
        "      - shell_crack: cracking shell, tension, about-to-break\n"
        "      - mouth_clamp: hands over mouth, fighting the laugh\n"
        "      - spit_take: peanut butter spray, genuine shock/disbelief\n"
        "      - explode: shell splits and kernel pops — total defeat\n"
        "- intensity: 0.0 to 1.0 (how big the reaction is; pair intensity 0.85+ with an escalated emotion)\n\n"
        "Example output: {\"text\": \"AND THERE IT IS. GAP IN THE SHELL.\", "
        "\"outcome\": \"fail\", \"emotion\": \"laughing\", \"intensity\": 0.8}\n\n"
        "No preamble, no explanation, just the JSON."
    )

    try:
        resp = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": persona},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": 150,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("DeepSeek call failed: %s", str(e)[:200])
        return Verdict(text="VERY IMPRESSIVE.", outcome="survive",
                       emotion="idle", intensity=0.3)

    parsed = _parse_verdict_json(raw)
    if not parsed:
        log.warning("Could not parse DeepSeek response: %s", raw[:200])
        return Verdict(text="NO GAPS. MOVING ON.", outcome="survive",
                       emotion="idle", intensity=0.3)

    # Validate + clamp fields
    text = str(parsed.get("text", "")).strip() or "VERY IMPRESSIVE."
    outcome = str(parsed.get("outcome", "survive")).lower()
    if outcome not in ("survive", "fail"):
        outcome = "survive"

    emotion = str(parsed.get("emotion", "idle")).lower()
    if emotion not in VALID_EMOTIONS:
        emotion = "laughing" if outcome == "fail" else "idle"

    try:
        intensity = float(parsed.get("intensity", 0.5))
        intensity = max(0.0, min(1.0, intensity))
    except (TypeError, ValueError):
        intensity = 0.5

    log.info("[CTX] Verdict: text=%r outcome=%s emotion=%s intensity=%.2f",
             text, outcome, emotion, intensity)
    return Verdict(text=text, outcome=outcome, emotion=emotion, intensity=intensity)


# ── Convenience: full pipeline in one call ─────────────────────────────────

def describe_and_verdict(
    clip_path: Path,
    history: Optional[list[str]] = None,
) -> tuple[str, Verdict]:
    """Run vision describe -> LLM verdict. Returns (description, verdict)."""
    description = describe_clip(clip_path)
    verdict = generate_verdict(description, history=history)
    return description, verdict
