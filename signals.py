"""
signals.py — Detection signals for Provenance Guard.

Each signal returns a dict:
    {
        "name":      str,
        "available": bool,          # False if the signal could not run (e.g. no API key)
        "p_ai":      float | None,  # this signal's estimate P(content is AI-generated), 0..1
        "detail":    dict,          # human-readable breakdown for the audit log / README
    }

Three genuinely distinct signals (used together = ensemble stretch feature):
    1. llm_signal        — semantic/holistic judgement from a Groq LLM.
    2. stylometric_signal— structural statistics (burstiness, vocabulary diversity, punctuation).
    3. lexical_signal    — surface lexical markers (AI "tell" phrases, hedging, connective density).

They capture different properties, so combining them is more informative than any one alone:
    - llm is semantic, stylometric is structural, lexical is surface-lexical.
"""

from __future__ import annotations

import math
import re
from statistics import mean, pstdev

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"[.!?]+(?:\s+|$)")
_WORD = re.compile(r"[A-Za-z']+")


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _lerp_score(value: float, human_end: float, ai_end: float) -> float:
    """Map a raw metric onto 0..1 where `human_end` -> 0 (human) and `ai_end` -> 1 (AI)."""
    if ai_end == human_end:
        return 0.5
    return _clamp01((value - human_end) / (ai_end - human_end))


# ---------------------------------------------------------------------------
# Signal 2: Stylometric heuristics (structural)
# ---------------------------------------------------------------------------

def stylometric_signal(text: str) -> dict:
    """
    Structural statistics that differ between human and AI prose.

    Measures:
      - burstiness: coefficient of variation of sentence lengths.
          Human writing mixes long and short sentences (high variance);
          AI writing is more uniform (low variance).  Low burstiness -> AI.
      - type_token_ratio: unique words / total words (vocabulary evenness).
      - regularity: density of informal marks (ellipses, doubled !/?, emoji,
          lowercase sentence starts, standalone lowercase "i").
          AI text is 'clean'; human casual text is messier.

    Blind spot: a careful human essayist writing uniformly can look AI-like,
    and AI told to 'write casually' can raise its variance.  That is exactly
    why we never rely on this signal alone.
    """
    sents = _sentences(text)
    words = _words(text)
    detail: dict = {}

    if len(words) < 8:
        return {
            "name": "stylometric",
            "available": True,
            "p_ai": 0.5,
            "detail": {"note": "text too short for reliable stylometry", "n_words": len(words)},
        }

    # --- burstiness (sentence-length variation) ---
    lengths = [len(_words(s)) for s in sents] or [len(words)]
    m = mean(lengths)
    cv = (pstdev(lengths) / m) if m else 0.0
    detail["sentence_count"] = len(sents)
    detail["mean_sentence_len"] = round(m, 2)
    detail["burstiness_cv"] = round(cv, 3)
    # CV ~0.15 (very uniform) -> AI(1) ; CV ~0.75 (very bursty) -> human(0)
    burst_ai = 1.0 - _lerp_score(cv, 0.15, 0.75)

    # --- type/token ratio (vocabulary diversity), length-normalised ---
    ttr = len(set(words)) / len(words)
    ttr_norm = ttr * math.sqrt(len(words) / 100.0) if len(words) > 100 else ttr
    detail["type_token_ratio"] = round(ttr, 3)
    # "Suspiciously even" mid diversity (~0.62) reads AI; extremes push human.
    ttr_ai = _clamp01(1.0 - abs(ttr_norm - 0.62) / 0.62)

    # --- punctuation / casing regularity ---
    # Informal marks and lowercase sentence starts are human 'mess'; clean, formal
    # punctuation and consistent capitalisation read as AI.  (Sentence splitting
    # strips terminal punctuation, so we measure informal-marker density.)
    informal_hits = len(re.findall(r"(\.{3}|!{2,}|\?{2,}|[\U0001F300-\U0001FAFF]|(?:^|\s)i(?:\s|'))", text))
    lower_start = sum(1 for s in sents if s[:1].islower())
    informal = informal_hits + lower_start
    reg_ai = _clamp01(1.0 - informal / len(sents))
    detail["informal_markers"] = informal

    # Weighted blend — burstiness is the most reliable structural cue.
    p_ai = 0.55 * burst_ai + 0.20 * ttr_ai + 0.25 * reg_ai
    detail["components"] = {
        "burstiness_ai": round(burst_ai, 3),
        "ttr_ai": round(ttr_ai, 3),
        "regularity_ai": round(reg_ai, 3),
    }
    return {"name": "stylometric", "available": True, "p_ai": round(_clamp01(p_ai), 3), "detail": detail}


# ---------------------------------------------------------------------------
# Signal 3: Lexical markers (surface) — powers the ensemble stretch feature
# ---------------------------------------------------------------------------

# Connectives / hedges that LLMs over-use relative to casual human writing.
_AI_TELLS = [
    "moreover", "furthermore", "in conclusion", "it is important to note",
    "it is worth noting", "importantly", "notably", "overall", "in summary",
    "additionally", "consequently", "as a result", "on the other hand",
    "delve", "delving", "tapestry", "landscape", "realm", "paradigm",
    "navigate the", "in today's", "ever-evolving", "plays a crucial role",
    "a testament to", "underscores", "fostering", "leverage", "robust",
    "seamless", "holistic", "multifaceted", "it is essential", "stakeholders",
]


