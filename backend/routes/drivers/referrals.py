"""Driver referral program and leaderboard.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Decimal,
    Depends,
    Dict,
    HTTPException,
    Query,
    RideStatus,
    datetime,
    db_supabase,
    get_current_user,
    logger,
    paid_referee_earnings,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    _money_str,
)

router = APIRouter()


def _coverage_end_utc(max_stat_date: str) -> "datetime":
    """UTC instant up to which the newest driver_daily_stats row covers rides.

    Rollups are keyed to America/Regina business days (day_tz='regina',
    utils/driver_daily_rollup), so a row for date D covers created_at in
    [D 06:00 UTC, D+1 06:00 UTC). The freshness top-up must start at that
    row's END — a UTC-midnight boundary would re-count the last 6 Regina
    evening hours the row already aggregated.
    """
    try:
        from ...utils.driver_activity import regina_day_bounds_utc
    except ImportError:
        from utils.driver_activity import regina_day_bounds_utc  # type: ignore

    d = datetime.strptime(max_stat_date[:10], "%Y-%m-%d").date()
    return regina_day_bounds_utc(d)[1]


# ============ Referral Program Endpoints ============


class ApplyReferralCodeRequest(BaseModel):
    referral_code: str


# Referral reward terms — single source of truth so the earnings calc, the
# per-referee progress, and the displayed terms can never drift apart.
REFERRAL_RIDES_REQUIRED = 10
REFERRAL_REWARD_AMOUNT = 10  # CAD, paid per referee who reaches the ride target
# Referee's own signup bonus once they reach REFERRAL_RIDES_REQUIRED (the same
# threshold as the referrer — there is no separate referee ride count). 0 by
# default: this is a distinct payout stream from the rider program's symmetric
# $5/$5, and must not start crediting money until an admin opts in per service
# area (service_areas.driver_referee_reward, migration 201).
DRIVER_REFEREE_REWARD = 0
# Days the referee has to reach REFERRAL_RIDES_REQUIRED (from referral_applied_at)
# before the referral expires unpaid. 0 = no deadline. Per-area override lives in
# service_areas.driver_referral_window_days (migration 189).
REFERRAL_WINDOW_DAYS = 30


def _fmt_money(v) -> str:
    """Format a money amount for display copy: '10' for 10.00, '10.50' otherwise."""
    d = Decimal(str(v))
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"


def _driver_referral_codes(driver: dict) -> list:
    """Every code this driver may have been shared under — the current
    driver_code plus the legacy referral_code / DRIVER<id8> defaults — so
    referees who signed up with an older code still count in the summary."""
    out: list = []
    for c in (driver.get("driver_code"), driver.get("referral_code"), f"DRIVER{driver['id'][:8].upper()}"):
        if c and c not in out:
            out.append(c)
    return out


@router.get("/referral")
async def get_driver_referral_info(current_user: dict = Depends(get_current_user)):
    """Get driver's referral code and earnings from referrals."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # The shareable referral code IS the human-readable driver_code (DRV-XXXXXX)
    # when present — it's designed to be spoken/typed. Fall back to a stored
    # custom referral_code, then to the id-derived default for legacy rows that
    # predate driver_code (migration 156).
    codes = _driver_referral_codes(driver)
    referral_code = codes[0]  # primary code shown/shared (driver_code when present)

    # Find users who used ANY of this driver's codes (incl. legacy ones) so a
    # referrer doesn't lose progress for referees who applied an older code.
    # Only the id is used below (to look up each referred user's driver row),
    # so project it and keep base64 profile_image out of the read.
    referred_users = await db_supabase.get_rows(
        "users", {"referral_code_used": {"$in": codes}}, columns="id", limit=100
    )

    # Reward terms follow this driver's assigned service area (global default
    # when unassigned). The ride threshold is per-area, so resolve before the
    # qualification loop.
    terms = await _deps.resolve_referral_terms(driver.get("service_area_id"), "driver")
    rides_required = terms["rides"]
    reward_amount = terms["referrer"]

    # A referral pays out once the referred driver completes rides_required
    # rides; until then it's "in progress". Earnings are the sum of qualified ones.
    total_referrals = len(referred_users)
    qualified_referrals = 0

    for user in referred_users:
        # Check if user became a driver and completed rides
        referred_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        if referred_driver:
            completed_rides = await db_supabase.count_documents(
                "rides",
                {"driver_id": referred_driver["id"], "status": RideStatus.COMPLETED},
            )
            if completed_rides >= rides_required:
                qualified_referrals += 1

    # Earned total: prefer the snapshotted sum of PAID payouts so it never changes
    # retroactively when area terms or the driver's area change; fall back to the
    # estimate (reward × qualified) until the payout loop has actually paid this
    # driver (the loop runs every 5 min, so the snapshot wins shortly after a
    # referee qualifies).
    paid = await _deps.paid_referral_earnings(current_user["id"], "driver")
    referral_earnings = paid if paid is not None else (reward_amount * qualified_referrals)

    # The viewer's OWN signup bonus — what they earned as a REFEREE (referred by
    # another driver). Actual paid amount only (paid at most once), 0 when not
    # referred / not yet paid / the area has the referee side set to 0. Mirrors
    # the rider "Refer & Earn" screen's referee_earnings (routes/users.py).
    referee_earned = await paid_referee_earnings(current_user["id"], "driver") or Decimal("0")

    # Who referred THIS driver (inbound). users.referred_by holds the referrer's
    # DRIVER id for driver referrals; resolve to a name + code. None if this
    # driver wasn't referred (or was referred via a rider code → not a driver row).
    me = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("users", {"id": current_user["id"]}, columns="referred_by", limit=1)
    )
    referred_by = None
    ref_drv_id = (me or {}).get("referred_by")
    if ref_drv_id:
        ref_drv = await db_supabase.get_driver_by_id(ref_drv_id)
        if ref_drv:
            ref_user = await db_supabase.get_user_by_id(ref_drv.get("user_id")) if ref_drv.get("user_id") else None
            referred_by = {
                "name": f"{(ref_user or {}).get('first_name', '')} {(ref_user or {}).get('last_name', '')}".strip()
                or ref_drv.get("name")
                or "Driver",
                "code": _driver_referral_codes(ref_drv)[0],
            }

    return {
        "referral_code": referral_code,
        "referral_link": f"https://spinr.app/join/{referral_code}",
        "total_referrals": total_referrals,
        "qualified_referrals": qualified_referrals,
        "pending_referrals": total_referrals - qualified_referrals,
        # Money serialised as 2-dp strings (house convention; clients parseFloat).
        "referral_earnings": _money_str(referral_earnings),
        "reward_amount": _money_str(reward_amount),
        # The viewer's own signup bonus (paid only, 0 if not referred / not yet
        # paid / area has the referee side at 0).
        "referee_earnings": _money_str(referee_earned),
        # Both sides' reward amounts so the app shows who earns what. referee_reward
        # is 0 unless an admin has opted this service area into a driver signup
        # bonus (service_areas.driver_referee_reward, migration 201) → app renders
        # "$0 = that party earns nothing".
        "referrer_reward": _money_str(reward_amount),
        "referee_reward": _money_str(terms["referee"]),
        "referred_by": referred_by,
        "rides_required": rides_required,
        # Admin-authored per-area T&C wins; otherwise generate the default
        # sentence from this area's reward numbers. Mention the referee bonus
        # only when this area actually pays one.
        "terms": terms.get("terms")
        or (
            f"Earn ${_fmt_money(reward_amount)} for each driver who signs up with your code "
            f"and completes {rides_required} rides."
            + (
                f" They earn ${_fmt_money(terms['referee'])} too once they complete {rides_required} rides."
                if terms["referee"] > 0
                else ""
            )
        ),
    }


