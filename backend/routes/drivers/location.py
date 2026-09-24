"""Nearby drivers, location batch updates, device attestation, admin CRUD.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

import asyncio
import uuid
from typing import Literal

from pydantic import ValidationError, model_validator

from . import _deps, _shared
from ._deps import (  # noqa: F401
    APIRouter,
    BackgroundTasks,
    BaseModel,
    Depends,
    Driver,
    Field,
    HTTPException,
    List,
    Query,
    Request,
    Union,
    calculate_distance,
    datetime,
    db_supabase,
    generate_driver_code,
    get_admin_user,
    get_current_user,
    get_token_session_id,
    intent_online,
    location_update_limit,
    logger,
    parse_iso_utc,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    serialize_doc,
)

try:
    from ...utils import metrics
    from ...utils.driver_presence import availability_aware_present_driver_ids_checked
    from ...utils.driver_presence import renew_driver_presence as renew_scoped_presence
    from ...utils.error_handling import DatabaseError
    from ...utils.gps_filtering import point_epoch_seconds
    from ...utils.location_write_gate import should_write_marker
except ImportError:  # pragma: no cover - top-level execution fallback
    from utils import metrics  # type: ignore
    from utils.driver_presence import availability_aware_present_driver_ids_checked  # type: ignore
    from utils.driver_presence import renew_driver_presence as renew_scoped_presence  # type: ignore
    from utils.error_handling import DatabaseError  # type: ignore
    from utils.gps_filtering import point_epoch_seconds
    from utils.location_write_gate import should_write_marker  # type: ignore

router = APIRouter()

_V2_ACTIVE_RIDE_STATUSES = {"driver_assigned", "driver_accepted", "driver_arrived", "in_progress"}
_RAW_LOCATION_RETENTION = timedelta(days=90)

# Period 1 insurance accumulator columns. A marker UPDATE carrying either of
# these is never coalesced away — dropping one silently under-counts a
# regulated SGI audit figure. See utils/location_write_gate.
_PERIOD1_COLUMNS = ("period1_accum_km", "period1_accum_since")


async def _availability_v2_enabled() -> bool:
    """Read the rollout gate directly; presence must fail closed on lookup errors."""
    try:
        rows = await db_supabase.get_rows("settings", {"id": "app_settings"}, limit=1)
    except Exception as exc:
        logger.error("driver availability rollout flag lookup failed", exc_info=True)
        raise HTTPException(status_code=503, detail={"code": "PRESENCE_UNAVAILABLE"}) from exc
    return bool(rows and rows[0].get("driver_availability_v2_enabled", False))


async def _current_online_epoch(user_id: str, token_session_id: str | None) -> str | None:
    try:
        try:
            from ...services.driver_availability_service import get_driver_availability
        except ImportError:
            from services.driver_availability_service import get_driver_availability  # type: ignore
        snapshot = await get_driver_availability(user_id, token_session_id)
        return snapshot.get("online_epoch")
    except Exception:
        logger.error("could not read current driver epoch for location conflict", exc_info=True)
        return None


async def _require_current_token_session(user_id: str, token_session_id: str | None) -> None:
    """Durably reject replaced credentials before acknowledging v2 trip history."""
    if not token_session_id:
        raise HTTPException(status_code=409, detail={"code": "SESSION_RECONCILE_REQUIRED"})
    try:
        rows = await db_supabase.get_rows("users", {"id": user_id}, limit=1, columns="current_session_id")
    except Exception as exc:
        logger.error("could not verify current token session for trip batch", exc_info=True)
        raise HTTPException(status_code=503, detail={"code": "SESSION_AUTHORITY_UNAVAILABLE"}) from exc
    current_session_id = rows[0].get("current_session_id") if rows else None
    if current_session_id != token_session_id:
        raise HTTPException(status_code=409, detail={"code": "SESSION_SUPERSEDED"})


async def _require_presence_epoch(
    user_id: str,
    token_session_id: str | None,
    supplied_epoch: str | int | None,
    *,
    availability_v2: bool | None = None,
) -> int | None:
    """Require decimal epoch and token session only while the v2 gate is on."""
    if availability_v2 is None:
        availability_v2 = await _availability_v2_enabled()
    if not availability_v2:
        return None
    valid_epoch = None
    if (
        isinstance(supplied_epoch, str)
        and supplied_epoch.isascii()
        and supplied_epoch.isdecimal()
        and len(supplied_epoch) <= 19
    ):
        valid_epoch = int(supplied_epoch)
    elif type(supplied_epoch) is int:
        valid_epoch = supplied_epoch
    if valid_epoch is not None and 0 <= valid_epoch <= 9_223_372_036_854_775_807 and token_session_id:
        return valid_epoch

    current_epoch = await _current_online_epoch(user_id, token_session_id)
    if not token_session_id:
        code, reason = "SESSION_RECONCILE_REQUIRED", "SESSION_MISSING"
    elif valid_epoch is None or not 0 <= valid_epoch <= 9_223_372_036_854_775_807:
        code, reason = "AVAILABILITY_UPGRADE_REQUIRED", "ONLINE_EPOCH_REQUIRED"
    else:
        code, reason = "SESSION_RECONCILE_REQUIRED", "SESSION_MISSING"
    raise HTTPException(
        status_code=409,
        detail={"code": code, "reason_code": reason, "online_epoch": current_epoch},
    )


def _presence_conflict(result: dict) -> HTTPException | None:
    """Build the shared v2 REST conflict shape without exposing session IDs."""
    code = result.get("code")
    # F2-6: READINESS_EXPIRED is treated like CONTACT_GAP — both signal an
    # epoch-stale condition with a specific reason the app can act on.
    if code in {"ONLINE_EPOCH_STALE", "CONTACT_GAP", "READINESS_EXPIRED"}:
        return HTTPException(
            status_code=409,
            detail={
                "code": "ONLINE_EPOCH_STALE",
                "reason_code": result.get("reason_code") or code,
                "online_epoch": result.get("online_epoch"),
            },
        )
    if code == "SESSION_SUPERSEDED":
        return HTTPException(
            status_code=409,
            detail={"code": "SESSION_SUPERSEDED", "online_epoch": result.get("online_epoch")},
        )
    if code == "DRIVER_OFFLINE":
        return HTTPException(
            status_code=409,
            detail={"code": "DRIVER_OFFLINE", "online_epoch": result.get("online_epoch")},
        )
    if code in {"PRESENCE_UNAVAILABLE", "AVAILABILITY_V2_DISABLED"}:
        return HTTPException(status_code=503, detail={"code": "PRESENCE_UNAVAILABLE"})
    return None


async def _write_marker_if_due(
    driver_filter: dict,
    update_data: dict,
    driver_id: str,
    path: str,
    *,
    presence_session_id: str | None = None,
    presence_epoch: int | None = None,
) -> bool | None:
    """Coalesce writes; Postgres alone decides capture ordering across replicas."""
    extra = {key: update_data[key] for key in _PERIOD1_COLUMNS if key in update_data}
    if await should_write_marker(driver_id, path=path, force=bool(extra)):
        # An untimed queued point is history, never a current position.
        captured_at = update_data.get("location_captured_at") or datetime.fromtimestamp(0, timezone.utc)
        try:
            return await db_supabase.update_driver_location(
                driver_id,
                update_data["lat"],
                update_data["lng"],
                heading=update_data.get("heading"),
                captured_at=captured_at,
                extra_fields=extra,
                authenticated_session_id=presence_session_id,
                online_epoch=presence_epoch,
            )
        except DatabaseError:
            # Count every REST marker-write DB failure (same counter as the WS
            # path) and re-raise: each caller keeps its existing response
            # contract and its own ERROR log / Sentry capture.
            metrics.inc("spinr_live_marker_write_failures_total", {"path": path})
            raise
    return None


_MARKER_ORDER_CACHE_TTL = 120  # seconds; matches location_integrity's teleport-cache TTL


async def _newer_than_last_written_marker(driver_id: str, captured_at: datetime) -> bool:
    """Legacy cache helper, unused by writes: ordering is enforced in Postgres."""
    try:
        from ...utils.redis_client import redis_get, redis_set
    except ImportError:
        from utils.redis_client import redis_get, redis_set  # type: ignore

    cache_key = f"loc:marker_hwm:{driver_id}"
    try:
        prev_raw = await redis_get(cache_key)
        if prev_raw:
            prev_captured_at = datetime.fromisoformat(prev_raw)
            if prev_captured_at >= captured_at:
                return False
        await redis_set(cache_key, captured_at.isoformat(), ttl=_MARKER_ORDER_CACHE_TTL)
    except Exception:
        logger.debug("[location] marker order cache check failed for driver_id=%s", driver_id, exc_info=True)
    return True


async def _apply_v2_live_marker_update(
    driver_id: str,
    ride_id: str,
    lat: float,
    lng: float,
    heading: float | None,
    speed: float | None,
    accuracy: float | None,
    mocked: bool,
    is_online: bool,
    captured_at: datetime,
    *,
    refresh_presence: bool = True,
    token_session_id: str | None = None,
    online_epoch: int | None = None,
    availability_v2: bool = False,
) -> None:
    """Background task: GPS-integrity-gated live marker write + presence refresh.

    Split off the response path -- the client's ack (``result.ack.to_dict()``)
    reflects only durable breadcrumb persistence, which has already completed
    by the time this task is scheduled. A failure here still surfaces loudly
    via ``logger.error(exc_info=True)`` per this repo's "do not silently
    swallow DB errors" rule; it just no longer fails an already-successfully-
    persisted batch's HTTP response, which previously meant a marker-write
    failure could make the driver-app outbox re-send points the server had
    already durably stored.
    """
    if not -5 <= (datetime.now(timezone.utc) - captured_at).total_seconds() <= 60:
        return  # Durable history was already acknowledged; no live side effects.

    try:
        from ...utils.location_integrity import check_location_integrity
    except ImportError:
        from utils.location_integrity import check_location_integrity  # type: ignore

    try:
        trusted, reason = await check_location_integrity(
            driver_id, lat, lng, speed=speed, accuracy=accuracy, mocked=mocked
        )
    except Exception:
        logger.error(
            "location-batch v2: integrity check failed for driver_id=%s ride_id=%s", driver_id, ride_id, exc_info=True
        )
        return

    if not trusted:
        logger.warning(
            "location-batch v2: rejected live marker update for driver_id=%s ride_id=%s reason=%s",
            driver_id,
            ride_id,
            reason,
        )
    else:
        if availability_v2:
            if not token_session_id or online_epoch is None or not is_online:
                return
            result = await renew_scoped_presence(
                driver_id,
                token_session_id,
                online_epoch,
                location_captured_at=captured_at,
            )
            if result.get("status") not in {"renewed", "unavailable"}:
                logger.info("scoped presence GPS renewal rejected code=%s", result.get("code"))
                return
        update_data = {"lat": lat, "lng": lng, "location_captured_at": captured_at}
        if heading is not None:
            update_data["heading"] = heading % 360
        try:
            accepted = await _write_marker_if_due(
                {"id": driver_id},
                update_data,
                driver_id,
                "rest_v2_trip",
                presence_session_id=token_session_id if availability_v2 else None,
                presence_epoch=online_epoch if availability_v2 else None,
            )
            if accepted is False:
                return
            if availability_v2 and accepted is not True:
                # A throttled write has no fresh DB owner/epoch proof to bind
                # rider fanout to, so wait for the next live marker window.
                return
        except Exception:
            # Stable, path-neutral message (this task serves /location-live AND
            # /location-batch; the old "location-batch v2:" prefix mislabelled
            # live pings). ERROR + exc_info is captured to Sentry by the stdlib
            # LoggingIntegration (see utils/sentry_runtime.py) -- one event, no
            # duplicate capture. Response contract unchanged: the durable ack was
            # already returned before this background task ran.
            logger.error(
                "live marker write failed driver_id=%s ride_id=%s path=rest_v2_trip",
                driver_id,
                ride_id,
                exc_info=True,
                extra={"domain": "dispatch"},
            )
            if availability_v2:
                return
        # Live delivery must not depend on whether the DB write was coalesced.
        if -5 <= (datetime.now(timezone.utc) - captured_at).total_seconds() <= 60:
            try:
                try:
                    from ...settings_loader import get_app_settings
                except ImportError:
                    from settings_loader import get_app_settings
                settings = await get_app_settings() or {}
                if settings.get("background_location_fanout_enabled", False):
                    rides = await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver_id}, limit=1)
                    ride = rides[0] if rides else {}
                    if (
                        ride.get("id") == ride_id
                        and ride.get("driver_id") == driver_id
                        and ride.get("status") in {"driver_accepted", "driver_arrived", "in_progress"}
                        and ride.get("rider_id")
                    ):
                        await _deps.manager.send_personal_message(
                            {
                                "type": "driver_location_update",
                                "driver_id": driver_id,
                                "ride_id": ride_id,
                                "lat": lat,
                                "lng": lng,
                                "heading": heading,
                                "speed": speed,
                                "accuracy": accuracy,
                                "captured_at": captured_at.isoformat(),
                            },
                            f"rider_{ride['rider_id']}",
                            durable=False,
                        )
            except Exception:
                logger.error(
                    "location-batch v2: rider delivery failed for driver_id=%s ride_id=%s",
                    driver_id,
                    ride_id,
                    exc_info=True,
                )

    if not availability_v2 and is_online and refresh_presence:
        await _deps.mark_present(driver_id)


class TripLocationPoint(BaseModel):
    """One immutable driver sensor fix submitted by the durable outbox."""

    sequence_number: int = Field(ge=0)
    captured_at: datetime
    lat: float | None = None
    lng: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy: float | None = None
    speed: float | None = None
    heading: float | None = None
    altitude: float | None = None
    monotonic_ms: int | None = Field(default=None, ge=0)
    source: str | None = None
    mocked: bool = False
    is_completion_fix: bool = False


class LocationBatchRequest(BaseModel):
    """Strict v2 payload: exactly one ordered recording session for one ride."""

    ride_id: str = Field(min_length=1)
    recording_session_id: uuid.UUID
    online_epoch: str | None = None
    points: List[TripLocationPoint] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _sequences_are_contiguous(self):
        sequences = [point.sequence_number for point in self.points]
        if len(set(sequences)) != len(sequences):
            raise ValueError("sequence_number values must be unique")
        expected = list(range(sequences[0], sequences[0] + len(sequences)))
        if sequences != expected:
            raise ValueError("sequence_number values must be contiguous and ordered")
        return self


class IdleLocationBatchRequest(BaseModel):
    """Strict v2 idle payload: one ordered Period-1 recording session, no ride."""

    session_kind: Literal["online_idle"]
    recording_session_id: uuid.UUID
    online_epoch: str | None = None
    ride_id: None = None
    points: List[TripLocationPoint] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _sequences_are_contiguous(self):
        sequences = [point.sequence_number for point in self.points]
        if len(set(sequences)) != len(sequences):
            raise ValueError("sequence_number values must be unique")
        expected = list(range(sequences[0], sequences[0] + len(sequences)))
        if sequences != expected:
            raise ValueError("sequence_number values must be contiguous and ordered")
        return self


def _parse_v2_location_batch(
    batch: Union[List[dict], dict, LocationBatchRequest],
) -> LocationBatchRequest | IdleLocationBatchRequest | None:
    """Identify v2 bodies while preserving historical list/points payloads."""
    if isinstance(batch, LocationBatchRequest):
        return batch
    if not isinstance(batch, dict) or not ({"ride_id", "recording_session_id"} & set(batch)):
        return None
    if batch.get("session_kind") == "online_idle" or ("recording_session_id" in batch and not batch.get("ride_id")):
        try:
            return IdleLocationBatchRequest.model_validate({**batch, "session_kind": "online_idle"})
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
    try:
        return LocationBatchRequest.model_validate(batch)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _completed_batch_is_within_retention(request: LocationBatchRequest, ride: dict) -> bool:
    """Allow delayed offline delivery only inside the completed ride lifecycle."""
    completed_at = parse_iso_utc(ride.get("ride_completed_at"))
    if completed_at is None or datetime.now(timezone.utc) - completed_at > _RAW_LOCATION_RETENTION:
        return False
    window_start = parse_iso_utc(ride.get("driver_accepted_at")) or parse_iso_utc(ride.get("created_at"))
    return all(
        (window_start is None or point.captured_at >= window_start) and point.captured_at <= completed_at
        for point in request.points
    )


async def _persist_v2_idle_batch(
    request: IdleLocationBatchRequest, current_user: dict, token_session_id: str | None = None
) -> dict:
    """Authorize and persist one durable Period-1 (online-idle) outbox batch.

    409s are TERMINAL for the client outbox (it drains the batch), so they are
    reserved for states where these points can never be accepted: the feature
    flag off, or the driver offline. A driver with an active ride still gets a
    per-point 'ride_active' rejection path inside the persist call instead —
    pre-assignment points in the same batch must not be lost.
    """
    driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    if not driver_rows:
        raise HTTPException(status_code=403, detail="Driver profile required")
    driver = driver_rows[0]
    availability_v2 = await _availability_v2_enabled()
    presence_epoch = (
        await _require_presence_epoch(current_user["id"], token_session_id, request.online_epoch, availability_v2=True)
        if availability_v2
        else None
    )

    try:
        try:
            from ...settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        settings = await get_app_settings() or {}
    except Exception as exc:
        logger.error("idle batch settings read failed; rejecting batch as retryable", exc_info=True)
        raise HTTPException(status_code=503, detail="Settings unavailable") from exc
    if not settings.get("idle_location_v2_enabled", False):
        raise HTTPException(status_code=409, detail="Idle location recording is not enabled")
    if not driver.get("is_online"):
        # F5: under v2, structured conflict so the app can classify it.
        if availability_v2:
            raise HTTPException(
                status_code=409,
                detail={"code": "DRIVER_OFFLINE", "online_epoch": str(driver.get("online_epoch", 0))},
            )
        raise HTTPException(status_code=409, detail="Driver is not online")

    if availability_v2:
        contact = await renew_scoped_presence(driver["id"], token_session_id, presence_epoch)
        conflict = _presence_conflict(contact)
        if conflict:
            raise conflict

    try:
        from ...utils.breadcrumbs import persist_idle_location_batch, resolve_active_ride
    except ImportError:
        from utils.breadcrumbs import persist_idle_location_batch, resolve_active_ride  # type: ignore

    active_ride = await resolve_active_ride(driver["id"])
    try:
        result, accepted_rows = await persist_idle_location_batch(
            driver["id"],
            str(request.recording_session_id),
            [point.model_dump(mode="json") for point in request.points],
            active_ride=active_ride,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("idle location-batch persistence failed for driver_id=%s", driver["id"], exc_info=True)
        raise HTTPException(status_code=503, detail="Location persistence unavailable") from exc

    # Live marker from the newest accepted point (same contract as trips).
    if accepted_rows:
        latest = max(accepted_rows, key=lambda point: point_epoch_seconds(point) or 0)
        update_data: dict = {"lat": latest["lat"], "lng": latest["lng"], "location_captured_at": latest["captured_at"]}
        if latest.get("heading") is not None:
            update_data["heading"] = latest["heading"] % 360
        # Period-1 deadhead accumulator (same flag as the legacy v1 path). The
        # v1 path skips v2-shaped batches, so this is the single writer for
        # idle sessions — no double count.
        if settings.get("period1_distance_tracking_enabled", False):
            try:
                try:
                    from ...utils.period1_distance import batch_incremental_distance_km
                except ImportError:
                    from utils.period1_distance import batch_incremental_distance_km  # type: ignore
                _p1_delta = batch_incremental_distance_km(accepted_rows)
                if _p1_delta > 0:
                    update_data["period1_accum_km"] = round(float(driver.get("period1_accum_km") or 0) + _p1_delta, 3)
                    if not driver.get("period1_accum_since"):
                        update_data["period1_accum_since"] = datetime.now(timezone.utc)
            except Exception:
                logger.error("period1 accumulator update failed for driver %s", driver["id"], exc_info=True)
        if availability_v2:
            gps_presence = await renew_scoped_presence(
                driver["id"], token_session_id, presence_epoch, location_captured_at=latest["captured_at"]
            )
            if gps_presence.get("status") == "renewed":
                await _write_marker_if_due(
                    {"id": driver["id"]},
                    update_data,
                    str(driver["id"]),
                    "rest_v2_idle",
                    presence_session_id=token_session_id,
                    presence_epoch=presence_epoch,
                )
        else:
            await _write_marker_if_due({"id": driver["id"]}, update_data, str(driver["id"]), "rest_v2_idle")

    if (
        not availability_v2
        and accepted_rows
        and -5 <= (datetime.now(timezone.utc).timestamp() - (point_epoch_seconds(latest) or 0)) <= 60
    ):
        await _deps.mark_present(driver["id"])
    return result.ack.to_dict()


async def _persist_v2_location_batch(
    request: LocationBatchRequest,
    current_user: dict,
    background_tasks: BackgroundTasks,
    token_session_id: str | None,
) -> dict:
    """Authorize and persist one acknowledged v2 outbox batch before marker updates."""
    # The driver row and the ride row are independent reads; issue them
    # together and check ride ownership in Python instead of serialising the
    # second read behind the first. This path is on the < 150 ms driver
    # location-write SLA and measured p50 232 ms / p95 288 ms on 2026-09-11,
    # dominated by sequential Supabase round-trips (~20-25 ms each from Fly
    # yyz to ca-central-1). Persist-before-marker ordering below is untouched:
    # the live-marker GPS-integrity-check + write + presence refresh (below)
    # is now deferred via BackgroundTasks instead of awaited inline, since
    # the response (``result.ack.to_dict()``) only reflects the durable
    # persist above and never depends on the marker update's outcome -- see
    # docs/change-log/2026-09-14-location-batch-marker-update-deferred.md.
    driver_rows, rides = await asyncio.gather(
        db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1),
        db_supabase.get_rows("rides", {"id": request.ride_id}, limit=1),
    )
    if not driver_rows:
        raise HTTPException(status_code=403, detail="Driver profile required")
    driver = driver_rows[0]
    availability_v2 = await _availability_v2_enabled()
    if availability_v2:
        await _require_current_token_session(current_user["id"], token_session_id)
    presence_epoch = None
    if availability_v2 and request.online_epoch is not None:
        presence_epoch = await _require_presence_epoch(
            current_user["id"], token_session_id, request.online_epoch, availability_v2=True
        )

    # Same contract as the previous {id, driver_id} filter: a ride that is not
    # this driver's — including an unassigned one — is "not found", never a
    # hint that it exists.
    if not rides or rides[0].get("driver_id") != driver["id"]:
        raise HTTPException(status_code=404, detail="Assigned ride not found")
    ride = rides[0]
    if ride.get("status") == "completed":
        if not _completed_batch_is_within_retention(request, ride):
            raise HTTPException(status_code=422, detail="Points fall outside completed ride retention window")
    elif ride.get("status") not in _V2_ACTIVE_RIDE_STATUSES:
        # F5: under v2, structured conflict so the app can classify it.
        if availability_v2:
            raise HTTPException(
                status_code=409,
                detail={"code": "RIDE_STATE_CONFLICT", "ride_status": ride.get("status")},
            )
        raise HTTPException(status_code=409, detail="Ride cannot accept location points in its current state")

    try:
        try:
            from ...utils.breadcrumbs import persist_trip_location_batch
        except ImportError:
            from utils.breadcrumbs import persist_trip_location_batch  # type: ignore

        result = await persist_trip_location_batch(
            driver["id"],
            request.ride_id,
            str(request.recording_session_id),
            [point.model_dump(mode="json") for point in request.points],
            active_ride=ride,
            # Already-fetched row above -- no extra DB read. Anchors the
            # plausibility chain's boundary pair (driver's last known
            # position -> this batch's first point).
            driver_last_known={
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                # #5357: location_captured_at (the sensor timestamp for
                # lat/lng specifically) instead of updated_at (a
                # generic row-modified stamp any field write bumps,
                # e.g. go-online/go-offline) -- see breadcrumbs.py's
                # chain-seed for why the distinction matters.
                "location_captured_at": driver.get("location_captured_at"),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "location-batch durable persistence failed for driver_id=%s ride_id=%s",
            driver["id"],
            request.ride_id,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Location persistence unavailable") from exc

    rejected_sequences = {rejection.sequence_number for rejection in result.ack.rejected}
    latest = max(
        (point for point in request.points if point.sequence_number not in rejected_sequences),
        key=lambda point: parse_iso_utc(point.captured_at.isoformat()),
        default=None,
    )
    lat = lng = None
    if latest is not None:
        lat = latest.latitude if latest.latitude is not None else latest.lat
        lng = latest.longitude if latest.longitude is not None else latest.lng

    if lat is not None and lng is not None:
        # Same GPS spoofing/teleport guard the legacy (v1) path already runs
        # before trusting a point for the driver's LIVE marker — see
        # ACTION_ITEMS.md A40 finding #7. Historical breadcrumbs are already
        # persisted above (raw `mocked` flag kept for the settlement-time
        # anomaly filter in utils/trip_distance.py, and for the regulatory
        # GPS-trace record); this only gates whether a spoofed point is
        # allowed to move `drivers.lat/lng`, which dispatch, the rider map,
        # and admin all read as the driver's real-time position. Deferred
        # (see _apply_v2_live_marker_update) since the ack below never
        # depends on its outcome; that task also does the is_online-gated
        # presence refresh the synchronous path used to do here, so it is
        # not scheduled a second time below. `latest.captured_at` is passed
        # through so the deferred write can detect and skip a stale overwrite
        # if a later batch's task happens to run first (see
        # _newer_than_last_written_marker).
        background_tasks.add_task(
            _apply_v2_live_marker_update,
            driver["id"],
            request.ride_id,
            lat,
            lng,
            latest.heading,
            latest.speed,
            latest.accuracy,
            latest.mocked,
            bool(driver.get("is_online")),
            parse_iso_utc(latest.captured_at.isoformat()),
            token_session_id=token_session_id,
            online_epoch=presence_epoch,
            availability_v2=availability_v2,
        )
    elif driver.get("is_online") and not availability_v2:
        background_tasks.add_task(_deps.mark_present, driver["id"])

    return result.ack.to_dict()


@router.get("/nearby")
async def get_nearby_drivers_public(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(None),
    vehicle_type: str = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get nearby active drivers for riders. Filters by service area + vehicle type.

    Positions are **coarsened** (see utils/driver_map_visibility). Pre-match there
    is no assigned ride to justify exact coordinates, and this endpoint is
    reachable by any authenticated rider with arbitrary lat/lng, so exact
    coordinates here let a caller enumerate and follow individual drivers.
    """
    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        from ...utils.driver_map_visibility import clamp_radius, map_settings, prematch_driver_list
    except ImportError:
        from utils.driver_map_visibility import (  # type: ignore
            clamp_radius,
            map_settings,
            prematch_driver_list,
        )

    app_settings = await get_app_settings() or {}
    show_locations, cell_m, max_radius_km = map_settings(app_settings)
    default_radius = float(app_settings.get("search_radius_km", 10.0))

    # Kill switch (launch plan: map visibility stays behind one). Returning an
    # empty list rather than 404/403 keeps the rider map functional — it renders
    # no cars, and the availability count from /rides/estimate is unaffected
    # because that endpoint never carried coordinates.
    if not show_locations:
        return []

    # Use admin-configured search_radius_km if caller didn't override, and cap
    # whatever we end up with: unbounded, one request sweeps the province.
    radius = clamp_radius(radius if radius is not None else default_radius, max_radius_km, default_radius)

    # is_verified + status='active' prevent unverified / suspended / needs_review
    # drivers from appearing on the rider map even if their is_online flag is
    # stale.
    try:
        from ...services.dispatch_service import dispatch_geo_bounds
    except ImportError:
        from services.dispatch_service import dispatch_geo_bounds  # type: ignore

    query = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        # Geo-bound the fetch (same box the dispatch path uses) so the 100-row
        # cap applies to in-area drivers only — otherwise, above 100 online
        # drivers province-wide, the map shows an arbitrary 100 and nearby cars
        # can be missing while far ones render. Scan bounded by the
        # migration-138 partial index; see the no-geo-index rationale at the
        # dispatch fetch in routes/rides.py.
        "$and": dispatch_geo_bounds(lat, lng, radius),
    }
    if vehicle_type:
        query["vehicle_type_id"] = vehicle_type

    # Get all matching drivers — service area filtering by distance (not polygon yet)
    drivers = await db_supabase.get_rows("drivers", query, limit=100)

    # Presence filter: hide drivers whose app is not reachable (force-killed,
    # phone dead, backgrounded past the TTL). Without this, the rider sees
    # ghost cars on the map and tries to book someone who will never receive
    # the offer.
    #
    # Three cases, distinguished by the active lease reader:
    #   * reachable + non-empty → filter normally (ghost drivers removed).
    #   * reachable + empty     → every candidate is genuinely offline; hide
    #     them all (an empty map is correct here, not a bug).
    #   * NOT reachable (Redis configured but down) → presence is unknowable,
    #     so fall back to DB state rather than blanking the map during a
    #     failover. Dispatch still presence-filters, so a ghost booking that
    #     slips onto the map cannot actually complete an offer.
    try:
        driver_ids = [d["id"] for d in drivers if d.get("id")]
        if driver_ids:
            present, reachable = await availability_aware_present_driver_ids_checked(driver_ids)
            if reachable:
                drivers = [d for d in drivers if d["id"] in present]
            else:
                logger.warning(
                    "/drivers/nearby: presence store unreachable, showing DB "
                    "state (dispatch still presence-filters before any offer)"
                )
    except Exception as exc:
        logger.warning(f"/drivers/nearby presence filter failed, using DB state: {exc}")

    # Resolve vehicle type names + admin-configured marker variant once so
    # the rider app can pick the matching map marker (standard / XL /
    # premium) without an extra round trip.
    vt_name_by_id: dict = {}
    vt_marker_by_id: dict = {}
    if drivers:
        vehicle_types = await db_supabase.get_rows("vehicle_types", {}, limit=100)
        vt_name_by_id = {vt["id"]: vt.get("name") for vt in vehicle_types if vt.get("id")}
        vt_marker_by_id = {vt["id"]: vt.get("marker_variant") for vt in vehicle_types if vt.get("id")}

    # Manual filtering by distance
    in_radius = []
    for d in drivers:
        # Exclude orphan/demo driver rows (no user_id → cannot be dispatched).
        if not d.get("user_id"):
            continue
        # Authoritative intent check using the went_online_at /
        # went_offline_at timestamps (migration 97). The DB pre-filter
        # already used `is_online=True`, but intent_online() also catches
        # the case where the column is stale and prefers the timestamp
        # when both are present. Falls back to is_online for unmigrated rows.
        if not intent_online(d):
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        # `is not None` (not truthy) so a driver legitimately at lat=0 or
        # lng=0 still matches. The literal (0, 0) is the registration
        # default and means "no GPS yet" — skip those so a freshly-online
        # driver doesn't surface as a ghost car at the origin.
        if d_lat is not None and d_lng is not None and (d_lat != 0 or d_lng != 0):
            # Distance filtering uses the TRUE position so the radius stays
            # accurate; only the coordinates we hand back are coarsened.
            dist = calculate_distance(lat, lng, d_lat, d_lng)
            if dist <= radius:
                in_radius.append(
                    {
                        **d,
                        "vehicle_type_name": vt_name_by_id.get(d.get("vehicle_type_id")),
                        "marker_variant": vt_marker_by_id.get(d.get("vehicle_type_id")),
                    }
                )

    # Single projection point for what a pre-match rider may see — an allowlist,
    # so a new `drivers` column cannot become rider-visible by default. This also
    # drops vehicle_make/vehicle_model, which together with heading made a
    # specific car re-identifiable.
    # viewer_id scopes the marker pseudonyms to this rider so observations from
    # two accounts cannot be pooled into one trace.
    return prematch_driver_list(in_radius, cell_m, viewer_id=current_user.get("id"))


