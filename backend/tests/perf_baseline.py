"""
SPR-02/2d — Performance Baseline Script
========================================

Measures P50 / P95 / P99 response times for key API endpoints and
WebSocket connection + auth latency. Runs the ASGI app in-process via
httpx.ASGITransport — no real network or Supabase connection required.

Usage (standalone):
    cd backend
    python tests/perf_baseline.py

Usage (CI / from repo root):
    cd backend && python tests/perf_baseline.py --samples 50 --out perf_report.json

Output:
    - Human-readable table printed to stdout
    - JSON report written to --out (default: perf_report.json in backend/)

Pass --baseline perf_report.json to compare against a saved baseline and
exit 1 if any endpoint regresses beyond the configured threshold.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from statistics import mean
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path bootstrap — mirrors what the test suite uses
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
# backend/ on sys.path so `import routes.X` works (matches server.py behaviour)
sys.path.insert(0, _BACKEND_DIR)
# repo root on sys.path so `from backend.server import app` works
sys.path.insert(0, _REPO_ROOT)

# Set required env vars before any backend module is imported.
# Mirrors the pattern in tests/conftest.py so server.py's Settings() model
# can initialise without real credentials.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "perf-test-secret-key-for-ci-only!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")
os.environ.setdefault("ADMIN_EMAIL", "admin@spinr.ca")
os.environ.setdefault("ENV", "test")

# Suppress loguru JSON output that would pollute the perf table.
# Must run before server.py and its dependencies are imported.
import logging  # noqa: E402  (must precede server imports to suppress log noise)

logging.disable(logging.WARNING)
try:
    from loguru import logger as _loguru_logger

    _loguru_logger.disable("routes")
    _loguru_logger.disable("core")
    _loguru_logger.disable("utils")
    _loguru_logger.disable("socket_manager")
    _loguru_logger.remove()  # Remove default sink (stderr / stdout)
    _loguru_logger.add(sys.stderr, level="CRITICAL")  # Only show crashes
except Exception:
    pass

import httpx  # noqa: E402  (must follow logging-suppression block above)

# ---------------------------------------------------------------------------
# Shared mock fixtures
# ---------------------------------------------------------------------------
MOCK_USER = {"id": "perf_user_1", "phone": "+13065550001", "role": "rider", "is_driver": False}
MOCK_WALLET = {"id": "wallet_perf_1", "user_id": "perf_user_1", "balance": 500.0, "currency": "CAD", "is_active": True}
MOCK_RIDE = {
    "id": "ride_perf_1",
    "rider_id": "perf_user_1",
    "status": "completed",
    "total_fare": 20.0,
    "grand_total": 20.0,
    "pickup": {"address": "123 Main St", "lat": 52.13, "lng": -106.67},
    "dropoff": {"address": "456 Broadway Ave", "lat": 52.12, "lng": -106.65},
    "created_at": "2026-01-01T00:00:00",
}
MOCK_ESTIMATES = [
    {"id": "economy", "type": "economy", "name": "Economy", "price": 18.5, "eta_minutes": 4},
    {"id": "comfort", "type": "comfort", "name": "Comfort", "price": 24.0, "eta_minutes": 5},
]
MOCK_LOYALTY = {
    "id": "acct_perf_1",
    "user_id": "perf_user_1",
    "points": 100,
    "lifetime_points": 500,
    "tier": "bronze",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}


def make_mock_db():
    """Return a fully-mocked DB object matching the backend DB class interface."""
    mock = MagicMock()
    mock.get_rows = AsyncMock(return_value=[])
    for col in (
        "rides",
        "drivers",
        "users",
        "wallets",
        "wallet_transactions",
        "loyalty_accounts",
        "loyalty_transactions",
        "fare_splits",
        "fare_split_participants",
        "quests",
        "quest_progress",
        "scheduled_rides",
        "promotions",
        "saved_addresses",
    ):
        col_mock = MagicMock()
        col_mock.find_one = AsyncMock(return_value=None)
        col_mock.insert_one = AsyncMock(return_value=None)
        col_mock.update_one = AsyncMock(return_value=None)
        setattr(mock, col, col_mock)

    # Wire up canned responses for performance endpoints
    mock.wallets.find_one = AsyncMock(return_value=MOCK_WALLET)
    mock.loyalty_accounts.find_one = AsyncMock(return_value=MOCK_LOYALTY)
    mock.rides.find_one = AsyncMock(return_value=MOCK_RIDE)
    return mock


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------
def percentile(data: list[float], pct: float) -> float:
    """Return the <pct>th percentile of <data>."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
    frac = k - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


