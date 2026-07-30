"""Fetchers for public ATS job-board JSON APIs.

Each fetcher takes a company board slug and returns a list of Job objects.
All of these endpoints are public and require no authentication.
"""

from dataclasses import dataclass

import requests

TIMEOUT = 20
UA = {"User-Agent": "NaijaJobs/1.0 (job-alert bot)"}


@dataclass
class Job:
    uid: str  # "<ats>:<company>:<external id>" — stable dedupe key
    ats: str
    company: str
    title: str
    location: str
    url: str
    is_remote: bool
    posted: str = ""  # ISO date (YYYY-MM-DD) the posting went live, "" if unknown


def _iso_date(value) -> str:
    """'2026-07-25T16:39:07.428Z' or epoch millis -> '2026-07-25'."""
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return ""


def _get_json(url: str, **kwargs):
    resp = requests.get(url, timeout=TIMEOUT, headers=UA, **kwargs)
    resp.raise_for_status()
    return resp.json()


def fetch_greenhouse(slug: str) -> list[Job]:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name") or "Unspecified"
        jobs.append(Job(
            uid=f"greenhouse:{slug}:{j['id']}",
            ats="greenhouse",
            company=slug,
            title=j.get("title", ""),
            location=location,
            url=j.get("absolute_url", ""),
            is_remote="remote" in location.lower(),
            posted=_iso_date(j.get("first_published") or j.get("updated_at")),
        ))
    return jobs


def fetch_lever(slug: str) -> list[Job]:
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs = []
    for j in data:
        location = (j.get("categories") or {}).get("location") or "Unspecified"
        remote = "remote" in location.lower() or j.get("workplaceType") == "remote"
        jobs.append(Job(
            uid=f"lever:{slug}:{j['id']}",
            ats="lever",
            company=slug,
            title=j.get("text", ""),
            location=location,
            url=j.get("hostedUrl", ""),
            is_remote=remote,
            posted=_iso_date(j.get("createdAt")),
        ))
    return jobs


def fetch_ashby(slug: str) -> list[Job]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        location = j.get("location") or "Unspecified"
        jobs.append(Job(
            uid=f"ashby:{slug}:{j['id']}",
            ats="ashby",
            company=slug,
            title=j.get("title", ""),
            location=location,
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            is_remote=bool(j.get("isRemote")) or "remote" in location.lower(),
            posted=_iso_date(j.get("publishedAt")),
        ))
    return jobs


def fetch_workable(slug: str) -> list[Job]:
    jobs = []
    token = None
    while True:
        body = {"query": ""} if token is None else {"query": "", "token": token}
        resp = requests.post(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            json=body, timeout=TIMEOUT, headers=UA,
        )
        resp.raise_for_status()
        data = resp.json()
        for j in data.get("results", []):
            loc = j.get("location") or {}
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            location = ", ".join(p for p in parts if p) or "Unspecified"
            jobs.append(Job(
                uid=f"workable:{slug}:{j['shortcode']}",
                ats="workable",
                company=slug,
                title=j.get("title", ""),
                location=location,
                url=f"https://apply.workable.com/{slug}/j/{j['shortcode']}/",
                is_remote=bool(j.get("remote")) or j.get("workplace") == "remote",
                posted=_iso_date(j.get("published")),
            ))
        token = data.get("nextPage")
        if not token:
            break
    return jobs


def fetch_smartrecruiters(slug: str) -> list[Job]:
    jobs = []
    offset = 0
    while True:
        data = _get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": 100, "offset": offset},
        )
        content = data.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            location = ", ".join(p for p in parts if p) or "Unspecified"
            jobs.append(Job(
                uid=f"smartrecruiters:{slug}:{j['id']}",
                ats="smartrecruiters",
                company=slug,
                title=j.get("name", ""),
                location=location,
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                is_remote=bool(loc.get("remote")) or "remote" in location.lower(),
                posted=_iso_date(j.get("releasedDate")),
            ))
        offset += len(content)
        if not content or offset >= data.get("totalFound", 0):
            break
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
}


def fetch_all(companies: dict[str, list[str]]) -> tuple[list[Job], list[str]]:
    """Fetch every board; returns (jobs, error descriptions). One bad board
    never aborts the run."""
    jobs: list[Job] = []
    errors: list[str] = []
    for ats, slugs in companies.items():
        fetcher = FETCHERS.get(ats)
        if fetcher is None:
            errors.append(f"unknown ATS '{ats}' in companies list")
            continue
        for slug in slugs:
            try:
                jobs.extend(fetcher(slug))
            except Exception as exc:
                errors.append(f"{ats}/{slug}: {exc}")
    return jobs, errors