@router.get("")
async def get_drivers(
    lat: float = Query(None),
    lng: float = Query(None),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    admin_user: dict = Depends(get_admin_user),
):
    """
    Get all drivers (admin only) or nearby drivers (if lat/lng provided).
    """
    if lat is not None and lng is not None:
        # Should rely on RPC or geospatial query
        # For now, simplistic implementation as seen in other parts
        drivers = await db_supabase.get_rows("drivers", {"is_online": True}, limit=100)
        return serialize_doc(drivers)

    # Return all drivers for admin
    drivers = await db_supabase.get_rows("drivers", {}, limit=100)
    return serialize_doc(drivers)


@router.post("")
async def create_driver(driver: Driver, admin_user: dict = Depends(get_admin_user)):
    """Register a new driver (admin only or internal process)"""
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"phone": driver.phone}, limit=1)
    )
    if existing:
        raise HTTPException(status_code=400, detail="Driver with this phone already exists")

    row = driver.dict()
    row.setdefault("driver_code", generate_driver_code())
    # regulatory_authority/regulatory_region must never be left NULL on a
    # new driver row — see ACTION_ITEMS.md B13. The `Driver` schema has no
    # `service_area_id` field, so this always resolves via the single-market
    # (SGI/SK) fallback — correct for this repo's current SK-only footprint.
    row["regulatory_authority"], row["regulatory_region"] = await _shared._resolve_regulatory_defaults(None)
    await db_supabase.insert_one("drivers", row)
    return row