# ---------------------------------------------------------------------------
# HTTP benchmarks
# ---------------------------------------------------------------------------
ENDPOINTS = [
    # (label, method, path, json_body)
    ("GET /api/v1/wallet", "GET", "/api/v1/wallet", None),
    ("GET /api/v1/loyalty", "GET", "/api/v1/loyalty", None),
    ("GET /api/v1/wallet/transactions", "GET", "/api/v1/wallet/transactions", None),
    ("GET /api/v1/fare-split/ride/ride_perf_1", "GET", "/api/v1/fare-split/ride/ride_perf_1", None),
    ("GET /api/v1/quests/my-quests", "GET", "/api/v1/quests/my-quests", None),
    ("POST /api/v1/wallet/top-up", "POST", "/api/v1/wallet/top-up", {"amount": 10.0}),
    ("POST /api/v1/loyalty/redeem", "POST", "/api/v1/loyalty/redeem", {"points": 100}),
]


async def bench_http(app, samples: int) -> list[dict]:
    """
    Drive each endpoint through <samples> requests in sequence and capture
    wall-clock time for each request. Returns list of result dicts.
    """
    import dependencies

    mock_db = make_mock_db()
    results = []

    with (
        patch("routes.wallet.db", mock_db),
        patch("routes.loyalty.db", mock_db),
        patch("routes.fare_split.db", mock_db),
        patch("routes.quests.db", mock_db),
    ):
        # Temporarily override auth for the duration of this benchmark
        original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[dependencies.get_current_user] = lambda: MOCK_USER

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for label, method, path, body in ENDPOINTS:
                samples_ms: list[float] = []
                errors = 0
                for _ in range(samples):
                    t0 = time.perf_counter()
                    try:
                        if method == "GET":
                            await client.get(path)
                        else:
                            await client.post(path, json=body)
                        # 400-level responses are expected in some cases (e.g. redeem
                        # with insufficient points) — still a valid latency sample.
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                        samples_ms.append(elapsed_ms)
                    except Exception:
                        errors += 1

                if samples_ms:
                    results.append(
                        {
                            "label": label,
                            "samples": len(samples_ms),
                            "errors": errors,
                            "p50_ms": round(percentile(samples_ms, 50), 2),
                            "p95_ms": round(percentile(samples_ms, 95), 2),
                            "p99_ms": round(percentile(samples_ms, 99), 2),
                            "mean_ms": round(mean(samples_ms), 2),
                            "min_ms": round(min(samples_ms), 2),
                            "max_ms": round(max(samples_ms), 2),
                        }
                    )
                else:
                    results.append({"label": label, "samples": 0, "errors": errors})

        app.dependency_overrides = original_overrides

    return results


# ---------------------------------------------------------------------------
# WebSocket benchmarks
# ---------------------------------------------------------------------------
async def bench_ws(app, samples: int) -> dict:
    """
    Measure WebSocket connection + auth handshake latency using Starlette's
    built-in test WebSocket client (synchronous under the hood, wraps ASGI).
    Returns stats dict with p50/p95/p99 in ms.

    The WS auth path needs: db.users.find_one returns MOCK_USER. Firebase
    verify_id_token is bypassed via fallback to legacy JWT. We provide a
    plaintext 'token' that triggers the fallback path, which calls
    verify_jwt_token. That call is patched to succeed.
    """
    from fastapi.testclient import TestClient

    mock_db = make_mock_db()
    mock_db.users.find_one = AsyncMock(return_value=MOCK_USER)

    connect_ms: list[float] = []

    def mock_verify_jwt(token):
        """Accept any token in perf tests."""
        return {"user_id": MOCK_USER["id"]}

    with (
        patch("routes.websocket.db", mock_db),
        patch("routes.websocket.verify_jwt_token", mock_verify_jwt),
        patch("routes.websocket.firebase_auth.verify_id_token", side_effect=Exception("firebase_mock")),
    ):
        with TestClient(app) as tc:
            for _ in range(samples):
                t0 = time.perf_counter()
                try:
                    with tc.websocket_connect("/ws/rider/perf_user_1") as ws:
                        ws.send_json({"type": "auth", "token": "perf-test-token"})
                        # The server registers the connection; no explicit ack is sent.
                        # We measure up to and including the first message received.
                        # If no message arrives within 200 ms, we consider auth done
                        # (no error = success path).
                        try:
                            ws.receive_json(timeout=0.2)
                        except Exception:
                            # Timeout → server didn't send an error → auth succeeded
                            pass
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                        connect_ms.append(elapsed_ms)
                except Exception:
                    pass  # Connection errors counted via len mismatch

    if not connect_ms:
        return {"samples": 0, "errors": samples, "note": "all attempts failed"}

    return {
        "label": "WS /ws/rider/{id} connect+auth",
        "samples": len(connect_ms),
        "errors": samples - len(connect_ms),
        "p50_ms": round(percentile(connect_ms, 50), 2),
        "p95_ms": round(percentile(connect_ms, 95), 2),
        "p99_ms": round(percentile(connect_ms, 99), 2),
        "mean_ms": round(mean(connect_ms), 2),
        "min_ms": round(min(connect_ms), 2),
        "max_ms": round(max(connect_ms), 2),
    }


