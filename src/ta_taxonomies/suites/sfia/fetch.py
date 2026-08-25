"""Fetch a local SFIA snapshot for the loader (pointer, not payload).

Use case: a contributor who holds SFIA's free personal/internal licence runs
this once to keep a **local, gitignored** copy of the published SFIA pages, so
``load.py --mode full`` is reproducible on their machine.

Why it exists, and the one thing to understand before using it:

    SFIA's free licence covers personal and internal use. Redistributing SFIA
    material to another organisation requires a fee-bearing licence, and a
    public Apache-2.0 repository is redistribution to everyone
    (TA-workspace ADR-0006 §2). So the snapshot this writes stays under
    ``data/sfia/`` — gitignored, never committed, never published — and only
    the *factual* fields survive extraction: skill codes, skill names, level
    numbers, and structure. ``extract.py`` drops everything else.

There is no public bulk download: SFIA's spreadsheet distribution is behind
registration. This fetches the same pages a browser would, one at a time with a
delay, and stores them verbatim so extraction is reproducible without re-fetching.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path

from ta_taxonomies.suites.sfia.config import (
    SNAPSHOT_INDEX_FILE,
    SNAPSHOT_SKILLS_DIR,
    SOURCE,
    VERSION,
)

BASE = "https://sfia-online.org/en"
INDEX_URL = f"{BASE}/sfia-{VERSION}/skills/all-skills-a-z"
SKILL_URL = f"{BASE}/sfia-{VERSION}/skills/{{slug}}"

# Identify the project rather than impersonating a browser: a site owner who
# wants to say no should be able to see who is asking.
USER_AGENT = f"TA-taxonomies/{SOURCE}-suite (+https://github.com/LFX-Talent-Angels)"

# One request every DELAY seconds, single-threaded. 147 skill pages is a small
# ask of a not-for-profit's web server only if it is spread out.
DELAY_SECONDS = 0.4

_SLUG = re.compile(rf"/en/sfia-{VERSION}/skills/([a-z0-9\-]+)")


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https host
        return str(response.read().decode("utf-8", errors="replace"))


def skill_slugs(index_html: str) -> list[str]:
    """Slugs of every skill linked from the A–Z directory page, in page order."""
    seen: dict[str, None] = {}
    for slug in _SLUG.findall(index_html):
        if slug != "all-skills-a-z":
            seen.setdefault(slug, None)
    return list(seen)


def fetch_snapshot(data_dir: Path, *, delay: float = DELAY_SECONDS) -> dict[str, int]:
    """Write the A–Z page and every skill page under ``data_dir``. Returns counts."""
    skills_dir = data_dir / SNAPSHOT_SKILLS_DIR
    skills_dir.mkdir(parents=True, exist_ok=True)

    print(f"  GET {INDEX_URL}", flush=True)
    index_html = _get(INDEX_URL)
    (data_dir / SNAPSHOT_INDEX_FILE).write_text(index_html, encoding="utf-8")

    slugs = skill_slugs(index_html)
    print(f"    → {len(slugs)} skills listed", flush=True)
    fetched = 0
    for i, slug in enumerate(slugs, start=1):
        target = skills_dir / f"{slug}.html"
        if target.exists():
            continue
        time.sleep(delay)
        target.write_text(_get(SKILL_URL.format(slug=slug)), encoding="utf-8")
        fetched += 1
        if i % 25 == 0:
            print(f"    … {i}/{len(slugs)}", flush=True)
    return {"skills_listed": len(slugs), "pages_fetched": fetched}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a local, gitignored SFIA snapshot")
    parser.add_argument("--data-dir", type=Path, default=Path("data/sfia/raw"))
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS)
    args = parser.parse_args(argv)
    print(
        "SFIA is licensed material. This snapshot is for your own personal or\n"
        "internal use under SFIA's free licence; it must never be committed or\n"
        "redistributed. See NOTES.md and TA-workspace ADR-0006 §2.",
        flush=True,
    )
    counts = fetch_snapshot(args.data_dir, delay=args.delay)
    print(counts, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
