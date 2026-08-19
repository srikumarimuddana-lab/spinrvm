"""Referral reward payout loop (rider + driver).

Pays a referrer (and, where the area opts in, the referee too) once the referee
reaches the ride threshold WITHIN the completion deadline (window_days from
referral_terms; 0 = no deadline):
  - driver referral: referrer earns $10 once the referee (a driver) completes
    REFERRAL_RIDES_REQUIRED (10) rides; the referee additionally earns
    DRIVER_REFEREE_REWARD (0 by default — admin opt-in per service area via
    service_areas.driver_referee_reward, migration 201) on the SAME threshold.
  - rider referral: referrer AND referee each earn $5 once the referee completes
    RIDER_REFERRAL_RIDES_REQUIRED (1) ride ("first-ride bonus, both sides").

If the deadline passes before the threshold is met, the referral is recorded as
'expired' (rewards 0) and never paid — this forces referred drivers to complete
their rides promptly.

Money safety:
  - Reward amounts are admin-controlled per service area (see referral_terms).
    A per-area reward of 0 means that side is not paid — there is no global
    on/off flag; "don't pay" is expressed by setting the amount to 0.
  - Idempotent: a payout is claimed by INSERTing a referral_payouts row whose
    UNIQUE(referee_user_id) makes a duplicate/concurrent claim fail, so each
    referral is paid at most once even across replicas and retries.
  - Decimal-only money; credits go through the same wallet RPC + immutable
    ledger entry used by the quest-reward payout.
  - A credit failure marks the row 'failed' (NO auto-retry) so it surfaces for
    manual reconciliation rather than risking a double-credit race.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

try:
    from .. import db_supabase  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.error_handling import DuplicateRecordError  # type: ignore
    from ..utils.redis_client import (  # type: ignore
        redis_expire,
        redis_get,
        redis_incr,
        redis_set_nx,
    )
    from ..utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        resolve_referral_terms,
    )
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.error_handling import DuplicateRecordError  # type: ignore
    from utils.redis_client import (  # type: ignore
        redis_expire,
        redis_get,
        redis_incr,
        redis_set_nx,
    )
    from utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        resolve_referral_terms,
    )

try:
    from .metrics import inc as _metric_inc  # type: ignore
except ImportError:
    from utils.metrics import inc as _metric_inc  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
except ImportError:
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            return None


logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # every 5 minutes
_TWO = Decimal("0.01")
# referral_payouts claims are looked up by referee_user_id IN (...) per chunk of
# candidates; 200 text ids ≈ 7 KB of URL, safely under common 8-16 KB proxy caps.
_CLAIM_LOOKUP_CHUNK = 200
# Cap on the batched completed-rides prefetch per chunk. Hitting it flags the
# chunk truncated, and affected referees fall back to exact per-referee counts
# (an undercount at the deadline could wrongly expire a qualified referral).
_RIDES_FETCH_LIMIT = 10_000

# ── Fraud guards (ranked blocker #6 / audit finding N2, 2026-08-19) ─────────
# A $0-cost first_ride_only/free_ride promo ride otherwise satisfies rider-
# referral qualification on its own, making the referrer_reward farmable at
# zero marginal cost via throwaway phone numbers, with no cap on how many
# times one referrer could cash in. Two independent, complementary guards:
#   1. a completed ride only counts toward the rider-referral threshold when
#      grand_total > 0 (see _process_one / _count_prefetched_rides below and
#      the matching filter in routes/users.py::_rider_referral_summary);
#   2. a rolling-window velocity cap on referrer_reward payouts per referrer,
#      below.
# Fixed window anchored on the referrer's first payout in the window (mirrors
# the OTP brute-force lockout convention in routes/auth.py: redis_incr, then
# redis_expire only on the first increment of a key).
_VELOCITY_WINDOW_SECONDS = 24 * 60 * 60  # 24h
_DEFAULT_VELOCITY_CAP_PER_REFERRER = 5  # app_settings.referral_payout_velocity_cap_per_day overrides


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> str:
    return str(v)


def _velocity_key(referrer_user_id: str, kind: str) -> str:
    return f"spinr:referral_payout:velocity:{kind}:{referrer_user_id}"


async def _velocity_cap_for(kind: str) -> int:
    """Admin-tunable cap (app_settings.referral_payout_velocity_cap_per_day);
    falls back to the conservative default when unset/unparseable. <= 0
    explicitly disables the cap (documented escape hatch)."""
    try:
        settings = await get_app_settings() or {}
    except Exception as e:
        # Settings lookup failing loudly matters here too (it usually means the
        # DB is down), but this is a rate-limit knob, not the payout itself —
        # log and fall back to the safe default rather than blocking every
        # referral payout tick on a settings-table hiccup.
        logger.error(f"referral_payout: app_settings lookup failed, using default velocity cap: {e}", exc_info=True)
        settings = {}
    raw = settings.get("referral_payout_velocity_cap_per_day", _DEFAULT_VELOCITY_CAP_PER_REFERRER)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_VELOCITY_CAP_PER_REFERRER


async def _referrer_velocity_capped(referrer_user_id: str, kind: str) -> bool:
    """True when this referrer has already hit the rolling-window cap on
    referrer_reward payouts (kind='rider'|'driver', tracked separately).

    Read-only (does not increment) so a still-capped referrer can be re-checked
    every tick without inflating the counter; _record_referrer_payout_velocity
    is what actually advances it, and only after a real credit succeeds.

    A Redis error fails OPEN (returns False / not capped): this loop already
    tolerates a Redis outage for its leader-election lock (see referral_payout_loop
    above) by degrading to per-replica ticks, and the velocity cap is a fraud
    throttle layered on top of the correctness-critical UNIQUE(referee_user_id)
    claim, not a substitute for it — refusing every payout during a Redis blip
    would incorrectly freeze legitimate referrers. Logged at error (not
    swallowed) so a sustained outage that silently disables the cap is visible.
    """
    cap = await _velocity_cap_for(kind)
    if cap <= 0:
        return False
    try:
        current = await redis_get(_velocity_key(referrer_user_id, kind))
    except Exception as e:
        logger.error(
            f"referral_payout: velocity-cap Redis read failed for referrer {referrer_user_id} — "
            f"failing open (not capping this tick): {e}",
            exc_info=True,
        )
        return False
    return int(current or 0) >= cap


async def _record_referrer_payout_velocity(referrer_user_id: str, kind: str) -> None:
    """Advance the rolling-window counter after a referrer credit has actually
    succeeded. Best-effort by design: this must never raise into the caller's
    try/except, because that block's failure path marks the referral_payouts
    claim 'failed' for manual reconciliation — and the money has already moved
    at this point, so mis-marking a real payout as 'failed' would be actively
    wrong. A Redis failure here is logged loudly (not silently swallowed) since
    it degrades the fraud guard for this referrer's next payout, but it cannot
    be allowed to misreport a real, completed credit."""
    try:
        count = await redis_incr(_velocity_key(referrer_user_id, kind))
        if count == 1:
            await redis_expire(_velocity_key(referrer_user_id, kind), _VELOCITY_WINDOW_SECONDS)
    except Exception as e:
        logger.error(
            f"referral_payout: velocity-cap increment failed for referrer {referrer_user_id} "
            f"(payout still succeeded): {e}",
            exc_info=True,
        )


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp to a tz-aware UTC datetime (assume UTC if naive)."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def referral_payout_loop() -> None:
    """Every 5 min, pay any newly-qualified referral rewards. Replay-safe."""
    while True:
        # Single-writer across replicas (load-shedding, not correctness: the
        # referral_payouts UNIQUE claim already prevents double-pays). Without
        # this gate every replica runs the full candidate scan + per-referee
        # ride counts every 5 minutes against the shared connection pool. Lock
        # on a per-interval wall-clock bucket so exactly one replica handles
        # each window (fresh bucket key each interval; TTL 2x absorbs jitter).
        # Copies the surge_engine leader-lock pattern.
        bucket = int(datetime.now(timezone.utc).timestamp() // INTERVAL_SECONDS)
        try:
            is_leader = await redis_set_nx(f"spinr:referral_payout:lock:{bucket}", "1", int(INTERVAL_SECONDS * 2))
        except Exception as _lock_err:
            # Redis unavailable (dev in-memory fallback / outage): proceed. Dev
            # is single-process; a prod Redis outage degrades to per-replica
            # ticks (safe via the UNIQUE claim) and self-heals once Redis
            # returns. Mirrors the surge_engine degrade.
            logger.warning(f"referral_payout: leader lock unavailable ({_lock_err}), proceeding without lock")
            is_leader = True

        if is_leader:
            try:
                await _tick()
            except Exception:
                logger.error("referral_payout tick failed", exc_info=True)
        else:
            # Not this window's leader — still alive, just idle.
            _record_heartbeat("referral_payout (5min)")
        await asyncio.sleep(INTERVAL_SECONDS)


async def _tick() -> None:
    # Reclaim crash-stranded claims: a row stuck 'processing' past the grace
    # window means a replica died between claiming and finalising. We can't tell
    # whether the credit landed, so mark it 'failed' for manual review rather
    # than risk a double-credit by auto-retrying.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    try:
        stale = await db_supabase.get_rows(
            "referral_payouts",
            {"status": "processing", "created_at": {"$lt": cutoff}},
            columns="id,referee_user_id",
            limit=500,
        )
        for s in stale:
            await db_supabase.update_one(
                "referral_payouts", {"id": s["id"], "status": "processing"}, {"$set": {"status": "failed"}}
            )
            logger.error(
                "referral_payout: stale 'processing' claim marked 'failed' for manual review",
                extra={"referee_id": s.get("referee_user_id")},
            )
    except Exception:
        logger.error("referral_payout: stale-claim sweep failed", exc_info=True)

    # Referred users only, filtered server-side ($notnull → not.is.null; the
    # partial index from migration 241 covers it). The empty-string guard stays:
    # NOT NULL admits '' and a blank code is not a referral.
    users = await db_supabase.get_rows(
        "users",
        {"referral_code_used": {"$notnull": True}},
        columns="id,referral_code_used,referred_by,referral_applied_at",
        limit=10000,
    )
    candidates = [u for u in users if u.get("referral_code_used")]
    if not candidates:
        return

    # Referees we've already claimed/paid/failed — skip them. ('failed' rows are
    # NOT deleted: they stay in the table (and in this `done` set) to block a
    # re-claim and the double-credit it would risk; they need manual
    # reconciliation, not auto-retry — see the credit-failure block below.)
    # Looked up per candidate chunk via the UNIQUE(referee_user_id) index rather
    # than scanning the whole (ever-growing) payouts table each tick; chunks
    # keep the PostgREST in.() URL comfortably under proxy length limits.
    done: set = set()
    candidate_ids = [u["id"] for u in candidates]
    for i in range(0, len(candidate_ids), _CLAIM_LOOKUP_CHUNK):
        chunk = candidate_ids[i : i + _CLAIM_LOOKUP_CHUNK]
        existing = await db_supabase.get_rows(
            "referral_payouts",
            {"referee_user_id": {"$in": chunk}},
            columns="referee_user_id",
            limit=len(chunk),
        )
        done.update(r["referee_user_id"] for r in existing)

    # Process the unclaimed referees chunk-by-chunk with batched lookups: one
    # drivers query, one referrer-drivers query, and one completed-rides query
    # per chunk replace the per-referee N+1. Terms are memoized per (area, kind)
    # for the whole tick. A prefetch failure degrades loudly to the legacy
    # per-referee queries (ctx=None) rather than skipping payouts.
    pending = [u for u in candidates if u["id"] not in done]
    terms_cache: dict = {}
    for i in range(0, len(pending), _CLAIM_LOOKUP_CHUNK):
        chunk = pending[i : i + _CLAIM_LOOKUP_CHUNK]
        try:
            ctx = await _prefetch_chunk(chunk, terms_cache)
        except Exception:
            logger.error("referral_payout: chunk prefetch failed — falling back to per-referee lookups", exc_info=True)
            ctx = None
        for u in chunk:
            try:
                await _process_one(u, u["referral_code_used"], ctx)
            except Exception:
                logger.error("referral_payout: processing referee failed", exc_info=True, extra={"referee_id": u["id"]})


def _is_rider_code(code: str) -> bool:
    return str(code).upper().startswith("RIDE")


async def _prefetch_chunk(chunk: list, terms_cache: dict) -> dict:
    """Batch the per-referee lookups for one chunk of unclaimed referees.

    Returns the context consumed by _process_one:
      referrer_user_by_driver_id — driver referrals store referred_by = driver id
      driver_row_by_user         — the referee's own driver row (driver kind)
      rides_by_rider             — completed rides per rider referee
                                   ({created_at, service_area_id}; area + count)
      rides_by_driver            — completed-ride created_at list per driver row
      riders_truncated /
      drivers_truncated          — a rides fetch hit its limit, so Python-side
                                   counts could UNDERcount; _process_one must
                                   fall back to exact per-referee counting
                                   (an undercount at the deadline could expire
                                   a referral that actually qualified).
    """
    rider_ids = [u["id"] for u in chunk if _is_rider_code(u["referral_code_used"])]
    driver_referees = [u for u in chunk if not _is_rider_code(u["referral_code_used"])]

    ctx: dict = {
        "terms_cache": terms_cache,
        "referrer_user_by_driver_id": {},
        "driver_row_by_user": {},
        "rides_by_rider": {},
        "rides_by_driver": {},
        "riders_truncated": False,
        "drivers_truncated": False,
    }

    referrer_driver_ids = sorted({u["referred_by"] for u in driver_referees if u.get("referred_by")})
    if referrer_driver_ids:
        rows = await db_supabase.get_rows(
            "drivers", {"id": {"$in": referrer_driver_ids}}, columns="id,user_id", limit=len(referrer_driver_ids)
        )
        ctx["referrer_user_by_driver_id"] = {r["id"]: r.get("user_id") for r in rows}

    if driver_referees:
        referee_ids = [u["id"] for u in driver_referees]
        rows = await db_supabase.get_rows(
            "drivers",
            {"user_id": {"$in": referee_ids}},
            columns="id,user_id,service_area_id",
            limit=len(referee_ids),
        )
        for r in rows:
            ctx["driver_row_by_user"].setdefault(r["user_id"], r)

        driver_row_ids = sorted({r["id"] for r in ctx["driver_row_by_user"].values()})
        if driver_row_ids:
            rides = await db_supabase.get_rows(
                "rides",
                {"driver_id": {"$in": driver_row_ids}, "status": "completed"},
                columns="driver_id,created_at",
                limit=_RIDES_FETCH_LIMIT,
            )
            ctx["drivers_truncated"] = len(rides) >= _RIDES_FETCH_LIMIT
            for r in rides:
                ctx["rides_by_driver"].setdefault(r["driver_id"], []).append(r)

    if rider_ids:
        rides = await db_supabase.get_rows(
            "rides",
            {"rider_id": {"$in": rider_ids}, "status": "completed"},
            # grand_total needed so _count_prefetched_rides can exclude a $0
            # promo-covered ride from qualifying the referral on its own.
            columns="rider_id,created_at,service_area_id,grand_total",
            limit=_RIDES_FETCH_LIMIT,
        )
        ctx["riders_truncated"] = len(rides) >= _RIDES_FETCH_LIMIT
        for r in rides:
            ctx["rides_by_rider"].setdefault(r["rider_id"], []).append(r)

    if ctx["riders_truncated"] or ctx["drivers_truncated"]:
        # Degraded but recovered per-referee below — warning + continue.
        logger.warning(
            "referral_payout: chunk rides prefetch hit its %s-row limit — using exact per-referee counts",
            _RIDES_FETCH_LIMIT,
        )
    return ctx


def _area_from_prefetched_rides(rides: list, since_iso) -> str | None:
    """Replicate area_id_for_rider on prefetched rows: the newest completed ride
    (created_at desc, only the newest 20 considered) at/after since_iso whose
    service_area_id is set. Must stay behaviour-identical — the area picks the
    money terms."""
    since = _parse_iso(since_iso) if since_iso else None
    eligible = [r for r in rides if since is None or _parse_iso(r["created_at"]) >= since]
    eligible.sort(key=lambda r: _parse_iso(r["created_at"]), reverse=True)
    for r in eligible[:20]:
        if r.get("service_area_id"):
            return r["service_area_id"]
    return None


def _count_prefetched_rides(rides: list, applied_at, deadline, *, exclude_zero_fare: bool = False) -> int:
    """Count prefetched completed rides within [applied_at, deadline] — the same
    inclusive bounds count_documents applied server-side.

    exclude_zero_fare=True (rider referrals only) additionally requires
    grand_total > 0, so a ride fully covered by a first_ride_only/free_ride
    promo ($0 to the rider) does not by itself satisfy referral qualification
    — ranked blocker #6 / audit finding N2, 2026-08-19. Decimal comparison
    (never float) per house money-arithmetic convention."""
    applied = _parse_iso(applied_at) if applied_at else None
    n = 0
    for r in rides:
        dt = _parse_iso(r["created_at"])
        if applied is not None and dt < applied:
            continue
        if deadline is not None and dt > deadline:
            continue
        if exclude_zero_fare and _d(r.get("grand_total")) <= 0:
            continue
        n += 1
    return n


async def _process_one(referee: dict, code: str, ctx: dict | None = None) -> None:
    referee_id = referee["id"]
    is_rider = _is_rider_code(code)
    kind = "rider" if is_rider else "driver"

    # Resolve the referrer's USER id (wallets are per user).
    referrer_user_id = None
    if is_rider:
        # rider referral stores referred_by = referrer's user id
        referrer_user_id = referee.get("referred_by")
    else:
        # driver referral stores referred_by = referrer's DRIVER id
        ref_driver_id = referee.get("referred_by")
        if ref_driver_id:
            if ctx is not None:
                referrer_user_id = ctx["referrer_user_by_driver_id"].get(ref_driver_id)
            else:
                ref_driver = await db_supabase.get_driver_by_id(ref_driver_id)
                referrer_user_id = (ref_driver or {}).get("user_id")
    if not referrer_user_id or referrer_user_id == referee_id:
        return  # unresolved or self — nothing to pay

    # Never pay retroactively for rides that predate the referral. (Legacy rows
    # with no referral_applied_at fall back to a lifetime count.)
    applied_at = referee.get("referral_applied_at")

    # Resolve the referee's service area + the ride base filter. The ride
    # threshold AND the completion deadline are both per-area, so resolve terms
    # before counting. area_id == None → resolve_referral_terms falls back to the
    # global default.
    prefetched_rides = None
    if is_rider:
        if ctx is not None and not ctx["riders_truncated"]:
            prefetched_rides = ctx["rides_by_rider"].get(referee_id, [])
            area_id = _area_from_prefetched_rides(prefetched_rides, applied_at)
        else:
            area_id = await area_id_for_rider(referee_id, applied_at)
        # grand_total > 0: a ride fully covered by a first_ride_only/free_ride
        # promo ($0 to the rider) must not by itself satisfy qualification —
        # ranked blocker #6 / audit finding N2, 2026-08-19. Mirrored on the
        # prefetched-rows path via _count_prefetched_rides(exclude_zero_fare=True)
        # below, and in routes/users.py::_rider_referral_summary's display count.
        ride_filter: dict = {"rider_id": referee_id, "status": "completed", "grand_total": {"$gt": 0}}
    else:
        if ctx is not None:
            ref_as_driver = ctx["driver_row_by_user"].get(referee_id)
        else:
            ref_as_driver = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("drivers", {"user_id": referee_id}, limit=1)
            )
        if not ref_as_driver:
            return
        area_id = ref_as_driver.get("service_area_id")
        ride_filter = {"driver_id": ref_as_driver["id"], "status": "completed"}
        if ctx is not None and not ctx["drivers_truncated"]:
            prefetched_rides = ctx["rides_by_driver"].get(ref_as_driver["id"], [])

    # Terms are memoized per (area, kind) for the tick — areas are few, referees
    # are many, and the terms row can't change mid-tick in a way we must honour
    # (the claim snapshots the amounts anyway).
    if ctx is not None:
        terms_key = (area_id, kind)
        if terms_key not in ctx["terms_cache"]:
            ctx["terms_cache"][terms_key] = await resolve_referral_terms(area_id, kind)
        t = ctx["terms_cache"][terms_key]
    else:
        t = await resolve_referral_terms(area_id, kind)

    # Completion deadline: the referee must reach the ride threshold within
    # window_days of referral_applied_at, or the referral expires unpaid. This is
    # what forces referred drivers to complete their rides promptly. window_days
    # <= 0 (or no applied_at, e.g. a legacy referral) → no deadline, preserving
    # the original "count forever" behaviour.
    window_days = int(t.get("window_days") or 0)
    deadline = None
    if applied_at and window_days > 0:
        deadline = _parse_iso(applied_at) + timedelta(days=window_days)

    # Count completed rides from referral_applied_at, capped at the deadline so a
    # ride taken after the window closed can never qualify the referral. The
    # prefetched path applies the same inclusive bounds in Python; a truncated
    # prefetch (prefetched_rides None) falls back to the exact server-side count
    # — an undercount here could wrongly expire a qualified referral. The filter
    # translator honours only one operator per field, so AND the two bounds via
    # $and (see repositories/_base._apply_filters).
    if prefetched_rides is not None:
        completed = _count_prefetched_rides(prefetched_rides, applied_at, deadline, exclude_zero_fare=is_rider)
    else:
        bounds = []
        if applied_at:
            bounds.append({"created_at": {"$gte": applied_at}})
        if deadline is not None:
            bounds.append({"created_at": {"$lte": deadline.isoformat()}})
        count_filter = dict(ride_filter)
        if bounds:
            count_filter["$and"] = bounds
        completed = await db_supabase.count_documents("rides", count_filter)

    if completed < t["rides"]:
        # Threshold not met. If the window has closed, the referral has expired:
        # record a terminal 'expired' claim (rewards 0) so it's never paid and the
        # referee is excluded from every future tick by the same unique claim.
        # Until the deadline passes, just wait for the next tick.
        if deadline is not None and datetime.now(timezone.utc) > deadline:
            await _record_expiry(referee_id, referrer_user_id, kind, area_id)
        return

    # resolve_referral_terms already returns Decimal; _d re-quantises defensively.
    referrer_reward = _d(t["referrer"])
    referee_reward = _d(t["referee"])
    now_iso = datetime.now(timezone.utc).isoformat()

    # Admin set BOTH sides to 0 for this area → there is nothing to pay. Don't
    # burn the UNIQUE(referee_user_id) claim on a $0 row: leaving the referee
    # unclaimed means that if an admin later raises the area's reward above 0,
    # this referral becomes payable again on a future tick. (A one-sided 0 still
    # claims-and-pays the non-zero side below.)
    if referrer_reward <= 0 and referee_reward <= 0:
        return

    # Fraud guard (ranked blocker #6 / audit finding N2, 2026-08-19): cap how
    # many referrer_reward payouts a single referrer can earn in a rolling
    # window, regardless of how many distinct referee accounts (e.g. throwaway
    # phone numbers) hit the ride threshold. Checked (not incremented) here so
    # a still-capped referrer keeps being retried every tick without inflating
    # the counter — _record_referrer_payout_velocity below is what actually
    # advances it, and only after a real credit succeeds. Defers the WHOLE
    # claim (not just the referrer side): opening a claim now would burn the
    # UNIQUE(referee_user_id) and permanently lose the referrer's reward for
    # this referee once the claim can no longer be re-attempted, instead of
    # simply paying it on a later tick once the window clears.
    if referrer_reward > 0 and await _referrer_velocity_capped(referrer_user_id, kind):
        logger.info(
            f"referral_payout: referrer {referrer_user_id} ({kind}) hit the velocity cap — "
            f"deferring referee {referee_id}'s payout to a later tick"
        )
        _metric_inc("spinr_referral_payout_velocity_capped_total", {"kind": kind})
        return

    # Atomic claim: the UNIQUE(referee_user_id) means only one replica/tick wins
    # this INSERT; a duplicate raises and we skip (already being handled).
    try:
        await db_supabase.insert_one(
            "referral_payouts",
            {
                "referee_user_id": referee_id,
                "referrer_user_id": referrer_user_id,
                "kind": kind,
                # Snapshot the area whose terms were applied (NULL = global
                # default) so later admin edits never retro-change this payout.
                "service_area_id": area_id,
                "referrer_reward": _f(referrer_reward),
                "referee_reward": _f(referee_reward),
                "status": "processing",
                "created_at": now_iso,
            },
        )
    except DuplicateRecordError:
        # Another replica/tick already claimed this referee — expected, skip.
        logger.info(f"referral_payout: claim already exists for referee {referee_id} — skipping")
        return
    # Any other DB error (table missing, RLS/schema, connectivity) propagates to
    # the outer handler in _tick, which logs it as an error rather than silently
    # treating it as 'already claimed'.

    # Credit each side only when its admin-configured reward is > 0 (a per-area
    # reward of 0 means "don't pay this side"). _credit routes DRIVER rewards to
    # the payable driver_bonuses ledger (append-only insert) and RIDER rewards to
    # the wallet (self-atomic: reverses its own increment if the ledger write
    # fails, so money is never left unrecorded).
    # On ANY credit failure we mark the claim
    # 'failed' and stop: we deliberately do NOT delete/retry, because the claim
    # row staying in place is exactly what prevents a re-claim and a double
    # credit on the next tick. 'failed' rows surface for manual reconciliation.
    #
    # Which side(s) actually got credited is tracked in memory and written as
    # part of the SAME row update that records the outcome (paid OR failed) —
    # never in a separate write between the wallet credit and the status update.
    # That closes the window where an intermediate timestamp write could fail and
    # leave a referrer-paid row looking like "neither paid". A 'failed' row then
    # reads: referrer_credited_at set + referee_credited_at NULL → pay only the
    # referee; both NULL → neither side paid.
    meta = {"kind": kind, "referee_id": referee_id, "referrer_user_id": referrer_user_id}
    referrer_paid = False
    referee_paid = False
    try:
        if referrer_reward > 0:
            await _credit(referrer_user_id, referrer_reward, kind, referee_id, "referral_reward", meta)
            referrer_paid = True
            await _record_referrer_payout_velocity(referrer_user_id, kind)
        if referee_reward > 0:
            await _credit(referee_id, referee_reward, kind, referee_id, "referral_bonus", meta)
            referee_paid = True
    except Exception:
        logger.error(
            "referral_payout: credit failed — marking claim 'failed' for manual reconciliation",
            exc_info=True,
            extra={**meta, "referrer_paid": referrer_paid, "referee_paid": referee_paid},
        )
        await db_supabase.update_one(
            "referral_payouts",
            {"referee_user_id": referee_id},
            {"$set": _credit_marks("failed", referrer_paid, referee_paid)},
        )
        return

    await db_supabase.update_one(
        "referral_payouts",
        {"referee_user_id": referee_id},
        {"$set": _credit_marks("paid", referrer_paid, referee_paid)},
    )
    logger.info(f"referral_payout: paid {kind} referral reward for referee {referee_id}")


async def _record_expiry(referee_id: str, referrer_user_id: str, kind: str, area_id) -> None:
    """Record a terminal, never-paid 'expired' claim for a referee who missed the
    completion deadline. Reuses the UNIQUE(referee_user_id) claim (rewards 0) so
    the referee is excluded from every future tick and is never paid. A duplicate
    means another replica/tick already finalised this referee — expected, skip."""
    try:
        await db_supabase.insert_one(
            "referral_payouts",
            {
                "referee_user_id": referee_id,
                "referrer_user_id": referrer_user_id,
                "kind": kind,
                "service_area_id": area_id,
                "referrer_reward": _f(_d(0)),
                "referee_reward": _f(_d(0)),
                "status": "expired",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info(
            "referral_payout: referral expired unpaid (completion deadline missed)",
            extra={"referee_id": referee_id, "kind": kind},
        )
    except DuplicateRecordError:
        logger.info(f"referral_payout: claim already exists for referee {referee_id} — skipping expiry")


def _credit_marks(status: str, referrer_paid: bool, referee_paid: bool) -> dict:
    """Build the referral_payouts update recording the outcome + which sides were
    actually credited. Timestamps come from in-memory flags so a row never
    reports a paid side as un-paid."""
    now_iso = datetime.now(timezone.utc).isoformat()
    marks: dict = {"status": status}
    if status == "paid":
        marks["paid_at"] = now_iso
    if referrer_paid:
        marks["referrer_credited_at"] = now_iso
    if referee_paid:
        marks["referee_credited_at"] = now_iso
    return marks


class ReferralClaimNotFound(Exception):
    """No 'failed' referral_payouts claim exists for the given referee (or it was
    concurrently taken). Callers map this to a 404 / skip."""


async def recredit_failed_claim(referee_user_id: str) -> dict:
    """Re-credit ONLY the not-yet-credited side(s) of a FAILED referral claim and
    finalise it 'paid'. Side-aware via referrer_credited_at / referee_credited_at,
    so a referrer who was already paid (only the referee credit had failed — e.g.
    the pre-migration-199 'referral_bonus' constraint bug, where the referrer's
    'referral_reward' lands but the referee's 'referral_bonus' is rejected) is
    NEVER double-paid. The old delete-and-let-the-loop-re-pay path re-credited BOTH.

    Credits the amounts SNAPSHOTTED on the claim row, so a later admin change to the
    area's rewards can't retro-alter this payout, and a deadline that has since
    passed can't strand a referee who legitimately qualified at claim time.

    No-double-credit is enforced by writing each side's credited_at to the DB
    IMMEDIATELY after its credit lands (not just at the end): once money has moved,
    the mark is durable, so even if a later step (or the loop's stale-'processing'
    sweep) resets the row to 'failed', a subsequent re-credit correctly skips the
    already-paid side. The claim also refreshes created_at so the sweep grants this
    requeue a fresh 15-min grace window instead of reclaiming a days-old backlog row
    mid-credit.

    Atomic: claims the row failed -> processing first; a concurrent re-credit (or a
    double-click) that already took it raises ReferralClaimNotFound. On a credit
    failure the row is returned to 'failed' and the error re-raised.

    Returns {id, referee_user_id, kind, credited: [...], status}.
    Raises ReferralClaimNotFound when there is no failed claim to re-credit.

    Do NOT call this for a claim whose logs show "ledger write AND its reversal
    failed" — that row already moved money while its credited-at stayed NULL, so it
    looks owed and would double-credit. Those need manual reconciliation.
    """
    row = await db_supabase.find_one("referral_payouts", {"referee_user_id": referee_user_id, "status": "failed"})
    if not row:
        raise ReferralClaimNotFound(referee_user_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    # Atomic claim: only one caller can move this row failed -> processing. Refresh
    # created_at too: the loop's stale-claim sweep reclaims 'processing' rows whose
    # created_at is older than 15 min, and a backlog 'failed' row's created_at is
    # days old — without this refresh the sweep would flip the row back to 'failed'
    # mid-credit, and a later re-credit could double-pay. The refresh gives the
    # requeue a fresh grace window.
    claimed = await db_supabase.update_one(
        "referral_payouts",
        {"id": row["id"], "status": "failed"},
        {"$set": {"status": "processing", "created_at": now_iso}},
    )
    if not claimed:
        raise ReferralClaimNotFound(referee_user_id)

    kind = row.get("kind") or "rider"
    referrer_user_id = row.get("referrer_user_id")
    referrer_reward = _d(row.get("referrer_reward"))
    referee_reward = _d(row.get("referee_reward"))

    # A side is owed only if its snapshotted reward is > 0 AND it was not already
    # credited on the original (partial) attempt.
    referrer_owed = referrer_reward > 0 and not row.get("referrer_credited_at")
    referee_owed = referee_reward > 0 and not row.get("referee_credited_at")

    # Owed money but no one to pay it to: the referrer's user row was anonymised
    # (PIPEDA right-to-delete → referrer_user_id SET NULL per migration 171) while
    # the reward stayed owed. NEVER fall through to the 'paid' write — that would
    # silently lose the reward. Surface loudly and leave the row 'failed' for manual
    # reconciliation (CLAUDE.md: don't silently swallow payment errors).
    if referrer_owed and not referrer_user_id:
        await db_supabase.update_one("referral_payouts", {"id": row["id"]}, {"$set": {"status": "failed"}})
        raise RuntimeError(
            f"referral re-credit: claim {row['id']} owes the referrer (reward {referrer_reward}) but "
            "referrer_user_id is NULL (anonymised?) — manual reconciliation required"
        )

    meta = {"kind": kind, "referee_id": referee_user_id, "referrer_user_id": referrer_user_id, "requeued": True}
    referrer_paid_now = False
    referee_paid_now = False
    try:
        if referrer_owed:
            await _credit(referrer_user_id, referrer_reward, kind, referee_user_id, "referral_reward", meta)
            referrer_paid_now = True
            # Persist the mark the instant the money moves, before the next credit
            # or any crash/sweep — so a later reset to 'failed' can never re-pay it.
            await db_supabase.update_one(
                "referral_payouts", {"id": row["id"]}, {"$set": {"referrer_credited_at": now_iso}}
            )
        if referee_owed:
            await _credit(referee_user_id, referee_reward, kind, referee_user_id, "referral_bonus", meta)
            referee_paid_now = True
            await db_supabase.update_one(
                "referral_payouts", {"id": row["id"]}, {"$set": {"referee_credited_at": now_iso}}
            )
    except Exception:
        logger.error(
            "referral_payout: re-credit failed — returning claim to 'failed' for manual reconciliation",
            exc_info=True,
            extra={**meta, "referrer_paid_now": referrer_paid_now, "referee_paid_now": referee_paid_now},
        )
        await db_supabase.update_one("referral_payouts", {"id": row["id"]}, {"$set": {"status": "failed"}})
        raise

    await db_supabase.update_one(
        "referral_payouts", {"id": row["id"]}, {"$set": {"status": "paid", "paid_at": now_iso}}
    )
    credited = [s for s, ok in (("referrer", referrer_paid_now), ("referee", referee_paid_now)) if ok]
    logger.info("referral_payout: re-credited failed referral claim", extra={**meta, "credited": credited})
    return {"id": row["id"], "referee_user_id": referee_user_id, "kind": kind, "credited": credited, "status": "paid"}


async def _credit(user_id: str, amount: Decimal, kind: str, reference_id: str, txn_type: str, metadata: dict) -> None:
    """Credit a referral reward. Decimal-only.

    DRIVER referrals pay a driver — record a PAYABLE driver_bonuses row (folds
    into payable_balance + the Stripe payout), not the rider wallet the driver
    app can't see. RIDER referrals keep the wallet credit + immutable ledger
    entry; if that ledger write fails after the balance moved, reverse the
    increment so money is never left unrecorded, then re-raise.
    """
    if kind == "driver":
        # driver_bonuses is append-only and idempotent at the referral_payouts
        # claim level (one _credit call per side, no retry on failure), so no
        # source_id dedup is needed here. id defaults to gen_random_uuid().
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1)
        )
        if not driver:
            raise RuntimeError(f"referral_payout: no driver row for user {user_id} — cannot credit referral bonus")
        await db_supabase.insert_one(
            "driver_bonuses",
            {
                "driver_id": driver["id"],
                "user_id": user_id,
                "amount": _f(amount),
                "kind": "referral",
                "description": f"{kind.capitalize()} referral reward",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return

    # Rider referral → wallet credit + immutable ledger entry.
    try:
        from ..routes.wallet import _record_transaction, get_or_create_wallet  # type: ignore
    except ImportError:
        from routes.wallet import _record_transaction, get_or_create_wallet  # type: ignore

    wallet = await get_or_create_wallet(user_id)
    new_balance = await db_supabase.wallet_increment_balance(wallet["id"], amount)
    try:
        await _record_transaction(
            wallet_id=wallet["id"],
            user_id=user_id,
            txn_type=txn_type,
            amount=_f(amount),
            balance_after=_f(new_balance),
            reference_id=reference_id,
            description=f"{kind.capitalize()} referral reward",
            metadata=metadata,
        )
    except Exception:
        # Ledger write failed after the balance moved — reverse the increment so
        # no money is left unrecorded, then surface the error to the caller.
        try:
            await db_supabase.wallet_increment_balance(wallet["id"], -amount)
        except Exception:
            logger.error(
                "referral_payout: ledger write AND its reversal failed — wallet %s left with an "
                "unrecorded %s credit; manual reconciliation required",
                wallet["id"],
                _f(amount),
                exc_info=True,
            )
        raise
