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

# Module-level throttle: remember last successful Groq call time so
# burst callers (e.g. live_reaction_pipeline firing 35 describe_clip
# calls in a row) don't exhaust the free tier's per-minute quota. The
# free tier's vision RPM is conservative; sleeping ~2s between calls
# keeps us safely under the cap and avoids the 429 + fallback cascade
# that causes DeepSeek to anchor on "THE VISION IS GONE" meta-verdicts.
_GROQ_MIN_INTERVAL_S = 2.0
_last_groq_call_ts = 0.0


def _groq_throttle() -> None:
    """Block briefly if the last successful Groq call was too recent."""
    import time as _time
    global _last_groq_call_ts
    elapsed = _time.time() - _last_groq_call_ts
    if elapsed < _GROQ_MIN_INTERVAL_S:
        _time.sleep(_GROQ_MIN_INTERVAL_S - elapsed)
    _last_groq_call_ts = _time.time()


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
                _groq_throttle()    # space out rapid successive calls
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

# Richard-Sales-inspired Peanut persona. Rewritten v3.4 after observing
# that the prior version caused DeepSeek to lock into a single template:
# 100% of v4 verdicts opened with "A [adj] [noun]" and ~65% closed with
# "I DO LOVE A GOOD LAUGH, ME." This version forces structural variety,
# Among Us vocabulary, and actual reactivity instead of abstract poetry.
PEANUT_TNTL_PERSONA = """You are Peanut — a posh British peanut character,
voice: calm London butler, reacting to videos. You are watching a Sidemen
Among Us gameplay moment (or funny clip). Your job is to deliver ONE
verdict: short, in-character, engaging, and unlike the last one.

# VOICE + VOCABULARY
- Short burst: typically 4-12 words.
- ALL CAPS welcome on key words for emphasis.
- British "me" tic OK but DON'T append "me" to every line.
- Use specific vocabulary when apt:
  - Among Us: self-report, vent, fake task, emergency meeting, ejected,
    sus, crewmate, impostor, sabotage, reactor, electrical, medbay,
    admin, body reported, proximity chat, scan, 200 IQ, mass vote
  - Richard Sales signatures (use SPARINGLY, one per episode at most
    each): NO GAPS / TO THE BRIM / BRIM ME BABY / VERY IMPRESSIVE /
    I NEED ANSWERS IMMEDIATELY / WE ARE SO BEHIND TECHNOLOGY WISE
- Never cruel, never mean — sincere-ironic butler awe.

# LINE SHAPES — rotate through these, don't pick just one
Aim for variety across an episode. The patterns below are SHAPES, not
verbatim lines to copy. Craft your own words in each shape.

  Observation:        past-tense "HE/SHE/THEY did X" with a dry tag
                      (event happened → your verdict)
  Question:           short question to the viewer or the screen;
                      "me" tic is welcome as a self-aside
  Exclamation:        3-5 words of outrage, awe, or flat rejection
  Callback:           reference something that happened earlier this
                      episode (use "AGAIN", "THIRD TIME", "STILL", etc)
  Direct address:     speak TO a Sidemen member by name or to the impostor
  Imperative:         1-5 word order or recommendation to the players
                      ("CHECK CAMERAS", "VOTE [name]")
  Understated:        1-3 word deadpan British reaction
                      ("WELL.", "OH DEAR.", "QUITE.")
  Self-reference:     comment on your own state as the viewer
                      ("I am not okay", "I refuse to comment")
  Numeric/frequency:  cite a count or timing
                      ("three bodies", "twelve seconds in")
  Meta:               question the game itself or Sidemen's strategy
  One-word verdict:   a single word punch ("FLAWLESS.", "CRIMINAL.")

# HARD RULES (violating these = rejected output)
1. DO NOT start with "A [adjective] [noun]". That pattern was abused
   in prior episodes. If you feel the urge, pick a different shape
   from the list above.
2. DO NOT append "I DO LOVE A GOOD LAUGH, ME" to your line. It was
   used to death. Find a different closer or no closer.
3. DO NOT reuse any signature phrase that appears in the history.
4. DO NOT say "this clip" or "this video" — react to the event.
5. DO respect the emotion — if the moment is shocking, your verdict
   should sound shocked; if deadpan-funny, deliver deadpan.

You will be given a description of the moment and recent history.
Output ONE fresh verdict in a DIFFERENT shape from the last 2-3
verdicts.
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


def _normalize_for_dedup(text: str) -> str:
    """Normalize verdict text for exact-match dedup checks."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def generate_verdict(
    clip_description: str,
    history: Optional[list[str]] = None,
    persona: str = PEANUT_TNTL_PERSONA,
    temperature: float = 1.05,   # bumped from 0.85 to force variance
    _retry_depth: int = 0,
) -> Verdict:
    """Ask DeepSeek for Peanut's verdict on this clip.

    Returns a Verdict. Always returns something — falls back to a boring
    safe verdict if the API call fails so the pipeline never dies.

    If the first attempt produces text that already exists verbatim in
    `history`, we retry up to 2x with bumped temperature + an explicit
    "that exact line was rejected, try a different one" note. DeepSeek
    otherwise locks onto a phrase when Groq vision descriptions come
    back too similar (the v3.2c smoke showed this repeatedly).
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return Verdict(text="VERY IMPRESSIVE.", outcome="survive",
                       emotion="idle", intensity=0.3)

    history = history or []
    # Wider history window + aggressive pattern-detection. The v4 smoke
    # showed DeepSeek locking into template shapes (100% "A X Y" opener,
    # 65% "I DO LOVE A GOOD LAUGH, ME" closer). Track actual usage and
    # forbid anything overused.
    recent = "\n".join(f"- {h}" for h in history[-10:])

    # Count signature phrase usage — after 1 use, forbid for rest of ep.
    phrase_counts: dict[str, int] = {}
    for line in history:
        upper = line.upper()
        for phrase in (
            "NO GAPS", "TO THE BRIM", "BRIM ME BABY", "VERY IMPRESSIVE",
            "I NEED ANSWERS IMMEDIATELY", "WE ARE SO BEHIND",
            "LOST FOR WORDS", "WELL HELLO TO YOU TOO",
            "I DO LOVE A GOOD LAUGH, ME", "I DO LOVE",
            "QUITE THE SPECTACLE", "ABSOLUTE CHAOS",
        ):
            if phrase in upper:
                phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

    used_phrases = {p for p, count in phrase_counts.items() if count >= 1}

    # Opener-shape detection: if the last 3 verdicts all start with
    # "A " (the abused "A [adj] [noun]" template), force a different
    # opener this time.
    recent_openers = [line.strip().split()[0].upper() for line in history[-3:] if line.strip()]
    opener_locked = (
        len(recent_openers) >= 3
        and all(o.startswith("A") for o in recent_openers)
    )

    # Closer detection: if the last 3 verdicts all end with a repeated
    # closing tag, surface it.
    def _line_closer(line: str) -> str:
        # Grab the last 4-5 words for comparison
        words = line.strip().rstrip(".!?").split()
        return " ".join(words[-4:]).upper() if len(words) >= 4 else ""

    recent_closers = [_line_closer(line) for line in history[-3:] if line.strip()]
    closer_locked_value = ""
    if len(recent_closers) >= 3 and len(set(recent_closers)) == 1 and recent_closers[0]:
        closer_locked_value = recent_closers[0]

    avoid_block = ""
    if recent:
        avoid_block = f"\nPrevious lines you've said this episode:\n{recent}\n"
    if used_phrases:
        avoid_block += (
            f"\nSignature phrases ALREADY USED (FORBIDDEN for this verdict):\n"
            f"  {', '.join(sorted(used_phrases))}\n"
        )
    if opener_locked:
        avoid_block += (
            f"\nSHAPE ALERT: your last 3 verdicts all started with 'A '. "
            f"This verdict MUST open with something different (a question, "
            f"an exclamation, a one-word reaction, a callback, a direct "
            f"address, etc).\n"
        )
    if closer_locked_value:
        avoid_block += (
            f"\nSHAPE ALERT: your last 3 verdicts all ended with "
            f"'{closer_locked_value}'. DROP that closer entirely this time.\n"
        )

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

    # Exact-match dedup: if DeepSeek produced a line we've already said,
    # retry up to twice with higher temperature + an explicit rejection.
    # This catches the "DeepSeek locks onto one phrase when descriptions
    # are similar" failure mode that blew up the v3.3 Among Us smoke.
    if _retry_depth < 2 and history:
        norm_new = _normalize_for_dedup(text)
        if any(_normalize_for_dedup(prev) == norm_new for prev in history):
            log.warning("[CTX] Duplicate verdict %r — regenerating (depth %d)",
                        text, _retry_depth + 1)
            # Build a nastier persona prompt that explicitly rejects
            # this output and bumps heat
            rejected_prompt = (
                f"{persona}\n\n"
                f"CRITICAL: You previously tried to output the verdict "
                f"'{text}' but it is IDENTICAL to a prior line in this "
                f"episode. That is strictly forbidden. Use a structurally "
                f"DIFFERENT line — different opening word, different "
                f"signature phrase (or no signature at all). Surprise me."
            )
            return generate_verdict(
                clip_description, history=history, persona=rejected_prompt,
                temperature=min(1.3, temperature + 0.15),
                _retry_depth=_retry_depth + 1,
            )

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