@router.post("/referral/apply")
async def apply_referral_code(req: ApplyReferralCodeRequest, current_user: dict = Depends(get_current_user)):
    """Apply a referral code during driver onboarding."""
    code = req.referral_code.strip().upper()

    # Check if user already has a referral code applied
    user = await db_supabase.get_user_by_id(current_user["id"])
    if user and user.get("referral_code_used"):
        raise HTTPException(status_code=400, detail="Referral code already applied")

    # Resolve the referrer. The primary shareable code is the human-readable
    # driver_code (DRV-XXXXXX) shown in the profile / referral screen; also
    # accept a stored custom referral_code for backward compatibility.
    ref_driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"driver_code": code}, limit=1)
    )
    if not ref_driver:
        ref_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"referral_code": code}, limit=1)
        )
    if not ref_driver:
        # Fallback for the auto-generated default code, which is
        # "DRIVER" + the first 8 chars of the driver id, upper-cased
        # (see get_driver_referral_info). Resolve it back to the driver.
        #
        # NOTE: _apply_filters maps {"$regex": x} onto a SQL LIKE/ILIKE
        # pattern (%x%), NOT a real regex — so the previous ".*<id>.*"
        # value was matched *literally* (looking for ".*" inside the id)
        # and never hit. Pass the bare 8-char token and set $options:"i"
        # for a case-insensitive contains match (the code upper-cases the
        # lower-case hex id, so a case-sensitive match would also miss).
        potential_id = code.replace("DRIVER", "")
        if len(potential_id) == 8 and potential_id.isalnum():
            try:
                ref_driver = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows(
                        "drivers",
                        {"id": {"$regex": potential_id, "$options": "i"}},
                        limit=1,
                    )
                )
            except Exception as e:
                logger.warning(f"Referral default-code fallback lookup failed: {e}")

    if not ref_driver:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    # Block self-referral — a driver can't refer themselves (now that the code
    # can be entered at signup, this is an easy thing to try).
    if ref_driver.get("user_id") == current_user["id"]:
        raise HTTPException(status_code=400, detail="You can't use your own referral code")

    # Apply referral code to user
    await db_supabase.update_one(
        "users",
        {"id": current_user["id"]},
        {
            "referral_code_used": code,
            "referred_by": ref_driver["id"],
            # Recorded so the payout loop only rewards rides completed AFTER this.
            "referral_applied_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"success": True, "referral_code": code}


@router.get("/referrals")
async def get_referred_drivers(
    limit: int = Query(50),
    offset: int = Query(0),
    current_user: dict = Depends(get_current_user),
):
    """Get list of drivers referred by current driver."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Match every code this driver may have been shared under (incl. legacy)
    # so referees who applied an older code still appear in the list.
    codes = _driver_referral_codes(driver)

    # Use the viewing driver's area terms so the per-referee progress cards agree
    # with the summary endpoint (both follow the referrer's area). Actual payout
    # still uses each referee's own area; this list is an estimate.
    terms = await _deps.resolve_referral_terms(driver.get("service_area_id"), "driver")
    rides_required = terms["rides"]
    reward_amount = terms["referrer"]

    # Each referred user contributes name + email + signup date to the response
    # — project those columns and keep base64 profile_image out of the read.
    referred_users = await db_supabase.get_rows(
        "users", {"referral_code_used": {"$in": codes}}, columns="id,first_name,last_name,email,created_at", limit=100
    )

    referred_drivers = []
    for user in referred_users:
        referred_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        if referred_driver:
            # Get completed rides count
            completed_rides = await db_supabase.count_documents(
                "rides",
                {"driver_id": referred_driver["id"], "status": RideStatus.COMPLETED},
            )
            # Progress toward the reward: qualified once they hit the ride target.
            qualified = completed_rides >= rides_required
            rides_remaining = max(0, rides_required - completed_rides)
            referred_drivers.append(
                {
                    "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Driver",
                    "email": user.get("email", ""),
                    "referred_at": user.get("created_at", ""),
                    "total_trips": completed_rides,
                    # Reward-progress detail surfaced to the referrer.
                    "rides_required": rides_required,
                    "rides_remaining": rides_remaining,
                    "reward_amount": _money_str(reward_amount),
                    "qualified": qualified,
                    # "earned"  → reward unlocked (>= target rides)
                    # "in_progress" → started but not yet at target
                    "status": "earned" if qualified else "in_progress",
                }
            )

    return {"referred_drivers": referred_drivers[:limit]}


# ── Leaderboard ──────────────────────────────────────────────────────


@router.get("/leaderboard")
async def get_driver_leaderboard(
    period: str = Query("week", pattern="^(week|month|all)$"),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Get driver leaderboard rankings by rides, earnings, and rating.

    Returns the top drivers for the specified period with the current
    driver's rank highlighted.

    Aggregates from driver_daily_stats via one GROUP BY RPC (migration 204)
    and tops up with completed rides newer than each driver's last rollup
    date — one batched query, no double count. Replaces the per-driver
    rides+users scan (~1,001 sequential round-trips at a 500-driver fleet,
    whose per-driver 1,000-ride cap silently undercounted long periods).
    """
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.now(timezone.utc)
    if period == "week":
        start_dt = now - timedelta(days=7)
    elif period == "month":
        start_dt = now - timedelta(days=30)
    else:
        start_dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    try:
        all_drivers = await _deps.db.get_rows("drivers", {}, limit=500) or []
    except Exception:
        all_drivers = []

    # Rollup totals: one aggregate round-trip for the whole fleet.
    totals: Dict[str, dict] = {}
    _oldest_uncovered = start_dt.isoformat()
    try:
        _agg = await db_supabase.run_sync(
            lambda: db_supabase.supabase.rpc("driver_leaderboard_totals", {"p_start": start_date_str}).execute()
        )
        _max_stat_date = None
        for row in _agg.data or []:
            totals[row["driver_id"]] = {
                "rides": int(row.get("rides") or 0),
                "earnings": Decimal(str(row.get("earnings") or 0)),
                "tips": Decimal(str(row.get("tips") or 0)),
            }
            _row_date = row.get("last_stat_date")
            if _row_date and (_max_stat_date is None or _row_date > _max_stat_date):
                _max_stat_date = _row_date
        if _max_stat_date:
            # Rides on days after the newest rollup row aren't aggregated yet;
            # top them up below. Keyed off the actual MAX(stat_date), not
            # "yesterday" — the rollup endpoint takes an arbitrary target_date.
            # Coverage of a stat row now ends at the REGINA day end (06:00 UTC
            # next day, day_tz='regina' rollups): a UTC-midnight boundary here
            # would double-count the last 6 Regina-evening hours already inside
            # the row. For a legacy 'utc' MAX row this over-extends coverage by
            # 6h (a transient undercount that heals when the loop recomputes
            # the day as Regina) — the safe direction vs. persistent inflation.
            _oldest_uncovered = max(start_dt, _coverage_end_utc(str(_max_stat_date))).isoformat()
    except Exception:
        # Pre-migration-204 environment (or RPC outage): approximate from the
        # most recent daily rows, capped so PostgREST can't silently truncate
        # an unbounded fetch. Never falls back to the old per-driver N+1.
        logger.error("leaderboard: aggregate RPC failed; using capped daily-row fallback", exc_info=True)
        try:
            _rows = (
                await _deps.db.get_rows(
                    "driver_daily_stats",
                    {"stat_date": {"$gte": start_date_str}},
                    columns="driver_id,stat_date,rides_completed,total_earnings,total_tips",
                    order="stat_date",
                    desc=True,
                    limit=5000,
                )
                or []
            )
        except Exception:
            # Deliberate degrade: display-only surface, so an empty
            # leaderboard beats a 503 — but this read failing means the DB
            # itself is unreachable (the RPC outage was already logged
            # above), so it is error-level, never silent.
            logger.error("leaderboard: daily-row fallback read failed", exc_info=True)
            _rows = []
        _fb_max_date = None
        for r in _rows:
            t = totals.setdefault(r["driver_id"], {"rides": 0, "earnings": Decimal("0"), "tips": Decimal("0")})
            t["rides"] += int(r.get("rides_completed") or 0)
            t["earnings"] += Decimal(str(r.get("total_earnings") or 0))
            t["tips"] += Decimal(str(r.get("total_tips") or 0))
            _r_date = str(r.get("stat_date") or "")[:10]
            if _r_date and (_fb_max_date is None or _r_date > _fb_max_date):
                _fb_max_date = _r_date
        if _fb_max_date:
            # Same no-double-count boundary as the RPC path.
            _oldest_uncovered = max(start_dt, _coverage_end_utc(_fb_max_date)).isoformat()

    # Freshness top-up: completed rides the rollup hasn't covered yet, in one
    # batched query (same earnings formula as the old implementation).
    try:
        _fresh_rides = (
            await _deps.db.get_rows(
                "rides",
                {"status": RideStatus.COMPLETED, "created_at": {"$gte": _oldest_uncovered}},
                columns="driver_id,base_fare,distance_fare,time_fare,tip_amount,created_at",
                limit=5000,
            )
            or []
        )
    except Exception:
        # Deliberate degrade: stale-but-served beats a 503 for a display-only
        # surface; error-level because a failing rides read is a DB fault.
        logger.error("leaderboard: freshness top-up read failed", exc_info=True)
        _fresh_rides = []
    for r in _fresh_rides:
        _rid = r.get("driver_id")
        if not _rid:
            continue
        t = totals.setdefault(_rid, {"rides": 0, "earnings": Decimal("0"), "tips": Decimal("0")})
        t["rides"] += 1
        t["earnings"] += (
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            + Decimal(str(r.get("tip_amount") or 0))
        )
        t["tips"] += Decimal(str(r.get("tip_amount") or 0))

    # Names: one batched users read instead of one find_one per driver.
    _user_ids = [d.get("user_id") for d in all_drivers if d.get("user_id")]
    _names: Dict[str, str] = {}
    if _user_ids:
        try:
            _users = (
                await _deps.db.get_rows(
                    "users",
                    {"id": {"$in": _user_ids}},
                    columns="id,first_name,last_name",
                    limit=len(_user_ids),
                )
                or []
            )
            _names = {u["id"]: f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() for u in _users}
        except Exception:
            # Deliberate degrade (placeholder names still render), but a
            # failing users read is a DB fault — error-level per convention.
            logger.error("leaderboard: batched users lookup failed; using placeholders", exc_info=True)

    rankings = []
    for d in all_drivers:
        d_id = d["id"]
        t = totals.get(d_id, {"rides": 0, "earnings": Decimal("0"), "tips": Decimal("0")})
        rankings.append(
            {
                "driver_id": d_id,
                "name": _names.get(d.get("user_id"), "") or "Driver",
                "rides": t["rides"],
                "earnings": _money_str(t["earnings"]),
                "tips": _money_str(t["tips"]),
                "rating": d.get("rating", 0),
                "is_current_user": d_id == driver["id"],
            }
        )

    # Sort by rides (primary), then earnings (secondary)
    rankings.sort(key=lambda x: (x["rides"], Decimal(x["earnings"])), reverse=True)

    # Assign ranks
    for i, r in enumerate(rankings):
        r["rank"] = i + 1

    # Find current driver's rank
    my_rank = next((r for r in rankings if r["is_current_user"]), None)

    return {
        "period": period,
        "leaderboard": rankings[:limit],
        "my_rank": my_rank,
        "total_drivers": len(rankings),
    }