def lexical_signal(text: str) -> dict:
    """
    Surface lexical markers.  Counts density of formal connectives, hedges and
    well-known LLM 'tell' words, plus contraction rate (humans contract more).

    Blind spot: formal academic humans use these connectives too (see the
    'monetary policy' borderline example), which is why this signal is capped
    and down-weighted, not decisive.
    """
    words = _words(text)
    detail: dict = {}
    if len(words) < 8:
        return {
            "name": "lexical",
            "available": True,
            "p_ai": 0.5,
            "detail": {"note": "text too short for reliable lexical analysis"},
        }

    low = text.lower()
    tell_hits = sum(low.count(t) for t in _AI_TELLS)
    tell_density = tell_hits / (len(words) / 100.0)  # hits per 100 words
    detail["ai_tell_hits"] = tell_hits
    detail["ai_tell_per_100w"] = round(tell_density, 2)

    contractions = len(re.findall(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", low))
    contraction_rate = contractions / (len(words) / 100.0)
    detail["contractions_per_100w"] = round(contraction_rate, 2)

    tell_ai = _lerp_score(tell_density, 0.0, 3.0)          # 0/100w human, 3+/100w AI
    contr_ai = 1.0 - _lerp_score(contraction_rate, 0.0, 4.0)  # many contractions -> human

    p_ai = 0.7 * tell_ai + 0.3 * contr_ai
    detail["components"] = {"tell_ai": round(tell_ai, 3), "contraction_ai": round(contr_ai, 3)}
    return {"name": "lexical", "available": True, "p_ai": round(_clamp01(p_ai), 3), "detail": detail}


# ---------------------------------------------------------------------------
# Signal 1: Groq LLM (semantic / holistic)
# ---------------------------------------------------------------------------

_LLM_PROMPT = (
    "You are an AI-text-detection assistant. Assess whether the following text reads as "
    "HUMAN-written or AI-GENERATED, judging semantic coherence, stylistic uniformity, and "
    "the presence of generic 'assistant' phrasing.\n"
    "Respond with ONLY a JSON object: {{\"p_ai\": <float 0..1>, \"reason\": <short string>}} "
    "where p_ai is your probability the text is AI-generated.\n\n"
    "TEXT:\n\"\"\"\n{text}\n\"\"\""
)


def llm_signal(text: str, client=None, model: str = "llama-3.3-70b-versatile") -> dict:
    """
    Ask a Groq LLM for a holistic P(AI) estimate.

    If no client/API key is available, returns available=False and the ensemble
    re-weights onto the two heuristic signals.  This keeps the whole system
    runnable for local testing without a key while still using real Groq in
    production.  Blind spot: LLM detectors are themselves imperfect and can be
    fooled by lightly edited AI text — hence we never let it decide alone.
    """
    detail: dict = {"model": model}
    if client is None:
        return {"name": "llm", "available": False, "p_ai": None,
                "detail": {"note": "GROQ_API_KEY not set — LLM signal skipped, using heuristics"}}
    try:
        import json as _json
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "user", "content": _LLM_PROMPT.format(text=text[:4000])}],
        )
        raw = resp.choices[0].message.content.strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = _json.loads(m.group(0)) if m else {}
        p_ai = _clamp01(float(data.get("p_ai", 0.5)))
        detail["reason"] = str(data.get("reason", ""))[:200]
        return {"name": "llm", "available": True, "p_ai": round(p_ai, 3), "detail": detail}
    except Exception as exc:  # network/parse/rate-limit — degrade gracefully
        return {"name": "llm", "available": False, "p_ai": None,
                "detail": {"note": f"LLM signal error, using heuristics: {type(exc).__name__}"}}


# ---------------------------------------------------------------------------
# Multimodal stretch: image-metadata signal
# ---------------------------------------------------------------------------

_AI_IMAGE_TOOLS = ["midjourney", "dall-e", "dalle", "stable diffusion", "stablediffusion",
                   "sdxl", "firefly", "flux", "ideogram", "leonardo", "nightcafe",
                   "gan", "diffusion", "generated", "ai art", "novelai"]


def image_metadata_signal(metadata: dict, caption: str = "") -> dict:
    """
    Multimodal stretch: score image *metadata* (not pixels) for AI provenance.

    Looks at generation-tool tags, C2PA/'ai-generated' flags, and missing camera
    EXIF (real photos usually carry Make/Model/Exposure).  Also runs the text
    signals on the caption if provided.  Blind spot: metadata is trivially
    stripped or forged, so a low score here is weak evidence of 'human'.
    """
    detail: dict = {}
    blob = " ".join(f"{k}={v}".lower() for k, v in (metadata or {}).items())

    tool_hit = next((t for t in _AI_IMAGE_TOOLS if t in blob), None)
    c2pa_ai = str((metadata or {}).get("c2pa_ai_generated", "")).lower() in ("true", "1", "yes")
    has_camera_exif = any(k.lower() in ("make", "model", "exposuretime", "fnumber", "iso", "lensmodel")
                          for k in (metadata or {}))
    detail["tool_tag"] = tool_hit
    detail["c2pa_ai_generated"] = c2pa_ai
    detail["has_camera_exif"] = has_camera_exif

    if c2pa_ai or tool_hit:
        p_ai = 0.9
    elif has_camera_exif:
        p_ai = 0.2
    else:
        p_ai = 0.55  # no signal either way -> lean uncertain
    detail["metadata_p_ai"] = p_ai

    # Blend with caption text signals if a caption was supplied.
    if caption and len(_words(caption)) >= 8:
        sty = stylometric_signal(caption)["p_ai"]
        lex = lexical_signal(caption)["p_ai"]
        cap_p = 0.6 * sty + 0.4 * lex
        detail["caption_p_ai"] = round(cap_p, 3)
        p_ai = 0.6 * p_ai + 0.4 * cap_p

    return {"name": "image_metadata", "available": True, "p_ai": round(_clamp01(p_ai), 3), "detail": detail}
