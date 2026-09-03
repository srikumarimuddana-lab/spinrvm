"""Shared bot-identity helpers for locustfile.py and preauth_bots.py.

Factored out so the phone-generation sequence used by the one-time
pre-auth pass (preauth_bots.py) is *guaranteed* identical to the sequence
RiderBot/DriverBot.on_start would have generated live -- a single source
of truth instead of two copies that could silently drift (e.g. the next
time PHONE_PREFIX's format needs a fix, per T16 round 1's E.164 bug).

Env knobs (see locustfile.py's module docstring for the full list):
  LOADTEST_OTP            login OTP (default 1234 -- staging dev bypass)
  LOADTEST_PHONE_PREFIX   bot phone prefix
  LOADTEST_CENTER_LAT/LNG service-area centre (default Saskatoon downtown)
  LOADTEST_TOKEN_CACHE    path to the JSON token cache written by
                          preauth_bots.py and read by locustfile.py
                          (default: results/bot_tokens.json, relative to
                          loadtest/)
"""

from __future__ import annotations

import os
import random

BASE_OTP = os.environ.get("LOADTEST_OTP", "1234")
# Real Saskatoon area code (306) + the NANP fictional-555 exchange -- see
# docs/audit/2026-09-02-t16-staging-load-test-results.md for why the old
# default ("+1****55", literal asterisks) 422'd against the E.164 validator.
PHONE_PREFIX = os.environ.get("LOADTEST_PHONE_PREFIX", "+1306555")
CENTER_LAT = float(os.environ.get("LOADTEST_CENTER_LAT", "52.1332"))
CENTER_LNG = float(os.environ.get("LOADTEST_CENTER_LNG", "-106.6700"))
API = "/api/v1"

TOKEN_CACHE_PATH = os.environ.get(
    "LOADTEST_TOKEN_CACHE",
    os.path.join(os.path.dirname(__file__), "results", "bot_tokens.json"),
)

_phone_counter = {"rider": 0, "driver": 0}


def next_phone(kind: str) -> str:
    """Deterministic phone sequence: riders get even suffixes, drivers odd.

    Called with the same (kind, call-order) by both preauth_bots.py (once,
    up front) and locustfile.py's on_start (when no token cache is
    present, i.e. small ad-hoc/smoke runs) -- must stay a pure function of
    call count so the two never diverge.
    """
    _phone_counter[kind] += 1
    suffix = _phone_counter[kind] * 2 + (1 if kind == "driver" else 0)
    return f"{PHONE_PREFIX}{suffix:04d}"


def jitter_point(radius_deg: float = 0.02) -> tuple[float, float]:
    return (
        CENTER_LAT + random.uniform(-radius_deg, radius_deg),
        CENTER_LNG + random.uniform(-radius_deg, radius_deg),
    )
