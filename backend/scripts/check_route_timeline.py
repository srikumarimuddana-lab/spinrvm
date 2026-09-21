#!/usr/bin/env python3
"""Read-only: does a ride's reconstructed route agree with its own clock?

Answers the question a rider or driver actually asks -- "I never drove there,
where did those points come from?" -- by walking the evidence in timestamp
order and asking, for every hole in the GPS, whether what the map draws across
it could physically have been driven in the time available.

    python -m backend.scripts.check_route_timeline --ride-id <uuid>
    python -m backend.scripts.check_route_timeline --ride-id <uuid> --interpolate
    python -m backend.scripts.check_route_timeline \
        --ride-json ride.json --locations-json locations.json --route-json route.json

Console output is always coordinate-free. Precise coordinates -- including the
time-based estimates from --interpolate -- are written only to --output, the
same convention analyze_ride_route.py uses for --route-output.

Nothing here writes. It reads rides, driver_location_history and ride_routes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from .. import db_supabase
    from ..geo_utils import calculate_distance
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.route_segments import (
        MAX_CONTINUOUS_DISPLACEMENT_METERS,
        MAX_CONTINUOUS_GAP_SECONDS,
        MAX_PLAUSIBLE_SPEED_KPH,
    )
except ImportError:  # python -m backend.scripts.* vs top-level
    import db_supabase  # type: ignore
    from geo_utils import calculate_distance  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.route_segments import (  # type: ignore
        MAX_CONTINUOUS_DISPLACEMENT_METERS,
        MAX_CONTINUOUS_GAP_SECONDS,
        MAX_PLAUSIBLE_SPEED_KPH,
    )

_PAGE = 1000
_MAX_POINTS = 10_000


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check a reconstructed route against the ride's own timeline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ride-id", help="Completed ride UUID, loaded read-only.")
    source.add_argument("--ride-json", help="Path to an exported ride JSON object.")
    parser.add_argument("--locations-json", help="Path to an exported GPS-row JSON array.")
    parser.add_argument("--route-json", help="Path to an exported ride_routes JSON object.")
    parser.add_argument(
        "--interpolate",
        action="store_true",
        help="Include time-proportional coordinates across each hole (estimates, not evidence) in --output.",
    )
    parser.add_argument("--output", help="Write the coordinate-bearing detail to this JSON path.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing --output file.")
    return parser


def _load_json(path: str, expected: type, label: str) -> Any:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, expected):
        raise ValueError(f"{label} must contain a JSON {expected.__name__}")
    return value


async def _load_live(ride_id: str):
    rides = await db_supabase.get_rows("rides", {"id": ride_id}, limit=1)
    if not rides:
        raise ValueError(f"ride {ride_id} not found")
    points: List[Dict[str, Any]] = []
    offset = 0
    while len(points) < _MAX_POINTS:
        page = await db_supabase.get_rows(
            "driver_location_history", {"ride_id": ride_id}, order="timestamp", limit=_PAGE, offset=offset
        )
        if not page:
            break
        points.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    routes = await db_supabase.get_rows("ride_routes", {"ride_id": ride_id}, limit=1)
    return rides[0], points[:_MAX_POINTS], (routes[0] if routes else {})


def _ordered(points: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Timestamp order is the only order that means anything here.

    Rows can arrive late or out of order (offline buffering, retries), so the
    stored order is not necessarily travel order.
    """
    usable = []
    for point in points:
        captured = parse_iso_utc(point.get("captured_at") or point.get("timestamp"))
        lat, lng = point.get("lat"), point.get("lng")
        if captured is None or lat is None or lng is None:
            continue
        usable.append({"at": captured, "lat": float(lat), "lng": float(lng), "accuracy": point.get("accuracy")})
    usable.sort(key=lambda row: row["at"])
    return usable


