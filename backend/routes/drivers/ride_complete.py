"""Trip completion: fare settlement kickoff, earnings snapshot.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps, _shared
from ._deps import (  # noqa: F401
    EVENT_END,
    Any,
    APIRouter,
    BaseModel,
    Body,
    Decimal,
    Depends,
    Dict,
    Field,
    HTTPException,
    Optional,
    RideStateError,
    RideStatus,
    asyncio,
    build_earnings_snapshot,
    calculate_distance,
    datetime,
    db_error_text,
    db_supabase,
    flush_driver_breadcrumbs,
    get_current_user,
    logger,
    parse_iso_utc,
    pg_error_code,
    recalculate_fare_for_distance,
    send_live_activity_update,
    spawn,
    timezone,
    to_decimal,
    uuid,
)
from ._shared import (  # noqa: F401
    COMPLETE_FROM_STATES,
    _snap_pickup_leg_async,
    _validate_ride_route,
    serialize_doc,
)

router = APIRouter()


try:
    from ...utils.breadcrumbs import persist_trip_location_batch
except ImportError:
    from utils.breadcrumbs import persist_trip_location_batch  # type: ignore


_COMPLETION_MAX_CAPTURE_AGE_SECONDS = 120
_COMPLETION_MAX_FUTURE_SKEW_SECONDS = 30
_COMPLETION_MAX_ACCURACY_METERS = 100
_AT_DESTINATION_METERS = 200
_NEAR_DESTINATION_METERS = 1000
_OFF_ROUTE_CONFIRMATIONS = {
    "rider_requested_stop",
    "changed_destination",
    "emergency",
    "location_unavailable",
}


class CompletionFix(BaseModel):
    """A final GPS sample captured immediately before driver completion."""

    recording_session_id: uuid.UUID
    sequence_number: int = Field(ge=0)
    captured_at: datetime
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(default=None, ge=0)
    speed: float | None = None
    heading: float | None = None
    altitude: float | None = None
    monotonic_ms: int | None = Field(default=None, ge=0)
    source: str | None = None
    mocked: bool = False
    is_completion_fix: bool = True


class RideCompletionRequest(BaseModel):
    """Backward-compatible body for endpoint-level route-integrity evidence."""

    completion_fix: CompletionFix | None = None
    final_session_id: uuid.UUID | None = None
    final_sequence_number: int | None = Field(default=None, ge=0)
    pending_outbox_count: int | None = Field(default=None, ge=0)
    off_route_confirmation: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.off_route_confirmation is not None and self.off_route_confirmation not in _OFF_ROUTE_CONFIRMATIONS:
            raise ValueError("off_route_confirmation is not recognized")
        if self.completion_fix is not None:
            if self.final_session_id is not None and self.final_session_id != self.completion_fix.recording_session_id:
                raise ValueError("final_session_id must match completion_fix.recording_session_id")
            if (
                self.final_sequence_number is not None
                and self.final_sequence_number != self.completion_fix.sequence_number
            ):
                raise ValueError("final_sequence_number must match completion_fix.sequence_number")


class CompletionLocationOutcome:
    """Safe completion-tail result returned to the driver, never containing GPS."""

    def __init__(
        self,
        *,
        location_ack: Dict[str, Any] | None,
        legacy_client_missing_tail: bool,
        distance_band: str | None,
        completion_fix_rejection: str | None = None,
    ) -> None:
        self.location_ack = location_ack
        self.legacy_client_missing_tail = legacy_client_missing_tail
        self.distance_band = distance_band
        self.completion_fix_rejection = completion_fix_rejection


async def _get_route_integrity_mode() -> str:
    """Read the rollout mode without silently weakening an enabled guard."""
    try:
        try:
            from ...settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        settings = await get_app_settings()
    except Exception as exc:
        logger.error("completion route-integrity configuration read failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Route-integrity configuration is temporarily unavailable") from exc

    mode = str((settings or {}).get("route_integrity_v2_mode", "shadow")).lower()
    if mode not in {"off", "shadow", "on"}:
        logger.error("completion route-integrity mode is invalid: %s", mode)
        raise HTTPException(status_code=503, detail="Route-integrity configuration is invalid")
    return mode


def _completion_fix_rejection(fix: CompletionFix, now: datetime) -> str | None:
    """Return an auditable rejection code for a non-authoritative final fix."""
    captured_at = parse_iso_utc(fix.captured_at)
    if captured_at is None:
        return "invalid_capture_time"
    age_seconds = (now - captured_at).total_seconds()
    if age_seconds > _COMPLETION_MAX_CAPTURE_AGE_SECONDS:
        return "stale_capture"
    if age_seconds < -_COMPLETION_MAX_FUTURE_SKEW_SECONDS:
        return "future_capture"
    if fix.mocked:
        return "mocked_location"
    if fix.accuracy is not None and fix.accuracy > _COMPLETION_MAX_ACCURACY_METERS:
        return "low_accuracy"
    if fix.lat == 0 and fix.lng == 0:
        return "invalid_coordinate"
    return None


def _completion_distance_band(ride: Dict[str, Any], fix: CompletionFix) -> tuple[str, int | None]:
    """Classify final fix distance from the requested destination without logging it."""
    try:
        dropoff_lat = float(ride["dropoff_lat"])
        dropoff_lng = float(ride["dropoff_lng"])
    except (KeyError, TypeError, ValueError):
        return "unknown", None

    distance_meters = int(round(calculate_distance(fix.lat, fix.lng, dropoff_lat, dropoff_lng) * 1000))
    if distance_meters <= _AT_DESTINATION_METERS:
        return "at_destination", distance_meters
    if distance_meters <= _NEAR_DESTINATION_METERS:
        return "near_destination", distance_meters
    return "off_route", distance_meters


async def prepare_completion_location(
    ride: Dict[str, Any], driver_id: str, request: RideCompletionRequest
) -> CompletionLocationOutcome:
    """Persist a trusted final fix before completion mutates ride state.

    Older apps are allowed through with an explicit missing-tail marker. A new
    fix that is stale, mocked, or too imprecise is retained nowhere as a final
    endpoint and is reported as a non-sensitive rejection code for telemetry.
    """
    fix = request.completion_fix
    if fix is None:
        return CompletionLocationOutcome(
            location_ack=None,
            legacy_client_missing_tail=True,
            distance_band=None,
        )

    rejection = _completion_fix_rejection(fix, datetime.now(timezone.utc))
    if rejection is not None:
        return CompletionLocationOutcome(
            location_ack=None,
            legacy_client_missing_tail=False,
            distance_band=None,
            completion_fix_rejection=rejection,
        )

    distance_band, distance_meters = _completion_distance_band(ride, fix)
    mode = await _get_route_integrity_mode()
    if mode == "on" and distance_band == "off_route" and not request.off_route_confirmation:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "completion_confirmation_required",
                "distance_band": distance_band,
                "distance_meters": distance_meters,
            },
        )

    point = fix.model_dump(mode="json")
    point["is_completion_fix"] = True
    try:
        persisted = await persist_trip_location_batch(
            driver_id,
            str(ride["id"]),
            str(fix.recording_session_id),
            [point],
            active_ride=ride,
        )
        await db_supabase.update_one(
            "ride_routes",
            {"ride_id": ride["id"]},
            {
                "route_schema_version": 2,
                "completion_point": {
                    **point,
                    "distance_band": distance_band,
                    "distance_meters": distance_meters,
                },
            },
            upsert=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("completion final fix persistence failed for ride %s", ride.get("id"), exc_info=True)
        raise HTTPException(status_code=503, detail="Unable to persist completion location") from exc

    return CompletionLocationOutcome(
        location_ack=persisted.ack.to_dict(),
        legacy_client_missing_tail=False,
        distance_band=distance_band,
    )


@router.post("/rides/{ride_id}/complete")
async def complete_ride(
    ride_id: str,
    completion_request: RideCompletionRequest | None = Body(default=None),
    current_user: dict = Depends(get_current_user),
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"]}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("status") not in COMPLETE_FROM_STATES:
        raise RideStateError(f"Cannot complete ride from state '{ride.get('status')}'; ride must be in_progress")

    # Persist the driver-captured endpoint before the status transition and
    # before legacy aggregation reads the breadcrumb trail. This ensures a
    # 40-minute trip cannot lose its final location merely because finalization
    # starts before the normal outbox flush returns.
    # Direct unit callers receive FastAPI's ``Body`` sentinel rather than
    # ``None`` when omitting this parameter; production requests have already
    # been parsed into RideCompletionRequest by FastAPI.
    parsed_completion_request = (
        completion_request if isinstance(completion_request, RideCompletionRequest) else RideCompletionRequest()
    )
    completion_location = await prepare_completion_location(
        ride,
        driver["id"],
        parsed_completion_request,
    )

    # B3.3: drain this driver's WS breadcrumb buffer before aggregating —
    # otherwise the last ~10s of the trip would miss the settled distance
    # and the SGI trail read below. Only meaningful when the driver's WS is
    # on THIS replica (which it is for the driver calling this endpoint via
    # the same affinity-LB'd session); a failed flush must not block
    # completion — the buffer's disconnect flush still covers the points.
    try:
        await flush_driver_breadcrumbs(driver["id"])
    except Exception:
        logger.error("[complete_ride] breadcrumb flush failed for driver %s", driver["id"], exc_info=True)

    # ── Aggregate all GPS breadcrumbs for this ride ──
    # On completion we compute everything once and store it on the ride row.
    # After this the admin dashboard reads from the ride row directly — no
    # need to join against driver_location_history for historical rides.
    planned_distance = ride.get("planned_distance_km") or ride.get("distance_km", 0) or 0
    actual_distance_km = planned_distance
    actual_distance_km_haversine: Optional[float] = None
    actual_distance_km_road: Optional[float] = None
    phase_distances: Dict[str, float] = {}
    phase_durations: Dict[str, int] = {}
    phase_polylines: Dict[str, list] = {}
    pickup_to_driver_km = 0.0
    route_polyline = []
    road_polyline: list = []
    road_polyline_pickup: list = []
    gps_points_count = 0
    trip_points_count = 0
    rejected_segments = 0
    max_segment_gap_s = 0
    road_distance_provider = "haversine_filtered"
    route_quality: Dict[str, Any] = {"confidence": "low", "reason": "no_gps_breadcrumbs"}
    route_geometry_status = "pending"
    route_geometry_error: Optional[str] = None

    try:
        # Page through ALL breadcrumbs for the ride (time-ordered). The old
        # single limit=1000 read dropped the tail of long, densely-sampled
        # trips (>~67 min at the 4s background cadence), under-reporting
        # billable/phase distance and the SGI trail. Bounded by a hard ceiling
        # so a pathological trail can't blow up settlement memory; the per-phase
        # and route polylines are downsampled below regardless of input size.
        _PAGE = 1000
        _MAX_BREADCRUMBS = 10000  # ~11 h at 4s — beyond any real single trip
        all_breadcrumbs = []
        _offset = 0
        while True:
            _page = await db_supabase.get_rows(
                "driver_location_history",
                {"ride_id": ride_id},
                order="timestamp",
                limit=_PAGE,
                offset=_offset,
            )
            if not _page:
                break
            all_breadcrumbs.extend(_page)
            if len(_page) < _PAGE or len(all_breadcrumbs) >= _MAX_BREADCRUMBS:
                break
            _offset += _PAGE
        if len(all_breadcrumbs) >= _MAX_BREADCRUMBS:
            logger.warning(
                f"GPS breadcrumbs hit ceiling {_MAX_BREADCRUMBS} for ride {ride_id}; tail beyond this is not summed"
            )
        all_breadcrumbs = [b for b in all_breadcrumbs if b.get("lat") is not None and b.get("lng") is not None]
        all_breadcrumbs.sort(key=lambda b: str(b.get("timestamp", "")))
        gps_points_count = len(all_breadcrumbs)

        if gps_points_count >= 2:
            # Compute per-phase distances (attribute each segment to the
            # current point's phase) and per-phase durations from the
            # timestamp deltas. Phase 1 (online_idle) is not expected
            # against a ride_id but tolerated if it shows up.
            # GPS sanity caps — reject segments that are physically impossible
            # before summing. Without these, a single tower-handoff jump on a
            # 7 km trip could inflate actual_distance_km to 90+ km (the
            # ingestion-time filter in utils/location_integrity.py is not
            # retroactively re-applied to stored breadcrumbs).
            #   MAX_SEG_KM      — single ping-to-ping displacement. Even at
            #                     240 km/h with a 30 s gap that's 2 km; 5 km
            #                     is well above any realistic value.
            #   MAX_SEG_KMH     — sustained ground speed. Saskatchewan max
            #                     posted is 110 km/h; allow 150 for downhill
            #                     /overtake transients before rejecting.
            #   MAX_SEG_GAP_S   — long gaps (background, signal loss) make
            #                     straight-line distance unreliable; treat
            #                     same as the duration cap.
            MAX_SEG_KM = 5.0
            MAX_SEG_KMH = 150.0
            MAX_SEG_GAP_S = 300
            rejected_segments = 0
            max_segment_gap_s = 0
            phase_totals: Dict[str, float] = {}
            phase_secs: Dict[str, float] = {}
            for i in range(1, len(all_breadcrumbs)):
                prev = all_breadcrumbs[i - 1]
                curr = all_breadcrumbs[i]
                phase = curr.get("tracking_phase") or "unknown"
                seg_km = calculate_distance(prev["lat"], prev["lng"], curr["lat"], curr["lng"])

                t_prev = parse_iso_utc(prev.get("timestamp"))
                t_curr = parse_iso_utc(curr.get("timestamp"))
                delta = None
                if t_prev and t_curr:
                    delta = (t_curr - t_prev).total_seconds()
                    if delta is not None and delta > max_segment_gap_s:
                        max_segment_gap_s = int(round(delta))

                # Reject anomalous segments before adding to phase totals.
                if seg_km > MAX_SEG_KM:
                    rejected_segments += 1
                    continue
                if delta is not None and delta > MAX_SEG_GAP_S:
                    rejected_segments += 1
                    continue
                if delta is not None and delta > 0:
                    seg_kmh = seg_km / (delta / 3600.0)
                    if seg_kmh > MAX_SEG_KMH:
                        rejected_segments += 1
                        continue

                phase_totals[phase] = phase_totals.get(phase, 0.0) + seg_km
                # Duration: only count if the gap is reasonable (< 5 min)
                # to avoid one stale breadcrumb inflating a phase by hours.
                if delta is not None and 0 < delta <= 300:
                    phase_secs[phase] = phase_secs.get(phase, 0.0) + delta
            if rejected_segments:
                logger.info(
                    f"Ride {ride_id}: dropped {rejected_segments}/{len(all_breadcrumbs) - 1} "
                    "GPS segments as anomalous (speed/distance/gap caps)"
                )
            phase_distances = {k: round(v, 3) for k, v in phase_totals.items()}
            phase_durations = {k: int(round(v)) for k, v in phase_secs.items()}

            # Actual distance = trip_in_progress only (the paid portion).
            # Guard against sparse GPS: if fewer than 5 trip_in_progress
            # points were recorded the haversine sum is essentially just
            # a straight-line from pickup to dropoff (equivalent to the
            # booking-time haversine). In that case keep planned_distance
            # so the displayed km matches what the fare was calculated on,
            # rather than showing a misleadingly short GPS value.
            trip_points_count = sum(1 for b in all_breadcrumbs if b.get("tracking_phase") == "trip_in_progress")
            actual_distance_km = round(phase_distances.get("trip_in_progress", 0.0), 2)
            if trip_points_count < 5:
                logger.warning(
                    f"Ride {ride_id}: only {trip_points_count} trip_in_progress GPS points "
                    f"— GPS data too sparse for accurate distance; keeping planned={planned_distance}km"
                )
                actual_distance_km = planned_distance
            elif actual_distance_km == 0:
                # >= 5 points recorded but every segment was rejected by the
                # speed/distance/gap caps (e.g. GPS dead zone, spoofed trace).
                logger.warning(
                    f"Ride {ride_id}: {trip_points_count} trip_in_progress GPS points but all "
                    f"segments rejected by anomaly filter — keeping planned={planned_distance}km"
                )
                actual_distance_km = planned_distance

            pickup_to_driver_km = round(phase_distances.get("navigating_to_pickup", 0.0), 2)

            # Road-snapped recompute (P2): the haversine sum above is already
            # spike-protected by the speed/distance/gap caps, but it still
            # approximates straight-line distance between consecutive pings,
            # missing road curvature and turns. Roads API snapToRoads with
            # interpolate=true gives us the actual road-network distance and
            # respects the driver's chosen route (detours included), so it's
            # the structurally correct billable distance.
            #
            # When the recompute succeeds AND its value is within sanity range
            # of the haversine baseline (1/3× to 3×), it wins. Otherwise we
            # log the discrepancy and stick with the haversine value — Maps
            # outage or an empty response can't be allowed to corrupt billing.
            actual_distance_km_haversine = actual_distance_km
            actual_distance_km_road = None
            road_result = None
            try:
                try:
                    from ...utils.route_distance import compute_road_route
                except ImportError:
                    from utils.route_distance import compute_road_route  # type: ignore
                # Hard deadline: settlement targets <1s P95 and an unbounded
                # provider call here (OSRM/Google over up to 10k breadcrumbs)
                # previously held the driver's Complete tap for as long as the
                # provider hung. On timeout we settle on the spike-protected
                # haversine value — the same fallback as a provider error, and
                # already an accepted billable baseline (the 1/3×–3× sanity
                # band treats haversine as the reference).
                road_result = await asyncio.wait_for(compute_road_route(all_breadcrumbs), timeout=1.5)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[complete_ride] road-snap recompute exceeded 1.5s for ride {ride_id}; keeping haversine"
                )
            except Exception:
                logger.warning(
                    "[complete_ride] road-snap recompute raised; keeping haversine",
                    exc_info=True,
                )
            if road_result is not None:
                actual_distance_km_road = road_result["distance_km"]
                lo = max(0.1, actual_distance_km_haversine / 3.0)
                hi = max(0.1, actual_distance_km_haversine * 3.0)
                if lo <= actual_distance_km_road <= hi:
                    actual_distance_km = round(actual_distance_km_road, 2)
                    road_distance_provider = str(road_result.get("provider") or "road_snapped")
                    # Trusted match → persist the road-snapped geometry (saved to
                    # ride_routes below) for SGI / dispute map review.
                    road_polyline = road_result.get("polyline") or []
                else:
                    logger.warning(
                        f"Ride {ride_id}: road-snap distance {actual_distance_km_road}km "
                        f"out of sanity range [{lo:.2f}, {hi:.2f}] from haversine "
                        f"{actual_distance_km_haversine}km — keeping haversine value"
                    )

            # The PICKUP-leg road-snap (Phase 2, driver→pickup) is display-only
            # (admin map) and does NOT feed billing, so it must NOT add a second
            # provider round-trip to the /complete hot path. It is backgrounded
            # after settlement (see _snap_pickup_leg_async below); road_polyline_pickup
            # stays empty here and the column is backfilled best-effort.

            # Per-phase polylines for SGI / dispute tooling. Each phase is
            # downsampled to MAX_PER_PHASE points so a long trip's payload
            # stays bounded. Stored as [lat, lng, iso_ts] tuples so the
            # admin can replay the trip with timing on the detail map.
            MAX_PER_PHASE = 150
            phases_to_split = ("navigating_to_pickup", "trip_in_progress")
            for phase in phases_to_split:
                pts = [b for b in all_breadcrumbs if b.get("tracking_phase") == phase]
                if not pts:
                    continue
                step = max(1, len(pts) // MAX_PER_PHASE)
                sampled = pts[::step]
                if sampled and sampled[-1] is not pts[-1]:
                    sampled.append(pts[-1])
                phase_polylines[phase] = [
                    [
                        round(p["lat"], 6),
                        round(p["lng"], 6),
                        str(p.get("timestamp") or ""),
                    ]
                    for p in sampled
                ]

            # Trip-leg polyline for the static map snapshot (route_snapshot_url)
            # ONLY — kept in-memory, not persisted to rides (geometry now lives in
            # ride_routes). [[lat, lng, phase], ...]. The navigating_to_pickup leg
            # is excluded: the receipt map shows the travelled pickup→dropoff
            # route only (drawing both legs read as two routes on the snapshot).
            trip_points = [b for b in all_breadcrumbs if b.get("tracking_phase") == "trip_in_progress"]
            if trip_points:
                MAX_POINTS = 200
                step = max(1, len(trip_points) // MAX_POINTS)
                sampled = trip_points[::step]
                if sampled and sampled[-1] is not trip_points[-1]:
                    sampled.append(trip_points[-1])
                route_polyline = [
                    [
                        round(p["lat"], 6),
                        round(p["lng"], 6),
                        p.get("tracking_phase", ""),
                    ]
                    for p in sampled
                ]

            rejected_ratio = rejected_segments / max(1, len(all_breadcrumbs) - 1)
            if trip_points_count >= 20 and rejected_ratio <= 0.1 and max_segment_gap_s <= 120:
                confidence = "high"
            elif trip_points_count >= 5 and rejected_ratio <= 0.25 and max_segment_gap_s <= 300:
                confidence = "medium"
            else:
                confidence = "low"
            route_quality = {
                "confidence": confidence,
                "gps_points_count": gps_points_count,
                "trip_points_count": trip_points_count,
                "rejected_segments": rejected_segments,
                "rejected_segment_ratio": round(rejected_ratio, 3),
                "max_segment_gap_seconds": max_segment_gap_s,
                "distance_provider": road_distance_provider,
                "actual_distance_km_haversine": (
                    round(float(actual_distance_km_haversine), 3) if actual_distance_km_haversine is not None else None
                ),
                "actual_distance_km_road_snapped": (
                    round(float(actual_distance_km_road), 3) if actual_distance_km_road is not None else None
                ),
                "road_snap_accepted": bool(road_polyline),
            }

    except Exception as e:
        logger.error(f"Could not aggregate GPS data for ride {ride_id}: {e}", exc_info=True)

    # Persist the heavy route geometry to the ride_routes side-table (1:1),
    # keeping it OFF the hot rides row — written once here, read on-demand by the
    # admin map modal. Best-effort: a settlement must not fail on the side write.
    route_payload = {
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "phase_polylines": phase_polylines,
        "road_polyline": road_polyline,
        "road_polyline_pickup": road_polyline_pickup,
        "gps_points_count": gps_points_count,
        "route_quality": route_quality,
        "save_status": "saved",
        "save_error": None,
        "computed_at": datetime.now(timezone.utc),
    }
    for attempt in range(1, 4):
        try:
            await db_supabase.update_one(
                "ride_routes",
                {"ride_id": ride_id},
                route_payload,
                upsert=True,
            )
            route_geometry_status = "saved"
            route_geometry_error = None
            break
        except Exception as exc:
            route_geometry_error = str(exc)[:500]
            route_geometry_status = "failed"
            logger.error(
                "Could not persist ride_routes for ride %s (attempt %s/3): %s",
                ride_id,
                attempt,
                route_geometry_error,
                exc_info=True,
            )
            if attempt < 3:
                await asyncio.sleep(0.2 * attempt)
    if route_geometry_status != "saved":
        try:
            await db_supabase.update_one(
                "rides",
                {"id": ride_id},
                {
                    "route_geometry_status": route_geometry_status,
                    "route_geometry_error": route_geometry_error,
                    "route_quality": route_quality,
                },
            )
        except Exception:
            logger.error("Could not record ride_routes persistence failure for ride %s", ride_id, exc_info=True)

    # ── Build update payload ──
    # P0-5: do NOT write payment_status here. The driver completing the
    # trip does not mean the rider's card has been charged. Leave
    # payment_status as whatever it was (typically "pending") and let
    # rides.py::process_payment be the single writer that flips it to
    # "paid" / "failed" / "requires_action" based on the real Stripe
    # outcome. Previously this hardcoded "completed" — not a valid
    # payment_status value, and it masked genuine failures from the
    # webhook dispatcher in webhooks.py.
    update_fields: Dict[str, Any] = {
        "status": RideStatus.COMPLETED,
        "ride_completed_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "planned_distance_km": planned_distance,
        "actual_distance_km": actual_distance_km,
        "pickup_to_driver_km": pickup_to_driver_km,
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "gps_points_count": gps_points_count,
        "route_quality": route_quality,
        "route_geometry_status": route_geometry_status,
        # Clear any previous failed-save message when a retry/re-completion saves
        # ride_routes successfully; otherwise admin list/detail can show a stale
        # error while the status is now "saved".
        "route_geometry_error": route_geometry_error,
    }

    # Check fare lock setting — when enabled, rider pays the booking-time
    # estimate regardless of actual distance/time (SK regulatory requirement).
    _fare_lock = False
    try:
        try:
            from ...settings_loader import get_app_settings as _gas
        except ImportError:
            from settings_loader import get_app_settings as _gas  # type: ignore
        _fl_settings = await _gas()
        _fare_lock = (_fl_settings or {}).get("fare_lock_enabled", False)
    except Exception:
        logger.debug("fare_lock_enabled check failed, defaulting to False", exc_info=True)

    if _fare_lock:
        update_fields["distance_km"] = actual_distance_km
        logger.info(
            f"Ride {ride_id}: fare_lock active — keeping booking-time fare. "
            f"planned={planned_distance}km actual={actual_distance_km}km"
        )
    elif actual_distance_km > 0 and abs(actual_distance_km - planned_distance) > 0.1:
        fare_adj = recalculate_fare_for_distance(ride, actual_distance_km)
        if fare_adj:
            update_fields.update(fare_adj)
            logger.info(
                f"Ride {ride_id}: fare recalculated on completion. "
                f"planned={planned_distance}km actual={actual_distance_km}km"
            )
    else:
        update_fields["distance_km"] = actual_distance_km

    # ── ride_metrics consolidated planned-vs-actual summary ──
    # Read cache assembled from existing data sources at completion:
    #   * Pickup-leg estimates: already written into ride.ride_metrics at acceptance.
    #   * Per-phase actual durations: derived from ride timestamps (the same
    #     transitions that drive driver_insurance_periods).
    #   * Distances: pickup leg from phase_distances/pickup_to_driver_km; trip
    #     leg from planned_distance_km / actual_distance_km.
    # driver_insurance_periods remains the regulatory system of record for
    # timing; this column is a read cache for the rider-app / admin detail UI.
    # Failure here must not block ride completion.
    try:

        def _minutes_between(start, end) -> int | None:
            start_dt = parse_iso_utc(start) if isinstance(start, str) else start
            end_dt = parse_iso_utc(end) if isinstance(end, str) else end
            if not start_dt or not end_dt:
                return None
            secs = (end_dt - start_dt).total_seconds()
            if secs <= 0:
                return None
            return max(1, int(round(secs / 60)))

        nav_minutes = _minutes_between(ride.get("driver_accepted_at"), ride.get("driver_arrived_at"))
        wait_minutes = _minutes_between(ride.get("driver_arrived_at"), ride.get("ride_started_at"))
        trip_minutes = _minutes_between(ride.get("ride_started_at"), update_fields["ride_completed_at"])

        existing_metrics = ride.get("ride_metrics") or {}
        nav_phase = dict((existing_metrics.get("phases") or {}).get("navigating_to_pickup") or {})
        # Actuals overwrite anything previously written; estimates are preserved.
        nav_phase["actual_distance_km"] = round(float(pickup_to_driver_km or 0), 3)
        if nav_minutes is not None:
            nav_phase["actual_duration_minutes"] = nav_minutes

        trip_phase: Dict[str, Any] = {
            "estimated_distance_km": round(float(planned_distance or 0), 3),
            "estimated_duration_minutes": int(ride.get("duration_minutes") or 0) or None,
            "actual_distance_km": round(float(actual_distance_km or 0), 3),
            # P2: ops visibility — both raw computations stored so a regression
            # in either path (haversine spike filter, or Roads API outage) can
            # be diagnosed from a single ride row. actual_distance_km above is
            # the canonical billed value.
            "actual_distance_km_haversine": (
                round(float(actual_distance_km_haversine), 3) if actual_distance_km_haversine is not None else None
            ),
            "actual_distance_km_road_snapped": (
                round(float(actual_distance_km_road), 3) if actual_distance_km_road is not None else None
            ),
        }
        if trip_minutes is not None:
            trip_phase["actual_duration_minutes"] = trip_minutes
        # Drop the estimate keys when we genuinely have no value, rather than
        # writing 0 / None that the UI would render as "0 km" / "0 min".
        trip_phase = {k: v for k, v in trip_phase.items() if v not in (None, 0, 0.0)}

        wait_phase: Dict[str, Any] = {}
        if wait_minutes is not None:
            wait_phase["actual_duration_minutes"] = wait_minutes

        phases: Dict[str, Any] = {"navigating_to_pickup": nav_phase, "trip_in_progress": trip_phase}
        if wait_phase:
            phases["arrived_at_pickup"] = wait_phase

        def _sum(field: str) -> float | int | None:
            vals = [p.get(field) for p in phases.values() if p.get(field) is not None]
            if not vals:
                return None
            if all(isinstance(v, int) for v in vals):
                return sum(vals)
            return round(sum(float(v) for v in vals), 3)

        totals = {
            "estimated_distance_km": _sum("estimated_distance_km"),
            "estimated_duration_minutes": _sum("estimated_duration_minutes"),
            "actual_distance_km": _sum("actual_distance_km"),
            "actual_duration_minutes": _sum("actual_duration_minutes"),
        }
        totals = {k: v for k, v in totals.items() if v is not None}

        update_fields["ride_metrics"] = {"phases": phases, "totals": totals}
    except Exception as metrics_err:
        logger.error(
            "complete_ride: ride_metrics assembly failed for ride %s: %s",
            ride_id,
            metrics_err,
            exc_info=True,
        )

    # Atomic completion guard: filter on status=in_progress so a concurrent
    # complete/cancel that won the race after the read above matches zero rows
    # instead of writing a second completion (same CAS pattern as ride
    # acceptance filtering on status='searching').
    _complete_filters = {"id": ride_id, "driver_id": driver["id"], "status": RideStatus.IN_PROGRESS}
    try:
        _updated_ride_row = await db_supabase.update_one("rides", _complete_filters, update_fields)
    except Exception as e:
        # Some columns may not exist yet in older deployments. Retry with only
        # the essential fields so ride completion never fails. run_sync wraps the
        # PostgREST error, so match the real text + structured code, not str(e)
        # (the generic "Database operation failed" sentinel never contains it).
        err_msg = db_error_text(e)
        if pg_error_code(e).upper() == "PGRST204" or "column" in err_msg or "pgrst204" in err_msg:
            logger.warning(f"Retrying ride update with minimal fields: {e}")
            safe_keys = {
                "status",
                "ride_completed_at",
                "payment_status",
                "updated_at",
                "distance_km",
            }
            safe_updates = {k: v for k, v in update_fields.items() if k in safe_keys}
            _updated_ride_row = await db_supabase.update_one("rides", _complete_filters, safe_updates)
        else:
            raise
    if _updated_ride_row is None:
        raise RideStateError(
            f"Ride {ride_id} is no longer in_progress — completion already processed by a concurrent request"
        )

    # Corporate guest rides settle server-side: the guest customer has no app
    # and never calls /process-payment. Fire-and-forget — the atomic
    # pending→processing claim lives inside auto_settle_guest_corporate, and
    # the payment-retry sweep re-drives it if this task dies with the process.
    if ride.get("guest_booking") and ride.get("payment_method") == "company_allowance":
        try:
            from ...services.payment_service import auto_settle_guest_corporate
        except ImportError:
            from services.payment_service import auto_settle_guest_corporate  # type: ignore
        spawn(auto_settle_guest_corporate(ride_id))

    # ── Record incentive claims ──────────────────────────────────
    # Fetch active incentives matching this ride's service area / vehicle type
    # and insert claim records into ride_incentive_claims. driver_earnings in
    # the rides table is NOT updated here — it holds the fare-only amount
    # (base + distance + time). The bonus is summed from ride_incentive_claims
    # at read time by get_ride() and exposed as incentive_amount / total_earned.
    _total_bonus = Decimal("0")
    try:
        sa_id = ride.get("service_area_id")
        vt_id = ride.get("vehicle_type_id")
        iq = (
            db_supabase.supabase.table("ride_incentives")
            .select("id, bonus_amount, vehicle_type_id")
            .eq("is_active", True)
        )
        if sa_id:
            iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
        else:
            iq = iq.is_("service_area_id", "null")
        inc_result = await db_supabase.run_sync(iq.execute)
        for inc in inc_result.data or []:
            if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
                continue
            ba = Decimal(str(inc.get("bonus_amount") or 0))
            if ba <= 0:
                continue
            await db_supabase.insert_one(
                "ride_incentive_claims",
                {
                    "id": str(uuid.uuid4()),
                    "ride_id": ride_id,
                    "driver_id": driver["id"],
                    "incentive_id": inc["id"],
                    "bonus_amount": float(ba.quantize(Decimal("0.01"))),
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            _total_bonus += ba
        if _total_bonus > 0:
            logger.info(
                "complete_ride: claimed %s incentive bonus for ride %s (driver %s)",
                float(_total_bonus.quantize(Decimal("0.01"))),
                ride_id,
                driver["id"],
            )
    except Exception as inc_err:
        logger.error(
            "complete_ride: incentive claim failed for ride %s: %s",
            ride_id,
            inc_err,
            exc_info=True,
        )

    # ── Driver earnings snapshot (JSONB) ───────────────────────
    # Freeze the full breakdown so totals never drift from recomputation.
    # _total_bonus comes from the incentive block above; default to 0 if
    # the variable wasn't set (incentive block errored before assignment).
    try:
        _fare_d = (
            to_decimal(update_fields.get("base_fare") or ride.get("base_fare") or 0)
            + to_decimal(update_fields.get("distance_fare") or ride.get("distance_fare") or 0)
            + to_decimal(update_fields.get("time_fare") or ride.get("time_fare") or 0)
        )
        _snapshot = build_earnings_snapshot(
            fare=_fare_d,
            tip=ride.get("tip_amount") or 0,
            incentive=_total_bonus,
            tax=ride.get("tax_amount") or 0,
            cancel_fee=ride.get("cancellation_fee_driver") or 0,
        )
        await db_supabase.update_one("rides", {"id": ride_id}, {"driver_earnings_snapshot": _snapshot})
    except Exception:
        logger.error("complete_ride: driver_earnings_snapshot failed for ride %s", ride_id, exc_info=True)

    # Post-ride receipt notification stub
    rider = await db_supabase.get_user_by_id(ride.get("rider_id"))
    if rider and rider.get("email"):
        logger.info(f"Sending email receipt for ride {ride_id} (rider_id={rider.get('id')})")

    # Fire-and-forget: render the route PNG from phase_polylines and
    # upload to Supabase Storage so the admin drawer + email receipt can
    # embed a permanent image URL. Only regenerate if we have enough GPS
    # data to produce a meaningful route — otherwise preserve the planned-
    # route snapshot from creation (which used the Google Directions polyline).
    # A road-snapped trip geometry (sanity-gated above) always qualifies; a raw
    # breadcrumb trail needs >= 10 trip_in_progress points, mirroring the
    # renderer's own sparsity guard. Below that the Static Maps API draws
    # straight chords between the few points — the "straight line P→D next to
    # the real path" snapshot artifact — and the planned-route image from
    # creation is more accurate than the GPS trace.
    trip_points_for_snapshot = sum(
        1
        for b in (all_breadcrumbs if "all_breadcrumbs" in locals() else [])
        if b.get("tracking_phase") == "trip_in_progress"
    )
    has_gps_trail = bool(road_polyline) or trip_points_for_snapshot >= 10
    if has_gps_trail:
        # Prefer the road-snapped trip polyline: it follows the road network
        # even where raw GPS was sparse. phase_polylines is dropped in that
        # case so the renderer can't pick the raw trail over it.
        spawn(
            _shared._generate_and_store_ride_snapshot(
                ride_id=ride_id,
                pickup_lat=ride.get("pickup_lat"),
                pickup_lng=ride.get("pickup_lng"),
                dropoff_lat=ride.get("dropoff_lat"),
                dropoff_lng=ride.get("dropoff_lng"),
                phase_polylines=None if road_polyline else phase_polylines,
                route_polyline=road_polyline or route_polyline,
            )
        )
    else:
        logger.info(
            f"Ride {ride_id}: skipping completion snapshot ({trip_points_for_snapshot} GPS points) "
            "— planned-route snapshot from creation is preserved."
        )

    # Fire-and-forget: validate GPS trace against road network.
    # Flags spoofed trips for admin review without blocking completion.
    _breadcrumbs_for_validation = all_breadcrumbs if "all_breadcrumbs" in locals() else []
    spawn(_validate_ride_route(ride_id, _breadcrumbs_for_validation, driver["id"]))
    # Fire-and-forget: road-snap the pickup leg for the admin map (display-only;
    # must not block completion on a provider round-trip).
    spawn(_snap_pickup_leg_async(ride_id, _breadcrumbs_for_validation))

    # Update driver stats. Setting is_available=True is safe here because the
    # ride has just transitioned to `completed`, and the driver's row already
    # has is_online=True (a driver cannot be on an active trip while offline).
    # See update_driver_status docstring for the is_online/is_available invariant.
    await db_supabase.set_driver_available(driver["id"], available=True, total_rides_inc=1)
    # M-5: SGI insurance period audit — ride completed, driver returns to
    # period 1 (still online, no ride). No ride_id on period 1.
    await _deps.record_period_transition(driver["id"], 1)

    # Daily Spinr Pass allowance: if this completion used the driver's last ride
    # for the day, flip them offline now (DB-level, so dispatch stops offering)
    # until the allowance resets at the next local midnight. The driver WS
    # notice is sent at the end, after the ride_completed events, so the app
    # doesn't reset its completion UI prematurely.
    try:
        from ...utils.spinr_pass import force_offline_if_exhausted
    except ImportError:
        from utils.spinr_pass import force_offline_if_exhausted  # type: ignore
    try:
        _quota_offline = await force_offline_if_exhausted(driver)
    except Exception:
        _quota_offline = None
        logger.error("complete_ride: quota offline check failed for driver=%s", driver["id"], exc_info=True)

    completed_ride = await db_supabase.get_ride(ride_id)

    if completed_ride and completed_ride.get("rider_id"):
        rider_bill = completed_ride.get("grand_total") or completed_ride.get("total_fare", ride.get("total_fare", 0))
        await _deps.manager.send_personal_message(
            {
                "type": "ride_completed",
                "ride_id": ride_id,
                "total_fare": completed_ride.get("total_fare", ride.get("total_fare", 0)),
                "grand_total": rider_bill,
            },
            f"rider_{completed_ride['rider_id']}",
        )
        # Backgrounded: FCM must not sit inside the <1s settlement SLA; the
        # awaited WS message above already drives the in-app transition.
        spawn(
            _deps.send_push_notification(
                completed_ride["rider_id"],
                "Ride Completed! ✅",
                f"Your ride has finished. Total fare: ${rider_bill}",
                data={"type": "ride_completed", "ride_id": str(ride_id)},
            )
        )

    total_fare = (completed_ride or {}).get("total_fare", ride.get("total_fare", 0))
    await _deps.manager.broadcast_ride_status(
        ride_id,
        RideStatus.COMPLETED,
        rider_id=(completed_ride or {}).get("rider_id"),
        total_fare=total_fare,
    )
    # End the rider's live activity on trip completion.
    spawn(send_live_activity_update(completed_ride or {"id": ride_id, "status": RideStatus.COMPLETED}, EVENT_END))
    # Keep the specific ``ride_completed`` event on admin too for dashboards
    # that switch directly on the event name rather than status.
    try:
        await _deps.manager.broadcast_to_admins(
            {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare}
        )
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"complete_ride: admin broadcast failed: {_exc}")

    # Advance any active driver quests this completion contributes to. Runs once
    # per ride because the atomic in_progress→completed guard above lets only the
    # winning completion path reach here. Scheduled as a background task so the
    # per-quest queries/updates never block the completion response (the ride is
    # already completed); the tracker swallows its own errors internally.
    try:
        try:
            from ...utils.quest_tracker import update_quest_progress_on_ride_complete
        except ImportError:
            from utils.quest_tracker import update_quest_progress_on_ride_complete
        spawn(update_quest_progress_on_ride_complete(driver["id"], completed_ride or ride))
    except Exception:
        logger.error("complete_ride: scheduling quest progress update failed for ride %s", ride_id, exc_info=True)

    # Notify the driver (and admins) if this completion took them offline for
    # the day. Sent last so the app handles ride_completed first. Reuses the
    # existing 'auto_offline' client handler (stops offer sound, flips offline).
    if _quota_offline and driver.get("user_id"):
        _reset_h = round(_quota_offline.get("hours_until_reset") or 0)
        try:
            await _deps.manager.send_personal_message(
                {
                    "type": "auto_offline",
                    "reason": "quota_exhausted",
                    "message": (
                        f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for "
                        f"today. You're now offline — your allowance resets in about {_reset_h}h."
                    ),
                    "quota_resets_at": _quota_offline.get("quota_resets_at"),
                },
                f"driver_{driver['user_id']}",
            )
            await _deps.manager.broadcast_to_admins(
                {"type": "driver_status_changed", "driver_id": driver["id"], "is_online": False}
            )
        except Exception:
            logger.warning("complete_ride: quota auto_offline notify failed for driver=%s", driver["id"])
        # Push so the driver sees it even with the app backgrounded.
        # Backgrounded off the settlement response; send_push_notification
        # handles its own failures.
        spawn(
            _deps.send_push_notification(
                driver["user_id"],
                "Daily ride limit reached",
                (
                    f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for today. "
                    f"You're now offline — your allowance resets in about {_reset_h}h."
                ),
                data={"type": "quota_exhausted", "driver_id": str(driver["id"])},
                target_app="driver",
            )
        )

    response = serialize_doc(completed_ride)
    response["location_ack"] = completion_location.location_ack
    response["legacy_client_missing_tail"] = completion_location.legacy_client_missing_tail
    response["completion_distance_band"] = completion_location.distance_band
    response["completion_fix_rejection"] = completion_location.completion_fix_rejection
    return response
