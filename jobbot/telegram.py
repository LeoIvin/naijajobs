"""Minimal Telegram Bot API client: send messages, poll updates."""

import html

import requests

TIMEOUT = 30
MAX_MESSAGE_CHARS = 3800  # Telegram hard limit is 4096; leave headroom


def _call(token: str, method: str, **params):
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=params,
        timeout=TIMEOUT,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data["result"]


def send_message(token: str, chat_id: str, text: str) -> None:
    _call(
        token, "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def send_long_message(token: str, chat_id: str, lines: list[str]) -> None:
    """Send lines as one or more messages, each under the length limit."""
    chunk: list[str] = []
    size = 0
    for line in lines:
        if chunk and size + len(line) + 1 > MAX_MESSAGE_CHARS:
            send_message(token, chat_id, "\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        send_message(token, chat_id, "\n".join(chunk))


def get_updates(token: str, offset: int) -> list[dict]:
    return _call(token, "getUpdates", offset=offset, timeout=0)


def format_job_line(job, with_date: bool = False) -> str:
    title = html.escape(job.title)
    company = html.escape(job.company)
    location = html.escape(job.location)
    remote = " 🌍" if job.is_remote else ""
    date = f" · 🗓 {job.posted}" if with_date and job.posted else ""
    return (f"• <a href=\"{html.escape(job.url)}\">{title}</a>\n"
            f"   🏢 {company} · 📍 {location}{remote}{date}")
