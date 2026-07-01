"""
scoring.py — Combine detection signals into one calibrated confidence score,
then map that score to a verdict and label category.

Design decisions (documented in planning.md / README):

* The single number we report, `p_ai`, is the system's probability that the
  content is AI-generated (0 = confidently human, 1 = confidently AI).
  This is the "0.51 vs 1.0" number the spec asks about: 0.51 sits in the
  Uncertain band, 1.0 is a confident AI verdict.

* Signals are combined as a WEIGHTED AVERAGE of whichever signals are available
  (ensemble stretch: LLM + stylometric + lexical, with a documented weighting).
  If the LLM signal is unavailable, its weight is redistributed to the heuristics
  and we mark the result `degraded` so the label/README can be honest about it.

* FALSE-POSITIVE ASYMMETRY (the core UX decision): on a creative platform,
  wrongly branding a *human's* work as AI is worse than missing some AI.
  So the thresholds are asymmetric — it takes a high p_ai (>= 0.72) to say
  "Likely AI", but only p_ai <= 0.40 to clear a piece as "Likely human".
  Everything in between is surfaced honestly as "Uncertain" rather than guessed.
"""

from __future__ import annotations

# Ensemble weights (must sum to 1 across available signals; re-normalised below).
WEIGHTS = {"llm": 0.50, "stylometric": 0.30, "lexical": 0.20, "image_metadata": 1.00}

# Asymmetric decision thresholds on p_ai.
T_AI = 0.72       # need strong evidence to accuse
T_HUMAN = 0.40    # easier to clear as human (benefit of the doubt)

VERDICT_AI = "likely_ai"
VERDICT_HUMAN = "likely_human"
VERDICT_UNCERTAIN = "uncertain"


def combine(signals: list[dict]) -> dict:
    """
    signals: list of signal dicts from signals.py.
    Returns a scoring dict with the combined p_ai, verdict, and full breakdown.
    """
    usable = [s for s in signals if s.get("available") and s.get("p_ai") is not None]
    if not usable:
        # No signal ran at all — refuse to guess.
        return {
            "p_ai": 0.5, "verdict": VERDICT_UNCERTAIN, "degraded": True,
            "signals_used": [], "weights": {}, "note": "no signals available",
        }

    total_w = sum(WEIGHTS.get(s["name"], 0.1) for s in usable)
    weights = {s["name"]: round(WEIGHTS.get(s["name"], 0.1) / total_w, 3) for s in usable}
    p_ai = sum((WEIGHTS.get(s["name"], 0.1) / total_w) * s["p_ai"] for s in usable)
    p_ai = round(max(0.0, min(1.0, p_ai)), 3)

    verdict = classify(p_ai)
    degraded = not any(s["name"] == "llm" for s in usable) and any(
        s["name"] in ("stylometric", "lexical") for s in usable
    )

    return {
        "p_ai": p_ai,
        "verdict": verdict,
        "degraded": degraded,  # True when LLM signal was unavailable
        "signals_used": [s["name"] for s in usable],
        "per_signal": {s["name"]: s["p_ai"] for s in usable},
        "weights": weights,
        "thresholds": {"ai": T_AI, "human": T_HUMAN},
    }


def classify(p_ai: float) -> str:
    if p_ai >= T_AI:
        return VERDICT_AI
    if p_ai <= T_HUMAN:
        return VERDICT_HUMAN
    return VERDICT_UNCERTAIN


def display_confidence(p_ai: float, verdict: str) -> int:
    """
    A 0..100 'how sure are we about the verdict we stated' number for end users.
    For an AI verdict this rises with p_ai; for a human verdict it rises as p_ai
    falls; for uncertain it reflects how central the score is.
    """
    if verdict == VERDICT_AI:
        conf = p_ai
    elif verdict == VERDICT_HUMAN:
        conf = 1.0 - p_ai
    else:
        conf = 1.0 - abs(p_ai - 0.5) * 2  # 0.5 -> 1.0 "confidently uncertain"
    return round(conf * 100)
