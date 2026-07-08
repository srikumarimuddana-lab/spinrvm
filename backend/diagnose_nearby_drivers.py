"""
Read-only diagnostic for "XL driver online but not showing in rider app".

Usage:
    cd spinr/backend
    python diagnose_nearby_drivers.py
    python diagnose_nearby_drivers.py --pickup-lat=52.1332 --pickup-lng=-106.6700

The script inspects the same data the /rides/estimate and /drivers/nearby
endpoints read, applies the same filters, and prints exactly why an XL driver
is or isn't being surfaced to the rider app. No writes.
"""

import argparse
import asyncio
import math
from pathlib import Path

from dotenv import load_dotenv

# Load env the same way seed_vehicle_types.py does
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

import db_supabase  # noqa: E402


def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def section(title: str):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


async def main(pickup_lat, pickup_lng):
    section("1. Active vehicle types")
    vts = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
    if not vts:
        print("  ❌ NO active vehicle types at all. This explains why nothing works.")
        print("     Fix: run seed_vehicle_types.py or admin-create vehicle types.")
        return

    xl_vt = None
    for vt in vts:
        name = vt.get("name") or ""
        marker = "  <-- XL" if name.lower() == "xl" else ""
        print(f"  id={vt['id']}  name={name}  is_active={vt.get('is_active')}{marker}")
        if name.lower() == "xl":
            xl_vt = vt

    if not xl_vt:
        print("\n  ❌ No row with name='XL' and is_active=True in db.vehicle_types.")
        print("     Fix: seed or admin-create an XL vehicle type with is_active=True.")
        return
    print(f"\n  ✅ XL vehicle_type.id = {xl_vt['id']}")

    # Also look for orphan / inactive XL rows so we catch seed drift
    all_vts = await db_supabase.get_rows("vehicle_types", {}, limit=200)
    xl_all = [v for v in all_vts if (v.get("name") or "").lower() == "xl"]
    if len(xl_all) > 1:
        print(f"\n  ⚠️  Found {len(xl_all)} XL rows total (orphan seed drift risk):")
        for v in xl_all:
            print(f"       id={v['id']}  is_active={v.get('is_active')}")

    section("2. All online drivers")
    online = await db_supabase.get_rows("drivers", {"is_online": True}, limit=200)
    if not online:
        print("  (none)")
        print("\n  ❌ No drivers have is_online=True. Make sure the driver app has")
        print("     toggled online and the go-online endpoint actually flipped the flag.")
        return

    vt_by_id = {v["id"]: v for v in all_vts}
    xl_online = []
    for d in online:
        vt_id = d.get("vehicle_type_id")
        vt_row = vt_by_id.get(vt_id)
        vt_name = vt_row.get("name") if vt_row else "UNKNOWN / orphan"
        print(f"  driver_id={d.get('id')}")
        print(f"    is_online={d.get('is_online')}  is_available={d.get('is_available')}")
        # The /drivers/nearby (car pin) query additionally requires
        # is_verified=True AND status='active'. A driver can be online +
        # available but still hidden from the MAP if either of these is off,
        # while /rides/estimate (which skips them) would still count them.
        print(f"    is_verified={d.get('is_verified')}  status={d.get('status')}")
        print(f"    vehicle_type_id={vt_id}  -> {vt_name}")
        print(f"    lat={d.get('lat')}  lng={d.get('lng')}")
        if vt_id == xl_vt["id"]:
            xl_online.append(d)

    section("2b. Redis presence (heartbeat) — the filter that hides 'online-in-admin' drivers")
    print("Both /drivers/nearby (map pins) and /rides/estimate drop any driver")
    print("whose Redis presence key is not live. Admin reads the durable is_online")
    print("COLUMN instead, so a driver with a lapsed presence key shows ONLINE in")
    print("admin but is invisible to riders. The key is set on Go Online + every WS")
    print("heartbeat, and cleared on explicit Go Offline or a session revocation.")
    print("A plain network drop is NOT cleared — it rides a 30s TTL — so a driver")
    print("hidden here means their app stopped heartbeating for longer than the TTL")
    print("(WS never established, backgrounded/killed, or dead network).")
    print()
    # Persisted for the section-3 XL verdict below: a driver missing from the
    # presence set is hidden from /rides/estimate even when every DB filter
    # passes, so section 3 must factor presence in — otherwise it prints the
    # exact "NO presence key" + "will be counted" contradiction this tool exists
    # to debug. presence_reachable is None when the check couldn't run,
    # True/False from present_driver_ids_checked otherwise.
    present: set = set()
    presence_reachable = None
    try:
        from utils.driver_presence import present_driver_ids_checked
        from utils.redis_client import _get_redis

        _r = await _get_redis()
        print(f"  REDIS configured/connected: {'yes' if _r is not None else 'NO (in-process dict fallback)'}")
        if _r is None:
            print("  ⚠️  With >1 backend replica (e.g. two Fly machines) and NO shared")
            print("     Redis, presence lives in a PER-REPLICA dict: a driver whose WS")
            print("     landed on machine A is invisible to rider requests served by")
            print("     machine B. Set REDIS_URL / WS_REDIS_URL on every replica.")
        online_ids = [d["id"] for d in online if d.get("id")]
        present, reachable = await present_driver_ids_checked(online_ids)
        presence_reachable = reachable
        print(f"  presence store reachable: {reachable}")
        if not reachable:
            # Redis is configured but unavailable: present is an empty set that
            # means "unknown", NOT "everyone offline". The rider endpoints fall
            # back to DB state in this case (they do NOT hide drivers), so it
            # would be wrong to flag every driver as hidden here.
            print("  ⚠️  Presence store is configured but UNREACHABLE — presence is")
            print("     UNKNOWN, not empty. /drivers/nearby + /rides/estimate fall back")
            print("     to DB is_online here, so drivers are NOT hidden for this reason.")
            print("     Fix Redis connectivity, then re-run to read real presence.")
        else:
            for d in online:
                did = d.get("id")
                here = did in present
                tick = "✅ present" if here else "❌ NO presence key (hidden from riders)"
                print(f"    driver_id={did}  is_online={d.get('is_online')}  ->  {tick}")
    except Exception as _pres_exc:  # noqa: BLE001
        print(f"  (presence check skipped: {_pres_exc})")

    if not xl_online:
        print(f"\n  ❌ No online driver has vehicle_type_id == {xl_vt['id']} (the XL id).")
        print("     Possible causes:")
        print("     - Driver's saved vehicle_type_id points at an orphan/deactivated XL row.")
        print("     - Driver finished signup without selecting a vehicle type.")
        print("     - Driver was seeded against a different vehicle_types id set.")
        print("     Fix: open the driver in admin or have the driver re-save their vehicle type")
        print("          and confirm vehicle_type_id matches the active XL row above.")
        return
    print(f"\n  ✅ {len(xl_online)} online driver(s) are pointing at the active XL row.")

    section("3. Per XL driver — will the estimate endpoint count them?")
    print("Estimate endpoint filters (routes/rides.py:227-245):")
    print("  - is_online=True AND is_available=True")
    print("  - live Redis presence key (unless the store is unreachable)")
    print("  - lat and lng both truthy")
    print("  - distance(pickup, driver) <= 10 km")
    print()
    for d in xl_online:
        reasons = []
        if not d.get("is_available"):
            reasons.append("is_available=False  (stuck from prior ride? see drivers.py:1070/1113/1131)")
        # Presence gate: only when the store was actually reachable. When it's
        # unreachable (presence_reachable is False) the rider endpoints fall
        # back to DB state, so absence from `present` is NOT a hide reason.
        if presence_reachable and d.get("id") not in present:
            reasons.append("NO presence key  (heartbeat lapsed — dispatch + /rides/estimate can't reach; see section 2b)")
        if d.get("lat") in (None, 0) or d.get("lng") in (None, 0):
            reasons.append("lat/lng missing or 0  (driver app hasn't posted a location update)")
        if pickup_lat is not None and pickup_lng is not None and d.get("lat") and d.get("lng"):
            dist = haversine_km(pickup_lat, pickup_lng, d["lat"], d["lng"])
            print(f"  driver {d.get('id')}: distance to pickup = {dist:.2f} km")
            if dist > 10.0:
                reasons.append(f"distance {dist:.2f}km > 10km (estimate radius — XL tile will say 'No drivers nearby')")
            elif dist > 5.0:
                reasons.append(f"distance {dist:.2f}km > 5km (home-map /nearby radius — car pin won't show)")
        else:
            if pickup_lat is None:
                print(f"  driver {d.get('id')}: (distance check skipped — no --pickup-lat/--pickup-lng)")

        if reasons:
            print(f"    ❌ will NOT be counted: {'; '.join(reasons)}")
        else:
            print("    ✅ will be counted by /rides/estimate for XL")

    section("4. Service areas × XL fare_config coverage")
    print("If the rider's pickup is inside a service area that has fare_configs,")
    print("the /rides/estimate endpoint only returns vehicle types that have an")
    print("active fare_config row for that area (routes/fares.py:61-85).")
    print()
    areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    if not areas:
        print("  (no active service areas — estimate will use default fares for all vehicle types)")
    else:
        for a in areas:
            fares = await db_supabase.get_rows(
                "fare_configs",
                {
                    "service_area_id": a["id"],
                    "vehicle_type_id": xl_vt["id"],
                    "is_active": True,
                },
                limit=10,
            )
            tick = "✅" if fares else "❌"
            name = a.get("name") or a.get("id")
            print(f"  {tick} service_area '{name}': {len(fares)} active XL fare_config row(s)")
            if not fares:
                # Also show what IS configured so the user can see the gap
                all_area_fares = await db_supabase.get_rows(
                    "fare_configs",
                    {
                        "service_area_id": a["id"],
                        "is_active": True,
                    },
                    limit=20,
                )
                if all_area_fares:
                    configured_names = []
                    for f in all_area_fares:
                        v = vt_by_id.get(f.get("vehicle_type_id"))
                        configured_names.append(v.get("name") if v else f"orphan:{f.get('vehicle_type_id')}")
                    print(f"       (this area has fare_configs only for: {', '.join(configured_names)})")
                    print("       → pickups inside this area will NOT see the XL tile.")
                else:
                    print("       (this area has zero fare_configs — falls through to defaults, XL should still show)")

    section("4b. Service area boundaries — the green map zone + point-in-area match")
    print("The rider-app draws the green coverage zone from /service-areas -> polygon")
    print("(normalized by get_service_area_polygon). The SAME polygon decides whether a")
    print("pickup 'matches' an area for area-specific fares/surge (point_in_polygon).")
    print("An empty polygon => NO green zone AND pickups never match the area (they fall")
    print("through to default fares).")
    print()
    try:
        from geo_utils import get_service_area_polygon, point_in_polygon
    except Exception as _geo_exc:  # noqa: BLE001
        print(f"  (boundary check skipped: {_geo_exc})")
    else:
        for a in (areas or []):
            poly = get_service_area_polygon(a)
            name = a.get("name") or a.get("id")
            if len(poly) >= 3:
                inside = ""
                if pickup_lat is not None and pickup_lng is not None:
                    inside = (
                        "  — pickup INSIDE ✅"
                        if point_in_polygon(pickup_lat, pickup_lng, poly)
                        else "  — pickup OUTSIDE"
                    )
                print(f"  ✅ '{name}': boundary has {len(poly)} points (renders green){inside}")
            else:
                print(f"  ❌ '{name}': NO boundary polygon — no green zone; pickups never match this area")
        print()
        print("  If Regina shows ❌ above: draw + save its boundary in Admin → Service Areas.")
        print("  That renders the green zone AND lets pickups match Regina for area-specific")
        print("  fare_configs / surge (also add Regina fare_configs — see section 4).")

    section("Done")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--pickup-lat", type=float, default=None, help="Rider pickup latitude (optional, enables distance check)"
    )
    p.add_argument(
        "--pickup-lng", type=float, default=None, help="Rider pickup longitude (optional, enables distance check)"
    )
    args = p.parse_args()
    asyncio.run(main(args.pickup_lat, args.pickup_lng))