async def _guard_revoked_session(token_session_id: str | None) -> None:
    """Reject a location batch from a session that has been signed out.

    Defense in depth behind the driver app's own logout teardown. A signed-out
    app still holds an access token valid for the rest of its exp, and a stale
    build — or one killed mid-logout before its teardown ran — will keep
    uploading GPS with it. The client fix stops that at the source; this makes a
    client-side regression non-silent instead of re-opening a PIPEDA hole.

    Gated on an ``app_settings`` flag so it can be switched off without a
    redeploy (the settings-in-DB pattern in CLAUDE.md). Defaults ON: the check
    only fires on positive evidence of a logout, so leaving it dark would mean
    shipping the guard without the protection.

    Fails open on every ambiguity, including an unreadable settings row. Dropping
    a legitimate batch loses breadcrumbs that settle billed distance and back the
    SGI per-insurance-period audit, so a false 401 here is worse than a zombie
    writer that the client fix already stopped.
    """
    if not token_session_id:
        return
    try:
        try:
            from ...settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        # An explicit NULL in app_settings must mean "unset → use the default",
        # not "disabled". `.get(key, True)` alone returns None for a NULL column
        # and bool(None) is False, which would silently ship the guard dark.
        _flag = (await get_app_settings() or {}).get("location_reject_revoked_sessions_enabled")
        enabled = True if _flag is None else bool(_flag)
    except Exception:
        logger.warning("could not read location_reject_revoked_sessions_enabled; allowing batch", exc_info=True)
        return
    if not enabled:
        return

    try:
        from ...utils.session_revocation import is_session_revoked
    except ImportError:
        from utils.session_revocation import is_session_revoked  # type: ignore

    if await is_session_revoked(token_session_id):
        # 401 (not 403): the credential is dead, so the client should re-auth
        # rather than retry. No session_id or driver_id in the message.
        raise HTTPException(status_code=401, detail="ERR_SESSION_REVOKED")


