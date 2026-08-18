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
    ErrorCode,
    ErrorKeys,
    HTTPException,
    RideStatus,
    SpinrException,
    asyncio,
    datetime,
    db_supabase,
    hashlib,
    logger,
    timezone,
)

_TWO_PLACES = Decimal("0.01")

# Google Static Maps render failures defer-and-retry this many times (via the
# route finalizer's pending queue) before the explicit OSM last resort. Keeps
# every receipt on the one first-class renderer instead of freezing a random
# transient failure into a permanently different-looking snapshot.
_SNAPSHOT_MAX_GOOGLE_ATTEMPTS = 5


async def _defer_snapshot_retry(ride_id: str, route_revision, finalized_at) -> bool:
    """Re-queue route finalization so the Google snapshot render retries later.

    Returns True when the retry was scheduled. False means the caller must
    fall through (legacy unrevisioned pipeline, superseded revision, or the
    attempt budget is exhausted and the OSM last resort should render now).
    """
    from datetime import datetime, timedelta, timezone

    revision = int(route_revision) if route_revision is not None else 0
    if revision <= 0 or finalized_at is None:
        return False
    rows = await db_supabase.get_rows("ride_routes", {"ride_id": ride_id}, limit=1)
    route_row = rows[0] if rows else None
    if not route_row or int(route_row.get("route_revision") or 0) != revision:
        return False
    attempts = int(route_row.get("snapshot_attempts") or 0)
    if attempts >= _SNAPSHOT_MAX_GOOGLE_ATTEMPTS:
        return False
    retry_seconds = min(300, 15 * (2 ** min(attempts, 4)))
    try:
        updated = await db_supabase.update_one(
            "ride_routes",
            # Filter on revision + finalization token: a newer evidence batch
            # supersedes this retry the same way it supersedes a snapshot upload.
            {"ride_id": ride_id, "route_revision": revision, "finalized_at": finalized_at},
            {
                "processing_status": "pending",
                "processing_claimed_at": None,
                "next_retry_at": datetime.now(timezone.utc) + timedelta(seconds=retry_seconds),
                "snapshot_attempts": attempts + 1,
            },
            upsert=False,
        )
    except Exception:
        # The snapshot_attempts column (migration 243) may not be deployed
        # yet. Fall through to the OSM last resort so the receipt still
        # gets a route image.
        logger.warning(
            "snapshot defer-retry failed for ride %s (migration 243 may be missing)",
            ride_id,
            exc_info=True,
        )
        return False
    return updated is not None


def _route_snapshot_retention_due_at(finalized_at):
    """Return the exact calendar three-year GPS-retention deadline."""
    try:
        return finalized_at.replace(year=finalized_at.year + 3)
    except ValueError:
        # Feb 29 has no equivalent in a non-leap target year.
        return finalized_at.replace(year=finalized_at.year + 3, day=28)


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


def _ride_tax(r: dict) -> Decimal:
    """GST/PST collected on a ride, passed through to the driver as their
    income (not part of driver_earnings/fare components — see
    services/fare_service.py: driver_earnings = total_fare - admin_earnings,
    computed before tax is added on top). Falls back to the fare snapshot's
    tax lines for rows where tax_amount wasn't backfilled. Same rule as
    utils/driver_statement.py's _ride_tax (duplicated there because utils
    must not import from routes — keep the two in sync)."""
    tax = _d(r.get("tax_amount"))
    if tax != Decimal("0"):
        return tax
    snap = r.get("fare_breakdown_snapshot") or {}
    for line in snap.get("lines") or []:
        if line.get("type") in ("tax", "gst", "pst"):
            tax += _d(line.get("amount"))
    return tax


# ── Vault encryption for driver PII (P2-5) ───────────────────────────────────
# licence_number lives in a plain TEXT column, but the value stored there is a
# vault.secrets UUID, not plaintext — actual ciphertext is held in vault.secrets
# under the drivers_pii_key pgsodium key.
# The application passes plaintext to encrypt_driver_pii() before writing and
# calls decrypt_driver_pii() after reading. Both are Postgres functions created
# by migration 32_encrypt_sensitive_fields.sql and exposed via Supabase RPC.
# Transparent column encryption (vault.encrypted_text) was removed by Supabase
# in mid-2024; we intentionally keep the columns as TEXT and encrypt explicitly.
#
# vehicle_vin was moved OUT of this set (migration 244): it is now stored as
# plaintext and treated as mask-in-UI PII (shown masked, revealed with Show
# PII), like phone/plate — not encrypt-at-rest. license_number stays encrypted.