def _legs(ordered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-step elapsed / distance / implied speed between consecutive fixes."""
    legs = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        seconds = (current["at"] - previous["at"]).total_seconds()
        metres = calculate_distance(previous["lat"], previous["lng"], current["lat"], current["lng"]) * 1000.0
        kph = (metres / seconds * 3.6) if seconds > 0 else None
        legs.append({"from": previous, "to": current, "seconds": seconds, "metres": metres, "kph": kph})
    return legs


def _holes(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Legs the segmenter would treat as a break in continuous tracking."""
    holes = []
    for index, leg in enumerate(legs):
        by_time = leg["seconds"] > MAX_CONTINUOUS_GAP_SECONDS
        by_distance = leg["metres"] > MAX_CONTINUOUS_DISPLACEMENT_METERS
        if by_time or by_distance:
            holes.append(
                {
                    "leg_index": index,
                    "reason": "time_gap" if by_time else "distance_gap",
                    "seconds": leg["seconds"],
                    "straight_line_m": leg["metres"],
                    # The slowest you could have gone and still covered the
                    # straight line. Any real road path is longer, so this is a
                    # floor, never an estimate of actual speed.
                    "min_kph": leg["kph"],
                    "from": leg["from"],
                    "to": leg["to"],
                }
            )
    return holes


def _inferred_segments(route_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    segments = route_row.get("road_matched_segments")
    if not isinstance(segments, list):
        return []
    out = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("geometry_kind") != "inferred":
            continue
        coordinates = segment.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        out.append(
            {
                "segment_no": index + 1,
                "gap_reason": segment.get("gap_reason"),
                "provider": segment.get("provider"),
                "distance_km": float(segment.get("distance_km") or 0),
                "point_count": len(coordinates),
                "start": coordinates[0],
                "end": coordinates[-1],
            }
        )
    return out


def _nearest_hole(segment: Dict[str, Any], holes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Match an inferred segment to the hole it spans, by its start point."""
    best, best_m = None, None
    for hole in holes:
        metres = calculate_distance(segment["start"][0], segment["start"][1], hole["from"]["lat"], hole["from"]["lng"])
        metres *= 1000.0
        if best_m is None or metres < best_m:
            best, best_m = hole, metres
    if best is None or best_m is None or best_m > 250.0:
        return None
    return best


def _interpolate(hole: Dict[str, Any], step_seconds: float = 4.0) -> List[Dict[str, Any]]:
    """Where you *could* have been, spaced by time along the straight line.

    This is the honest shape of a hole: constant speed between the two fixes
    that actually exist. It is an estimate and is labelled as one -- it is not
    a claim about which road was used, which is exactly the claim a routed
    connector makes without evidence.
    """
    seconds = hole["seconds"]
    if seconds <= step_seconds:
        return []
    start, end = hole["from"], hole["to"]
    steps = int(seconds // step_seconds)
    points = []
    for step in range(1, steps):
        fraction = (step * step_seconds) / seconds
        points.append(
            {
                "at": start["at"].timestamp() + step * step_seconds,
                "lat": round(start["lat"] + (end["lat"] - start["lat"]) * fraction, 6),
                "lng": round(start["lng"] + (end["lng"] - start["lng"]) * fraction, 6),
                "elapsed_s": round(step * step_seconds, 1),
                "estimated": True,
            }
        )
    return points


def _fmt(value: Optional[float], unit: str, places: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{places}f}{unit}"


def _write_detail(path: str, detail: Dict[str, Any], overwrite: bool) -> None:
    """Coordinates leave this tool only through a file the caller named."""
    output = Path(path)
    if output.exists() and not overwrite:
        raise FileExistsError("refusing to overwrite existing output; pass --overwrite")
    output.write_text(json.dumps(detail, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote coordinate detail to {path}", file=sys.stderr)
    print("Privacy warning: that file contains precise GPS coordinates.", file=sys.stderr)


def _report(ride: Dict[str, Any], points, route_row, *, interpolate: bool) -> "tuple[int, Dict[str, Any]]":
    ordered = _ordered(points)
    print("=" * 72)
    print("ROUTE TIMELINE CHECK")
    print("=" * 72)
    print(f"ride                : {ride.get('id')}")
    print(f"usable GPS fixes    : {len(ordered)} of {len(points)} rows")
    if len(ordered) < 2:
        print("\nNot enough timestamped GPS to check anything.")
        return 1, {}
    span = (ordered[-1]["at"] - ordered[0]["at"]).total_seconds()
    print(f"first fix           : {ordered[0]['at'].isoformat()}")
    print(f"last fix            : {ordered[-1]['at'].isoformat()}")
    print(f"tracked span        : {span / 60:.1f} min")

    legs = _legs(ordered)
    tracked_km = sum(leg["metres"] for leg in legs) / 1000.0
    holes = _holes(legs)
    print(f"distance along fixes: {tracked_km:.3f} km   (sum of straight hops between fixes)")
    print(f"holes in tracking   : {len(holes)}")

    print("\n" + "-" * 72)
    print("HOLES — where tracking stopped, and the slowest you could have gone")
    print("-" * 72)
    if not holes:
        print("None. Tracking was continuous; nothing had to be invented.")
    for number, hole in enumerate(holes, start=1):
        print(f"\n[{number}] {hole['reason']}  at {hole['from']['at'].isoformat()}")
        print(f"    time with no GPS   : {hole['seconds']:.0f} s")
        print(f"    straight-line gap  : {hole['straight_line_m']:.0f} m")
        print(f"    minimum speed      : {_fmt(hole['min_kph'], ' km/h')}  (to cover the straight line)")
        print("    endpoints          : written to --output, kept off the console")

    print("\n" + "-" * 72)
    print("WHAT THE MAP DREW ACROSS THOSE HOLES")
    print("-" * 72)
    segments = _inferred_segments(route_row or {})
    if not segments:
        print("No inferred segments stored for this ride (or no route row supplied).")
    for segment in segments:
        hole = _nearest_hole(segment, holes)
        print(f"\nsegment #{segment['segment_no']}  [{segment['gap_reason']}]  via {segment['provider']}")
        print(f"    drawn distance     : {segment['distance_km']:.3f} km over {segment['point_count']} points")
        if hole is None:
            print("    matching hole      : none found within 250 m — inspect manually")
            continue
        print(f"    time available     : {hole['seconds']:.0f} s")
        print(f"    straight-line gap  : {hole['straight_line_m']:.0f} m")
        detour = segment["distance_km"] / (hole["straight_line_m"] / 1000.0) if hole["straight_line_m"] > 0 else None
        print(f"    detour ratio       : {_fmt(detour, 'x', 2)}  (drawn / straight line)")
        implied = segment["distance_km"] / (hole["seconds"] / 3600.0) if hole["seconds"] > 0 else None
        print(f"    implied speed      : {_fmt(implied, ' km/h')}")
        if implied is not None and implied > MAX_PLAUSIBLE_SPEED_KPH:
            print(
                f"    VERDICT            : NOT TRAVELABLE — needs {implied:.0f} km/h, cap is "
                f"{MAX_PLAUSIBLE_SPEED_KPH} km/h"
            )
            print("                         Those points are invented. You did not drive them.")
        else:
            print("    VERDICT            : travelable in the time available")

    detail: Dict[str, Any] = {"ride_id": ride.get("id"), "holes": [], "inferred_segments": segments}
    for number, hole in enumerate(holes, start=1):
        entry: Dict[str, Any] = {
            "hole_no": number,
            "reason": hole["reason"],
            "seconds": hole["seconds"],
            "straight_line_m": round(hole["straight_line_m"], 1),
            "min_kph": hole["min_kph"],
            "from": {"at": hole["from"]["at"].isoformat(), "lat": hole["from"]["lat"], "lng": hole["from"]["lng"]},
            "to": {"at": hole["to"]["at"].isoformat(), "lat": hole["to"]["lat"], "lng": hole["to"]["lng"]},
        }
        if interpolate:
            entry["time_based_estimate"] = _interpolate(hole)
        detail["holes"].append(entry)

    if interpolate:
        print("\n" + "-" * 72)
        print("TIME-BASED ESTIMATE — where you could have been, spaced by the clock")
        print("(constant speed between the two real fixes; an estimate, NOT evidence)")
        print("-" * 72)
        for entry in detail["holes"]:
            count = len(entry.get("time_based_estimate") or [])
            print(
                f"[{entry['hole_no']}] {count} estimated points across {entry['seconds']:.0f} s "
                f"at {_fmt(entry['min_kph'], ' km/h')} — coordinates in --output"
            )
    return 0, detail


async def _run(args: argparse.Namespace) -> int:
    if args.ride_id:
        ride, points, route_row = await _load_live(args.ride_id)
    else:
        if not args.locations_json:
            raise ValueError("--locations-json is required with --ride-json")
        ride = _load_json(args.ride_json, dict, "ride input")
        points = _load_json(args.locations_json, list, "locations input")
        route_row = _load_json(args.route_json, dict, "route input") if args.route_json else {}
    status, detail = _report(ride, points, route_row, interpolate=args.interpolate)
    if args.output and detail:
        _write_detail(args.output, detail, args.overwrite)
    elif args.interpolate and not args.output:
        print("Note: --interpolate computed estimates but no --output was given to write them to.", file=sys.stderr)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        return asyncio.run(_run(args))
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Route timeline check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
