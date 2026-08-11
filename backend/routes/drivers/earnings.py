"""Driver earnings: balance, bonuses, periodic summaries, forecast.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    Any,
    APIRouter,
    Decimal,
    Depends,
    Dict,
    HTTPException,
    Optional,
    Query,
    RideStatus,
    ZoneInfo,
    datetime,
    db_supabase,
    get_current_user,
    logger,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    _d,
    _money_str,
    _ride_income,
)

try:
    from ...utils.legacy_rides import (
        EXCLUDE_LEGACY_RIDES,
        drop_legacy_offset_payouts,
        previous_app_history_visible,
    )
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    from utils.legacy_rides import (  # type: ignore
        EXCLUDE_LEGACY_RIDES,
        drop_legacy_offset_payouts,
        previous_app_history_visible,
    )

router = APIRouter()


@router.get("/balance")
async def get_driver_balance(current_user: dict = Depends(get_current_user)):
    """Get driver's current balance/earnings summary."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        rides = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                # Previous-app rides are history, not Spinr income — see
                # utils/legacy_rides. Their offsetting 'legacy_import' payout
                # is dropped below, so the balance arithmetic is unchanged.
                **EXCLUDE_LEGACY_RIDES,
            },
            limit=10000,
        )
        # Decimal-only money: float accumulation over many rows drifts cents,
        # and this feeds payable_balance which bounds the Stripe payout Transfer.
        total_earnings = sum(
            (
                _d(r.get("base_fare") or 0)
                + _d(r.get("distance_fare") or 0)
                + _d(r.get("time_fare") or 0)
                + _d(r.get("tip_amount") or 0)
                for r in rides
            ),
            Decimal("0"),
        )
        total_tips = sum((_d(r.get("tip_amount") or 0) for r in rides), Decimal("0"))
        total_rides = len(rides)

        # Deduct EVERY payout that represents money sent or in-flight — only
        # explicitly reversed/failed payouts (money returned or never left) are
        # excluded. The filter defaults to deducting, so an unknown/new status
        # still counts as money-out: worst case a driver is temporarily
        # under-paid (recoverable), NEVER a double-withdraw of platform money.
        # (Before, only status='pending' was deducted — a 'completed' /
        # 'transfer_completed' payout silently stopped reducing the balance, so
        # the driver could re-withdraw the same earnings.)
        #
        # Two payout types are NOT money out of a Spinr balance:
        #
        # - 'stripe_sync': legacy-app payout HISTORY materialized from Stripe
        #   transfer records (services/stripe_payout_sync_service.py) for
        #   T4A/tax completeness. The earnings they cashed out were paid in the
        #   OLD app and are not in this DB's rides, so deducting them would
        #   drive every migrated driver's payable_balance negative and block
        #   real withdrawals.
        # - 'legacy_import': the synthetic offset the booking importer wrote to
        #   cancel imported ride earnings. Those rides are now excluded above,
        #   so their offset must go too — dropping only one half would move the
        #   driver's payable balance (see utils/legacy_rides).
        payout_rows = drop_legacy_offset_payouts(
            await db_supabase.get_rows("payouts", {"driver_id": driver["id"]}, limit=5000)
        )
        _not_money_out = {"reversed", "failed"}
        total_payouts = sum(
            (
                _d(p.get("amount") or 0)
                for p in payout_rows
                if str(p.get("status") or "").lower() not in _not_money_out
                and p.get("payout_type") != "stripe_sync"
            ),
            Decimal("0"),
        )
        # 'pending' = recorded but not yet transferred (shown as "Pending");
        # the rest of total_payouts is money already sent ("Paid Out").
        pending_payouts = sum(
            (_d(p.get("amount") or 0) for p in payout_rows if str(p.get("status") or "").lower() == "pending"),
            Decimal("0"),
        )
    except Exception as e:
        # A transient DB error here must NOT be masked as a $0 balance — a
        # driver seeing their earnings drop to zero looks like money vanished
        # and triggers false support/payout escalations. Surface 503 so the
        # client retries (per CLAUDE.md: never log-and-continue on a DB read).
        logger.error(f"Error fetching balance: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Balance temporarily unavailable") from e

    # Quest + driver-referral bonuses are payable earnings (driver_bonuses
    # ledger) — they fold into payable_balance and pay out via the normal Stripe
    # Transfer, like ride earnings. Fetched in a SEPARATE try so a driver_bonuses
    # error (e.g. migration not yet applied) never zeroes the driver's ride
    # earnings/balance.
    total_bonuses = Decimal("0")
    total_referral_bonuses = Decimal("0")
    try:
        bonus_rows = await db_supabase.get_rows("driver_bonuses", {"driver_id": driver["id"]}, limit=10000)
        total_bonuses = sum((_d(b.get("amount") or 0) for b in bonus_rows), Decimal("0"))
        # Referral-only slice so the activity/payout view shows it distinctly from
        # quest bonuses (both live in driver_bonuses; `kind` tells them apart).
        total_referral_bonuses = sum(
            (_d(b.get("amount") or 0) for b in bonus_rows if b.get("kind") == "referral"),
            Decimal("0"),
        )
    except Exception as e:
        logger.error(f"Error fetching driver bonuses for balance: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Earnings temporarily unavailable",
        ) from e

    # Money the PREVIOUS app paid this driver (stripe_sync mirrors of real
    # Stripe Transfers). Excluded from the balance math above by design; the
    # app shows it as its own labeled figure so a migrated driver can see
    # their history is intact without it inflating current earnings.
    # Transition messaging with a sunset: after the cutoff in
    # utils/legacy_rides this reports 0.00 and the app note self-hides.
    previous_app_paid = Decimal("0")
    if previous_app_history_visible():
        previous_app_paid = sum(
            (
                _d(p.get("amount") or 0)
                for p in payout_rows
                if p.get("payout_type") == "stripe_sync" and str(p.get("status") or "").lower() not in _not_money_out
            ),
            Decimal("0"),
        )

    return {
        "total_earnings": _money_str(total_earnings + total_bonuses),
        # payable_balance = ride earnings + bonuses - ALL money-out payouts
        "payable_balance": _money_str(total_earnings + total_bonuses - total_payouts),
        "pending_payouts": _money_str(pending_payouts),
        "total_paid_out": _money_str(total_payouts - pending_payouts),
        "previous_app_paid_total": _money_str(previous_app_paid),
        "total_bonuses": _money_str(total_bonuses),
        "total_referral_bonuses": _money_str(total_referral_bonuses),
        "has_bank_account": bool(driver.get("bank_account")),
        "stripe_account_onboarded": bool(driver.get("stripe_account_onboarded", False)),
        "stripe_id_number_provided": bool(driver.get("stripe_id_number_provided", False)),
        "total_tips": _money_str(total_tips),
        "total_rides": total_rides,
    }