_VAULT_PII_FIELDS: frozenset = frozenset({"license_number"})

# Encrypted on write like the set above, but deliberately NOT decrypted on
# read — `_decrypt_driver_pii` ignores these.
#
# `license_number` is decrypted back into the driver's own profile response
# because a driver may legitimately re-read their own licence. A SIN is not
# that: it is collected once for T4A filing and has no reason to travel back
# over the wire on every profile poll. Collection and disclosure are separate
# decisions and only collection has been made — so there is no read path at
# all, rather than a read path nobody is watching. Admins see `sin_last4` and
# an on-file boolean, never the number.
#
# Adding a field here is the ONLY safe way to store write-once PII; putting it
# in `_VAULT_PII_FIELDS` instead would silently start returning it.
_VAULT_WRITE_ONLY_PII_FIELDS: frozenset = frozenset({"sin"})


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


async def _resolve_regulatory_defaults(service_area_id) -> tuple:
    """``(regulatory_authority, regulatory_region)`` for a newly-created
    driver row.

    ACTION_ITEMS.md B13: both real driver-creation write paths (``/register``
    and ``PUT /me``'s auto-create branch) left these two columns entirely
    unset, silently relying on `sgi_forms.py`'s NULL-passes-through
    grandfather allowance — which was only ever meant to cover the 22
    pre-existing legacy rows the original backfill (migration 265) closed,
    not a permanently-open gap. Confirmed live: 7 more NULL rows had
    accumulated in production by 2026-08-18, all real Saskatchewan drivers.

    Reuses the same resolver `driver_import_service.regulatory_authority_defaults`
    already uses for the bulk CSV-import path, so both write paths agree on
    one piece of logic. Falls back to the explicit single-market default
    (SGI/SK) only if that resolver still can't determine a region — e.g. no
    `service_area_id` was supplied at all — since every driver in production
    today is Saskatchewan-based (see B13's own note on `service_areas`'
    `regulatory_authority`/`province` columns still being NULL for every
    area but 'Regina', which the resolver route already gracefully handles
    via name-matching).
    """
    service_area: dict = {}
    if service_area_id:
        rows = await db_supabase.get_rows("service_areas", {"id": service_area_id}, limit=1)
        service_area = rows[0] if rows else {}
    try:
        from ...services.driver_import_service import regulatory_authority_defaults
    except ImportError:  # pragma: no cover - dual-import pattern
        from services.driver_import_service import regulatory_authority_defaults  # type: ignore

    authority, region = regulatory_authority_defaults({}, service_area)
    if not region:
        # The shared resolver couldn't determine a region at all (no
        # `service_area_id` supplied, or one that doesn't resolve to a real
        # row) — in that case it falls through to its own generic
        # "Provincial / municipal authority" placeholder, meant for a real
        # future non-SK province, not "we don't know". Every driver in
        # production today is Saskatchewan-based, so override BOTH fields
        # together rather than patching just the missing one — an
        # unresolved region means the authority it computed is wrong too.
        authority, region = "SGI", "SK"
    return authority, region


async def _encrypt_driver_pii(payload: dict) -> dict:
    """Encrypt vault PII fields in a write payload before sending to the DB.

    Covers both the round-trip set and the write-only set — every field that
    must never reach a database column as plaintext. `_vault_encrypt` is
    fail-closed, so a Vault outage raises 503 instead of writing a bare SIN.
    """
    out = dict(payload)
    for field in _VAULT_PII_FIELDS | _VAULT_WRITE_ONLY_PII_FIELDS:
        if field in out and out[field]:
            out[field] = await _vault_encrypt(str(out[field]), field)
    return out


