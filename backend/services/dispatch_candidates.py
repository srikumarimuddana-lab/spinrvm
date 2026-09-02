"""Dispatch candidate providers: legacy box, H3 index, PostGIS, shadow.

Default production provider is ``legacy`` (the lat/lng bounding box in
``matching.py``). H3 is the intended future default but is not flipped on
until Redis is ``noeviction`` with headroom and a staging/canary has passed.

H3 / PostGIS return IDs only. Postgres re-applies eligibility so a stale
index entry cannot receive an offer.

Failover is explicit and noisy: empty-but-healthy H3 is a real no-drivers
result; Redis errors / unready index / eviction-policy mismatch fall back
to PostGIS then legacy and emit an admin-visible event.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

try:
    from ..utils.background import spawn
    from ..utils.h3_cells import DEFAULT_DISPATCH_RESOLUTION, filter_ids_within_radius, haversine_km
    from ..utils.h3_location_index import (
        get_last_served,
        health_snapshot,
        is_ready,
        notify_dispatch_geo_ops,
        query_driver_ids,
        recent_events,
        record_event,
        remember_last_served,
    )
    from ..utils.metrics import inc as _metric_inc
    from ..utils.metrics import observe as _metric_observe
except ImportError:  # pragma: no cover
    from utils.background import spawn  # type: ignore
    from utils.h3_cells import DEFAULT_DISPATCH_RESOLUTION, filter_ids_within_radius, haversine_km  # type: ignore
    from utils.h3_location_index import (  # type: ignore
        get_last_served,
        health_snapshot,
        is_ready,
        notify_dispatch_geo_ops,
        query_driver_ids,
        recent_events,
        record_event,
        remember_last_served,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore

logger = logging.getLogger(__name__)

VALID_PROVIDERS = frozenset({"legacy", "shadow", "postgis", "h3"})
DEFAULT_PROVIDER = "legacy"
POSTGIS_RPC = "drivers_nearby_location_geog"
# RPC LIMIT before eligibility. 500 nearest generic drivers can drop the
# only WAV/area match; eligibility is applied in Python after this fetch.
POSTGIS_ID_CAP = 5000
_DISPATCH_COLUMNS = (
    "id,user_id,lat,lng,rating,is_wav,acceptance_rate,destination_mode,"
    "destination_lat,destination_lng,vehicle_type_id,service_area_id"
)


def resolve_provider(app_settings: Optional[dict], area: Optional[dict]) -> str:
    """Per-area override beats the global setting. Unknown values → legacy."""
    area_val = (area or {}).get("dispatch_geo_provider")
    if isinstance(area_val, str) and area_val.strip().lower() in VALID_PROVIDERS:
        return area_val.strip().lower()
    global_val = (app_settings or {}).get("dispatch_geo_provider") or DEFAULT_PROVIDER
    if isinstance(global_val, str) and global_val.strip().lower() in VALID_PROVIDERS:
        return global_val.strip().lower()
    return DEFAULT_PROVIDER


def _h3_resolution(app_settings: Optional[dict]) -> int:
    try:
        res = int((app_settings or {}).get("dispatch_h3_resolution") or DEFAULT_DISPATCH_RESOLUTION)
    except (TypeError, ValueError):
        res = DEFAULT_DISPATCH_RESOLUTION
    return res if res in (7, 8, 9) else DEFAULT_DISPATCH_RESOLUTION


def _filter_without_box(dispatch_filter: dict) -> dict:
    return {k: v for k, v in dispatch_filter.items() if k != "$and"}


def _area_id(area: Optional[dict]) -> Optional[str]:
    raw = (area or {}).get("id")
    return str(raw) if raw else None


async def _legacy_rows(db, dispatch_filter: dict, columns: str, limit: int) -> list[dict]:
    return await db.get_rows("drivers", dispatch_filter, columns=columns, limit=limit)


async def _rows_for_ids(
    db,
    dispatch_filter: dict,
    ids: list[str] | set[str],
    columns: str,
    limit: int,
    *,
    pickup_lat: float,
    pickup_lng: float,
    radius_km: float,
) -> list[dict]:
    """Eligibility first, then nearest ``limit``. Never slice the ID set first."""
    if not ids:
        return []
    id_list = list(ids)
    filters = _filter_without_box(dispatch_filter)
    batched = getattr(db, "get_rows_batched_in", None)
    if batched is not None:
        rows = await batched(
            "drivers",
            "id",
            id_list,
            extra_filters=filters,
            columns=columns,
            limit=None,
        )
    else:
        filters["id"] = {"$in": id_list}
        rows = await db.get_rows("drivers", filters, columns=columns, limit=len(id_list))
    scored: list[tuple[float, dict]] = []
    for row in rows or []:
        try:
            dist = haversine_km(pickup_lat, pickup_lng, float(row["lat"]), float(row["lng"]))
        except (TypeError, ValueError, KeyError):
            continue
        if dist > radius_km:
            continue
        scored.append((dist, row))
    scored.sort(key=lambda item: item[0])
    return [row for _, row in scored[:limit]]


async def _postgis_ids(
    db,
    lat: float,
    lng: float,
    radius_km: float,
    limit: int,
    dispatch_filter: Optional[dict] = None,
) -> list[str]:
    params: dict[str, Any] = {
        "p_lat": float(lat),
        "p_lng": float(lng),
        "p_radius_m": float(radius_km) * 1000.0,
        "p_limit": int(max(int(limit), POSTGIS_ID_CAP)),
    }
    filt = dispatch_filter or {}
    if filt.get("is_wav") is True:
        params["p_is_wav"] = True
    vehicle_type_id = filt.get("vehicle_type_id")
    if isinstance(vehicle_type_id, str) and vehicle_type_id:
        params["p_vehicle_type_id"] = vehicle_type_id
    rows = await db.rpc(POSTGIS_RPC, params)
    if not rows:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("driver_id"):
            out.append(str(row["driver_id"]))
    return out


async def _notify_admin_failover(event: dict) -> None:
    """Best-effort: live map WS + Sentry so ops see a failover without polling."""
    await notify_dispatch_geo_ops(event)


def _failover_log(*, from_provider: str, to_provider: str, reason: str, ride_id: Optional[str]) -> None:
    """Cheap path — log + metric only. Redis/WS/Sentry wait until after the fallback query."""
    logger.error(
        "[DISPATCH] geo failover from=%s to=%s reason=%s ride_id=%s",
        from_provider,
        to_provider,
        reason,
        ride_id,
        extra={
            "domain": "dispatch",
            "from_provider": from_provider,
            "to_provider": to_provider,
            **({"ride_id": ride_id} if ride_id else {}),
        },
    )
    _metric_inc(
        "spinr_dispatch_geo_failover_total",
        {"from": from_provider, "to": to_provider, "reason": reason[:48]},
    )


async def _announce_failover(
    *,
    from_provider: str,
    to_provider: str,
    reason: str,
    ride_id: Optional[str],
) -> None:
    extra = {
        "from_provider": from_provider,
        "to_provider": to_provider,
        "ride_id": ride_id,
    }
    await record_event("failover", reason=reason, extra=extra)
    await _notify_admin_failover(
        {
            "kind": "failover",
            "from_provider": from_provider,
            "to_provider": to_provider,
            "reason": reason,
            "ride_id": ride_id,
            "severity": "critical" if from_provider == "h3" else "warning",
        }
    )


async def _failover(
    *,
    from_provider: str,
    to_provider: str,
    reason: str,
    ride_id: Optional[str],
) -> None:
    """Kept for tests that patch ``_failover``; prefer log-then-query-then-announce."""
    _failover_log(from_provider=from_provider, to_provider=to_provider, reason=reason, ride_id=ride_id)
    await _announce_failover(
        from_provider=from_provider,
        to_provider=to_provider,
        reason=reason,
        ride_id=ride_id,
    )


async def fetch_dispatch_candidates(
    *,
    db: Any,
    dispatch_filter: dict,
    pickup_lat: float,
    pickup_lng: float,
    search_radius_km: float,
    app_settings: Optional[dict],
    area: Optional[dict],
    columns: str = _DISPATCH_COLUMNS,
    limit: int = 500,
    ride_id: Optional[str] = None,
) -> list[dict]:
    """Return driver rows for matching. Default path is identical to today."""
    provider = resolve_provider(app_settings, area)
    area_id = _area_id(area)
    t0 = time.monotonic()
    outcome = "ok"
    used = provider
    try:
        if provider == "legacy":
            rows = await _legacy_rows(db, dispatch_filter, columns, limit)
            used = "legacy"
        elif provider == "shadow":
            rows = await _legacy_rows(db, dispatch_filter, columns, limit)
            used = "legacy"
            spawn(
                _shadow_compare(
                    db=db,
                    dispatch_filter=dispatch_filter,
                    pickup_lat=pickup_lat,
                    pickup_lng=pickup_lng,
                    search_radius_km=search_radius_km,
                    app_settings=app_settings,
                    columns=columns,
                    limit=limit,
                    legacy_rows=rows,
                    ride_id=ride_id,
                )
            )
        elif provider == "h3":
            rows, used = await _h3_or_fallback(
                db=db,
                dispatch_filter=dispatch_filter,
                pickup_lat=pickup_lat,
                pickup_lng=pickup_lng,
                search_radius_km=search_radius_km,
                app_settings=app_settings,
                columns=columns,
                limit=limit,
                ride_id=ride_id,
            )
        elif provider == "postgis":
            rows, used = await _postgis_or_fallback(
                db=db,
                dispatch_filter=dispatch_filter,
                pickup_lat=pickup_lat,
                pickup_lng=pickup_lng,
                search_radius_km=search_radius_km,
                columns=columns,
                limit=limit,
                ride_id=ride_id,
            )
        else:
            rows = await _legacy_rows(db, dispatch_filter, columns, limit)
            used = "legacy"
        if provider != "legacy":
            await _record_served(configured=provider, used=used, ride_id=ride_id, area_id=area_id)
        return rows
    except Exception:
        outcome = "error"
        raise
    finally:
        ms = (time.monotonic() - t0) * 1000.0
        _metric_inc("spinr_dispatch_geo_query_total", {"provider": used, "outcome": outcome})
        _metric_observe("spinr_dispatch_geo_query_duration_ms", ms, {"provider": used})


async def _h3_or_fallback(
    *,
    db,
    dispatch_filter,
    pickup_lat,
    pickup_lng,
    search_radius_km,
    app_settings,
    columns,
    limit,
    ride_id,
) -> tuple[list[dict], str]:
    ready = False
    try:
        ready = await is_ready()
    except Exception as exc:
        logger.error("[DISPATCH] H3 ready check failed ride_id=%s: %s", ride_id, exc, exc_info=True)
        _failover_log(from_provider="h3", to_provider="postgis", reason="ready_check_failed", ride_id=ride_id)
        rows, used = await _postgis_or_fallback(
            db=db,
            dispatch_filter=dispatch_filter,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            search_radius_km=search_radius_km,
            columns=columns,
            limit=limit,
            ride_id=ride_id,
        )
        await _announce_failover(from_provider="h3", to_provider=used, reason="ready_check_failed", ride_id=ride_id)
        return rows, used
    if not ready:
        _failover_log(from_provider="h3", to_provider="postgis", reason="h3_not_ready", ride_id=ride_id)
        rows, used = await _postgis_or_fallback(
            db=db,
            dispatch_filter=dispatch_filter,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            search_radius_km=search_radius_km,
            columns=columns,
            limit=limit,
            ride_id=ride_id,
        )
        await _announce_failover(from_provider="h3", to_provider=used, reason="h3_not_ready", ride_id=ride_id)
        return rows, used
    try:
        ids = await query_driver_ids(
            pickup_lat,
            pickup_lng,
            search_radius_km,
            res=_h3_resolution(app_settings),
        )
    except Exception as exc:
        logger.error("[DISPATCH] H3 query failed ride_id=%s: %s", ride_id, exc, exc_info=True)
        _failover_log(from_provider="h3", to_provider="postgis", reason="h3_query_failed", ride_id=ride_id)
        rows, used = await _postgis_or_fallback(
            db=db,
            dispatch_filter=dispatch_filter,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            search_radius_km=search_radius_km,
            columns=columns,
            limit=limit,
            ride_id=ride_id,
        )
        await _announce_failover(from_provider="h3", to_provider=used, reason="h3_query_failed", ride_id=ride_id)
        return rows, used
    rows = await _rows_for_ids(
        db,
        dispatch_filter,
        list(ids),
        columns,
        limit,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        radius_km=search_radius_km,
    )
    return rows, "h3"


async def _postgis_or_fallback(
    *,
    db,
    dispatch_filter,
    pickup_lat,
    pickup_lng,
    search_radius_km,
    columns,
    limit,
    ride_id,
) -> tuple[list[dict], str]:
    try:
        ids = await _postgis_ids(db, pickup_lat, pickup_lng, search_radius_km, limit, dispatch_filter=dispatch_filter)
        rows = await _rows_for_ids(
            db,
            dispatch_filter,
            ids,
            columns,
            limit,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            radius_km=search_radius_km,
        )
        return rows, "postgis"
    except Exception as exc:
        logger.error("[DISPATCH] PostGIS query failed ride_id=%s: %s", ride_id, exc, exc_info=True)
        _failover_log(from_provider="postgis", to_provider="legacy", reason="postgis_failed", ride_id=ride_id)
        rows = await _legacy_rows(db, dispatch_filter, columns, limit)
        await _announce_failover(
            from_provider="postgis", to_provider="legacy", reason="postgis_failed", ride_id=ride_id
        )
        return rows, "legacy"


def _ids_in_radius(rows: list[dict], pickup_lat: float, pickup_lng: float, radius_km: float) -> set[str]:
    pairs: list[tuple[str, float, float]] = []
    for row in rows:
        driver_id = row.get("id")
        if driver_id is None:
            continue
        try:
            pairs.append((str(driver_id), float(row["lat"]), float(row["lng"])))
        except (TypeError, ValueError, KeyError):
            continue
    return set(filter_ids_within_radius(pairs, pickup_lat, pickup_lng, radius_km))


async def _shadow_compare(
    *,
    db,
    dispatch_filter,
    pickup_lat,
    pickup_lng,
    search_radius_km,
    app_settings,
    columns,
    limit,
    legacy_rows,
    ride_id,
) -> None:
    """Run H3 without serving those results. Compare after eligibility + haversine."""
    try:
        if not await is_ready():
            _metric_inc("spinr_dispatch_geo_shadow_total", {"result": "h3_skipped_unready"})
            await record_event("shadow_skipped", reason="h3_not_ready", extra={"ride_id": ride_id})
            await notify_dispatch_geo_ops(
                {
                    "kind": "shadow_skipped",
                    "reason": "h3_not_ready",
                    "ride_id": ride_id,
                    "severity": "warning",
                }
            )
            return
        ids = await query_driver_ids(
            pickup_lat,
            pickup_lng,
            search_radius_km,
            res=_h3_resolution(app_settings),
        )
        h3_rows = await _rows_for_ids(
            db,
            dispatch_filter,
            list(ids),
            columns,
            limit,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            radius_km=search_radius_km,
        )
        legacy_ids = _ids_in_radius(legacy_rows, pickup_lat, pickup_lng, search_radius_km)
        h3_ids = _ids_in_radius(h3_rows, pickup_lat, pickup_lng, search_radius_km)
        only_legacy = legacy_ids - h3_ids
        only_h3 = h3_ids - legacy_ids
        if only_legacy or only_h3:
            logger.warning(
                "[DISPATCH] H3 shadow divergence ride_id=%s only_legacy=%d only_h3=%d",
                ride_id,
                len(only_legacy),
                len(only_h3),
            )
            _metric_inc("spinr_dispatch_geo_shadow_total", {"result": "diverge"})
            await record_event(
                "shadow_diverge",
                reason="id_set_mismatch",
                extra={
                    "ride_id": ride_id,
                    "only_legacy": len(only_legacy),
                    "only_h3": len(only_h3),
                },
            )
            await notify_dispatch_geo_ops(
                {
                    "kind": "shadow_diverge",
                    "reason": "id_set_mismatch",
                    "ride_id": ride_id,
                    "severity": "warning",
                    "only_legacy": len(only_legacy),
                    "only_h3": len(only_h3),
                }
            )
        else:
            _metric_inc("spinr_dispatch_geo_shadow_total", {"result": "match"})
    except Exception as exc:
        logger.error("[DISPATCH] H3 shadow compare failed ride_id=%s: %s", ride_id, exc, exc_info=True)
        _metric_inc("spinr_dispatch_geo_shadow_total", {"result": "error"})
        await record_event("shadow_error", reason=str(exc)[:120], extra={"ride_id": ride_id})
        await notify_dispatch_geo_ops(
            {
                "kind": "shadow_error",
                "reason": str(exc)[:120],
                "ride_id": ride_id,
                "severity": "warning",
            }
        )


async def _record_served(
    *,
    configured: str,
    used: str,
    ride_id: Optional[str],
    area_id: Optional[str] = None,
) -> None:
    """Persist configured-vs-actual so the admin banner is not WS-only."""
    failed_over = used != configured and configured in {"h3", "postgis"}
    reason = ""
    if failed_over:
        events = await recent_events(8)
        hit = next((e for e in events if e.get("kind") == "failover"), None)
        reason = str((hit or {}).get("reason") or "")
    prev = await get_last_served(area_id or "_")
    await remember_last_served(
        provider=used,
        configured=configured,
        failed_over=failed_over,
        reason=reason,
        ride_id=ride_id,
        area_id=area_id,
    )
    if configured == "h3" and used == "h3" and prev and prev.get("failed_over"):
        await record_event(
            "recovered",
            reason="h3_serving_again",
            extra={"from_provider": prev.get("provider"), "ride_id": ride_id, "area_id": area_id},
        )
        await notify_dispatch_geo_ops(
            {
                "kind": "recovered",
                "from_provider": prev.get("provider"),
                "to_provider": "h3",
                "reason": "h3_serving_again",
                "ride_id": ride_id,
                "severity": "info",
            }
        )


def _status_summary(
    configured: str,
    snap: dict[str, Any],
    last_served: Optional[dict[str, Any]],
) -> Optional[str]:
    unhealthy = snap.get("unhealthy") or {}
    if last_served and last_served.get("failed_over"):
        return (
            f"Matching failed over from {last_served.get('configured')} to "
            f"{last_served.get('provider')}"
            f"{(': ' + last_served['reason']) if last_served.get('reason') else ''}."
        )
    if configured == "h3" and not snap.get("h3_ready"):
        blockers = ", ".join(snap.get("blockers") or []) or "not ready"
        return (
            f"H3 is selected but cannot serve ({blockers}). "
            "New rides will fail over to PostGIS, then the legacy bounding box."
        )
    if configured == "postgis" and last_served and last_served.get("provider") == "legacy":
        return "PostGIS candidate lookup failed; matching is using the legacy bounding box."
    if configured in {"h3", "postgis", "shadow"} and unhealthy.get("reason"):
        return f"H3 index flagged unhealthy: {unhealthy.get('reason')}."
    return None


async def admin_dispatch_geo_status(app_settings: Optional[dict] = None) -> dict[str, Any]:
    """Combined status for GET /api/admin/monitoring/dispatch-geo."""
    snap = await health_snapshot()
    configured = resolve_provider(app_settings, None)
    events = snap.get("events") or []
    last_failover = next((e for e in events if e.get("kind") == "failover"), None)
    last_served = snap.get("last_served")
    effective = (last_served or {}).get("provider") or configured
    return {
        "configured_provider": configured,
        "effective_provider": effective,
        "h3_would_serve": snap.get("h3_ready"),
        "last_failover": last_failover,
        "status_summary": _status_summary(configured, snap, last_served),
        **snap,
    }
