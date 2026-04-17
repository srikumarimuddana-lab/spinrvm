"""
Focused perf benchmark for POST /api/v1/rides.

Measures two things per request:

  1. Wall-clock latency (P50/P95/P99) through the ASGI app, same
     harness as perf_baseline.py.
  2. DB-call count — every call that goes through
     ``db_supabase.get_rows`` plus the direct-to-supabase helpers
     (``insert_ride``, ``get_ride``, ``update_ride``,
     ``get_user_status``, ``get_user_by_id``, ``claim_driver_atomic``)
     is counted per request.

Because the DB is mocked in-process, timing under-represents real
gains — the ground-truth metric here is **calls per request**. A
drop from N → M translates directly to fewer Supabase round-trips
in production.

Usage:
    cd backend
    python tests/perf_post_rides.py --samples 50 --out perf_rides.json

    # Compare against a saved run (e.g. after optimization):
    python tests/perf_post_rides.py --samples 50 --baseline perf_rides.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from statistics import mean
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path bootstrap (mirrors perf_baseline.py)
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)
sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "perf-test-secret-key-for-ci-only!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")
os.environ.setdefault("ADMIN_EMAIL", "admin@spinr.ca")
os.environ.setdefault("ENV", "test")

logging.disable(logging.WARNING)
try:
    from loguru import logger as _loguru_logger

    _loguru_logger.remove()
    _loguru_logger.add(sys.stderr, level="CRITICAL")
except Exception:
    pass

import httpx  # noqa: E402

# ---------------------------------------------------------------------------
# Mock payloads for the POST /rides path
# ---------------------------------------------------------------------------
MOCK_USER = {"id": "perf_user_1", "phone": "+13065550001", "role": "rider", "is_driver": False}

VEHICLE_TYPE_ID = "vt_economy_perf"
SERVICE_AREA_ID = "sa_perf"

# Polygon covering (52.0..52.5, -107.0..-106.5). Pickup/dropoff both land inside.
POLYGON = [
    {"lat": 52.0, "lng": -107.0},
    {"lat": 52.5, "lng": -107.0},
    {"lat": 52.5, "lng": -106.5},
    {"lat": 52.0, "lng": -106.5},
]

_MOCK_TABLES: Dict[str, List[Dict[str, Any]]] = {
    "users": [
        {
            "id": MOCK_USER["id"],
            "status": "active",
            "stripe_customer_id": "cus_perf",
            "first_name": "Perf",
            "last_name": "User",
        }
    ],
    "vehicle_types": [
        {
            "id": VEHICLE_TYPE_ID,
            "name": "Economy",
            "capacity": 4,
            "is_active": True,
        }
    ],
    "service_areas": [
        {
            "id": SERVICE_AREA_ID,
            "name": "Perf Area",
            "is_active": True,
            "is_airport": False,
            "polygon": POLYGON,
            "surge_multiplier": 1.0,
            "hst_enabled": False,
        }
    ],
    "fare_configs": [
        {
            "id": "fc_perf",
            "vehicle_type_id": VEHICLE_TYPE_ID,
            "service_area_id": SERVICE_AREA_ID,
            "base_fare": 3.5,
            "per_km_rate": 1.5,
            "per_minute_rate": 0.25,
            "minimum_fare": 8.0,
            "booking_fee": 2.0,
            "is_active": True,
        }
    ],
    "area_fees": [],
    "drivers": [],  # 0 drivers → match_driver_to_ride exits early after candidate query
    "settings": [
        {
            "id": "app_settings",
            "driver_matching_algorithm": "nearest",
            "min_driver_rating": 0,
            "search_radius_km": 10.0,
        }
    ],
    "rides": [],
}

RIDE_PAYLOAD = {
    "vehicle_type_id": VEHICLE_TYPE_ID,
    "pickup_address": "1 Pickup St",
    "pickup_lat": 52.13,
    "pickup_lng": -106.67,
    "dropoff_address": "2 Dropoff Ave",
    "dropoff_lat": 52.14,
    "dropoff_lng": -106.66,
    "stops": [],
    "is_scheduled": False,
    "payment_method": "cash",  # skip stripe-customer lookup
}


# ---------------------------------------------------------------------------
# Per-request DB-call counter
# ---------------------------------------------------------------------------
class CallCounter:
    """Tracks DB calls during a single request."""

    def __init__(self) -> None:
        self.current: Optional[Dict[str, int]] = None
        self.samples: List[Dict[str, int]] = []

    def start(self) -> None:
        self.current = {"get_rows": 0, "direct": 0, "total": 0, "by_table": {}}

    def record_get_rows(self, table: str) -> None:
        if self.current is None:
            return
        self.current["get_rows"] += 1
        self.current["total"] += 1
        self.current["by_table"][table] = self.current["by_table"].get(table, 0) + 1

    def record_direct(self, name: str) -> None:
        if self.current is None:
            return
        self.current["direct"] += 1
        self.current["total"] += 1
        self.current["by_table"][name] = self.current["by_table"].get(name, 0) + 1

    def finish(self) -> None:
        if self.current is not None:
            self.samples.append(self.current)
            self.current = None


def build_mocks(counter: CallCounter):
    """Create per-function mocks wired to the shared counter."""

    async def mock_get_rows(
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        order: Optional[str] = None,
        desc: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ):
        counter.record_get_rows(table)
        rows = _MOCK_TABLES.get(table, [])
        if not filters:
            return list(rows)
        out = []
        for row in rows:
            match = True
            for k, v in filters.items():
                if isinstance(v, dict):
                    # skip mongo-style operators ($ne, $in) — treat as match
                    continue
                if row.get(k) != v:
                    match = False
                    break
            if match:
                out.append(row)
        return out

    async def mock_insert_ride(payload: Dict[str, Any]):
        counter.record_direct("insert_ride")
        return {**payload, "id": payload.get("id") or "ride_perf_inserted"}

    async def mock_get_ride(ride_id: str):
        counter.record_direct("get_ride")
        return {
            "id": ride_id,
            "rider_id": MOCK_USER["id"],
            "vehicle_type_id": VEHICLE_TYPE_ID,
            "pickup_lat": RIDE_PAYLOAD["pickup_lat"],
            "pickup_lng": RIDE_PAYLOAD["pickup_lng"],
            "dropoff_lat": RIDE_PAYLOAD["dropoff_lat"],
            "dropoff_lng": RIDE_PAYLOAD["dropoff_lng"],
            "status": "searching",
            "service_area_id": SERVICE_AREA_ID,
        }

    async def mock_update_ride(ride_id: str, updates: Dict[str, Any]):
        counter.record_direct("update_ride")
        return None

    async def mock_get_user_status(user_id: str) -> Optional[str]:
        counter.record_direct("get_user_status")
        return "active"

    async def mock_get_user_by_id(user_id: str):
        counter.record_direct("get_user_by_id")
        return _MOCK_TABLES["users"][0]

    async def mock_claim_driver_atomic(driver_id: str):
        counter.record_direct("claim_driver_atomic")
        return MagicMock(modified_count=0)

    return {
        "get_rows": mock_get_rows,
        "insert_ride": mock_insert_ride,
        "get_ride": mock_get_ride,
        "update_ride": mock_update_ride,
        "get_user_status": mock_get_user_status,
        "get_user_by_id": mock_get_user_by_id,
        "claim_driver_atomic": mock_claim_driver_atomic,
    }


# ---------------------------------------------------------------------------
# Percentile helper (duplicated to keep this script standalone)
# ---------------------------------------------------------------------------
def percentile(data: List[float], pct: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
    frac = k - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
async def bench_create_ride(samples: int) -> Dict[str, Any]:
    from backend.server import app  # noqa: E402

    import db_supabase  # noqa: E402
    import dependencies  # noqa: E402
    from utils.rate_limiter import default_limiter  # noqa: E402

    counter = CallCounter()
    mocks = build_mocks(counter)

    # Disable rate limiter (10/min would cap the run)
    default_limiter.enabled = False

    latencies_ms: List[float] = []
    errors = 0
    statuses: List[int] = []

    with (
        patch.object(db_supabase, "get_rows", mocks["get_rows"]),
        patch.object(db_supabase, "insert_ride", mocks["insert_ride"]),
        patch.object(db_supabase, "get_ride", mocks["get_ride"]),
        patch.object(db_supabase, "update_ride", mocks["update_ride"]),
        patch.object(db_supabase, "get_user_status", mocks["get_user_status"]),
        patch.object(db_supabase, "get_user_by_id", mocks["get_user_by_id"]),
        patch.object(db_supabase, "claim_driver_atomic", mocks["claim_driver_atomic"]),
    ):
        original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[dependencies.get_current_user] = lambda: MOCK_USER

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Warmup (module import + first-call JIT paths)
            try:
                counter.start()
                await client.post("/api/v1/rides", json=RIDE_PAYLOAD)
                counter.finish()
            except Exception:
                pass

            for _ in range(samples):
                counter.start()
                t0 = time.perf_counter()
                try:
                    resp = await client.post("/api/v1/rides", json=RIDE_PAYLOAD)
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    counter.finish()
                    latencies_ms.append(elapsed_ms)
                    statuses.append(resp.status_code)
                except Exception:
                    counter.finish()
                    errors += 1

        app.dependency_overrides = original_overrides
        default_limiter.enabled = True

    # Drop the warmup sample so per-request DB counts reflect steady state
    steady_samples = counter.samples[1:] if len(counter.samples) > 1 else counter.samples

    if not latencies_ms:
        return {
            "label": "POST /api/v1/rides",
            "samples": 0,
            "errors": errors,
            "note": "all attempts failed",
        }

    # DB-call counts should be identical per request in this mocked harness;
    # we still report min/max to surface any drift.
    total_counts = [s["total"] for s in steady_samples]
    get_rows_counts = [s["get_rows"] for s in steady_samples]
    direct_counts = [s["direct"] for s in steady_samples]
    by_table = steady_samples[-1].get("by_table", {}) if steady_samples else {}

    return {
        "label": "POST /api/v1/rides",
        "samples": len(latencies_ms),
        "errors": errors,
        "status_codes": sorted(set(statuses)),
        "p50_ms": round(percentile(latencies_ms, 50), 2),
        "p95_ms": round(percentile(latencies_ms, 95), 2),
        "p99_ms": round(percentile(latencies_ms, 99), 2),
        "mean_ms": round(mean(latencies_ms), 2),
        "min_ms": round(min(latencies_ms), 2),
        "max_ms": round(max(latencies_ms), 2),
        "db_calls_per_request": {
            "total_min": min(total_counts) if total_counts else 0,
            "total_max": max(total_counts) if total_counts else 0,
            "get_rows_min": min(get_rows_counts) if get_rows_counts else 0,
            "get_rows_max": max(get_rows_counts) if get_rows_counts else 0,
            "direct_min": min(direct_counts) if direct_counts else 0,
            "direct_max": max(direct_counts) if direct_counts else 0,
            "by_table_last_sample": by_table,
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(current: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> None:
    divider = "-" * 72
    print("\n" + divider)
    print(" POST /api/v1/rides -- perf snapshot -- " + datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    print(divider)
    if current.get("samples", 0) == 0:
        print("  ERR: no successful samples")
        print(divider + "\n")
        return
    print(f"  samples:    {current['samples']}  (errors: {current.get('errors', 0)})")
    print(f"  statuses:   {current.get('status_codes')}")
    print(f"  P50:        {current['p50_ms']} ms")
    print(f"  P95:        {current['p95_ms']} ms")
    print(f"  P99:        {current['p99_ms']} ms")
    print(f"  mean:       {current['mean_ms']} ms")
    print(f"  min/max:    {current['min_ms']} / {current['max_ms']} ms")
    counts = current["db_calls_per_request"]
    total = counts["total_max"]
    print(f"  DB calls/req (total):    {counts['total_min']}-{counts['total_max']}  (get_rows: {counts['get_rows_max']}, direct: {counts['direct_max']})")
    print("  calls by table (last sample):")
    for tbl, n in sorted(counts["by_table_last_sample"].items(), key=lambda kv: -kv[1]):
        print(f"    {tbl:<24} {n}")
    if baseline:
        b_total = baseline["db_calls_per_request"]["total_max"]
        delta = total - b_total
        pct = (delta / b_total * 100) if b_total else 0
        print(f"\n  vs baseline -- DB calls: {b_total} -> {total}  ({delta:+d}, {pct:+.1f}%)")
        if baseline.get("p95_ms"):
            p95_delta = current["p95_ms"] - baseline["p95_ms"]
            p95_pct = (p95_delta / baseline["p95_ms"] * 100) if baseline["p95_ms"] else 0
            print(f"  vs baseline -- P95:      {baseline['p95_ms']} -> {current['p95_ms']} ms  ({p95_delta:+.2f} ms, {p95_pct:+.1f}%)")
    print(divider + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main(samples: int, out_path: str, baseline_path: Optional[str]) -> None:
    print(f"Running POST /rides benchmark: {samples} samples …")
    result = await bench_create_ride(samples)

    baseline = None
    if baseline_path and os.path.exists(baseline_path):
        try:
            with open(baseline_path) as f:
                baseline = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  (could not read baseline {baseline_path}: {e})")

    print_report(result, baseline)

    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Perf snapshot for POST /api/v1/rides")
    parser.add_argument("--samples", type=int, default=50, help="Request samples (default: 50)")
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "perf_rides.json"),
        help="Output JSON path",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Path to baseline JSON for side-by-side comparison (does not exit non-zero)",
    )
    args = parser.parse_args()

    asyncio.run(main(args.samples, args.out, args.baseline))