async def _decrypt_driver_pii(driver: dict) -> dict:
    """Decrypt vault PII fields in a driver record returned from the DB.

    Round-trip set ONLY. `_VAULT_WRITE_ONLY_PII_FIELDS` is excluded on
    purpose — this function feeds the driver's own profile response, and a
    SIN has no business being in it.
    """
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

        # Renderer policy: Google Static Maps is the only first-class
        # renderer. With a key configured, a failed render DEFERS (finalizer
        # retry with backoff) instead of silently freezing the very different
        # OSM look into an immutable receipt image; OSM renders only as the
        # explicit last resort after the attempt budget, or when no key is
        # configured at all (deliberate deployment choice, not a failure).
        app_settings = await get_app_settings() or {}
        gmap_key = app_settings.get("google_maps_api_key") or ""
        if gmap_key:
            try:
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
            except Exception as google_exc:
                logger.error(f"Google Static Maps render failed for ride {ride_id}: {google_exc}", exc_info=True)
            if not png_bytes:
                if await _defer_snapshot_retry(ride_id, route_revision, finalized_at):
                    logger.error(f"Google snapshot render failed for ride {ride_id}; deferred for finalizer retry")
                    return
                try:
                    from ...utils import metrics
                except ImportError:
                    from utils import metrics  # type: ignore
                metrics.inc("spinr_rides_snapshot_fallback_total", {"renderer": "osm"})
                logger.error(
                    f"Google snapshot render exhausted {_SNAPSHOT_MAX_GOOGLE_ATTEMPTS} attempts "
                    f"for ride {ride_id}; rendering OSM last resort"
                )
        else:
            logger.info(f"No Google Maps API key configured; using OSM renderer for ride {ride_id} snapshot")

        # OSM/staticmap — explicit last resort or key-less deployment only
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
        if revision > 0:
            if finalized_at is None:
                logger.error("route snapshot finalization token missing for ride %s", ride_id)
                return
            try:
                # Write the deletion inventory before uploading. Even if the
                # process dies during upload or a late batch invalidates this
                # revision, every possible private object has a durable purge
                # record and can never become an untracked retention orphan.
                await db_supabase.insert_many_ignore_conflicts(
                    "ride_route_snapshot_objects",
                    [
                        {
                            "ride_id": ride_id,
                            "storage_bucket": bucket,
                            "object_path": storage_path,
                            "route_revision": revision,
                            "route_finalized_at": finalized_at,
                            "retention_due_at": _route_snapshot_retention_due_at(finalized_at),
                        }
                    ],
                    on_conflict="storage_bucket,object_path",
                )
            except Exception as exc:
                # The ledger table (migration 240) may not be deployed yet.
                # Proceed with the upload so the receipt has a route image;
                # a missing ledger row is recoverable via backfill, but a
                # missing snapshot image is visible to the rider.
                logger.warning(
                    f"route snapshot ledger write failed for ride {ride_id} (migration 240 may be missing): {exc}",
                    exc_info=True,
                )
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
            # Persist only the private object path. Readers create a short-lived
            # signed URL after authorizing the rider, driver, admin, or receipt.
            try:
                snapshot_ref = {
                    "snapshot_object_path": storage_path,
                    "snapshot_url": None,
                    "snapshot_revision": revision,
                    "snapshot_attempts": 0,
                }
                try:
                    updated = await db_supabase.update_one(
                        "ride_routes",
                        {"ride_id": ride_id, "route_revision": revision, "finalized_at": finalized_at},
                        snapshot_ref,
                        upsert=False,
                    )
                except Exception:
                    # snapshot_attempts column (migration 243) may be missing.
                    # Retry without it so the object path still gets persisted.
                    snapshot_ref.pop("snapshot_attempts", None)
                    updated = await db_supabase.update_one(
                        "ride_routes",
                        {"ride_id": ride_id, "route_revision": revision, "finalized_at": finalized_at},
                        snapshot_ref,
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


async def get_own_driver_row(current_user: Dict) -> Dict:
    """Look up the authenticated user's own drivers row (user_id ->
    drivers.id). Previously duplicated inline in referrals.py and
    crc_consent.py — factored out here so a third self-scoped driver
    endpoint (appeals.py) doesn't add a fourth copy."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return driver


# Ride-row fields that must NEVER reach the driver client. The pickup OTP is the
# rider's proof-of-identity handshake at pickup — a driver who can read it can
# pass verify-pickup-otp without the rider present, defeating the right-car /
# right-passenger safety control. serialize_doc() is an identity function, so a
# raw `select("*")` ride row would otherwise carry this straight to the driver
# app. The rider-facing path already strips it (routes/rides/queries.py); this
# is the driver-side equivalent. Keep the two lists in sync.
_DRIVER_RIDE_SECRET_FIELDS = frozenset({"pickup_otp"})


def serialize_ride_for_driver(ride):
    """serialize_doc() for a ride row bound for the driver client, minus any
    rider-secret fields. Returns a shallow copy so the source row (which may be
    reused for state transitions in the same request) is left intact."""
    if not isinstance(ride, dict):
        return serialize_doc(ride)
    return {k: v for k, v in ride.items() if k not in _DRIVER_RIDE_SECRET_FIELDS}


# `sin` here is belt-and-braces: it is already excluded from
# `_decrypt_driver_pii`, so the value in the row is a vault token rather than
# a number. Stripped anyway, because the token is still an unnecessary handle
# to regulated data and any future decrypt would otherwise leak silently.
_STRIP_FROM_SELF_RESPONSE = {"stripe_account_id", "bank_account", "fcm_token", "sin"}


# ── Mid-session document-expiry re-check (P1 #12, 2026-08-11) ────────────────
# go_online (status.py::update_driver_status) fail-closes on document expiry
# at the moment a driver goes online, but the ONLY other place expiry is
# enforced afterwards is the 12h document_expiry sweep and the 6h subscription
# sweep — so a license/insurance/inspection/CRC-VSC that lapses while a driver
# is already online can keep accepting NEW rides for up to 12h. That is a
# regulatory + insurance-liability gap (see .claude/context/regulatory-sk.md:
# "re-check cadence: on expiry" for these documents), not just a UX one.
#
# This block is a deliberately-scoped, NEW re-check for ride_flow.py::accept_ride
# — it intentionally mirrors go_online's core-document-expiry semantics rather
# than calling into status.py's inline block directly. go_online's block is
# ~150 lines of tested, live-surface logic (per-area `required_documents`
# matching + legacy-column fallback); refactoring it to share this helper is a
# larger, higher-blast-radius change than this fix's scope justifies, so it is
# left untouched and this stays an intentionally-duplicated, smaller check.
# See docs/change-log/2026-08-11-accept-ride-mid-session-expiry-check.md for
# the full blast-radius note and the follow-up flagged for real consolidation.
#
# Scope note: unlike go_online, this check is NOT gated on the driver's
# service-area `required_documents` config. The four categories below (SK
# license, insurance ride-share endorsement, vehicle inspection, CRC/VSC) are
# unconditional regulatory requirements for every driver per
# regulatory-sk.md's onboarding table ("Enforced ... again on every
# go_online call") — they don't vary by area the way Spinr Pass requirements
# do — so checking them unconditionally is more correct here, not merely a
# simplification, and it avoids a second query (service_areas) that
# area-scoped matching would need.
_CORE_DOC_EXPIRY_CATEGORIES = (
    ("license_expiry_date", "Driving license", ("license", "driving", "permit")),
    ("insurance_expiry_date", "Vehicle insurance", ("insurance",)),
    ("vehicle_inspection_expiry_date", "Vehicle inspection", ("inspection",)),
    (
        "background_check_expiry_date",
        "Background check",
        ("background", "criminal", "crc", "vulnerable", "vsc"),
    ),
)


def _parse_document_expiry(val):
    """Return a tz-aware UTC datetime for a document expiry value, or None.

    Mirrors status.py::update_driver_status's `_parse_expiry` inline helper —
    duplicated (not imported) because that one is a closure local to
    go_online, not a module-level export; behaviour is kept identical
    intentionally so the two checks agree on what "expired" means.
    """
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _document_matches_category(doc: Dict[str, Any], keywords: tuple) -> bool:
    haystack = f"{doc.get('document_type') or ''} {doc.get('requirement_key') or ''}".lower()
    return any(kw in haystack for kw in keywords)


async def _suspend_driver_for_expired_documents(driver_id: str, expired_labels: list) -> None:
    """Best-effort mirror of what the 12h document_expiry sweep would
    eventually do: flip the driver to suspended so a retried accept (or a
    subsequent go-online) surfaces the clear "account suspended" message
    instead of repeating the same "expired" rejection with no path forward.

    CAS on status != 'suspended' so a concurrent request/sweep tick doesn't
    double-write. Failure here must NOT block the caller's rejection of this
    accept — the accept is already being denied via the SpinrException the
    caller raises; a failed suspend-write just means the sweep catches it
    later, same as today.
    """
    try:
        await db_supabase.update_one(
            "drivers",
            {"id": driver_id, "status": {"$ne": "suspended"}},
            {"is_online": False, "is_available": False, "status": "suspended"},
        )
        logger.info(
            "[ACCEPT] driver %s suspended mid-session for expired documents: %s",
            driver_id,
            ", ".join(expired_labels),
        )
    except Exception:
        logger.error(
            "[ACCEPT] failed to suspend driver %s after detecting expired documents (%s) — "
            "the 12h document_expiry sweep will still catch this",
            driver_id,
            ", ".join(expired_labels),
            exc_info=True,
        )


async def check_driver_documents_current(driver: Dict[str, Any]) -> None:
    """Re-check the driver's own regulatory-document expiry, right now.

    Uses the already-loaded `driver` row for the legacy expiry columns (zero
    extra queries) plus ONE additional indexed lookup — `driver_documents`
    filtered by `driver_id` + `status='approved'`, a simple row lookup on an
    indexed foreign key, not a join or scan — to catch expiry on a document
    that was re-uploaded and approved since onboarding (the legacy columns
    are onboarding-only and never refreshed on re-upload, same caveat as
    go_online's own comment on this).

    Raises ``SpinrException`` (400, DRIVER_DOCUMENTS_EXPIRED) on a genuine
    expiry — the same error shape go_online uses for its own expiry block —
    after best-effort suspending the driver so the rejection doesn't recur
    silently forever. Any DB error propagates uncaught: this is a regulatory
    eligibility gate on a ride-accept, and the caller is responsible for
    failing CLOSED (503) rather than letting a check failure wave the accept
    through.
    """
    now = datetime.now(timezone.utc)
    driver_id = driver["id"]

    approved_docs = await db_supabase.get_rows(
        "driver_documents",
        {"driver_id": driver_id, "status": "approved"},
        limit=200,
    )

    covered_legacy_fields: set = set()
    for field, label, keywords in _CORE_DOC_EXPIRY_CATEGORIES:
        docs = [d for d in approved_docs if _document_matches_category(d, keywords)]
        if not docs:
            continue
        docs.sort(key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)
        latest = docs[0]
        exp = _parse_document_expiry(latest.get("expiry_date") or latest.get("expires_at"))
        if exp and exp < now:
            await _suspend_driver_for_expired_documents(driver_id, [label])
            raise SpinrException(
                message=f"{label} has expired. Please update your documents before accepting rides.",
                error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                status_code=400,
                message_key=ErrorKeys.DRIVER_DOCUMENTS_EXPIRED,
                action_hint="Renew documents in Profile",
            )
        # A fresh approved upload covers this category — don't also enforce
        # the (possibly stale, never-refreshed) legacy column for it below.
        covered_legacy_fields.add(field)

    for field, label, _keywords in _CORE_DOC_EXPIRY_CATEGORIES:
        if field in covered_legacy_fields:
            continue
        exp = _parse_document_expiry(driver.get(field))
        if exp and exp < now:
            await _suspend_driver_for_expired_documents(driver_id, [label])
            raise SpinrException(
                message=f"{label} has expired ({field}). Please update your documents before accepting rides.",
                error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                status_code=400,
                message_key=ErrorKeys.DRIVER_DOCUMENTS_EXPIRED,
                action_hint="Renew documents in Profile",
            )
