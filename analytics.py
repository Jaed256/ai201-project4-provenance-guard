"""
analytics.py — Metrics for the analytics-dashboard stretch feature.

Aggregates the audit log + submission store into detection patterns, appeal rate,
and additional metrics (average confidence, signal-disagreement rate,
verified-human share).  Consumed by GET /analytics (JSON) and GET /dashboard (HTML).
"""

from __future__ import annotations

from collections import Counter

import storage


def compute() -> dict:
    subs = storage.all_submissions()
    log = storage.get_log()

    verdicts = Counter(s.get("verdict", "unknown") for s in subs.values())
    total = len(subs)
    appeals = sum(1 for s in subs.values() if s.get("status") == "under_review")
    content_types = Counter(s.get("content_type", "text") for s in subs.values())

    p_ais = [s["p_ai"] for s in subs.values() if isinstance(s.get("p_ai"), (int, float))]
    avg_conf = round(sum(p_ais) / len(p_ais), 3) if p_ais else None

    # Signal disagreement: submissions where the two heuristic signals land on
    # opposite sides of 0.5 (a proxy for "hard" content worth reviewing).
    disagreements = 0
    for s in subs.values():
        per = s.get("per_signal", {})
        vals = [v for k, v in per.items() if k in ("stylometric", "lexical", "llm")]
        if len(vals) >= 2 and (max(vals) >= 0.5 > min(vals)):
            disagreements += 1

    verified = sum(1 for c in storage.all_creators().values() if c.get("verified"))

    return {
        "total_submissions": total,
        "verdict_breakdown": dict(verdicts),
        "content_type_breakdown": dict(content_types),
        "appeals_filed": appeals,
        "appeal_rate": round(appeals / total, 3) if total else 0.0,
        "avg_p_ai": avg_conf,
        "signal_disagreement_rate": round(disagreements / total, 3) if total else 0.0,
        "verified_human_creators": verified,
        "audit_log_entries": len(log),
    }
