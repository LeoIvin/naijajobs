"""Entry point: fetch boards, publish the jobs blob, alert subscribers.

Telegram commands are handled in real time by the webhook worker (worker/);
this process only scrapes and sends alerts.

Usage:
  python -m jobbot                 # normal run (needs TELEGRAM_* env vars)
  python -m jobbot --dry-run       # fetch + filter, print to stdout, no state changes
  python -m jobbot --get-chat-id   # setup helper (only works before the webhook is set)
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from . import filters, telegram
from .ats import fetch_all
from .config import ROOT, load_companies, load_dotenv
from .state import load_state, prune_seen, save_state
from .userconfig import load_userconfig, merged_companies

JOBS_FILE = ROOT / "data" / "jobs.json"


def print_chat_ids(token: str) -> None:
    updates = telegram.get_updates(token, offset=0)
    chats = {}
    for update in updates:
        chat = (update.get("message") or {}).get("chat")
        if chat:
            name = chat.get("username") or chat.get("first_name") or "?"
            chats[chat["id"]] = name
    if not chats:
        print("No messages found. Send your bot any message on Telegram, then rerun.")
        return
    for chat_id, name in chats.items():
        print(f"chat_id: {chat_id}  ({name})")


def write_jobs_blob(jobs) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jobs": [asdict(j) for j in sorted(jobs, key=lambda j: j.uid)],
    }
    JOBS_FILE.write_text(json.dumps(data, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(prog="jobbot")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and print matches without Telegram or state changes")
    parser.add_argument("--get-chat-id", action="store_true",
                        help="print chat ids of people who messaged the bot")
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    if args.get_chat_id:
        if not token:
            print("Set TELEGRAM_BOT_TOKEN first.", file=sys.stderr)
            return 1
        print_chat_ids(token)
        return 0

    state = load_state()
    config = load_userconfig()
    companies = merged_companies(load_companies(), config)
    jobs, errors = fetch_all(companies)
    for err in errors:
        print(f"warning: {err}", file=sys.stderr)
    print(f"Fetched {len(jobs)} postings from "
          f"{sum(len(v) for v in companies.values())} company boards.")

    # A company scraped for the first time gets baselined silently so
    # /addcompany never floods subscribers with its whole backlog.
    current_companies = sorted(
        f"{ats}:{slug}" for ats, slugs in companies.items() for slug in slugs
    )
    known = set(state["known_companies"]) if state["known_companies"] is not None \
        else set(current_companies)

    new_matches = [
        j for j in jobs
        if j.uid not in state["seen"]
        and f"{j.ats}:{j.company}" in known
        and filters.matches(j, config["filters"])
    ]

    if args.dry_run:
        print(f"{len(new_matches)} new matching postings "
              f"(state untouched — every run will look 'new' until a real run):")
        for job in new_matches:
            print(f"  [{job.ats}/{job.company}] {job.title} — {job.location}\n"
                  f"    {job.url}")
        return 0

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date().isoformat()
    for job in jobs:
        state["seen"].setdefault(job.uid, today)
    state["known_companies"] = current_companies
    write_jobs_blob(jobs)

    if not state["initialized"]:
        state["initialized"] = True
        telegram.send_message(
            token, chat_id,
            "🤖 <b>NaijaJobs is live!</b>\n"
            f"Tracking {sum(len(v) for v in companies.values())} company boards "
            f"({len(jobs)} current postings baselined as seen).\n"
            "You'll be notified about new postings from now on. Send /help for commands.",
        )
    elif new_matches and not config["filters"]["paused"]:
        header = f"🆕 <b>{len(new_matches)} new job posting{'s' if len(new_matches) != 1 else ''}</b>"
        lines = [header] + [telegram.format_job_line(j) for j in new_matches]
        recipients = [chat_id] + [s for s in config["subscribers"] if s != str(chat_id)]
        for recipient in recipients:
            try:
                telegram.send_long_message(token, recipient, lines)
            except Exception as exc:
                print(f"warning: couldn't message {recipient}: {exc}", file=sys.stderr)
        print(f"Sent {len(new_matches)} new postings to {len(recipients)} chat(s).")
    else:
        print("No new matching postings.")

    prune_seen(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
