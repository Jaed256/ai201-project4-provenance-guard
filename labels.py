"""
labels.py — Transparency label generation.

The label a reader sees changes with the confidence score (never a constant).
There are exactly three verdict variants, plus an optional "Verified Human"
badge (provenance-certificate stretch) that is prepended when the creator holds
a credential.

The exact text of all three variants is reproduced in the README as required.
Plain language, no jargon, and every label states that it is an automated
estimate the creator can appeal — this is the false-positive safeguard in UX form.
"""

from __future__ import annotations

from scoring import VERDICT_AI, VERDICT_HUMAN, VERDICT_UNCERTAIN, display_confidence

VERIFIED_BADGE = (
    "🔵 Verified Human Creator — this creator completed Provenance Guard identity "
    "attestation. "
)


def label_text(p_ai: float, verdict: str, verified: bool = False) -> str:
    """Return the exact transparency-label string shown to a reader."""
    conf = display_confidence(p_ai, verdict)

    if verdict == VERDICT_AI:
        body = (
            f"⚠️ Likely AI-generated. Our automated analysis found strong signals of "
            f"machine authorship in this text (confidence {conf}%). This is an estimate, "
            f"not a certainty — no AI detector is perfect. The creator can appeal this label."
        )
    elif verdict == VERDICT_HUMAN:
        body = (
            f"✅ Likely human-written. Our automated analysis found little evidence of AI "
            f"generation (confidence {conf}%). Attribution estimates can be wrong; this label "
            f"reflects an automated check, not a guarantee."
        )
    else:  # VERDICT_UNCERTAIN
        body = (
            f"❓ Attribution uncertain. Our analysis could not confidently tell whether this "
            f"was written by a person or by AI (estimated {round(p_ai * 100)}% likelihood of "
            f"AI). We're showing this openly instead of guessing — treat authorship as unknown."
        )

    return (VERIFIED_BADGE + body) if verified else body


# The three canonical variants (used to render the README table and to test that
# all three are reachable).  Representative scores chosen inside each band.
def canonical_variants() -> dict:
    return {
        "high_confidence_ai": label_text(0.93, VERDICT_AI),
        "high_confidence_human": label_text(0.07, VERDICT_HUMAN),
        "uncertain": label_text(0.55, VERDICT_UNCERTAIN),
    }
