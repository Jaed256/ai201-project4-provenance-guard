"""
storage.py — Structured JSON persistence for Provenance Guard.

Three files under ./data/ :
    audit_log.json   — append-only list of every decision + appeal (the audit log)
    submissions.json — content_id -> submission record (status, scores, creator)
    creators.json    — creator_id -> {"verified": bool, "verified_at": ts}

JSON (not print statements) so the log is structured and queryable, and easy to
show in the README / via GET /log.  A process-wide lock keeps writes consistent.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

DATA_DIR = os.environ.get("PG_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
AUDIT_LOG = os.path.join(DATA_DIR, "audit_log.json")
SUBMISSIONS = os.path.join(DATA_DIR, "submissions.json")
CREATORS = os.path.join(DATA_DIR, "creators.json")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _write(path: str, obj) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def init() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    for path, default in ((AUDIT_LOG, []), (SUBMISSIONS, {}), (CREATORS, {})):
        if not os.path.exists(path):
            _write(path, default)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def append_log(entry: dict) -> dict:
    entry = {"timestamp": _now(), **entry}
    with _lock:
        log = _read(AUDIT_LOG, [])
        log.append(entry)
        _write(AUDIT_LOG, log)
    return entry


def get_log(limit: int | None = None) -> list[dict]:
    log = _read(AUDIT_LOG, [])
    return log[-limit:] if limit else log


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

def save_submission(record: dict) -> None:
    with _lock:
        subs = _read(SUBMISSIONS, {})
        subs[record["content_id"]] = record
        _write(SUBMISSIONS, subs)


def get_submission(content_id: str) -> dict | None:
    return _read(SUBMISSIONS, {}).get(content_id)


def update_status(content_id: str, status: str, extra: dict | None = None) -> dict | None:
    with _lock:
        subs = _read(SUBMISSIONS, {})
        rec = subs.get(content_id)
        if rec is None:
            return None
        rec["status"] = status
        if extra:
            rec.update(extra)
        subs[content_id] = rec
        _write(SUBMISSIONS, subs)
    return rec


def all_submissions() -> dict:
    return _read(SUBMISSIONS, {})


# ---------------------------------------------------------------------------
# Creators (provenance certificate)
# ---------------------------------------------------------------------------

def set_verified(creator_id: str) -> dict:
    with _lock:
        creators = _read(CREATORS, {})
        creators[creator_id] = {"verified": True, "verified_at": _now()}
        _write(CREATORS, creators)
    return creators[creator_id]


def is_verified(creator_id: str) -> bool:
    return bool(_read(CREATORS, {}).get(creator_id, {}).get("verified"))


def all_creators() -> dict:
    return _read(CREATORS, {})
