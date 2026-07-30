"""Scraper bookkeeping: seen jobs and known companies.

Stored as one JSON file that the GitHub Actions workflow commits back after
each run. The scraper is the only writer of this file; user-editable settings
live in data/filters.json (see userconfig.py) and are written by the webhook
worker.
"""

import json
from datetime import datetime, timedelta, timezone

from .config import STATE_FILE

SEEN_RETENTION_DAYS = 120

DEFAULT_STATE = {
    "initialized": False,
    "seen": {},               # uid -> ISO date first seen
    "known_companies": None,  # ["ats:slug"] scraped before; None = first run
}


def load_state() -> dict:
    state = json.loads(json.dumps(DEFAULT_STATE))  # deep copy of defaults
    if STATE_FILE.exists():
        loaded = json.loads(STATE_FILE.read_text())
        state.update({k: loaded[k] for k in loaded if k in DEFAULT_STATE})
    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: state[k] for k in DEFAULT_STATE}
    STATE_FILE.write_text(json.dumps(slim, indent=1, sort_keys=True))


def prune_seen(state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)).date().isoformat()
    state["seen"] = {uid: d for uid, d in state["seen"].items() if d >= cutoff}