class LiveLocationRequest(BaseModel):
    """Ephemeral position, independent of the durable history outbox."""

    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    captured_at: datetime
    heading: float | None = Field(default=None, allow_inf_nan=False)
    speed: float | None = Field(default=None, allow_inf_nan=False)
    accuracy: float | None = Field(default=None, allow_inf_nan=False)
    mocked: bool = False
    online_epoch: str | None = None

    @model_validator(mode="after")
    def _reject_missing_position(self):
        if self.lat == 0 and self.lng == 0:
            raise ValueError("A real position is required")
        return self


@router.post("/location-live")
@location_update_limit
async def update_live_location(
    point: LiveLocationRequest,
    background_tasks: BackgroundTasks,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    token_session_id: str | None = Depends(get_token_session_id),
):
    await _guard_revoked_session(token_session_id)
    drivers = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    if not drivers:
        raise HTTPException(status_code=403, detail="Driver profile required")
    driver = drivers[0]
    availability_enabled = await _availability_v2_enabled()
    if not driver.get("is_online"):
        if availability_enabled:
            current_epoch = await _current_online_epoch(current_user["id"], token_session_id)
            raise HTTPException(
                status_code=409,
                detail={"code": "DRIVER_OFFLINE", "online_epoch": current_epoch},
            )
        raise HTTPException(status_code=409, detail="Driver is not online")
    captured_at = parse_iso_utc(point.captured_at.isoformat())
    if not -5 <= (datetime.now(timezone.utc) - captured_at).total_seconds() <= 60:
        raise HTTPException(status_code=422, detail="A recent position is required")
    presence_epoch = await _require_presence_epoch(
        current_user["id"], token_session_id, point.online_epoch, availability_v2=availability_enabled
    )
    availability_v2 = presence_epoch is not None
    # Query the assigned ride before renewal so a fenced idle session returns a
    # conflict, while active-trip location delivery remains available for the
    # obligation and its insurance record.
    rides = await db_supabase.get_rows(
        "rides",
        {
            "driver_id": driver["id"],
            "status": {"$in": list(_V2_ACTIVE_RIDE_STATUSES)},
        },
        limit=1,
    )
    presence_code = None
    current_epoch = str(presence_epoch) if availability_v2 else None
    if availability_v2:
        presence_result = await renew_scoped_presence(driver["id"], token_session_id, presence_epoch)
        presence_code = presence_result.get("code")
        if presence_code == "CONTACT_GAP":
            presence_code = "ONLINE_EPOCH_STALE"
        current_epoch = presence_result.get("online_epoch") or current_epoch
        conflict = _presence_conflict(presence_result)
        if conflict and (not rides or presence_result.get("code") == "SESSION_SUPERSEDED"):
            raise conflict
    else:
        # Legacy presence stays intact behind the default-off gate.
        await _deps.mark_present(driver["id"])
    # Keep discovery/dispatch coordinates fresh independently of rider fanout.
    # The marker helper gates rider delivery internally.
    # Assignment is server-owned; never trust a caller's ride or driver ID.
    background_tasks.add_task(
        _apply_v2_live_marker_update,
        driver["id"],
        rides[0]["id"] if rides else "",
        point.lat,
        point.lng,
        point.heading,
        point.speed,
        point.accuracy,
        point.mocked,
        True,
        captured_at,
        refresh_presence=False,
        token_session_id=token_session_id,
        online_epoch=presence_epoch,
        availability_v2=availability_v2,
    )
    response = {"accepted": True}
    if availability_v2:
        response["online_epoch"] = str(current_epoch)
        if presence_code:
            response["presence_code"] = presence_code
    return response


