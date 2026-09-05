"""Build provenance and hosting-provider detection for the running replica.

Both backend deploy workflows (`.github/workflows/deploy-fly.yml` and
`.github/workflows/deploy-backend.yml`) write ``backend/build_info.json``
immediately before the Docker build::

    {"sha": "<git sha>", "ref": "refs/heads/main",
     "built_at": "2026-09-04T20:36:01Z", "provider": "fly" | "railway"}

so every running replica can say exactly which commit of ``main`` it was
built from. That is what lets the standby-parity monitor
(`.github/workflows/standby-parity-monitor.yml`) detect the warm standby
silently drifting behind the primary (ACTION_ITEMS.md C5) — before this,
nothing exposed by the backend answered "what commit is Railway running?".

The tracked file is a placeholder with ``"sha": null`` — it is committed
(not gitignored) because ``railway up`` honours ``.gitignore`` when uploading
the build context, so an ignored file would never reach the Railway image.
An un-stamped file (local dev, tests, a build that bypassed CI) makes
``load_build_info()`` return ``None`` rather than guessing.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# backend/build_info.json — sibling of server.py, one level above utils/.
BUILD_INFO_PATH = Path(__file__).resolve().parent.parent / "build_info.json"

_BUILD_INFO_KEYS = ("sha", "ref", "built_at", "provider")


@lru_cache(maxsize=1)
def load_build_info() -> Optional[dict]:
    """Return the deploy-time build stamp, or None when it was never written.

    Only the four known keys are returned, so a stray value in the file can
    never reach an API response. Cached for the process lifetime — the file
    is immutable inside the image.
    """
    try:
        raw = BUILD_INFO_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.error("build_info.json exists but could not be read: %s", exc)
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.error("build_info.json is not valid JSON: %s", exc)
        return None
    if not isinstance(data, dict):
        logger.error("build_info.json is not a JSON object")
        return None
    if not data.get("sha"):
        # The committed placeholder, or a build that bypassed the deploy
        # workflows: report "not stamped" rather than a dict of nulls.
        return None
    return {k: (str(data[k]) if data.get(k) is not None else None) for k in _BUILD_INFO_KEYS}


def detect_provider() -> str:
    """Best-effort hosting-provider detection from platform-injected env vars.

    Fly Machines always carry ``FLY_APP_NAME``/``FLY_MACHINE_ID``; Railway
    services always carry ``RAILWAY_PROJECT_ID``/``RAILWAY_ENVIRONMENT_ID``.
    Neither is something an operator sets by hand, so this cannot be
    misconfigured into lying — unlike the ``provider`` field of the build
    stamp, which records where the image was *deployed to* by CI.
    """
    if os.environ.get("FLY_APP_NAME") or os.environ.get("FLY_MACHINE_ID"):
        return "fly"
    if os.environ.get("RAILWAY_PROJECT_ID") or os.environ.get("RAILWAY_ENVIRONMENT_ID"):
        return "railway"
    return "unknown"