# ---------------------------------------------------------------------------
# Regression check
# ---------------------------------------------------------------------------
P95_REGRESSION_THRESHOLD_PCT = 30  # Fail if P95 regresses by more than 30%


def check_regression(current: list[dict], baseline_path: str) -> list[str]:
    """Compare current results against a saved baseline. Return list of failures."""
    try:
        with open(baseline_path) as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []  # No baseline to compare against

    baseline_by_label = {r["label"]: r for r in saved.get("http", [])}
    failures = []
    for result in current:
        bl = baseline_by_label.get(result["label"])
        if not bl or bl.get("p95_ms", 0) == 0:
            continue
        current_p95 = result.get("p95_ms", 0)
        baseline_p95 = bl["p95_ms"]
        pct_change = ((current_p95 - baseline_p95) / baseline_p95) * 100
        if pct_change > P95_REGRESSION_THRESHOLD_PCT:
            failures.append(
                f"{result['label']}: P95 regressed {pct_change:.1f}% ({baseline_p95:.1f}ms → {current_p95:.1f}ms)"
            )
    return failures


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
COL_W = 42


def print_table(http_results: list[dict], ws_result: dict):
    divider = "-" * (COL_W + 52)
    header = f"{'Endpoint':<{COL_W}} {'P50':>7} {'P95':>7} {'P99':>7} {'Mean':>7} {'Max':>7} {'Err':>4}"
    print("\n" + divider)
    print(" Spinr API Performance Baseline — " + datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    print(divider)
    print(header)
    print(divider)
    for r in http_results:
        if r.get("samples", 0) == 0:
            print(f"  {r['label']:<{COL_W - 2}} {'ERR':>7}")
            continue
        print(
            f"  {r['label']:<{COL_W - 2}}"
            f" {r['p50_ms']:>6.1f}ms"
            f" {r['p95_ms']:>6.1f}ms"
            f" {r['p99_ms']:>6.1f}ms"
            f" {r['mean_ms']:>6.1f}ms"
            f" {r['max_ms']:>6.1f}ms"
            f" {r.get('errors', 0):>4}"
        )
    print(divider)
    if ws_result.get("samples", 0) > 0:
        print(
            f"  {'WS connect+auth':<{COL_W - 2}}"
            f" {ws_result['p50_ms']:>6.1f}ms"
            f" {ws_result['p95_ms']:>6.1f}ms"
            f" {ws_result['p99_ms']:>6.1f}ms"
            f" {ws_result['mean_ms']:>6.1f}ms"
            f" {ws_result['max_ms']:>6.1f}ms"
            f" {ws_result.get('errors', 0):>4}"
        )
    else:
        print(f"  {'WS connect+auth':<{COL_W - 2}}  [skipped or all failed]")
    print(divider + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main(http_samples: int, ws_samples: int, out_path: str, baseline_path: Optional[str]):
    from backend.server import app  # triggers server.py sys.path bootstrap

    print(f"Running HTTP benchmark: {http_samples} samples × {len(ENDPOINTS)} endpoints …")
    http_results = await bench_http(app, http_samples)

    print(f"Running WS benchmark:   {ws_samples} samples …")
    ws_result = await bench_ws(app, ws_samples)

    print_table(http_results, ws_result)

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "http_samples_per_endpoint": http_samples,
        "ws_samples": ws_samples,
        "http": http_results,
        "ws": ws_result,
    }

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report written to: {out_path}")

    if baseline_path:
        failures = check_regression(http_results, baseline_path)
        if failures:
            print("\n⚠  REGRESSION DETECTED (P95 threshold: +30%):")
            for f_msg in failures:
                print(f"   • {f_msg}")
            sys.exit(1)
        else:
            print("✓  No regressions vs baseline.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spinr API performance baseline")
    parser.add_argument("--samples", type=int, default=50, help="HTTP requests per endpoint (default: 50)")
    parser.add_argument("--ws-samples", type=int, default=20, help="WS connection samples (default: 20)")
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "perf_report.json"),
        help="Output JSON path",
    )
    parser.add_argument("--baseline", default=None, help="Path to baseline JSON for regression check")
    args = parser.parse_args()

    asyncio.run(main(args.samples, args.ws_samples, args.out, args.baseline))
