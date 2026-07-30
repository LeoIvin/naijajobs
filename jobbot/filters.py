"""User-adjustable filtering: keywords, locations, remote-only.

All postings pass by default; only the user's filters narrow the feed.
"""

from .ats import Job


def matches(job: Job, filters: dict) -> bool:
    title = job.title.lower()
    location = job.location.lower()

    keywords = [k.lower() for k in filters.get("keywords", [])]
    if keywords and not any(k in title for k in keywords):
        return False

    if filters.get("remote_only") and not job.is_remote:
        return False

    locations = [loc.lower() for loc in filters.get("locations", [])]
    if locations:
        # A remote job is acceptable for any location preference.
        if not job.is_remote and not any(loc in location for loc in locations):
            return False

    return True
