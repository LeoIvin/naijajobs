"""Paths, environment loading, and default role matching."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "companies.json"
STATE_FILE = ROOT / "state" / "state.json"

def load_dotenv() -> None:
    """Load KEY=VALUE lines from a .env file in the project root, if present.

    Existing environment variables win, so CI secrets are never overridden.
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_companies() -> dict[str, list[str]]:
    with open(COMPANIES_FILE) as f:
        return json.load(f)