@router.post("/location-batch")
@location_update_limit
async def update_location_batch(
    batch: Union[List[dict], dict, LocationBatchRequest],
    background_tasks: BackgroundTasks,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    token_session_id: str | None = Depends(get_token_session_id),
):
    """Update driver location in batch (from background tracking).

    Rate limited at 60/minute per driver (`location_update_limit`). The driver
    app's outbox flushes every 5-15 s, i.e. ~4-12 requests/minute, so this is
    roughly 5x headroom over normal operation and only bites on a runaway
    client. It is keyed per user, so one misbehaving device cannot exhaust the
    budget for other drivers sharing a carrier NAT egress IP.
    """
    # Ahead of both the v1 and v2 paths so neither can persist a signed-out
    # driver's coordinates.
    await _guard_revoked_session(token_session_id)
    v2_request = _parse_v2_location_batch(batch)
    if isinstance(v2_request, IdleLocationBatchRequest):
        return await _persist_v2_idle_batch(v2_request, current_user, token_session_id)
    if v2_request is not None:
        return await _persist_v2_location_batch(v2_request, current_user, background_tasks, token_session_id)

    try:
        from ...utils.location_integrity import check_location_integrity, evaluate_gps_plausibility
    except ImportError:
        from utils.location_integrity import check_location_integrity, evaluate_gps_plausibility  # type: ignore

    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get("locations") or batch.get("points") or []

    # Simply take the last point and update current location
    if not points:
        return {"success": True}

    latest = max(points, key=lambda point: point_epoch_seconds(point) or 0)
    capture_epoch = point_epoch_seconds(latest)
    captured_at = datetime.fromtimestamp(capture_epoch or 0, timezone.utc)
    lat = latest.get("latitude") if latest.get("latitude") is not None else latest.get("lat")
    lng = latest.get("longitude") if latest.get("longitude") is not None else latest.get("lng")
    heading = latest.get("heading")

    if lat is not None and lng is not None:
        # GPS spoofing check
        driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        driver_id = driver_rows[0]["id"] if driver_rows else current_user["id"]
        availability_v2 = await _availability_v2_enabled()
        presence_epoch = None
        v2_active_ride = None
        if availability_v2:
            if driver_rows:
                active_rows = await db_supabase.get_rows(
                    "rides",
                    {"driver_id": driver_id, "status": {"$in": list(_V2_ACTIVE_RIDE_STATUSES)}},
                    limit=1,
                )
                v2_active_ride = active_rows[0] if active_rows else None
            raw_epoch = batch.get("online_epoch") if isinstance(batch, dict) else None
            raw_epoch = raw_epoch if raw_epoch is not None else latest.get("online_epoch")
            if raw_epoch is not None:
                try:
                    presence_epoch = await _require_presence_epoch(
                        current_user["id"], token_session_id, raw_epoch, availability_v2=True
                    )
                except HTTPException:
                    if v2_active_ride is None:
                        raise
            else:
                if not token_session_id:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "SESSION_RECONCILE_REQUIRED", "reason_code": "SESSION_MISSING"},
                    )
                if driver_rows and driver_rows[0].get("is_online") and v2_active_ride is None:
                    current_epoch = await _current_online_epoch(current_user["id"], token_session_id)
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "AVAILABILITY_UPGRADE_REQUIRED",
                            "reason_code": "ONLINE_EPOCH_REQUIRED",
                            "online_epoch": current_epoch,
                        },
                    )
        fresh = -5 <= (datetime.now(timezone.utc) - captured_at).total_seconds() <= 60
        trusted = False
        if fresh:
            trusted, _reason = await check_location_integrity(
                driver_id,
                lat,
                lng,
                speed=latest.get("speed"),
                accuracy=latest.get("accuracy"),
                mocked=latest.get("mocked"),
            )
        # Update via Supabase wrapper which now handles casting. `heading`
        # column added in migration 113 — persist it so rider/admin map
        # markers can rotate the car icon to the real direction of travel
        # (and so two drivers at the same point don't render as one).
        update_data = {"lat": lat, "lng": lng, "location_captured_at": captured_at}
        # Normalise to 0–359 and skip clearly-invalid values. We deliberately
        # only write heading when the device sent a usable number, so a
        # stationary fix with no bearing doesn't wipe the last good heading.
        if heading is not None:
            try:
                update_data["heading"] = float(heading) % 360
            except (TypeError, ValueError):
                pass

        marker_fence_valid = not availability_v2
        if availability_v2 and presence_epoch is not None:
            presence_result = await renew_scoped_presence(driver_id, token_session_id, presence_epoch)
            if presence_result.get("code") == "SESSION_SUPERSEDED":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "SESSION_SUPERSEDED", "online_epoch": presence_result.get("online_epoch")},
                )
            if presence_result.get("status") in {"stale_epoch", "offline"} and v2_active_ride is None:
                conflict = _presence_conflict(presence_result)
                if conflict:
                    raise conflict
            marker_fence_valid = presence_result.get("status") in {"renewed", "unavailable"}
            if marker_fence_valid and fresh and trusted:
                gps_result = await renew_scoped_presence(
                    driver_id,
                    token_session_id,
                    presence_epoch,
                    location_captured_at=captured_at,
                )
                if gps_result.get("code") == "SESSION_SUPERSEDED":
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "SESSION_SUPERSEDED", "online_epoch": gps_result.get("online_epoch")},
                    )
                if gps_result.get("status") in {"stale_epoch", "offline"}:
                    marker_fence_valid = False
                    if v2_active_ride is None:
                        conflict = _presence_conflict(gps_result)
                        if conflict:
                            raise conflict

        # Period-1 (online, no active ride) deadhead-distance scalar accumulator.
        # Off by default; a deliberate opt-in since it measures a contractor's
        # between-rides movement. We fold ONLY a running km total + span start
        # into the same driver update — never the coordinates. resolve_active_ride
        # and get_app_settings are cached, so this stays cheap on the hot path.
        driver_row = driver_rows[0] if driver_rows else None
        # v2-shaped points (sequence_number markers) belong to the v2 idle
        # session path, which feeds this same accumulator — never both.
        _has_v2_markers = any(isinstance(p, dict) and "sequence_number" in p for p in points)
        if driver_row is not None and driver_row.get("is_online") and not _has_v2_markers and (trusted or not fresh):
            try:
                from ...settings_loader import get_app_settings
                from ...utils.breadcrumbs import resolve_active_ride
                from ...utils.period1_distance import batch_incremental_distance_km
            except ImportError:
                from settings_loader import get_app_settings  # type: ignore
                from utils.breadcrumbs import resolve_active_ride  # type: ignore
                from utils.period1_distance import batch_incremental_distance_km  # type: ignore
            try:
                _p1_on = bool((await get_app_settings() or {}).get("period1_distance_tracking_enabled"))
            except Exception:
                _p1_on = False
            if _p1_on:
                try:
                    _p1_active = await resolve_active_ride(driver_id)
                except Exception:
                    _p1_active = None
                # A queued batch (offline/backgrounded blip, then flushed on
                # reconnect) can straddle the moment a ride got assigned. This
                # is a REST fallback with no per-point outbox sequencing, so
                # unlike the v2 idle path (utils/breadcrumbs.persist_idle_location_batch,
                # which already rejects individual points at/after
                # ride_window_start) an active-ride check here used to gate
                # the WHOLE batch: any ride now active silently dropped every
                # point, including the genuine pre-assignment deadhead driven
                # before the blip — an insurance-audit undercount at exactly
                # the Period 1/2 boundary. Split at the same boundary instead:
                # only points captured before ride_window_start are Period 1.
                _p1_points = points
                if _p1_active is not None:
                    # Period 2 starts on assignment, not acceptance (CLAUDE.md)
                    # — same precedence as breadcrumbs.py/ride_complete.py.
                    _ride_window_start = (
                        parse_iso_utc(_p1_active.get("assigned_at"))
                        or parse_iso_utc(_p1_active.get("driver_accepted_at"))
                        or parse_iso_utc(_p1_active.get("created_at"))
                    )
                    if _ride_window_start is None:
                        _p1_points = []
                    else:
                        _boundary_epoch = _ride_window_start.timestamp()
                        _p1_points = []
                        for p in points:
                            _ts = point_epoch_seconds(p)
                            if _ts is not None and _ts < _boundary_epoch:
                                _p1_points.append(p)
                # History never mutates the live integrity cache. Exclude
                # explicit mock/speed/accuracy failures before distance filters.
                _p1_points = [
                    p
                    for p in _p1_points
                    if evaluate_gps_plausibility(
                        p.get("latitude", p.get("lat")),
                        p.get("longitude", p.get("lng")),
                        speed=p.get("speed"),
                        accuracy=p.get("accuracy"),
                        mocked=p.get("mocked"),
                    )[0]
                ]
                if _p1_points:
                    _p1_delta = batch_incremental_distance_km(_p1_points)
                    if _p1_delta > 0:
                        update_data["period1_accum_km"] = round(
                            float(driver_row.get("period1_accum_km") or 0) + _p1_delta, 3
                        )
                        if not driver_row.get("period1_accum_since"):
                            update_data["period1_accum_since"] = datetime.now(timezone.utc)

        # Only when a drivers row actually exists. In the fallback branch
        # (driver_id = current_user["id"], no row) the update_one below would
        # match zero rows anyway — but routing it through the gate burned a
        # window keyed on a users.id no other path shares and, worse, counted
        # outcome="written" for a write that never happened, polluting the
        # exact counter the shadow measurement reads.
        if driver_rows and marker_fence_valid and (trusted or any(key in update_data for key in _PERIOD1_COLUMNS)):
            if not trusted:
                update_data["location_captured_at"] = datetime.fromtimestamp(0, timezone.utc)
            await _write_marker_if_due(
                {"user_id": current_user["id"]},
                update_data,
                str(driver_id),
                "rest_v1",
                presence_session_id=token_session_id if availability_v2 else None,
                presence_epoch=presence_epoch if availability_v2 else None,
            )
        # Also sync to generic lat/lng fields if they exist to support legacy queries
        # (Though update_one might not support setting multiple top-level fields easily if we rely on $set mapping)
        # Let's trust db.drivers.update_one to handle the schema or the wrapper.

        # Persist the FULL batch as breadcrumbs, not just the live marker above.
        # Until now this endpoint (background task + WS-down REST fallback) kept
        # only the last point, so any backgrounded stretch of a trip produced no
        # driver_location_history rows — settled distance and the per-insurance-
        # period SGI audit trail both undercounted. The helper is a no-op unless
        # the driver currently has an active ride, and derives ride_id + phase
        # server-side (so a point the client tagged "background" still lands in
        # trip_in_progress). Best-effort: never fail the marker update on it.
        try:
            from ...utils.breadcrumbs import persist_ride_breadcrumbs
        except ImportError:
            from utils.breadcrumbs import persist_ride_breadcrumbs  # type: ignore
        try:
            # #1231 finding 11: pass the driver row already fetched above (no
            # extra DB read) so the chained plausibility check has a real
            # boundary point instead of starting cold on every batch.
            await persist_ride_breadcrumbs(driver_id, points, driver_last_known=driver_row)
        except Exception:
            logger.error("location-batch breadcrumb persist failed", exc_info=True)

        # Keep presence alive even when the driver's WebSocket briefly
        # drops but the REST location batch keeps flowing (e.g. phone on
        # cellular switching towers).
        #
        # Reuses `driver_row` fetched above (~line 525) instead of issuing a
        # second `drivers` fetch for the same `user_id` in this same
        # sequential await chain (ranked #25 / audit N10 — driver-location
        # write is the tightest SLA budget in the system at <150ms). Safe
        # because the only field read here is `is_online`, and the sole
        # write to this row between the two points (`update_one` just
        # above) only ever touches lat/lng/updated_at/heading/
        # period1_accum_* -- never `is_online` -- so a second read could not
        # observe a different value than the first.
        if fresh and trusted and driver_row and driver_row.get("is_online") and not availability_v2:
            await _deps.mark_present(driver_row["id"])

    return {"success": True}


@router.post("/attest-nonce")
async def attest_nonce(current_user: dict = Depends(get_current_user)):
    """Issue a single-use nonce for Play Integrity / App Attest verification.

    The client passes this nonce to the platform attestation API so the
    signed token can't be replayed from a different session.
    """
    import secrets

    nonce = secrets.token_hex(32)
    return {"nonce": nonce}


@router.post("/attest-device")
async def attest_device(device_info: dict, current_user: dict = Depends(get_current_user)):
    """Verify device integrity on go-online. Flags emulators and suspicious devices."""
    try:
        from ...utils.device_attestation import verify_device
    except ImportError:
        from utils.device_attestation import verify_device  # type: ignore

    driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    driver_id = driver_rows[0]["id"] if driver_rows else current_user["id"]

    result = await verify_device(current_user["id"], driver_id, device_info)
    return result
