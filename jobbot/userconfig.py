"""User-editable config: filters, subscribers, tracked-company changes.

Lives in data/filters.json. The Telegram webhook worker is the writer; the
scraper only reads it. Keeping one writer per file avoids commit races.
"""

import json

from .config import ROOT

CONFIG_FILE = ROOT / "data" / "filters.json"

DEFAULT_CONFIG = {
    "filters": {
        "keywords": [],      # title must contain one of these (empty = any)
        "locations": [],     # location must contain one of these (empty = any)
        "remote_only": False,
        "paused": False,
    },
    "subscribers": [],       # chat ids subscribed via /start (owner is implicit)
    "extra_companies": {},   # ats -> [slug] added via /addcompany
    "removed_companies": [],  # "ats:slug" removed via /delcompany
}


def load_userconfig() -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        loaded = json.loads(CONFIG_FILE.read_text())
        config.update({k: loaded[k] for k in loaded if k in DEFAULT_CONFIG})
        for key, value in DEFAULT_CONFIG["filters"].items():
            config["filters"].setdefault(key, value)
    return config


def merged_companies(base: dict[str, list[str]], config: dict) -> dict[str, list[str]]:
    """companies.json plus /addcompany additions, minus /delcompany removals."""
    removed = set(config.get("removed_companies", []))
    merged: dict[str, list[str]] = {}
    for ats in set(base) | set(config.get("extra_companies", {})):
        slugs = list(base.get(ats, [])) + [
            s for s in config.get("extra_companies", {}).get(ats, [])
            if s not in base.get(ats, [])
        ]
        merged[ats] = [s for s in slugs if f"{ats}:{s}" not in removed]
    return merged
