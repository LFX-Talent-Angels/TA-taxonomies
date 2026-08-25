"""Fetch the BLS source files this suite loads. Reproducible, no registration.

Entrypoint::

    export BLS_CONTACT="you@example.org"
    python -m ta_taxonomies.suites.bls.fetch --out data/bls/raw

Why this is a module and not a line of ``curl`` in the README, which is what
the O*NET suite could get away with:

**www.bls.gov refuses automated clients, and download.bls.gov does not.**
Verified 2026-08-25. The workbooks the BLS website links from ``bls.gov/soc``
and ``bls.gov/emp`` — ``soc_structure_2018.xlsx``,
``ind-occ-matrix/occupation.xlsx`` — sit behind Akamai bot management and
answer **HTTP 403** to a plain request. They answer 403 to a request carrying
an honest identifying User-Agent too; what gets through is a full browser
navigation header set. This loader does not send one. Defeating a bot manager
an agency deliberately put in front of its site by pretending to be Chrome is
not "automating a public-domain download", and the 403 page says in terms that
bot activity outside BLS usage policy is prohibited.

It does not have to. ``download.bls.gov/pub/time.series/`` is the flat-file
server BLS publishes *for* bulk retrieval, it carries the same programs' data,
and it answers **200** to a request that identifies itself honestly — which is
what this module sends. So the answer to "is the download automatable without
registration" is **yes**: no account, no API key, no click-through, one
User-Agent naming who is asking.

What that costs is recorded rather than glossed: the flat files cover the SOC
codes BLS *publishes data for* — 825 detailed occupations, and 174 of the 450
broad groups its own codes imply. The complete SOC structure, with a title for
every group, lives in the workbook behind the 403. ``ids.soc_ancestors``
reconstructs the missing broad-group *codes* from the codes that are present,
so roll-up is exact; what is genuinely missing is their *titles*, and 276 nodes
carry ``title_source='derived'`` to say so. NOTES.md has the counts.

Licence: works of the US federal government are not subject to copyright
(17 U.S.C. §105). BLS asks to be cited, which ``fixtures/README.md`` and the
suite ``README.md`` do.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from ta_taxonomies.suites.bls.config import RELEASE

BASE = "https://download.bls.gov/pub/time.series"

# Employment Projections. ``ep.aspect`` is 46 MB and ``ep.series`` 18 MB; the
# rest are small. Every one of them is read by load.py — nothing is fetched
# "just in case", because a file nobody reads is a licence question nobody
# needed to ask.
EP_FILES: tuple[str, ...] = (
    "ep.txt",  # survey description — kept for provenance, not parsed
    "ep.occupation",  # SOC code → title, matrix display level
    "ep.industry",  # NEM industry code → title
    "ep.laytitle",  # everyday job titles → alt_labels
    "ep.series",  # occupation × industry cells + education/training codes
    "ep.data.1.AllData",  # base-year employment per cell
    "ep.aspect",  # projections, openings, wages, shares per cell
    "ep.eductrn",  # education code → text
    "ep.otjt",  # on-the-job-training code → text
    "ep.wkex",  # work-experience code → text
    "ep.dates",  # base year / projection year / wage year
)

# Occupational Employment and Wage Statistics. Only the occupation reference is
# read: it is where the SOC *definitions* are, and the OES data files are
# 331 MB and 1.2 GB, which this suite has no use for.
OE_FILES: tuple[str, ...] = ("oe.occupation",)

# BLS's stated expectation of an automated client is that it says who it is.
# The contact address is required rather than defaulted: an anonymous
# User-Agent is the thing their policy asks people not to send, and a
# hard-coded one would put whoever wrote this module on the hook for every
# later run.
CONTACT_ENV = "BLS_CONTACT"
PROJECT_URL = "https://github.com/LFX-Talent-Angels/TA-taxonomies"

# One request every two seconds. BLS asks automated clients not to interfere
# with other users; twelve files at this rate is under a minute either way, so
# the polite version costs nothing worth optimising.
REQUEST_INTERVAL_SECONDS = 2.0
TIMEOUT_SECONDS = 600


class BlsFetchError(RuntimeError):
    """Raised when a source file cannot be retrieved."""


def user_agent(contact: str | None = None) -> str:
    """Build the identifying User-Agent, or explain why it cannot be built."""
    resolved = (contact or os.getenv(CONTACT_ENV) or "").strip()
    if not resolved:
        raise BlsFetchError(
            f"set {CONTACT_ENV} to an email address before fetching. BLS asks "
            "automated clients to identify themselves, and this module will not "
            "send an anonymous or browser-imitating User-Agent on your behalf."
        )
    return f"TA-taxonomies/{RELEASE} ({PROJECT_URL}; {resolved})"


def fetch_file(url: str, target: Path, *, agent: str) -> int:
    """Download one file, returning its size in bytes.

    Writes to a ``.part`` file and renames on success, so an interrupted fetch
    leaves no half-file that the next run would treat as complete.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            with partial.open("wb") as handle:
                while chunk := response.read(1 << 20):
                    handle.write(chunk)
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise BlsFetchError(
            f"{url} returned HTTP {exc.code}. download.bls.gov serves these files "
            "to a client that identifies itself; a 403 here means the access terms "
            "moved, and ADR-0006 says to re-check them rather than work around them."
        ) from exc
    except urllib.error.URLError as exc:
        partial.unlink(missing_ok=True)
        raise BlsFetchError(f"{url} unreachable: {exc.reason}") from exc
    partial.replace(target)
    return target.stat().st_size


def fetch_all(out_dir: Path, *, contact: str | None = None, force: bool = False) -> dict[str, int]:
    """Fetch every source file into ``out_dir``; return name → size in bytes.

    An existing non-empty file is kept unless ``force``. These are pinned
    snapshots per ADR-0006 decision 6 — re-downloading on every run would make
    the graph depend on whatever BLS published this morning, which is the thing
    that decision exists to prevent.
    """
    agent = user_agent(contact)
    sizes: dict[str, int] = {}
    plan: list[tuple[str, str, Path]] = [
        (f"{BASE}/ep/{name}", f"ep/{name}", out_dir / "ep" / name) for name in EP_FILES
    ] + [(f"{BASE}/oe/{name}", f"oe/{name}", out_dir / "oe" / name) for name in OE_FILES]

    first = True
    for url, label, target in plan:
        if target.exists() and target.stat().st_size > 0 and not force:
            sizes[label] = target.stat().st_size
            print(f"  have {label} ({sizes[label]:,} bytes)", flush=True)
            continue
        if not first:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        first = False
        print(f"  fetching {label} …", flush=True)
        sizes[label] = fetch_file(url, target, agent=agent)
        print(f"    → {sizes[label]:,} bytes", flush=True)
    return sizes


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch BLS Employment Projections + OES occupation files"
    )
    parser.add_argument("--out", type=Path, default=Path("data/bls/raw"))
    parser.add_argument(
        "--contact",
        default=None,
        help=f"Email address for the User-Agent (default: ${CONTACT_ENV})",
    )
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sizes = fetch_all(args.out, contact=args.contact, force=args.force)
    print(f"{len(sizes)} files, {sum(sizes.values()):,} bytes in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
