"""Quarterly sub-processor monitoring for PIPEDA compliance.

Fetches each vendor's published sub-processor page and diffs against the
stored baseline in docs/subprocessors-baseline.json. Exits 1 if any vendor
page has changed since the last recorded baseline, so the GitHub Action can
open a PR for human review and re-disclosure in the privacy policy.

Usage (standalone):
    python -m backend.utils.subprocessor_audit            # check + update baseline
    python -m backend.utils.subprocessor_audit --dry-run  # check only, no writes

Exit codes:
    0 — no changes detected (or first run with null baseline — initialised)
    1 — one or more vendors changed; baseline updated (or would be in --dry-run)
    2 — fatal error (all vendor fetches failed, unreadable baseline)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Repository root is three levels up from backend/utils/subprocessor_audit.py
_REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = _REPO_ROOT / "docs" / "subprocessors-baseline.json"

_USER_AGENT = "Spinr-SubprocessorAudit/1.0 (PIPEDA compliance; quarterly cron)"
_FETCH_TIMEOUT = 20  # seconds per vendor


def _fetch_page(url: str) -> tuple[str, str | None]:
    """Fetch URL text content and ETag. Returns (content, etag_or_None)."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        etag = resp.headers.get("ETag")
        content = resp.read().decode("utf-8", errors="replace")
    return content, etag


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run_audit(dry_run: bool = False) -> int:
    """
    Returns 0 if no changes, 1 if changes detected, 2 on fatal error.
    """
    if not BASELINE_PATH.exists():
        logger.error("Baseline not found: %s", BASELINE_PATH)
        return 2

    try:
        baseline = json.loads(BASELINE_PATH.read_text())
    except Exception as exc:
        logger.error("Cannot read baseline: %s", exc)
        return 2

    vendors: dict = baseline.get("vendors", {})
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    changed: list[str] = []
    errors: list[str] = []
    initialised: list[str] = []

    for key, meta in vendors.items():
        url: str = meta.get("subprocessor_url", "")
        if not url:
            logger.warning("No URL for vendor %s — skipping", key)
            continue

        try:
            content, etag = _fetch_page(url)
        except Exception as exc:
            logger.warning("Fetch failed for %s (%s): %s", key, url, exc)
            errors.append(key)
            continue

        new_hash = _sha256(content)
        old_hash: str | None = meta.get("content_hash")
        old_etag: str | None = meta.get("etag")

        if old_hash is None:
            # First run — record without flagging as changed.
            logger.info("Initialising baseline for %s", key)
            initialised.append(key)
            meta["content_hash"] = new_hash
            meta["etag"] = etag
            meta["last_reviewed"] = now_date
        elif new_hash != old_hash or (etag and etag != old_etag):
            logger.warning("CHANGE DETECTED for %s (url: %s)", key, url)
            changed.append(key)
            meta["content_hash"] = new_hash
            meta["etag"] = etag
            meta["last_changed"] = now_date
            meta["last_reviewed"] = now_date
        else:
            logger.info("No change for %s", key)
            meta["last_reviewed"] = now_date

    baseline["_last_audit"] = now_iso

    if not dry_run:
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
        logger.info("Baseline updated at %s", BASELINE_PATH)

    # Print human-readable report to stdout (captured by the GH Action).
    print("=" * 60)
    print(f"Sub-processor audit  {now_iso}")
    print("=" * 60)

    if initialised:
        print("\nINITIALISED (first run — no prior baseline):")
        for k in initialised:
            print(f"  • {k}: {vendors[k]['name']}")

    if changed:
        print("\n⚠  CHANGES DETECTED — review and re-disclose in privacy policy:")
        for k in changed:
            print(f"  • {k}: {vendors[k]['name']}")
            print(f"    url: {vendors[k]['subprocessor_url']}")
    else:
        print("\n✓  No changes detected.")

    if errors:
        print("\n⚠  Fetch errors (investigate):")
        for k in errors:
            print(f"  • {k}: {vendors[k].get('subprocessor_url', 'no url')}")

    print()

    if errors and not changed and len(errors) == len(vendors):
        logger.error("All vendor fetches failed — possible network issue")
        return 2

    return 1 if changed else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check only; do not write updated baseline to disk",
    )
    args = parser.parse_args()
    sys.exit(run_audit(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
