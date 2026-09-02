"""One-time bot pre-authentication pass for the Locust harness (C50 T16 round 2).

THE PROBLEM (round 1 finding, docs/audit/2026-09-02-t16-staging-load-test-results.md):
`POST /auth/send-otp` is limited to 6/minute and `POST /auth/verify-otp` to
5/minute, both keyed by client IP (backend/routes/auth.py:396,900 via
slowapi's default per-IP key func -- see backend/utils/rate_limiter.py).
All 60 Locust bots ran from one process on one machine sharing one egress
IP against staging, so the previous harness's `on_start` (calling
send-otp + verify-otp *every time a bot spawned*, i.e. once per bot at
ramp-up) collectively blew through 5-6 logins/minute for the whole pool,
producing 529/535 and 530/535 429s -- a harness capacity artifact, not a
platform finding.

THE FIX (this file + locustfile.py's on_start change): authenticate every
bot user exactly ONCE, before the timed test starts, paced well under the
tighter of the two limits (verify-otp's 5/minute), and cache the resulting
access + refresh tokens to disk. locustfile.py's on_start then reads a
token from this cache instead of calling send-otp/verify-otp again -- so
the timed test itself (the part whose numbers matter) never touches the
OTP endpoints at all, and the real rate limiter is left completely
untouched (it still fires exactly as designed if a real client hits it).

Usage:
    cd loadtest
    export LOADTEST_BASE_URL=https://spinr-backend-staging.fly.dev
    python preauth_bots.py --riders 45 --drivers 15

Writes results/bot_tokens.json (path overridable via LOADTEST_TOKEN_CACHE,
same env var locustfile.py reads). Safe to re-run: it always logs in fresh
(dev OTP bypass allows repeat logins) and overwrites the cache atomically.

Pacing: paces logins at LOADTEST_PREAUTH_RATE per minute (default 4,
comfortably under verify-otp's 5/minute limit -- the binding constraint,
since send-otp's 6/minute is looser). 60 bots at 4/minute takes ~15
minutes; this runs once, before the clock starts on the timed scenario, so
the extra minutes cost nothing the SLA gates care about.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

from bot_common import API, BASE_OTP, TOKEN_CACHE_PATH, next_phone

DEFAULT_RATE_PER_MIN = float(os.environ.get("LOADTEST_PREAUTH_RATE", "4"))


def _login_one(base_url: str, session: requests.Session, phone: str) -> dict:
    r = session.post(f"{base_url}{API}/auth/send-otp", json={"phone": phone}, timeout=15)
    r.raise_for_status()
    r = session.post(
        f"{base_url}{API}/auth/verify-otp",
        json={"phone": phone, "code": BASE_OTP, "consent_accepted": True},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return {
        "phone": phone,
        "token": body["token"],
        "refresh_token": body.get("refresh_token"),
        "user": body.get("user") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--riders", type=int, default=45)
    parser.add_argument("--drivers", type=int, default=15)
    parser.add_argument(
        "--rate-per-min",
        type=float,
        default=DEFAULT_RATE_PER_MIN,
        help="Logins/minute across the whole pool -- keep below verify-otp's 5/minute (default 4).",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LOADTEST_BASE_URL", "http://localhost:8000"),
    )
    args = parser.parse_args()

    if args.rate_per_min >= 5:
        print(
            f"REFUSING: --rate-per-min={args.rate_per_min} is >= verify-otp's 5/minute "
            "limit. This would recreate the exact rate-limit artifact this script exists "
            "to fix. Use a value below 5 (default 4).",
            file=sys.stderr,
        )
        return 2

    delay_s = 60.0 / args.rate_per_min
    total = args.riders + args.drivers
    print(
        f"Pre-authenticating {total} bots ({args.riders} riders, {args.drivers} drivers) "
        f"against {args.base_url}, paced at {args.rate_per_min}/min "
        f"({delay_s:.1f}s between logins, ~{total * delay_s / 60:.1f} min total)."
    )

    riders: list[dict] = []
    drivers: list[dict] = []
    session = requests.Session()

    plan = [("rider", i) for i in range(args.riders)] + [("driver", i) for i in range(args.drivers)]
    for idx, (kind, _) in enumerate(plan):
        phone = next_phone(kind)
        try:
            record = _login_one(args.base_url, session, phone)
        except requests.HTTPError as exc:
            print(f"  [{idx + 1}/{total}] FAILED login for {kind} {phone}: {exc}", file=sys.stderr)
            raise
        (riders if kind == "rider" else drivers).append(record)
        print(f"  [{idx + 1}/{total}] {kind} {phone} -> token cached")
        if idx < total - 1:
            time.sleep(delay_s)

    cache_path = Path(TOKEN_CACHE_PATH)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"riders": riders, "drivers": drivers, "base_url": args.base_url}, indent=2))
    tmp_path.replace(cache_path)
    print(f"Wrote {len(riders)} rider + {len(drivers)} driver tokens to {cache_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
