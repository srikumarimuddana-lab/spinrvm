"""Driver insurance-period audit logging (M-5).

Every moment a driver spends in the app maps to one of four insurance
periods (0=offline, 1=available, 2=en-route, 3=passenger). SGI and the
Saskatchewan Transportation Act require an append-only audit trail of
those transitions; the schema lives in migration 64.

This module owns the close-and-open atomicity around the partial unique
index `driver_insurance_periods_open` (one open row per driver). Callers
in the ride/driver state machine fire-and-forget into
``record_period_transition`` whenever the driver moves between periods.

Compliance trade-off
--------------------
The CLAUDE.md "do not silently swallow errors" rule has an explicit
exception for compliance-grade audit writes: a missed audit row is
preferable to blocking the driver state machine. Failures here are
logged at ERROR (so they show up in Sentry / log search and can be
backfilled from the rides table) but never raised.
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    import db_supabase  # type: ignore

try:
    from . import metrics as _metrics
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    try:
        from utils import metrics as _metrics  # type: ignore
    except ImportError:  # pragma: no cover
        _metrics = None  # type: ignore

logger = logging.getLogger(__name__)

_VALID_PERIODS = (0, 1, 2, 3)
# PostgreSQL unique_violation SQLSTATE — postgrest-py surfaces this
# verbatim in the error message when an INSERT conflicts with the
# partial unique index on (driver_id) WHERE ended_at IS NULL.
_PG_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        _PG_UNIQUE_VIOLATION in msg or "duplicate key" in msg or "unique constraint" in msg or "already exists" in msg
    )


def _metric_inc(name: str, labels: Optional[dict] = None) -> None:
    if _metrics is None:
        return
    try:
        _metrics.inc(name, labels)
    except Exception:  # noqa: BLE001, S110 - metrics are best-effort by design
        pass


async def record_period_transition(
    driver_id: str,
    new_period: int,
    ride_id: Optional[str] = None,
) -> None:
    """Close the driver's currently-open period (if any) and open a new one.

    Compliance-grade: failures are logged at ERROR but never raised. A
    missed transition row is preferable to blocking the driver state
    machine. The partial unique index on ``(driver_id) WHERE
    ended_at IS NULL`` is the source of truth — racing callers will
    serialise on the index, and the loser turns into a logged warning.

    Args:
        driver_id: The drivers.id PK (NOT users.id).
        new_period: One of 0, 1, 2, 3. See module docstring.
        ride_id: Required when ``new_period == 3``; optional otherwise.

    Raises:
        ValueError: programmer error — invalid period or missing ride_id
            for period 3. These are pre-DB checks; they should never
            fire in production and surfacing them helps catch
            wiring bugs at call sites early.
    """
    if new_period not in _VALID_PERIODS:
        raise ValueError(f"new_period must be one of {_VALID_PERIODS}, got {new_period!r}")
    if new_period == 3 and not ride_id:
        raise ValueError("ride_id is required when new_period == 3 (passenger aboard)")

    try:
        rpc_params = {
            "p_driver_id": driver_id,
            "p_new_period": new_period,
            "p_ride_id": ride_id,
        }

        def _rpc_call():
            sb = db_supabase.supabase
            if sb is None:
                return None
            res = sb.rpc("record_insurance_period_transition", rpc_params).execute()
            return getattr(res, "data", None) or {}

        result = await db_supabase.run_sync(_rpc_call, retry_policy="write")

        if result is None:
            logger.error(
                "insurance_periods: supabase client unavailable, dropping transition "
                "driver_id=%s new_period=%s ride_id=%s",
                driver_id,
                new_period,
                ride_id,
            )
            _metric_inc(
                "spinr_insurance_period_write_failed_total",
                {"reason": "no_client"},
            )
            return

        status = result.get("status") if isinstance(result, dict) else "ok"

        if status == "noop":
            logger.info(
                "insurance_periods: no-op transition (already open) driver_id=%s new_period=%s ride_id=%s",
                driver_id,
                new_period,
                ride_id,
            )
            _metric_inc(
                "spinr_insurance_period_noop_total",
                {"period": str(new_period)},
            )
        elif status == "race":
            logger.warning(
                "insurance_periods: concurrent transition (race) driver_id=%s new_period=%s ride_id=%s",
                driver_id,
                new_period,
                ride_id,
            )
            _metric_inc(
                "spinr_insurance_period_race_total",
                {"period": str(new_period)},
            )
        else:
            _metric_inc(
                "spinr_insurance_period_recorded_total",
                {"period": str(new_period)},
            )
    except Exception:
        logger.error(
            "insurance_periods: transition write FAILED (swallowed) driver_id=%s new_period=%s ride_id=%s",
            driver_id,
            new_period,
            ride_id,
            exc_info=True,
        )
        _metric_inc(
            "spinr_insurance_period_write_failed_total",
            {"reason": "exception", "period": str(new_period)},
        )
