"""Shared helpers (PII vault, ride-state guard, snapshots) for drivers submodules.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    ROUND_HALF_UP,
    Any,
    BaseModel,
    Decimal,
    Dict,
    HTTPException,
    RideStatus,
    asyncio,
    db_supabase,
    hashlib,
    logger,
)

_TWO_PLACES = Decimal("0.01")


def _d(v) -> Decimal:
    """Parse a money value to an exact 2-dp Decimal. Money is Decimal-only —
    never accumulate fares/bonuses/payouts as float."""
    from decimal import InvalidOperation

    try:
        return Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


def _money_str(v) -> str:
    """Serialise a money value as an exact 2-dp Decimal string (never float)."""
    from decimal import InvalidOperation

    try:
        return str(Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, InvalidOperation):
        return "0.00"


def _ride_income(r: dict) -> Decimal:
    """A completed ride's driver income as a Decimal — the canonical
    driver_earnings, falling back to the fare components only for legacy rows
    that predate that column. Shared by the earnings + T4A summaries."""
    if r.get("driver_earnings") is not None:
        return _d(r.get("driver_earnings"))
    return _d(r.get("base_fare")) + _d(r.get("distance_fare")) + _d(r.get("time_fare")) + _d(r.get("tip_amount"))


# ── Vault encryption for driver PII (P2-5) ───────────────────────────────────
# licence_number and vehicle_vin live in plain TEXT columns, but the values
# stored there are vault.secrets UUIDs, not plaintext — actual ciphertext is
# held in vault.secrets under the drivers_pii_key pgsodium key.
# The application passes plaintext to encrypt_driver_pii() before writing and
# calls decrypt_driver_pii() after reading. Both are Postgres functions created
# by migration 32_encrypt_sensitive_fields.sql and exposed via Supabase RPC.
# Transparent column encryption (vault.encrypted_text) was removed by Supabase
# in mid-2024; we intentionally keep the columns as TEXT and encrypt explicitly.

_VAULT_PII_FIELDS: frozenset = frozenset({"license_number", "vehicle_vin"})


async def _vault_encrypt(value: str, hint: str = "") -> str:
    """Encrypt a PII string via Supabase Vault (encrypt_driver_pii RPC).

    Fail-closed: any failure raises 503 rather than storing plaintext PII.
    Storing unencrypted license numbers or VINs is a PIPEDA violation.
    """
    if not value:
        return value
    try:
        from supabase_client import supabase as _sb  # type: ignore[import]
    except ImportError as exc:
        logger.error(
            "vault_encrypt: supabase_client unavailable for %s — refusing to store plaintext",
            hint,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc
    if not _sb:
        logger.error(
            "vault_encrypt: Supabase client not initialised for %s — refusing to store plaintext",
            hint,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable")
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc("encrypt_driver_pii", {"plaintext": value}).execute())
        if not res.data:
            logger.error(
                "vault_encrypt: RPC returned no data for %s — refusing to store plaintext",
                hint,
            )
            raise HTTPException(status_code=503, detail="Encryption service unavailable")
        return str(res.data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "vault_encrypt: RPC failed for %s — refusing to store plaintext",
            hint,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc


async def _vault_decrypt(value: str, hint: str = "") -> str:
    """Decrypt a Vault-encrypted PII token via Supabase RPC (decrypt_driver_pii).

    On failure, returns the raw token rather than raising — the encrypted token
    is not PII, so this degrades to unreadable data rather than a privacy leak.
    """
    if not value:
        return value
    try:
        from supabase_client import supabase as _sb  # type: ignore[import]
    except ImportError:
        return value
    if not _sb:
        return value
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc("decrypt_driver_pii", {"secret_id": value}).execute())
        return str(res.data) if res.data else value
    except Exception:
        logger.error(
            "vault_decrypt: RPC failed for %s — returning raw token",
            hint,
            exc_info=True,
        )
        return value


async def _encrypt_driver_pii(payload: dict) -> dict:
    """Encrypt vault PII fields in a write payload before sending to the DB."""
    out = dict(payload)
    for field in _VAULT_PII_FIELDS:
        if field in out and out[field]:
            out[field] = await _vault_encrypt(str(out[field]), field)
    return out


async def _decrypt_driver_pii(driver: dict) -> dict:
    """Decrypt vault PII fields in a driver record returned from the DB."""
    out = dict(driver)
    for field in _VAULT_PII_FIELDS:
        if field in out and out[field]:
            out[field] = await _vault_decrypt(str(out[field]), field)
    return out


class RideOTPRequest(BaseModel):
    otp: str


# ── Ride state-machine guards ─────────────────────────────────────────────────
# Explicit source-state allowlists for each driver-initiated transition.
# A ride can only be moved forward from one of the allowed states; attempts
# to transition out of a terminal state (completed/cancelled) or out of order
# are rejected with 409 Conflict. This prevents:
#   • completing a ride that was never started → phantom charge
#   • restarting a cancelled ride
#   • marking "arrived" on a completed ride
# Idempotent destination states are included to make retries safe.
ARRIVE_FROM_STATES = (
    RideStatus.DRIVER_ASSIGNED,
    RideStatus.DRIVER_ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
)
# verify-otp and start both move driver_arrived → in_progress.
# in_progress is idempotent for both (retry after network blip).
START_FROM_STATES = (RideStatus.DRIVER_ARRIVED, RideStatus.IN_PROGRESS)
COMPLETE_FROM_STATES = (RideStatus.IN_PROGRESS,)


async def _generate_and_store_ride_snapshot(
    *,
    ride_id: str,
    pickup_lat,
    pickup_lng,
    dropoff_lat,
    dropoff_lng,
    phase_polylines,
    route_polyline,
    route_segments=None,
    completion_point=None,
    route_quality=None,
    route_revision=None,
    finalized_at=None,
) -> None:
    """Render the ride's route PNG and upload to Supabase Storage.

    Uses Google Static Maps API for high-quality map tiles with the route
    polyline drawn server-side. Falls back to OSM/staticmap if the Google
    API key is unavailable.

    V2 actual-route images use the private ``ride-route-snapshots`` bucket.
    Legacy planned images remain in the established public ``ride-snapshots``
    bucket until their readers are migrated.
    See backend/docs/STORAGE_BUCKETS.md for one-time setup.
    """
    if pickup_lat is None or pickup_lng is None or dropoff_lat is None or dropoff_lng is None:
        logger.warning(f"Snapshot skipped for ride {ride_id}: missing coordinates")
        return
    poly_len = len(route_polyline) if isinstance(route_polyline, list) else 0
    segment_count = len(route_segments) if isinstance(route_segments, list) else 0
    phase_keys = list((phase_polylines or {}).keys()) if phase_polylines else []
    logger.info(
        f"Snapshot pipeline start for ride {ride_id}: route_polyline={poly_len} pts, route_segments={segment_count}, phase_polylines={phase_keys}"
    )
    try:
        try:
            from ...core.config import settings
            from ...settings_loader import get_app_settings
            from ...supabase_client import supabase  # type: ignore
            from ...utils.route_snapshot import render_ride_snapshot, render_ride_snapshot_google
        except ImportError:
            from core.config import settings  # type: ignore
            from settings_loader import get_app_settings  # type: ignore
            from supabase_client import supabase  # type: ignore
            from utils.route_snapshot import render_ride_snapshot, render_ride_snapshot_google  # type: ignore

        png_bytes = None
        loop = asyncio.get_event_loop()

        # Try Google Static Maps first (proper Google Maps tiles + polyline)
        try:
            app_settings = await get_app_settings() or {}
            gmap_key = app_settings.get("google_maps_api_key") or ""
            if gmap_key:
                png_bytes = await render_ride_snapshot_google(
                    api_key=gmap_key,
                    pickup_lat=float(pickup_lat),
                    pickup_lng=float(pickup_lng),
                    dropoff_lat=float(dropoff_lat),
                    dropoff_lng=float(dropoff_lng),
                    phase_polylines=phase_polylines,
                    route_polyline=route_polyline,
                    route_segments=route_segments,
                    completion_point=completion_point,
                    route_quality=route_quality,
                )
                logger.info(
                    f"Google Static Maps for ride {ride_id}: "
                    f"{'success' if png_bytes else 'returned None'} "
                    f"({len(png_bytes) if png_bytes else 0} bytes)"
                )
            else:
                logger.warning(f"No Google Maps API key found for ride {ride_id} snapshot")
        except Exception as google_exc:
            logger.warning(f"Google Static Maps failed for ride {ride_id}, trying OSM fallback: {google_exc}")

        # Fallback to OSM/staticmap
        if not png_bytes:
            png_bytes = await loop.run_in_executor(
                None,
                lambda: render_ride_snapshot(
                    pickup_lat=float(pickup_lat),
                    pickup_lng=float(pickup_lng),
                    dropoff_lat=float(dropoff_lat),
                    dropoff_lng=float(dropoff_lng),
                    phase_polylines=phase_polylines,
                    route_polyline=route_polyline,
                    route_segments=route_segments,
                    completion_point=completion_point,
                    route_quality=route_quality,
                ),
            )

        if not png_bytes:
            return

        # Supabase Storage upload. Legacy snapshots retain their stable path;
        # finalized v2 snapshots are revisioned and immutable so a delayed GPS
        # upload cannot replace a receipt image for an older route revision.
        revision = int(route_revision) if route_revision is not None else 0
        bucket = "ride-route-snapshots" if revision > 0 else "ride-snapshots"
        storage_path = f"{ride_id}/route-v{revision}.png" if revision > 0 else f"ride_{ride_id}.png"
        try:
            await loop.run_in_executor(
                None,
                lambda: supabase.storage.from_(bucket).upload(
                    path=storage_path,
                    file=png_bytes,
                    file_options={
                        "content-type": "image/png",
                        # supabase-py serialises bools as JSON, so the string
                        # form is what ends up on the wire; lowercase "true".
                        "upsert": "true",
                        # Cache effectively forever (1 year, the HTTP max
                        # per RFC 9111). Snapshots are immutable per ride;
                        # a regenerated snapshot may never be re-fetched by
                        # clients that already cached it, which is accepted.
                        "cache-control": "31536000",
                    },
                ),
            )
        except Exception as upload_exc:
            logger.error(
                f"Supabase Storage upload failed for ride {ride_id}: {upload_exc}",
                exc_info=True,
            )
            return

        if revision > 0:
            if finalized_at is None:
                logger.error("route snapshot finalization token missing for ride %s", ride_id)
                return
            # Persist only the private object path. Readers create a short-lived
            # signed URL after authorizing the rider, driver, admin, or receipt.
            try:
                updated = await db_supabase.update_one(
                    "ride_routes",
                    {"ride_id": ride_id, "route_revision": revision, "finalized_at": finalized_at},
                    {
                        "snapshot_object_path": storage_path,
                        "snapshot_url": None,
                        "snapshot_revision": revision,
                    },
                    upsert=False,
                )
                if updated is None:
                    # A newer evidence batch invalidated this revision before
                    # the upload completed. Delete the unreachable object so it
                    # cannot outlive the current route evidence.
                    await loop.run_in_executor(
                        None,
                        lambda: supabase.storage.from_(bucket).remove([storage_path]),
                    )
                return
            except Exception as exc:
                logger.error(
                    f"private route snapshot reference write failed for ride {ride_id}: {exc}",
                    exc_info=True,
                )
                raise

        base = (settings.SUPABASE_URL or "").rstrip("/")
        if not base:
            logger.error("SUPABASE_URL not configured; cannot build public snapshot URL")
            return
        # Cache-bust: the object is upserted to a STABLE filename with a 1-year
        # immutable cache-control, so a regenerated snapshot (e.g. the accurate
        # completion render replacing an earlier planned/buggy one) would never
        # be re-fetched by clients/CDNs that cached the old bytes — the stale
        # image (including a pre-fix duplicate-line render) would persist. A
        # content-hash query param changes only when the bytes change, forcing
        # a fresh fetch on regeneration while staying stable across identical
        # re-renders. Supabase Storage ignores the query string for object
        # resolution, so this resolves to the same upserted file.
        digest = hashlib.sha256(png_bytes).hexdigest()[:12]
        url = f"{base}/storage/v1/object/public/{bucket}/{storage_path}?v={digest}"

        # Legacy planned snapshots remain on rides.route_snapshot_url for
        # backward compatibility until all old readers are migrated.
        try:
            await db_supabase.update_one("rides", {"id": ride_id}, {"route_snapshot_url": url})
        except Exception as exc:
            logger.error(
                f"route snapshot reference write failed for ride {ride_id}: {exc}",
                exc_info=True,
            )
    except Exception as exc:
        logger.error(f"Ride snapshot pipeline failed for {ride_id}: {exc}", exc_info=True)


async def _snap_pickup_leg_async(ride_id: str, breadcrumbs: list) -> None:
    """Background task: road-snap the Phase 2 (driver→pickup) leg for the admin map.

    Display-only — kept OFF the /complete hot path so a slow OSRM/Google Roads
    provider can never delay a driver finishing a ride. Best-effort: backfills
    ride_routes.road_polyline_pickup (idempotent single-column update) once the
    snap returns; a failure simply leaves it empty and the admin map falls back
    to the raw Phase 2 GPS breadcrumbs.
    """
    if not breadcrumbs:
        return
    try:
        try:
            from ...utils.route_distance import compute_road_route
        except ImportError:
            from utils.route_distance import compute_road_route  # type: ignore
        result = await compute_road_route(breadcrumbs, phase="navigating_to_pickup")
        poly = (result or {}).get("polyline") or []
        if poly:
            # update-only (no upsert): if the main settlement geometry write
            # failed and no ride_routes row exists, we must NOT insert a partial
            # row — that would default save_status and mask the failed settlement
            # in the admin detail merge. No row => this backfill is a no-op.
            await db_supabase.update_one(
                "ride_routes", {"ride_id": ride_id}, {"road_polyline_pickup": poly}, upsert=False
            )
    except Exception:
        logger.warning("[complete_ride] pickup-leg road-snap backfill failed for ride %s", ride_id, exc_info=True)


async def _validate_ride_route(ride_id: str, breadcrumbs: list, driver_id: str) -> None:
    """Background task: validate GPS trace against road network post-completion."""
    if not breadcrumbs or len(breadcrumbs) < 5:
        return
    try:
        try:
            from ...utils.route_validation import validate_trip_route
        except ImportError:
            from utils.route_validation import validate_trip_route  # type: ignore

        result = await validate_trip_route(breadcrumbs)
        if not result:
            return

        # Store validation result on ride
        try:
            await db_supabase.update_one("rides", {"id": ride_id}, {"route_validation": result})
        except Exception as db_exc:
            logger.error(f"[route_validation] failed to store results on ride {ride_id}: {db_exc}", exc_info=True)

        if result["verdict"] in ("suspicious", "likely_spoofed"):
            logger.warning(
                "[route_validation] ride=%s driver=%s verdict=%s deviation=%.1f%%",
                ride_id,
                driver_id,
                result["verdict"],
                result["deviation_pct"],
            )
    except Exception as exc:
        logger.error(f"[route_validation] failed for ride {ride_id}: {exc}", exc_info=True)


async def _require_ride_in_state(ride_id: str, driver_id: str, allowed_states: tuple) -> Dict[str, Any]:
    """Load a driver's ride only if it is in one of ``allowed_states``.

    Raises 409 Conflict if the ride exists but is in a terminal or
    wrong state; raises 404 if the ride doesn't exist or isn't owned
    by this driver.
    """
    ride = await _deps.db.find_one(
        "rides",
        {
            "id": ride_id,
            "driver_id": driver_id,
            "status": {"$in": list(allowed_states)},
        },
    )
    if ride:
        return ride
    existing = await _deps.db.find_one("rides", {"id": ride_id, "driver_id": driver_id})
    if existing:
        current = existing.get("status", "unknown")
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ride is in status '{current}'; cannot perform this action "
                f"from that state (allowed: {list(allowed_states)})."
            ),
        )
    raise HTTPException(status_code=404, detail="Ride not found")


def serialize_doc(doc):
    return doc


_STRIP_FROM_SELF_RESPONSE = {"stripe_account_id", "bank_account", "fcm_token"}
