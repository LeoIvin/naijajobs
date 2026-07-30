# NaijaJobs 🇳🇬

A Telegram job-alert bot for the Nigerian market. It polls the public JSON
APIs of the five major ATS platforms (Greenhouse, Lever, Ashby, Workable,
SmartRecruiters) across a curated list of company boards — Nigerian and
African employers alongside global remote-friendly tech companies — and
pushes **new postings** to Telegram within minutes of going live.

Subscribers control what they receive with in-chat commands: keywords,
locations, remote-only. No app to install, no email digest, no missed
posting.

## Status

Pre-launch. The scraper, alerting, dedup, and instant command handling are
production-tested (this is a fork of a working deployment). Before going
live you need to: create a bot with BotFather, deploy the Actions workflow
and the Cloudflare Worker, and decide the multi-user model (see Roadmap).

## Coverage

| ATS | Boards |
|---|---|
| Greenhouse | 38 (incl. Moniepoint, Jumia) |
| Lever | 8 |
| Ashby | 16 |
| Workable | 11 (incl. Paystack, Flutterwave, FairMoney, Kuda, Andela, Interswitch, PiggyVest, Carbon, Helium Health, Mono) |
| SmartRecruiters | 2 |

**75 boards.** Every slug is validated against the live API before being
added — see the ATS URL cheat-sheet below to add more, or use `/addcompany`
from Telegram.

Note: Greenhouse, Lever and Ashby return HTTP 404 for an unknown slug, so
validation is reliable. **SmartRecruiters returns `200` with zero postings
for nonexistent companies**, so never trust an empty SmartRecruiters board —
confirm it has real postings before adding it.

| ATS | Job board URL pattern | Slug |
|---|---|---|
| Greenhouse | `job-boards.greenhouse.io/moniepoint` | `moniepoint` |
| Lever | `jobs.lever.co/palantir` | `palantir` |
| Ashby | `jobs.ashbyhq.com/openai` | `openai` |
| Workable | `apply.workable.com/paystack` | `paystack` |
| SmartRecruiters | `careers.smartrecruiters.com/ServiceNow` | `servicenow` |

## Architecture

Two compute planes, git as the datastore, $0/month to run:

- **Scraper** (`jobbot/`, Python) — GitHub Actions on a 5-minute cron, four
  internal polls per run for a ~1-minute effective cadence. Fetches all
  boards, sends alerts for postings it hasn't seen, and commits
  `state/state.json` (seen-job history) plus `data/jobs.json` (snapshot of
  every current posting).
- **Command worker** (`worker/`, Cloudflare Worker) — receives every Telegram
  message by webhook and replies in milliseconds, reading `data/jobs.json`
  for `/recent` and owning `data/filters.json` (filters, subscribers,
  company edits) which it writes via the GitHub API.

Each file has exactly one writer, so the two planes never conflict.

## Commands

| Command | Effect |
|---|---|
| `/start` | subscribe |
| `/stop` | unsubscribe |
| `/recent` / `/recent 7` | postings from the last N days (default 3) |
| `/filters` | show active filters |
| `/addkeyword remote` / `/delkeyword remote` | title must contain one of your keywords |
| `/addlocation lagos` / `/dellocation lagos` | restrict by location (remote always passes) |
| `/remote on` / `/remote off` | remote postings only |
| `/pause` / `/resume` | mute / unmute alerts |
| `/companies` | list tracked boards |
| `/addcompany workable paystack` | track a new board (validated first) |
| `/delcompany workable paystack` | stop tracking a board |
| `/subscribers` | how many chats receive alerts |

There is no built-in role filter — every new posting is eligible, and
keywords are how a subscriber narrows to their field.

## Setup

1. **Bot:** message [@BotFather](https://t.me/BotFather) → `/newbot`, copy the token.
2. **Local:**
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env          # paste the token
   .venv/bin/python -m jobbot --get-chat-id   # then paste the chat id too
   .venv/bin/python -m jobbot --dry-run       # fetch + filter, no messages sent
   ```
3. **Scraper:** push to GitHub, add `TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_CHAT_ID` as Actions secrets, enable the workflow.
   A public repo gets unlimited free Actions minutes; a private repo has
   2,000 min/month, enough for roughly a 30-minute cadence.
4. **Command worker:**
   ```bash
   cd worker && npx wrangler login
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put GITHUB_TOKEN     # fine-grained PAT, Contents R/W, this repo only
   npx wrangler secret put OWNER_CHAT_ID
   npx wrangler secret put WEBHOOK_SECRET   # any random string
   npx wrangler deploy
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d "url=<WORKER_URL>" -d "secret_token=<WEBHOOK_SECRET>"
   ```

The first real run baselines every existing posting as "seen" so nobody gets
a launch-day flood; alerts start with the next genuinely new posting.

## Roadmap before charging money

1. **Per-user filters.** Today all subscribers share one filter set and any
   of them can change the company list. Paying users need isolated
   preferences and admin-only board management.
2. **Delivery at scale.** Alerts are sent sequentially with no retry;
   Telegram throttles around 30 messages/second. A rate-limited queue with
   429 handling is needed beyond ~50 subscribers.
3. **State store.** Git commits are a fine database for one user; per-user
   state belongs in Cloudflare KV/D1.
4. **Distribution.** A free public Telegram channel (unlimited members)
   broadcasting everything, with paid per-user filtered DMs, is the natural
   funnel.
5. **More sources.** Nigerian aggregators (MyJobMag, HotNigerianJobs)
   publish RSS, which fits the existing fetcher pattern. Check terms of
   service before scraping anything that lacks a public feed.