@router.get("/bonuses")
async def get_driver_bonuses(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List the driver's bonus credits (quest + referral) — the payable-earnings
    line items behind the bonus portion of payable_balance, for the earnings /
    payout history view."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    try:
        rows = await db_supabase.get_rows(
            "driver_bonuses", {"driver_id": driver["id"]}, limit=limit, order="created_at", desc=True
        )
    except Exception as e:
        logger.error(f"Error fetching driver bonuses: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Bonuses temporarily unavailable",
        ) from e
    return {
        "bonuses": [
            {
                "id": b.get("id"),
                "amount": _money_str(b.get("amount") or 0),
                "kind": b.get("kind"),
                "description": b.get("description"),
                "created_at": b.get("created_at"),
            }
            for b in rows
        ],
        "total": _money_str(sum((_d(b.get("amount") or 0) for b in rows), Decimal("0"))),
    }


@router.get("/earnings")
async def get_driver_earnings(period: str = Query("week"), current_user: dict = Depends(get_current_user)):
    """Get driver's earnings summary for a period."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        # Try to find by id directly in case user_id isn't set, or log error
        logger.error(f"Driver not found for user {current_user['id']}")
        raise HTTPException(status_code=404, detail="Driver not found")

    logger.info(f"Fetching earnings for driver {driver['id']} period {period}")

    # Calculate date range using the driver's service area timezone so "today"
    # reflects the driver's local calendar day regardless of which province they
    # operate in.  Falls back to America/Regina if no service area is set.
    _tz_name = "America/Regina"
    if driver.get("service_area_id"):
        _sa_rows = await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        if _sa_rows and _sa_rows[0].get("timezone"):
            _tz_name = _sa_rows[0]["timezone"]
    now = datetime.now(ZoneInfo(_tz_name))
    use_date_filter = True
    if period in ("today", "day"):
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "all":
        use_date_filter = False
        start_date = None
    else:
        # Fallback: treat unknown period as 'week'
        start_date = now - timedelta(days=7)

    try:
        filters: Dict[str, Any] = {
            "driver_id": driver["id"],
            "status": RideStatus.COMPLETED,
            **EXCLUDE_LEGACY_RIDES,
        }
        if use_date_filter and start_date:
            filters["ride_completed_at"] = {"$gte": start_date.isoformat()}

        rides = await db_supabase.get_rows("rides", filters, limit=10000)

        # Fetch incentive claims for these rides
        _ride_ids = [r["id"] for r in rides if r.get("id")]
        _incentive_total = Decimal("0")
        if _ride_ids:
            try:
                _claims = (
                    db_supabase.supabase.table("ride_incentive_claims")
                    .select("bonus_amount")
                    .in_("ride_id", _ride_ids)
                    .execute()
                ).data or []
                _incentive_total = sum(Decimal(str(c.get("bonus_amount") or 0)) for c in _claims)
            except Exception:
                logger.debug("earnings: ride_incentive_claims lookup failed", exc_info=True)

        # Fetch cancellation/no-show fees earned by this driver
        _cancel_filters: Dict[str, Any] = {
            "driver_id": driver["id"],
            "status": RideStatus.CANCELLED,
        }
        if use_date_filter and start_date:
            _cancel_filters["cancelled_at"] = {"$gte": start_date.isoformat()}
        _cancelled_rides = await db_supabase.get_rows("rides", _cancel_filters, limit=10000)
        _cancel_fees_total = sum(Decimal(str(r.get("cancellation_fee_driver") or 0)) for r in _cancelled_rides)

        # Tax collected from riders — passed through to driver as their income
        _total_tax = Decimal("0")
        for r in rides:
            _t = Decimal(str(r.get("tax_amount") or 0))
            if _t == 0:
                _snap = r.get("fare_breakdown_snapshot") or {}
                for _ln in _snap.get("lines") or []:
                    if _ln.get("type") in ("tax", "gst", "pst"):
                        _t += Decimal(str(_ln.get("amount") or 0))
            _total_tax += _t

        stats = {
            # Driver INCOME = driver_earnings (canonical), fare-component fallback
            # for legacy rows. Matches the T4A summary and the trips view.
            "total_earnings": sum((_ride_income(r) for r in rides), Decimal("0")),
            "total_tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
            "total_incentives": float(_incentive_total),
            "total_cancel_fees": float(_cancel_fees_total),
            "total_tax": float(_total_tax),
            "total_rides": len(rides),
            "total_distance_km": sum(r.get("distance_km", 0) or 0 for r in rides),
            "total_duration_minutes": sum(r.get("duration_minutes", 0) or 0 for r in rides),
        }
    except Exception as e:
        # Don't mask a DB failure as an all-zero earnings summary — surface 503
        # so the dashboard retries instead of telling the driver they earned
        # nothing this period.
        logger.error(f"Error fetching earnings: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Earnings temporarily unavailable") from e

    # Quest + referral bonuses earned in this period (driver_bonuses ledger).
    # Isolated so a bonus-fetch error never zeroes ride earnings. Distinct from
    # total_incentives (per-ride pickup/surge bonuses) — don't conflate them.
    _total_bonuses = Decimal("0")
    try:
        _bonus_filters: Dict[str, Any] = {"driver_id": driver["id"]}
        if use_date_filter and start_date:
            _bonus_filters["created_at"] = {"$gte": start_date.isoformat()}
        _bonus_rows = await db_supabase.get_rows("driver_bonuses", _bonus_filters, limit=10000)
        _total_bonuses = sum((_d(b.get("amount") or 0) for b in _bonus_rows), Decimal("0"))
    except Exception:
        logger.error("earnings: driver_bonuses lookup failed", exc_info=True)

    _total_with_extras = (
        Decimal(str(stats.get("total_earnings", 0)))
        + Decimal(str(stats.get("total_incentives", 0)))
        + Decimal(str(stats.get("total_cancel_fees", 0)))
        + Decimal(str(stats.get("total_tax", 0)))
        + _total_bonuses
    )
    return {
        "period": period,
        "total_earnings": _money_str(_total_with_extras),
        "total_tips": _money_str(stats.get("total_tips", 0)),
        "total_incentives": _money_str(stats.get("total_incentives", 0)),
        "total_bonuses": _money_str(_total_bonuses),
        "total_cancel_fees": _money_str(stats.get("total_cancel_fees", 0)),
        "total_tax": _money_str(stats.get("total_tax", 0)),
        "total_rides": stats.get("total_rides", 0),
        "total_distance_km": stats.get("total_distance_km", 0),
        "total_duration_minutes": stats.get("total_duration_minutes", 0),
        "average_per_ride": (
            _money_str(_total_with_extras / stats.get("total_rides", 1)) if stats.get("total_rides", 0) > 0 else "0.00"
        ),
    }


@router.get("/earnings/daily")
async def get_driver_daily_earnings(days: int = Query(7), current_user: dict = Depends(get_current_user)):
    """Get driver's daily earnings breakdown."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch completed rides in the period using the shared db layer
    try:
        rides = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": start_date.isoformat()},
                **EXCLUDE_LEGACY_RIDES,
            },
            order="ride_completed_at",
            limit=5000,
        )

        # Group by date (small dataset per driver, fine in Python)
        daily_data: dict = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            if date_str not in daily_data:
                daily_data[date_str] = {
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "distance_km": 0,
                }
            daily_data[date_str]["earnings"] += (
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
            )
            daily_data[date_str]["tips"] += r.get("tip_amount", 0) or 0
            daily_data[date_str]["rides"] += 1
            daily_data[date_str]["distance_km"] += r.get("distance_km", 0) or 0

        results = [{"date": date, **data} for date, data in sorted(daily_data.items())]
    except Exception as e:
        # An empty chart reads as "no rides this period" — surface the DB error
        # as 503 instead of fabricating an empty result.
        logger.error(f"Error fetching daily earnings: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Daily earnings temporarily unavailable") from e

    return results


