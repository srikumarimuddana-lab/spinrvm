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
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(env_path)

from db import db  # noqa: E402


def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def section(title: str):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


async def main(pickup_lat, pickup_lng):
    section("1. Active vehicle types")
    vts = await db.vehicle_types.find({'is_active': True}).to_list(100)
    if not vts:
        print("  ❌ NO active vehicle types at all. This explains why nothing works.")
        print("     Fix: run seed_vehicle_types.py or admin-create vehicle types.")
        return

    xl_vt = None
    for vt in vts:
        name = vt.get('name') or ''
        marker = "  <-- XL" if name.lower() == 'xl' else ""
        print(f"  id={vt['id']}  name={name}  is_active={vt.get('is_active')}{marker}")
        if name.lower() == 'xl':
            xl_vt = vt

    if not xl_vt:
        print("\n  ❌ No row with name='XL' and is_active=True in db.vehicle_types.")
        print("     Fix: seed or admin-create an XL vehicle type with is_active=True.")
        return
    print(f"\n  ✅ XL vehicle_type.id = {xl_vt['id']}")

    # Also look for orphan / inactive XL rows so we catch seed drift
    all_vts = await db.vehicle_types.find({}).to_list(200)
    xl_all = [v for v in all_vts if (v.get('name') or '').lower() == 'xl']
    if len(xl_all) > 1:
        print(f"\n  ⚠️  Found {len(xl_all)} XL rows total (orphan seed drift risk):")
        for v in xl_all:
            print(f"       id={v['id']}  is_active={v.get('is_active')}")

    section("2. All online drivers")
    online = await db.drivers.find({'is_online': True}).to_list(200)
    if not online:
        print("  (none)")
        print("\n  ❌ No drivers have is_online=True. Make sure the driver app has")
        print("     toggled online and the go-online endpoint actually flipped the flag.")
        return

    vt_by_id = {v['id']: v for v in all_vts}
    xl_online = []
    for d in online:
        vt_id = d.get('vehicle_type_id')
        vt_row = vt_by_id.get(vt_id)
        vt_name = vt_row.get('name') if vt_row else 'UNKNOWN / orphan'
        print(f"  driver_id={d.get('id')}")
        print(f"    is_online={d.get('is_online')}  is_available={d.get('is_available')}")
        print(f"    vehicle_type_id={vt_id}  -> {vt_name}")
        print(f"    lat={d.get('lat')}  lng={d.get('lng')}")
        if vt_id == xl_vt['id']:
            xl_online.append(d)

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
    print("  - lat and lng both truthy")
    print("  - distance(pickup, driver) <= 10 km")
    print()
    for d in xl_online:
        reasons = []
        if not d.get('is_available'):
            reasons.append("is_available=False  (stuck from prior ride? see drivers.py:1070/1113/1131)")
        if d.get('lat') in (None, 0) or d.get('lng') in (None, 0):
            reasons.append("lat/lng missing or 0  (driver app hasn't posted a location update)")
        if pickup_lat is not None and pickup_lng is not None and d.get('lat') and d.get('lng'):
            dist = haversine_km(pickup_lat, pickup_lng, d['lat'], d['lng'])
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
    areas = await db.service_areas.find({'is_active': True}).to_list(100)
    if not areas:
        print("  (no active service areas — estimate will use default fares for all vehicle types)")
    else:
        for a in areas:
            fares = await db.fare_configs.find({
                'service_area_id': a['id'],
                'vehicle_type_id': xl_vt['id'],
                'is_active': True,
            }).to_list(10)
            tick = "✅" if fares else "❌"
            name = a.get('name') or a.get('id')
            print(f"  {tick} service_area '{name}': {len(fares)} active XL fare_config row(s)")
            if not fares:
                # Also show what IS configured so the user can see the gap
                all_area_fares = await db.fare_configs.find({
                    'service_area_id': a['id'],
                    'is_active': True,
                }).to_list(20)
                if all_area_fares:
                    configured_names = []
                    for f in all_area_fares:
                        v = vt_by_id.get(f.get('vehicle_type_id'))
                        configured_names.append(v.get('name') if v else f"orphan:{f.get('vehicle_type_id')}")
                    print(f"       (this area has fare_configs only for: {', '.join(configured_names)})")
                    print("       → pickups inside this area will NOT see the XL tile.")
                else:
                    print("       (this area has zero fare_configs — falls through to defaults, XL should still show)")

    section("Done")


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pickup-lat', type=float, default=None, help='Rider pickup latitude (optional, enables distance check)')
    p.add_argument('--pickup-lng', type=float, default=None, help='Rider pickup longitude (optional, enables distance check)')
    args = p.parse_args()
    asyncio.run(main(args.pickup_lat, args.pickup_lng))
