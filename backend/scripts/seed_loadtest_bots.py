#!/usr/bin/env python3
"""Seed synthetic rider/driver bot accounts + a Saskatoon service area for
the Locust marketplace load test (loadtest/README.md, ACTION_ITEMS.md C50
Phase 3 T16 — staging validation).

STAGING/DEV ONLY. This talks to whatever SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY are set in the environment and hard-refuses to
run when ENV=production — checked in code below, not just documented,
mirroring the interlock in backend/scripts/seed_corporate_test_data.py
(docs/change-log/2026-07-30-corporate-dev-seed-script.md). This script
does NOT check settings.ENV via backend.core.config on purpose (importing
the full Settings object requires the whole backend's env surface to be
present) — it reads the same os.environ["ENV"] value config.py itself
reads (Settings.ENV, config.py:203), so the guard is equivalent, not weaker.

What it seeds, per loadtest/README.md's documented contract:
  - N rider bot `users` rows, even phone suffixes: +1306555NNN{0,2,4,6,8}
  - M driver bot `users` + `drivers` rows, odd suffixes: ...{1,3,5,7,9}
    each driver: is_verified=true, status='active', a vehicle_type_id,
    and no document-expiry fields set (NULL expiry passes go_online's
    expiry gate — see status.py:369 `if expiry_val:` — unset, not
    backdated, is the correct "no document on file" signal for a bot).
  - A Saskatoon downtown service area (52.1332, -106.6700) + a matching
    vehicle_type + fare_config, ONLY if none already exist — staging's
    vehicle_types/service_areas tables were found completely empty
    (2026-09-02 T16 verification), which is itself a prerequisite gap
    loadtest/README.md's "Prerequisites" section did not anticipate
    (it assumed a service area already existed, only a polygon needed
    adding). Existing rows are left untouched and reused.

Idempotent: re-running skips users/drivers that already exist by phone.

Usage:
    python backend/scripts/seed_loadtest_bots.py --riders 45 --drivers 15
    python backend/scripts/seed_loadtest_bots.py --cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("seed_loadtest_bots")

# Must match loadtest/locustfile.py's PHONE_PREFIX default exactly, or the
# harness's bots and this script's seeded rows point at different numbers.
PHONE_PREFIX = "+1306555"
_ALLOWED_ENVS = frozenset({"development", "dev", "test", "staging", "preview"})
SASKATOON_LAT = 52.1332
SASKATOON_LNG = -106.6700
# A generous square around downtown Saskatoon — must fully contain the
# locustfile's default 0.02-degree jitter radius around the same center
# (52.1332 ± 0.02 lat, -106.6700 ± 0.02 lng = 52.1132-52.1532 lat,
# -106.69..-106.65 lng). The original 52.11-52.15 lat bound clipped the
# jitter's upper edge by 0.0032° and caused intermittent "outside service
# area" 400s during the T16 smoke test — widened with a real margin here,
# not just patched to the exact jitter bound, so a slightly wider
# LOADTEST_CENTER_LAT/LNG override still lands inside it.
SASKATOON_POLYGON = [
    {"lat": 52.17, "lng": -106.72},
    {"lat": 52.17, "lng": -106.62},
    {"lat": 52.09, "lng": -106.62},
    {"lat": 52.09, "lng": -106.72},
]


def load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


load_dotenv()

try:
    from backend import db_supabase
except ImportError:
    import db_supabase  # type: ignore


def _rider_phone(n: int) -> str:
    # even suffixes per loadtest/README.md
    return f"{PHONE_PREFIX}{n * 2:04d}"


def _driver_phone(n: int) -> str:
    # odd suffixes per loadtest/README.md
    return f"{PHONE_PREFIX}{n * 2 + 1:04d}"


async def _ensure_vehicle_type_and_area() -> tuple[str, str]:
    """Return (vehicle_type_id, service_area_id), creating them only if the
    tables are empty. Never touches existing rows — reuses whatever an
    operator already configured."""
    areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=5)
    if areas:
        area = areas[0]
        logger.info("Reusing existing active service_area %r (id=%s)", area.get("name"), area["id"])
    else:
        area_id = str(uuid.uuid4())
        area_row = {
            "id": area_id,
            "name": "Saskatoon Downtown (loadtest seed)",
            "city": "Saskatoon",
            "polygon": SASKATOON_POLYGON,
            "is_active": True,
            "is_airport": False,
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": True,
            "pst_rate": 6.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_supabase.insert_one("service_areas", area_row)
        area = area_row
        logger.info("Created service_areas row %r (id=%s) — staging had none", area_row["name"], area_id)

    vts = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=5)
    if vts:
        vt = vts[0]
        logger.info("Reusing existing active vehicle_type %r (id=%s)", vt.get("name"), vt["id"])
    else:
        vt_id = str(uuid.uuid4())
        vt_row = {
            "id": vt_id,
            "name": "Standard (loadtest seed)",
            "description": "Synthetic vehicle type seeded for load testing",
            "capacity": 4,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_supabase.insert_one("vehicle_types", vt_row)
        vt = vt_row
        logger.info("Created vehicle_types row %r (id=%s) — staging had none", vt_row["name"], vt_id)

    fcs = await db_supabase.get_rows(
        "fare_configs", {"service_area_id": area["id"], "vehicle_type_id": vt["id"]}, limit=1
    )
    if not fcs:
        fc_row = {
            "id": str(uuid.uuid4()),
            "service_area_id": area["id"],
            "vehicle_type_id": vt["id"],
            # Money as decimal strings, never floats (CLAUDE.md money rule);
            # PostgREST/NUMERIC accepts the string form exactly.
            "base_fare": "3.50",
            "per_km_rate": "1.50",
            "per_minute_rate": "0.25",
            "minimum_fare": "8.00",
            "booking_fee": "2.00",
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_supabase.insert_one("fare_configs", fc_row)
        logger.info("Created fare_configs row for the seeded area/vehicle-type pair")

    return vt["id"], area["id"]


async def _find_user_by_phone(phone: str):
    rows = await db_supabase.get_rows("users", {"phone": phone}, limit=1)
    return rows[0] if rows else None


async def _ensure_rider(n: int) -> str:
    phone = _rider_phone(n)
    existing = await _find_user_by_phone(phone)
    if existing:
        return phone
    payload = {
        "id": str(uuid.uuid4()),
        "phone": phone,
        "first_name": "Loadtest",
        "last_name": f"Rider{n}",
        "role": "rider",
        "is_driver": False,
        "profile_complete": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.create_user(payload)
    return phone


async def _ensure_driver(n: int, vehicle_type_id: str, service_area_id: str) -> str:
    phone = _driver_phone(n)
    existing_user = await _find_user_by_phone(phone)
    if existing_user:
        existing_driver = await db_supabase.get_rows("drivers", {"user_id": existing_user["id"]}, limit=1)
        if existing_driver:
            return phone
        user_id = existing_user["id"]
    else:
        user_id = str(uuid.uuid4())
        user_payload = {
            "id": user_id,
            "phone": phone,
            "first_name": "Loadtest",
            "last_name": f"Driver{n}",
            "role": "driver",
            "is_driver": True,
            "profile_complete": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_supabase.create_user(user_payload)

    driver_payload = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": f"Loadtest Driver{n}",
        "phone": phone,
        "vehicle_type_id": vehicle_type_id,
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "vehicle_color": "Silver",
        "license_plate": f"LT{n:04d}",
        "vehicle_year": 2022,
        # status.py's go-online gate (:238-388): status must be 'active' and
        # is_verified True, or go_online rejects it before dispatch ever
        # sees it. No expiry fields are set at all (left NULL) — every
        # expiry check in status.py is `if expiry_val:` gated, so an unset
        # field is treated as "no document on file yet", not "expired",
        # and never blocks go-online. That is the correct, honest way to
        # represent a synthetic bot with no real documents, rather than
        # backdating a fake far-future expiry.
        "status": "active",
        "is_verified": True,
        "is_online": False,
        "is_available": False,
        "rating": 5.0,
        "total_rides": 0,
        "lat": SASKATOON_LAT,
        "lng": SASKATOON_LNG,
        "service_area_id": service_area_id,
        "regulatory_authority": "SGI",
        "regulatory_region": "SK",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("drivers", driver_payload)
    return phone


_CLEANUP_PAGE = 500


async def _cleanup() -> None:
    """Hard-delete every bot user (phone prefix +1306555) and its driver row.

    Review fix (2026-09-03): this used to pull ONE client-side page of 2 000
    `users` rows and filter in Python -- above 2 000 users it silently left
    bots behind while logging a confident "Cleanup complete". It now filters
    server-side on the phone prefix (`$regex` compiles to ILIKE in
    repositories/_base.py -- the layer owns escaping, callers pass the raw
    term) and loops until no matching row remains, so it cannot truncate.
    The caller must pass --yes (see main) because this is an unconditional
    hard delete of every account in the prefix range.
    """
    if not db_supabase.supabase:
        raise RuntimeError("Supabase client not configured")

    removed = 0
    while True:
        batch = await db_supabase.get_rows("users", {"phone": {"$regex": PHONE_PREFIX}}, limit=_CLEANUP_PAGE)
        # ILIKE is a contains-match; keep the exact prefix check so a real
        # number that merely CONTAINS the digits is never touched.
        batch = [u for u in batch if str(u.get("phone") or "").startswith(PHONE_PREFIX)]
        if not batch:
            break
        for u in batch:
            drv = await db_supabase.get_rows("drivers", {"user_id": u["id"]}, limit=1)
            if drv:

                def _del_driver(did=drv[0]["id"]):
                    return db_supabase.supabase.table("drivers").delete().eq("id", did).execute()

                await db_supabase.run_sync(_del_driver)

            def _del_user(uid=u["id"]):
                return db_supabase.supabase.table("users").delete().eq("id", uid).execute()

            await db_supabase.run_sync(_del_user)
            removed += 1
        logger.info("Removed %d bot users so far...", removed)
    logger.info("Cleanup complete — %d bot users (and their driver rows) removed.", removed)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--riders", type=int, default=45, help="number of rider bot accounts")
    parser.add_argument("--drivers", type=int, default=15, help="number of driver bot accounts")
    parser.add_argument("--cleanup", action="store_true", help="delete all previously-seeded bot users/drivers")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="required with --cleanup: confirms the unconditional hard delete of every user in the bot phone range",
    )
    args = parser.parse_args()

    # ── Hard safety interlocks (checked in code, not just documented) ──
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — refusing to run against an unknown project.")
        sys.exit(1)

    # Review fix (2026-09-03): allowlist, not a production denylist. The old
    # check only refused ENV=production, so an UNSET ENV (the default) with a
    # production SUPABASE_URL sailed through and would have written 60 users
    # and 15 drivers into the live project with the service key.
    env_value = os.environ.get("ENV", "").lower()
    if env_value not in _ALLOWED_ENVS:
        logger.error(
            "ENV=%r is not one of %s — refusing to seed synthetic bot accounts. Set ENV explicitly "
            "to the staging/dev environment you are targeting; this script never runs against production.",
            env_value,
            sorted(_ALLOWED_ENVS),
        )
        sys.exit(1)

    if args.cleanup:
        if not args.yes:
            logger.error(
                "--cleanup hard-deletes every user whose phone starts with %s; re-run with --yes to confirm.",
                PHONE_PREFIX,
            )
            sys.exit(1)
        await _cleanup()
        return

    vehicle_type_id, service_area_id = await _ensure_vehicle_type_and_area()

    logger.info("Seeding %d rider bots...", args.riders)
    for n in range(1, args.riders + 1):
        phone = await _ensure_rider(n)
        if n % 10 == 0 or n == args.riders:
            logger.info("  ...%d/%d riders (last=%s)", n, args.riders, phone)

    logger.info("Seeding %d driver bots...", args.drivers)
    for n in range(1, args.drivers + 1):
        phone = await _ensure_driver(n, vehicle_type_id, service_area_id)
        if n % 10 == 0 or n == args.drivers:
            logger.info("  ...%d/%d drivers (last=%s)", n, args.drivers, phone)

    print("\n=== Loadtest bots seeded ===")
    print(f"ENV={env_value}  SUPABASE_URL={os.environ.get('SUPABASE_URL')}")
    print(f"Riders: {args.riders} (phones {_rider_phone(1)}..{_rider_phone(args.riders)})")
    print(f"Drivers: {args.drivers} (phones {_driver_phone(1)}..{_driver_phone(args.drivers)})")
    print(f"Service area id: {service_area_id}  Vehicle type id: {vehicle_type_id}")
    print("============================\n")


if __name__ == "__main__":
    asyncio.run(main())
