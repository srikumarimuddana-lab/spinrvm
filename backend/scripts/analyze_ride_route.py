#!/usr/bin/env python3
"""Read-only diagnostic CLI for one completed ride's route evidence.

Examples (from the repository root):

    python -m backend.scripts.analyze_ride_route --ride-id <uuid>
    python -m backend.scripts.analyze_ride_route \
        --ride-json ride.json --locations-json locations.json --no-osrm

Console output is sanitized.  Precise coordinates are written only when the
caller explicitly requests ``--route-output``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from .. import db_supabase
    from ..utils.ride_route_analyzer import analyze_ride_evidence, build_incident_report, project_phase_3_route
except ImportError:
    import db_supabase  # type: ignore
    from utils.ride_route_analyzer import (  # type: ignore
        analyze_ride_evidence,
        build_incident_report,
        project_phase_3_route,
    )


_PAGE_SIZE = 1000
_MAX_POINTS = 10_000


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze one ride route without modifying production data.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ride-id", help="Completed ride UUID to load read-only from the configured database.")
    source.add_argument("--ride-json", help="Path to an exported ride JSON object.")
    parser.add_argument("--locations-json", help="Path to an exported GPS-row JSON array.")
    parser.add_argument("--route-output", help="Optional path for precise Phase 3 GeoJSON.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing route-output file.")
    parser.add_argument("--no-osrm", action="store_true", help="Skip provider projection and report observed GPS only.")
    parser.add_argument(
        "--incident-report",
        action="store_true",
        help="Emit the phase-by-phase incident report (distance ladder, accuracy histogram, gap timeline).",
    )
    return parser


def _load_json(path: str, expected_type: type, label: str) -> Any:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise ValueError(f"{label} must contain a JSON {expected_type.__name__}")
    return value


async def _load_side_rows(ride_id: str, table: str) -> list[dict[str, Any]]:
    """Best-effort read of an auxiliary per-ride table (gap events / recomputes).

    Never fatal: a missing table or empty result yields [] so the report still
    renders. These feed the incident report's gap timeline + recompute audit.
    """
    try:
        rows = await db_supabase.get_rows(table, {"ride_id": ride_id}, limit=500)
        return list(rows or [])
    except Exception as exc:  # noqa: BLE001 — diagnostic aux read, never fatal
        print(f"note: could not read {table}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []


async def _load_live(ride_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rides = await db_supabase.get_rows("rides", {"id": ride_id}, limit=1)
    if not rides:
        raise ValueError("ride not found")

    points: list[dict[str, Any]] = []
    offset = 0
    while len(points) < _MAX_POINTS:
        page = await db_supabase.get_rows(
            "driver_location_history",
            {"ride_id": ride_id},
            order="captured_at",
            limit=_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            break
        points.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    gap_events = await _load_side_rows(ride_id, "ride_location_gap_events")
    recomputes = await _load_side_rows(ride_id, "ride_distance_recomputes")
    return rides[0], points[:_MAX_POINTS], gap_events, recomputes


def _write_route(path: str, geojson: dict[str, Any], overwrite: bool) -> None:
    output = Path(path)
    if output.exists() and not overwrite:
        raise FileExistsError("refusing to overwrite existing route output; pass --overwrite")
    output.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    print("Privacy warning: route output contains precise GPS coordinates.", file=sys.stderr)


async def _run(args: argparse.Namespace) -> int:
    gap_events: list[dict[str, Any]] = []
    recomputes: list[dict[str, Any]] = []
    if args.ride_id:
        ride, locations, gap_events, recomputes = await _load_live(args.ride_id)
    else:
        if not args.locations_json:
            raise ValueError("--locations-json is required with --ride-json")
        ride = _load_json(args.ride_json, dict, "ride input")
        locations = _load_json(args.locations_json, list, "locations input")

    analysis = analyze_ride_evidence(ride, locations)
    if args.incident_report:
        report = build_incident_report(ride, locations, gap_events=gap_events, recomputes=recomputes)
    else:
        report = dict(analysis.report)
    projection = None
    if not args.no_osrm:
        projection = await project_phase_3_route(analysis, ride)
        report["route_projection"] = projection.report

    print(json.dumps(report, indent=2, sort_keys=True))
    if args.route_output:
        if projection is None:
            raise ValueError("--route-output requires provider projection; remove --no-osrm")
        if projection.geojson is None:
            raise RuntimeError("no drawable Phase 3 route is available")
        _write_route(args.route_output, projection.geojson, args.overwrite)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        return asyncio.run(_run(args))
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Ride route analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
