"""
app.py — Provenance Guard API (AI201 Project 4).

A backend a creative-sharing platform can plug in to classify submitted content
as human- vs AI-written, score confidence, surface a transparency label, and
handle appeals — with rate limiting and a structured audit log.

Endpoints
    GET  /                health + capability summary
    POST /submit          classify content  (rate limited)
    POST /appeal          contest a classification
    GET  /log             recent audit-log entries
    POST /verify          earn a "Verified Human" credential   (stretch: certificate)
    GET  /analytics       detection metrics as JSON             (stretch: dashboard)
    GET  /dashboard       simple HTML analytics view            (stretch: dashboard)

Run:  python app.py    (listens on http://localhost:5000)
"""

from __future__ import annotations

import os
import uuid

from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import analytics
import storage
from labels import label_text
from scoring import combine, display_confidence
from signals import image_metadata_signal, llm_signal, lexical_signal, stylometric_signal

# --- optional .env + Groq client ------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
_groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception:
        _groq_client = None

# --- app + rate limiting --------------------------------------------------------
app = Flask(__name__)
storage.init()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# Rate-limit rationale (documented in README): a genuine writer submits their own
# work a handful of times an hour; 10/min + 100/day comfortably covers real use
# while stopping a script from flooding the detector or running the LLM up a bill.
SUBMIT_LIMITS = os.environ.get("PG_SUBMIT_LIMITS", "10 per minute;100 per day")


# ------------------------------------------------------------------ pipeline ----

def run_pipeline(text: str, content_type: str, metadata: dict, caption: str) -> tuple[list[dict], dict]:
    """Run the right signals for the content type and combine them."""
    if content_type == "image":
        # Multimodal path: metadata provenance + caption stylometry/lexical (blended).
        sigs = [image_metadata_signal(metadata or {}, caption or "")]
    else:
        sigs = [
            llm_signal(text, _groq_client, GROQ_MODEL),  # semantic
            stylometric_signal(text),                    # structural
            lexical_signal(text),                        # surface-lexical
        ]
    scored = combine(sigs)
    return sigs, scored


# ------------------------------------------------------------------- routes -----

@app.get("/")
def index():
    return jsonify({
        "service": "Provenance Guard",
        "llm_signal_active": _groq_client is not None,
        "endpoints": ["POST /submit", "POST /appeal", "GET /log",
                      "POST /verify", "GET /analytics", "GET /dashboard"],
        "submit_rate_limit": SUBMIT_LIMITS,
    })


@app.post("/submit")
@limiter.limit(SUBMIT_LIMITS)
def submit():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    creator_id = (body.get("creator_id") or "anonymous").strip()
    content_type = (body.get("content_type") or "text").strip().lower()
    metadata = body.get("metadata") or {}
    caption = (body.get("caption") or "").strip()

    if content_type == "text" and not text:
        return jsonify({"error": "field 'text' is required for text submissions"}), 400
    if content_type == "image" and not (metadata or caption):
        return jsonify({"error": "image submissions need 'metadata' and/or 'caption'"}), 400

    signals, scored = run_pipeline(text, content_type, metadata, caption)
    p_ai, verdict = scored["p_ai"], scored["verdict"]
    verified = storage.is_verified(creator_id)
    label = label_text(p_ai, verdict, verified=verified)

    content_id = "pg_" + uuid.uuid4().hex[:12]
    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "content_type": content_type,
        "status": "classified",
        "verdict": verdict,
        "p_ai": p_ai,
        "confidence": display_confidence(p_ai, verdict),
        "degraded": scored["degraded"],
        "per_signal": scored.get("per_signal", {}),
        "weights": scored.get("weights", {}),
        "verified_creator": verified,
        "excerpt": (text or caption)[:120],
    }
    storage.save_submission(record)
    storage.append_log({
        "event": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "content_type": content_type,
        "verdict": verdict,
        "confidence": record["confidence"],
        "p_ai": p_ai,
        "signals": {s["name"]: s.get("p_ai") for s in signals},
        "signal_detail": {s["name"]: s.get("detail") for s in signals},
    })

    return jsonify({
        "content_id": content_id,
        "attribution": verdict,
        "confidence": record["confidence"],
        "p_ai": p_ai,
        "label": label,
        "signals": {s["name"]: s.get("p_ai") for s in signals},
        "signal_detail": {s["name"]: s.get("detail") for s in signals},
        "scoring": {"weights": scored.get("weights", {}),
                    "thresholds": scored.get("thresholds", {}),
                    "degraded": scored["degraded"]},
        "verified_creator": verified,
    })


@app.post("/appeal")
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = (body.get("content_id") or "").strip()
    reasoning = (body.get("creator_reasoning") or "").strip()

    if not content_id or not reasoning:
        return jsonify({"error": "'content_id' and 'creator_reasoning' are required"}), 400

    rec = storage.get_submission(content_id)
    if rec is None:
        return jsonify({"error": f"unknown content_id: {content_id}"}), 404

    rec = storage.update_status(content_id, "under_review",
                                {"appeal_reasoning": reasoning})
    storage.append_log({
        "event": "appeal",
        "content_id": content_id,
        "creator_id": rec.get("creator_id"),
        "status": "under_review",
        "appeal_reasoning": reasoning,
        "original_verdict": rec.get("verdict"),
        "original_confidence": rec.get("confidence"),
    })
    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received. This classification is now queued for human review.",
        "original_verdict": rec.get("verdict"),
    })


@app.get("/log")
def log():
    limit = request.args.get("limit", type=int)
    return jsonify({"entries": storage.get_log(limit)})


@app.post("/verify")
def verify():
    """
    Provenance-certificate stretch: a creator earns a 'Verified Human' credential
    by completing an attestation step.  Here that step is echoing a required
    pledge (a lightweight stand-in for real identity verification).  The badge is
    displayed on their content but deliberately does NOT feed the detector — so it
    can't be abused to launder AI text past the signals.
    """
    body = request.get_json(silent=True) or {}
    creator_id = (body.get("creator_id") or "").strip()
    attestation = (body.get("attestation") or "").strip().lower()
    required = "i am a human creator and this is my original work"

    if not creator_id:
        return jsonify({"error": "'creator_id' is required"}), 400
    if required not in attestation:
        return jsonify({
            "error": "attestation phrase missing",
            "required_attestation": f"Include the exact sentence: \"{required}\"",
        }), 400

    storage.set_verified(creator_id)
    cert_id = "cert_" + uuid.uuid4().hex[:10]
    storage.append_log({
        "event": "verification",
        "creator_id": creator_id,
        "credential": "verified_human",
        "certificate_id": cert_id,
    })
    return jsonify({
        "creator_id": creator_id,
        "credential": "verified_human",
        "certificate_id": cert_id,
        "note": "Future submissions by this creator will display the Verified Human badge.",
    })


@app.get("/analytics")
def analytics_json():
    return jsonify(analytics.compute())


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", m=analytics.compute())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
