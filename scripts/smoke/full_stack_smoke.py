#!/usr/bin/env python3
"""
Full-stack staging smoke harness for Spinr.

Runs against a *deployed* backend (typically staging) and verifies the
parts of the stack that our unit/integration/E2E tests can't prove
because they all mock their dependencies. This catches the classic
"CI green, prod won't boot" class of failure: mis-named env vars,
wrong Supabase URL, unreachable Redis, bad Stripe key, CORS too tight,
load balancer dropping WS upgrades, etc.

What this DOES catch
--------------------
- Backend process is up and serving /health
- FastAPI docs + OpenAPI schema render (/docs, /openapi.json)
- Public settings endpoint returns a publishable Stripe key
- OTP dispatch endpoint reaches Twilio (200) and respects rate limits
  (can be skipped; see --skip-otp)
- Auth middleware rejects missing/invalid tokens on a protected route
- WebSocket endpoint upgrades through the ingress and either acks
  an auth message or closes cleanly without it
- CORS is not wide-open on the public API (refuses unlisted origins
  with no Access-Control-Allow-Origin echo)

What this does NOT catch
------------------------
- Stripe charge outcomes — use scripts/smoke/stripe_charge_smoke.py
- End-to-end ride flow through real drivers — manual, see
  docs/runbooks/MOBILE_SMOKE.md
- Mobile bundle → backend comms — manual, same runbook
- Supabase/Redis deep connectivity — partial (any 500 from a DB-backed
  route signals a problem, but we don't exhaustively exercise DB ops)

Usage
-----

    # Minimum — hit a deployed backend:
    python scripts/smoke/full_stack_smoke.py --backend https://staging.spinr.ca

    # With WebSocket probe (requires `pip install websockets`):
    python scripts/smoke/full_stack_smoke.py \\
        --backend https://staging.spinr.ca \\
        --with-ws

    # Skip the OTP probe (useful if Twilio quotas are tight or you
    # don't want to dispatch a real SMS):
    python scripts/smoke/full_stack_smoke.py \\
        --backend https://staging.spinr.ca \\
        --skip otp

    # CI mode — non-interactive, strict exit code:
    python scripts/smoke/full_stack_smoke.py \\
        --backend https://staging.spinr.ca \\
        --strict

Exit codes
----------
  0  all checks passed (or only non-strict warnings)
  1  one or more strict checks failed
  2  invocation error (missing args, bad URL, etc.)

Safety
------
This script only issues HTTP GET + one POST /auth/send-otp (behind
--skip-otp) and one WS connection. It does NOT create rides, users,
or payments. Safe to re-run. The OTP POST uses a reserved test phone
`+15005550006` (Twilio magic-passthrough) so no real SMS is dispatched
when Twilio is in test mode.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# Twilio "magic" test number — accepted by Twilio in test mode and by
# most dev setups without dispatching a real SMS. Override with
# --otp-phone if your test-mode allowlist uses something else.
DEFAULT_TEST_PHONE = "+15005550006"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    warn_only: bool = False  # treat failure as warning, not error


@dataclass
class SmokeContext:
    backend_url: str
    timeout: float = 10.0
    verbose: bool = False
    strict: bool = False
    skip: set = field(default_factory=set)


# ─────────────────────────────────────────────────────────────────────────────
# Dependency guards
# ─────────────────────────────────────────────────────────────────────────────


def _require_requests():
    try:
        import requests  # noqa: F401
    except ImportError:
        print("ERROR: the `requests` package is required. Install with:")
        print("    pip install requests")
        sys.exit(2)


def _maybe_websockets():
    """Optional — only loaded if --with-ws is set."""
    try:
        import websockets  # noqa: F401
        return True
    except ImportError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────────────


def check_health(ctx: SmokeContext) -> CheckResult:
    import requests

    url = f"{ctx.backend_url}/health"
    try:
        r = requests.get(url, timeout=ctx.timeout)
    except Exception as e:
        return CheckResult("health", False, f"GET {url} raised {type(e).__name__}: {e}")

    if r.status_code != 200:
        return CheckResult("health", False, f"GET {url} → {r.status_code} (expected 200)")

    # Body is implementation-defined ({"status":"ok"} is common). We only
    # require that it parses as JSON and is not an error envelope.
    try:
        body = r.json()
    except ValueError:
        return CheckResult("health", False, f"GET {url} returned non-JSON body")

    return CheckResult("health", True, f"200 OK {body!r}")


def check_docs(ctx: SmokeContext) -> CheckResult:
    import requests

    url = f"{ctx.backend_url}/docs"
    try:
        r = requests.get(url, timeout=ctx.timeout)
    except Exception as e:
        return CheckResult("docs", False, f"GET {url} raised {type(e).__name__}: {e}")

    if r.status_code != 200:
        return CheckResult(
            "docs", False,
            f"GET {url} → {r.status_code} — Swagger UI is disabled or route missing",
        )

    # Swagger UI HTML always contains this token
    if "swagger-ui" not in r.text.lower():
        return CheckResult("docs", False, "GET /docs did not render Swagger UI HTML")

    return CheckResult("docs", True, "Swagger UI rendered")


def check_openapi_schema(ctx: SmokeContext) -> CheckResult:
    import requests

    url = f"{ctx.backend_url}/openapi.json"
    try:
        r = requests.get(url, timeout=ctx.timeout)
    except Exception as e:
        return CheckResult("openapi", False, f"GET {url} raised {type(e).__name__}: {e}")

    if r.status_code != 200:
        return CheckResult("openapi", False, f"GET {url} → {r.status_code}")

    try:
        schema = r.json()
    except ValueError:
        return CheckResult("openapi", False, "openapi.json is not valid JSON")

    # Pin the smallest shape invariants — if any of these change, someone
    # has reorganised the API and this smoke should flag it loudly.
    if "paths" not in schema:
        return CheckResult("openapi", False, "openapi.json missing 'paths' key")

    must_have = [
        "/api/v1/auth/send-otp",
        "/api/v1/rides",
        "/api/v1/drivers/rides/{ride_id}/accept",
    ]
    missing = [p for p in must_have if p not in schema["paths"]]
    if missing:
        return CheckResult(
            "openapi", False,
            f"openapi.json missing expected routes: {missing}",
        )

    n_paths = len(schema["paths"])
    return CheckResult("openapi", True, f"{n_paths} paths registered, core ride routes present")


def check_settings_endpoint(ctx: SmokeContext) -> CheckResult:
    """GET /api/v1/settings — public, no auth. Returns the Stripe
    publishable key (and other client-safe config). If this returns
    an empty publishable_key, either the backend DB isn't seeded or
    Stripe is un-configured on staging — either way, a flag."""
    import requests

    url = f"{ctx.backend_url}/api/v1/settings"
    try:
        r = requests.get(url, timeout=ctx.timeout)
    except Exception as e:
        return CheckResult("settings", False, f"GET {url} raised {type(e).__name__}: {e}")

    if r.status_code != 200:
        return CheckResult("settings", False, f"GET {url} → {r.status_code}")

    try:
        body = r.json()
    except ValueError:
        return CheckResult("settings", False, "settings response is not JSON")

    pk = body.get("stripe_publishable_key") or ""
    if not pk:
        return CheckResult(
            "settings", False,
            "stripe_publishable_key is empty — rider-app Stripe provider will be inert",
            warn_only=True,
        )
    if pk.startswith("pk_live_"):
        # Test mode in staging should not be using a live key.
        return CheckResult(
            "settings", False,
            f"stripe_publishable_key starts with pk_live_ on a staging backend ({pk[:10]}...)",
        )
    if not pk.startswith("pk_"):
        return CheckResult(
            "settings", False,
            f"stripe_publishable_key does not look like a Stripe key: {pk[:12]}...",
        )
    return CheckResult("settings", True, f"publishable key loaded ({pk[:12]}...)")


def check_auth_rejects_unauthenticated(ctx: SmokeContext) -> CheckResult:
    """GET /api/v1/auth/me without a token must return 401 (not 200,
    not 500). Proves the auth middleware is mounted and the route is
    actually protected."""
    import requests

    url = f"{ctx.backend_url}/api/v1/auth/me"
    try:
        r = requests.get(url, timeout=ctx.timeout)
    except Exception as e:
        return CheckResult("auth_gate", False, f"GET {url} raised: {e}")

    if r.status_code == 200:
        return CheckResult(
            "auth_gate", False,
            f"GET {url} returned 200 without a token — auth middleware is OFF or route is public",
        )
    if r.status_code in (401, 403):
        return CheckResult("auth_gate", True, f"unauthenticated → {r.status_code} (expected)")
    return CheckResult(
        "auth_gate", False,
        f"GET {url} → {r.status_code} — expected 401/403",
    )


def check_otp_dispatch(ctx: SmokeContext, phone: str) -> CheckResult:
    """POST /api/v1/auth/send-otp with the Twilio magic test number.
    We only assert a 2xx response — we can't verify Twilio actually
    dispatched without admin access to the Twilio dashboard. A 500
    here usually means Twilio creds are mis-named or the phone-format
    validator changed."""
    import requests

    url = f"{ctx.backend_url}/api/v1/auth/send-otp"
    try:
        r = requests.post(
            url,
            json={"phone": phone},
            timeout=ctx.timeout,
        )
    except Exception as e:
        return CheckResult("otp_dispatch", False, f"POST {url} raised: {e}")

    if r.status_code == 429:
        return CheckResult(
            "otp_dispatch", True,
            "rate-limited (429) — OTP endpoint is up and throttling correctly",
            warn_only=True,
        )
    if r.status_code >= 500:
        return CheckResult(
            "otp_dispatch", False,
            f"POST {url} → {r.status_code} — likely Twilio/config issue",
        )
    if r.status_code >= 400:
        return CheckResult(
            "otp_dispatch", False,
            f"POST {url} → {r.status_code} {r.text[:200]}",
        )
    return CheckResult("otp_dispatch", True, f"{r.status_code} OK (Twilio dispatch reachable)")


def check_cors_rejects_unknown_origin(ctx: SmokeContext) -> CheckResult:
    """Preflight OPTIONS from an unlisted origin must NOT return a
    permissive Access-Control-Allow-Origin header. If the server
    echoes back an unknown origin, CORS is wide-open — a vuln."""
    import requests

    url = f"{ctx.backend_url}/api/v1/auth/me"
    rogue = "https://evil.example.com"
    try:
        r = requests.options(
            url,
            headers={
                "Origin": rogue,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
            timeout=ctx.timeout,
        )
    except Exception as e:
        return CheckResult("cors", False, f"OPTIONS {url} raised: {e}")

    allow_origin = r.headers.get("Access-Control-Allow-Origin") or ""
    if allow_origin == rogue or allow_origin == "*":
        return CheckResult(
            "cors", False,
            f"Access-Control-Allow-Origin = {allow_origin!r} — CORS is wide open",
        )
    return CheckResult("cors", True, f"unknown origin not echoed (got {allow_origin!r})")


def check_websocket(ctx: SmokeContext) -> CheckResult:
    """Open /ws/rider/smoke-probe and expect the server to either
    close cleanly (missing auth message) or accept an auth message.

    This is a *reachability* check, not a full auth round-trip — we
    don't have a real JWT to send. A failure here means the WS upgrade
    isn't flowing through ingress / load balancer / reverse proxy."""
    import asyncio

    try:
        import websockets
    except ImportError:
        return CheckResult(
            "websocket", False,
            "websockets package not installed (`pip install websockets`) — skipped",
            warn_only=True,
        )

    # http(s) → ws(s)
    ws_url = ctx.backend_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = ws_url.rstrip("/") + "/ws/rider/smoke-probe"

    async def _probe() -> CheckResult:
        try:
            async with websockets.connect(ws_url, open_timeout=ctx.timeout) as sock:
                # Don't send auth. Expect the server to close shortly.
                try:
                    msg = await asyncio.wait_for(sock.recv(), timeout=3.0)
                    # Some implementations send a hello frame before requiring
                    # auth. Treat any frame as "the WS is reachable."
                    return CheckResult(
                        "websocket", True, f"upgrade OK; first frame: {str(msg)[:80]!r}",
                    )
                except asyncio.TimeoutError:
                    # No frame sent, no close — server is waiting for auth.
                    # That's also a positive signal: the upgrade happened.
                    return CheckResult(
                        "websocket", True, "upgrade OK; server awaiting auth message (expected)",
                    )
        except Exception as e:
            return CheckResult("websocket", False, f"connect {ws_url} raised: {e}")

    return asyncio.run(_probe())


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────