@router.get("/earnings/trips")
async def get_driver_trip_earnings(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    days: Optional[int] = Query(default=None, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """Get driver's individual trip earnings.

    ``days`` restricts results to the past N days (max 365).  Omit for no
    date restriction (capped by ``limit``).
    """
    if days is not None and days > 365:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 12 months (365 days)")

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    filters: Dict[str, Any] = {
        "driver_id": driver["id"],
        "status": RideStatus.COMPLETED,
        **EXCLUDE_LEGACY_RIDES,
    }
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        filters["ride_completed_at"] = {"$gte": since.isoformat()}

    try:
        rides = await db_supabase.get_rows(
            "rides",
            filters,
            order="ride_completed_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error fetching trip earnings: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Trip earnings temporarily unavailable",
        ) from e

    return {
        "trips": [
            {
                "ride_id": r["id"],
                "pickup_address": r.get("pickup_address", ""),
                "dropoff_address": r.get("dropoff_address", ""),
                "distance_km": r.get("distance_km", 0),
                "duration_minutes": r.get("duration_minutes", 0),
                "base_fare": r.get("base_fare", 0),
                "distance_fare": r.get("distance_fare", 0),
                "time_fare": r.get("time_fare", 0),
                "driver_earnings": r.get("driver_earnings", 0),
                "tip_amount": r.get("tip_amount", 0),
                "rider_rating": r.get("rider_rating"),
                "completed_at": (r.get("ride_completed_at") if r.get("ride_completed_at") else None),
            }
            for r in rides
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/earnings/weekly")
async def get_driver_weekly_earnings(weeks: int = Query(4), current_user: dict = Depends(get_current_user)):
    """Get driver's weekly earnings breakdown."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.now(timezone.utc) - timedelta(weeks=weeks)

    # Try driver_daily_stats first (pre-aggregated)
    try:
        stats = await _deps.db.get_rows(
            "driver_daily_stats",
            {
                "driver_id": driver["id"],
                "stat_date": {"$gte": start_date.strftime("%Y-%m-%d")},
            },
            order="stat_date",
            limit=weeks * 7,
        )
    except Exception:
        logger.error("[EARNINGS] driver_daily_stats lookup failed, falling back to rides table", exc_info=True)
        stats = []

    if stats:
        # Group by ISO week
        weekly_data: dict = {}
        for s in stats:
            date_str = s.get("stat_date", "")[:10]
            if not date_str:
                continue
            from datetime import date as date_type

            d = date_type.fromisoformat(date_str)
            iso_year, iso_week, _ = d.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            if week_key not in weekly_data:
                # Monday of that ISO week
                from datetime import timedelta as td

                monday = d - td(days=d.weekday())
                sunday = monday + td(days=6)
                weekly_data[week_key] = {
                    "week_start": monday.isoformat(),
                    "week_end": sunday.isoformat(),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            weekly_data[week_key]["earnings"] += s.get("total_earnings", 0) or 0
            weekly_data[week_key]["tips"] += s.get("total_tips", 0) or 0
            weekly_data[week_key]["rides"] += s.get("rides_completed", 0) or 0
            weekly_data[week_key]["online_hours"] += round((s.get("online_minutes", 0) or 0) / 60, 1)
            weekly_data[week_key]["distance_km"] += s.get("total_km", 0) or 0

        return sorted(weekly_data.values(), key=lambda x: x["week_start"])

    # Fallback: compute from rides table
    try:
        rides = await _deps.db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": start_date.isoformat()},
                **EXCLUDE_LEGACY_RIDES,
            },
            order="ride_completed_at",
            limit=5000,
        )

        weekly_data = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            from datetime import date as date_type

            d = date_type.fromisoformat(date_str)
            iso_year, iso_week, _ = d.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            if week_key not in weekly_data:
                from datetime import timedelta as td

                monday = d - td(days=d.weekday())
                sunday = monday + td(days=6)
                weekly_data[week_key] = {
                    "week_start": monday.isoformat(),
                    "week_end": sunday.isoformat(),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            weekly_data[week_key]["earnings"] += (
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
            )
            weekly_data[week_key]["tips"] += r.get("tip_amount", 0) or 0
            weekly_data[week_key]["rides"] += 1
            weekly_data[week_key]["distance_km"] += r.get("distance_km", 0) or 0

        return sorted(weekly_data.values(), key=lambda x: x["week_start"])
    except Exception as e:
        logger.error(f"Error fetching weekly earnings: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Weekly earnings temporarily unavailable",
        ) from e


@router.get("/earnings/monthly")
async def get_driver_monthly_earnings(months: int = Query(6), current_user: dict = Depends(get_current_user)):
    """Get driver's monthly earnings breakdown."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.now(timezone.utc) - timedelta(days=months * 30)

    # Try driver_daily_stats first
    try:
        stats = await _deps.db.get_rows(
            "driver_daily_stats",
            {
                "driver_id": driver["id"],
                "stat_date": {"$gte": start_date.strftime("%Y-%m-%d")},
            },
            order="stat_date",
            limit=months * 31,
        )
    except Exception:
        logger.error("[EARNINGS] driver_daily_stats lookup failed, falling back to rides table", exc_info=True)
        stats = []

    if stats:
        monthly_data: dict = {}
        for s in stats:
            date_str = s.get("stat_date", "")[:10]
            if not date_str:
                continue
            month_key = date_str[:7]  # YYYY-MM
            if month_key not in monthly_data:
                monthly_data[month_key] = {
                    "month": month_key,
                    "year": int(month_key[:4]),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            monthly_data[month_key]["earnings"] += s.get("total_earnings", 0) or 0
            monthly_data[month_key]["tips"] += s.get("total_tips", 0) or 0
            monthly_data[month_key]["rides"] += s.get("rides_completed", 0) or 0
            monthly_data[month_key]["online_hours"] += round((s.get("online_minutes", 0) or 0) / 60, 1)
            monthly_data[month_key]["distance_km"] += s.get("total_km", 0) or 0

        return sorted(monthly_data.values(), key=lambda x: x["month"])

    # Fallback: compute from rides table
    try:
        rides = await _deps.db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": start_date.isoformat()},
                **EXCLUDE_LEGACY_RIDES,
            },
            order="ride_completed_at",
            limit=10000,
        )

        monthly_data = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            month_key = date_str[:7]
            if month_key not in monthly_data:
                monthly_data[month_key] = {
                    "month": month_key,
                    "year": int(month_key[:4]),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            monthly_data[month_key]["earnings"] += (
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
            )
            monthly_data[month_key]["tips"] += r.get("tip_amount", 0) or 0
            monthly_data[month_key]["rides"] += 1
            monthly_data[month_key]["distance_km"] += r.get("distance_km", 0) or 0

        return sorted(monthly_data.values(), key=lambda x: x["month"])
    except Exception as e:
        logger.error(f"Error fetching monthly earnings: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Monthly earnings temporarily unavailable",
        ) from e


@router.get("/earnings/comparison")
async def get_driver_earnings_comparison(period: str = Query("week"), current_user: dict = Depends(get_current_user)):
    """Compare current period earnings vs previous period."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.now(timezone.utc)
    if period == "week":
        current_start = now - timedelta(days=7)
        previous_start = now - timedelta(days=14)
        previous_end = now - timedelta(days=7)
    else:  # month
        current_start = now - timedelta(days=30)
        previous_start = now - timedelta(days=60)
        previous_end = now - timedelta(days=30)

    try:
        # Current period
        current_rides = await _deps.db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": current_start.isoformat()},
                **EXCLUDE_LEGACY_RIDES,
            },
            limit=5000,
        )
        # Previous period
        all_rides = await _deps.db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": previous_start.isoformat()},
                **EXCLUDE_LEGACY_RIDES,
            },
            limit=10000,
        )
        previous_rides = [r for r in all_rides if r.get("ride_completed_at", "") < previous_end.isoformat()]
    except Exception as e:
        logger.error(f"Error fetching comparison: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Earnings comparison temporarily unavailable",
        ) from e

    def summarize(rides):
        return {
            "earnings": sum(
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
                for r in rides
            ),
            "rides": len(rides),
            "tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
        }

    current = summarize(current_rides)
    previous = summarize(previous_rides)

    def pct_change(curr, prev):
        if prev == 0:
            return 100.0 if curr > 0 else 0.0
        return round((curr - prev) / prev * 100, 1)

    return {
        "period": period,
        "current": current,
        "previous": previous,
        "change_pct": {
            "earnings": pct_change(current["earnings"], previous["earnings"]),
            "rides": pct_change(current["rides"], previous["rides"]),
            "tips": pct_change(current["tips"], previous["tips"]),
        },
    }


@router.get("/earnings/forecast")
async def get_driver_earnings_forecast(current_user: dict = Depends(get_current_user)):
    """Weekly earnings projection for the driver home screen widget.

    Algorithm:
      1. Compute average daily earnings over the last 28 days of completed rides.
      2. Multiply by 7 to get the weekly baseline.
      3. Compute remaining days in the current week (Mon–Sun) and add
         the *this-week* earnings already locked in.

    The result is intentionally simple — it's a motivational nudge, not
    a financial guarantee.  Decimal precision is kept to 2 dp throughout.
    """
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    _zero = {
        "this_week_earnings": "0.00",
        "projected_weekly_total": "0.00",
        "daily_avg_last_28d": "0.00",
        "days_remaining_this_week": 6 - datetime.now(timezone.utc).weekday(),
        "this_week_trips": 0,
    }

    now = datetime.now(timezone.utc)
    # Rolling 28-day window for the daily average
    window_start = (now - timedelta(days=28)).isoformat()
    # Start of the current ISO week (Monday)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        recent_rides = await _deps.db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": window_start},
                **EXCLUDE_LEGACY_RIDES,
            },
            limit=5000,
        )
    except Exception as e:
        logger.error(
            f"[FORECAST] earnings fetch failed driver={driver['id']}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Earnings forecast temporarily unavailable",
        ) from e

    try:
        this_week_rides = [r for r in recent_rides if (r.get("ride_completed_at") or "") >= week_start.isoformat()]
        prev_28_rides = [r for r in recent_rides if (r.get("ride_completed_at") or "") < week_start.isoformat()]

        this_week_earnings = sum(
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            + Decimal(str(r.get("tip_amount") or 0))
            for r in this_week_rides
        )
        prev_28_earnings = sum(
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            + Decimal(str(r.get("tip_amount") or 0))
            for r in prev_28_rides
        )

        # Daily average over the 28-day window excluding the current week
        days_in_window = 28 - now.weekday()  # days before current week in window
        daily_avg = (prev_28_earnings / days_in_window) if days_in_window > 0 else Decimal("0")

        # Days remaining in current week (today = partially elapsed)
        days_remaining = 6 - now.weekday()  # Mon=0 … Sun=6
        projected_additional = daily_avg * days_remaining
        projected_total = (this_week_earnings + projected_additional).quantize(Decimal("0.01"))

        return {
            "this_week_earnings": _money_str(this_week_earnings),
            "projected_weekly_total": _money_str(projected_total),
            "daily_avg_last_28d": _money_str(daily_avg),
            "days_remaining_this_week": days_remaining,
            "this_week_trips": len(this_week_rides),
        }
    except Exception as e:
        logger.error(f"[FORECAST] computation failed driver={driver['id']}: {e}", exc_info=True)
        return _zero