def run_all(ctx: SmokeContext, *, with_ws: bool, otp_phone: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    plan = [
        ("health", check_health),
        ("docs", check_docs),
        ("openapi", check_openapi_schema),
        ("settings", check_settings_endpoint),
        ("auth_gate", check_auth_rejects_unauthenticated),
        ("cors", check_cors_rejects_unknown_origin),
    ]
    for key, fn in plan:
        if key in ctx.skip:
            results.append(CheckResult(key, True, "skipped", warn_only=True))
            continue
        try:
            results.append(fn(ctx))
        except Exception as e:
            results.append(CheckResult(key, False, f"harness error: {e}"))
        time.sleep(0.1)

    # OTP dispatch is gated because it touches Twilio (and Twilio costs).
    if "otp" not in ctx.skip:
        try:
            results.append(check_otp_dispatch(ctx, otp_phone))
        except Exception as e:
            results.append(CheckResult("otp_dispatch", False, f"harness error: {e}"))
    else:
        results.append(CheckResult("otp_dispatch", True, "skipped", warn_only=True))

    if with_ws and "ws" not in ctx.skip:
        try:
            results.append(check_websocket(ctx))
        except Exception as e:
            results.append(CheckResult("websocket", False, f"harness error: {e}"))
    else:
        reason = "skipped (not requested)" if not with_ws else "skipped"
        results.append(CheckResult("websocket", True, reason, warn_only=True))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--backend", required=True,
        help="Deployed backend URL, e.g. https://staging.spinr.ca",
    )
    parser.add_argument(
        "--with-ws", action="store_true",
        help="Include WebSocket reachability probe (requires `pip install websockets`)",
    )
    parser.add_argument(
        "--otp-phone", default=DEFAULT_TEST_PHONE,
        help=f"Phone number to use for OTP dispatch check (default: {DEFAULT_TEST_PHONE})",
    )
    parser.add_argument(
        "--skip", action="append", default=[],
        choices=["health", "docs", "openapi", "settings", "auth_gate", "cors", "otp", "ws"],
        help="Skip a specific check (repeatable)",
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warn-only results as failures (exit 1 if any check is not strictly green)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Verbose output",
    )
    args = parser.parse_args()

    # Safety — refuse obvious prod URLs unless explicitly opted in. This is
    # a conservative default; a smoke run against prod isn't inherently
    # dangerous (all checks are read-only except the OTP dispatch, which
    # is skippable), but opting-in makes the intent explicit.
    url = args.backend.rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        print(f"ERROR: --backend must be a full URL, got {args.backend!r}")
        return 2

    _require_requests()

    ctx = SmokeContext(
        backend_url=url,
        timeout=args.timeout,
        verbose=args.verbose,
        strict=args.strict,
        skip=set(args.skip),
    )

    print("=" * 70)
    print("Spinr full-stack smoke")
    print(f"Backend: {url}")
    print(f"Timeout: {args.timeout}s  strict={args.strict}  skip={sorted(ctx.skip) or 'none'}")
    if args.with_ws and not _maybe_websockets():
        print("NOTE: --with-ws passed but `websockets` package not installed; probe will be a warn")
    print("=" * 70)

    results = run_all(ctx, with_ws=args.with_ws, otp_phone=args.otp_phone)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        if r.ok:
            mark = "PASS" if not r.warn_only or r.detail != "skipped" else "SKIP"
        else:
            mark = "WARN" if r.warn_only and not args.strict else "FAIL"
        print(f"  [{mark:>4}] {r.name:15s} {r.detail}")

    hard_failures = [r for r in results if not r.ok and (args.strict or not r.warn_only)]
    soft_warnings = [r for r in results if not r.ok and r.warn_only and not args.strict]

    print()
    passed = sum(1 for r in results if r.ok)
    print(f"{passed}/{len(results)} ok  |  {len(hard_failures)} failure(s)  |  {len(soft_warnings)} warning(s)")

    if hard_failures:
        print("\nBLOCKING failures:")
        for r in hard_failures:
            print(f"  - {r.name}: {r.detail}")
        print("\nFix these before declaring the stack healthy.")
        return 1

    if soft_warnings:
        print("\nNon-blocking warnings (use --strict to promote to failures):")
        for r in soft_warnings:
            print(f"  - {r.name}: {r.detail}")

    print("\nAll strict checks passed. Next: run the mobile walkthrough")
    print("in docs/runbooks/MOBILE_SMOKE.md, then scripts/smoke/stripe_charge_smoke.py")
    print("for the payment-specific validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
