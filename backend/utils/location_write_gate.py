"""Driver location marker write gate — coalesces ``drivers``-row position UPDATEs.

Driver GPS is the highest-frequency write path in the system. Every ingestion
route ends in an ``UPDATE drivers SET lat, lng, updated_at`` for the live map
marker, and until this module existed the two families of route throttled that
write differently — or not at all:

  * WebSocket (``routes/websocket.py``) throttled to one write per 3 s using
    per-connection in-process state, which reset on every reconnect.
  * REST ``POST /drivers/location-batch`` (``routes/drivers/location.py``, three
    handlers) had **no** write throttle, bounded only by the 60/min rate limit.

Because the two used independent state they could not coalesce with each other:
a driver flushing the REST outbox while pinging over WebSocket wrote the same
row from two uncoordinated paths. This module replaces both with one gate keyed
in Redis, so every ingestion route for a given driver shares a single window.

Mechanics: ``SET spinr:locwrite:{driver_id} 1 NX EX <interval>``. Acquiring the
key means no marker write landed within the interval, so this caller should do
it. Losing means another write already did. The window is claimed *before* the
UPDATE executes, so a write that then fails at the DB layer holds the window and
the retry can be coalesced for up to one more interval — a ≤2×-interval marker
gap worst case, accepted against the 30 s dispatch freshness budget because the
gate cannot see the write's result.

**Degraded modes are bounded and restore pre-gate behaviour.** Two distinct
Redis failure shapes exist and both are handled:

  * A *raising* Redis (connection refused, reset, DNS): ``redis_set_nx``
    deliberately raises and leaves the degradation choice to its caller (see
    its docstring).
  * A *hung* Redis (black-holed socket): the shared client sets no socket
    timeouts, so an unbounded ``await`` here would stall the durable write —
    and the sequential WS message loop behind it — indefinitely, on a path
    with a <150 ms P95 SLA. Every Redis call in this module is therefore
    wrapped in ``asyncio.wait_for`` (``_GATE_REDIS_TIMEOUT_S``); a timeout
    lands in the same degraded branch as an error. A timed-out-but-applied
    SET NX costs at most one extra write, never a suppressed one.

In the degraded branch the decision restores what each family did before the
gate existed: REST (previously unthrottled) writes every time; WS (previously
throttled by Redis-free in-process state) falls back to a module-level
per-driver floor, so a Redis outage cannot un-throttle the hottest write path
at exactly the moment the non-autoscaling DB tier can least absorb it. WS
connections are replica-pinned, so the per-process floor has the same scope as
the pre-gate throttle. A Redis failure therefore never suppresses a REST write
and never amplifies WS write volume past its pre-gate rate.

**Shadow mode, and why it is not universal.** Gated on the
``location_marker_write_gate_enabled`` app setting (default OFF; the column is
created by migration 370). While off, the gate evaluates and counts what it
*would* have skipped as ``outcome="shadow_throttled"`` but the write still
lands — yielding a real production measurement of write volume and throttle
hit-rate before any behaviour changes. Nothing on this path has ever been
measured against live traffic (ACTION_ITEMS E2 is built but blocked on E1).

That is only sound for a caller whose pre-gate behaviour was "write every time",
which is true of the REST handlers and **not** of the WebSocket ones. The WS
handlers already enforced an unconditional 3 s throttle
(``conn_state["last_loc_db_write"]``), so letting them fall through in shadow
mode would delete a shipped throttle and *increase* write volume on merge — the
exact opposite of this module's purpose. Callers that were already throttled
therefore pass ``unthrottled_before=False`` and honour the window regardless of
the flag. Get this wrong and the failure is silent and backwards: the gate
appears inert while quietly amplifying the write path it exists to protect.

**Period 1 is never skipped.** ``routes/drivers/location.py`` folds the
insurance-period deadhead accumulator (``period1_accum_km`` /
``period1_accum_since``) into the *same* ``update_data`` as lat/lng. Dropping
one of those writes would silently under-count a regulated SGI audit figure, so
callers carrying an accumulator delta must pass ``force=True``: the write lands
and the window restarts.

**Interval invariant.** ``utils/stale_intent_reconciler.py`` selects candidates
on ``drivers.updated_at < now - stale_intent_offline_hours`` (default 4 h) to
flip force-killed apps intent-offline. That remains correct only while this
interval stays orders of magnitude below that threshold — at 3 s a live driver's
row stays fresh to within seconds. Do **not** raise MARKER_WRITE_INTERVAL_S to
anything approaching hours without revisiting that loop.
"""

from __future__ import annotations

import asyncio
import logging
import time

try:
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set, redis_set_nx
except ImportError:  # pragma: no cover - top-level execution fallback
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set, redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

# Minimum seconds between durable ``drivers``-row marker UPDATEs per driver.
# The WS handlers' former private copy (``_DRIVER_LOC_DB_WRITE_INTERVAL_S`` in
# routes/websocket.py) was deleted when they moved onto this gate; the interval
# now lives only here. See the interval invariant in the module docstring
# before changing it. Kept integral: the Redis TTL is ``int(...)`` seconds, so
# a fractional value would silently diverge from the effective window (guarded
# by test_interval_stays_far_below_stale_intent_threshold).
MARKER_WRITE_INTERVAL_S = 3.0

# Upper bound on every Redis call this gate makes. The shared client has no
# socket timeouts, so without this a hung (not merely down) Redis would stall
# the marker write — and the WS message loop — behind an unbounded await.
_GATE_REDIS_TIMEOUT_S = 0.1

# app_settings flag; default OFF so the gate ships in shadow mode.
GATE_FLAG = "location_marker_write_gate_enabled"

_PREFIX = "spinr:locwrite:"

_WRITE_METRIC = "spinr_drivers_location_write_total"
_GATE_FAILED_METRIC = "spinr_drivers_location_gate_failed_total"
_SETTINGS_FAILED_METRIC = "spinr_drivers_location_gate_settings_failed_total"

# Degraded-mode floor for previously-throttled (WS) callers: driver_id → last
# monotonic write time, consulted only when Redis cannot answer. Bounded
# crudely — a clear mid-outage costs at most one extra write per driver, never
# a suppressed one.
_DEGRADED_MAX_ENTRIES = 5000
_degraded_last_write: dict[str, float] = {}


def _key(driver_id: str) -> str:
    return f"{_PREFIX}{driver_id}"


def _degraded_should_write(driver_id: str, unthrottled_before: bool) -> bool:
    """Decide a write while Redis is unavailable: restore pre-gate behaviour.

    REST (``unthrottled_before=True``) wrote every time before the gate
    existed, so it fails fully open. The WS handlers had an in-process 3 s
    throttle that never depended on Redis — deleting it during an outage would
    amplify the hottest write path exactly when the DB tier (which does not
    autoscale, see docs/runbooks/capacity-scaling.md) can least absorb it — so
    they fall back to this module-level per-driver monotonic floor instead.
    """
    if unthrottled_before:
        return True
    now = time.monotonic()
    last = _degraded_last_write.get(driver_id)
    if last is not None and now - last < MARKER_WRITE_INTERVAL_S:
        return False
    if len(_degraded_last_write) >= _DEGRADED_MAX_ENTRIES:
        _degraded_last_write.clear()
    _degraded_last_write[driver_id] = now
    return True


async def _gate_enabled() -> bool:
    """Read the app_settings flag. Any failure means 'off' (never throttle)."""
    try:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:  # pragma: no cover - top-level execution fallback
            from settings_loader import get_app_settings  # type: ignore

        settings = await get_app_settings() or {}
        return bool(settings.get(GATE_FLAG, False))
    except Exception as exc:
        # Settings unreadable — degrade to today's behaviour (always write)
        # rather than throttling on an unknown flag state.
        #
        # DatabaseError.__str__ is only ever "Database operation failed"; the
        # real driver error lives in details["original"] (CLAUDE.md: include it
        # explicitly). No exc_info, for the same reason as the Redis branch
        # below: the repository layer already logged the traceback one level
        # down, and this can repeat per REST flush during a settings outage.
        _details = getattr(exc, "details", None) or {}
        logger.error(
            "location write gate: settings read failed",
            extra={
                "error": str(exc),
                "original": _details.get("original"),
                "flag": GATE_FLAG,
            },
        )
        # Paired metric so this branch is alertable, not log-only — it is the
        # same degraded-but-recovered shape as the Redis fail-open below, and
        # previously only that half emitted a counter.
        _metric_inc(_SETTINGS_FAILED_METRIC)
        return False


async def should_write_marker(
    driver_id: str, *, path: str, force: bool = False, unthrottled_before: bool = True
) -> bool:
    """Return True iff this caller should issue the ``drivers`` marker UPDATE.

    ``path`` labels the ingestion route for metrics (``rest_v1``,
    ``rest_v2_trip``, ``rest_v2_idle``, ``ws_single``, ``ws_batch``).

    ``force=True`` guarantees a True return for a write that must not be
    dropped (a Period 1 accumulator delta). The window is still claimed or
    restarted, since the row genuinely is written; the outcome label records
    whether force actually overrode a held window (``period1_forced``) or the
    window was free anyway (``written``), so the shadow measurement stays
    truthful about how many writes coalescing really suppressed.

    ``unthrottled_before`` describes what this call site did *before* the gate
    existed, and decides both shadow mode and the degraded fallback. ``True``
    (the REST handlers) means the path wrote every time, so it falls through
    while the flag is off and fails fully open when Redis cannot answer.
    ``False`` (the WebSocket handlers) means the path already enforced its own
    3 s throttle, so the window is honoured regardless of the flag and the
    in-process floor applies when Redis cannot answer — otherwise shipping
    this module (or losing Redis) would *raise* write volume.
    """
    if not driver_id:
        return True

    key = _key(str(driver_id))
    interval = max(1, int(MARKER_WRITE_INTERVAL_S))

    if force:
        outcome = "period1_forced"
        try:
            acquired = await asyncio.wait_for(redis_set_nx(key, "1", ttl=interval), _GATE_REDIS_TIMEOUT_S)
            if acquired:
                # Window was free — this write would have happened anyway.
                # Counting it as forced would inflate the forced share of the
                # shadow measurement and hide how often coalescing actually
                # yields to Period 1.
                outcome = "written"
            else:
                # Genuinely overriding a held window: restart it, per the
                # module contract ("the write lands and the window restarts").
                await asyncio.wait_for(redis_set(key, "1", ttl=interval), _GATE_REDIS_TIMEOUT_S)
        except Exception as exc:
            # Non-fatal either way: the write proceeds regardless, we just
            # lose one window's coalescing.
            logger.warning(
                "location write gate: window refresh failed",
                extra={"driver_id": str(driver_id), "path": path, "error": str(exc)},
            )
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": outcome})
        return True

    try:
        acquired = await asyncio.wait_for(redis_set_nx(key, "1", ttl=interval), _GATE_REDIS_TIMEOUT_S)
    except Exception as exc:
        # Degraded: Redis raised or hung past the bound. ERROR level
        # deliberately retained — this is a real failure of the gate, not a
        # soft condition — but NO exc_info: this fires on the highest-
        # frequency write path in the system, so during an outage it would
        # emit a full traceback per ping per driver (~150/s at 500 drivers),
        # flooding logs exactly when the incident needs them readable.
        # redis_client already logs the underlying exception with detail, and
        # spinr_drivers_location_gate_failed_total is the alerting path.
        logger.error(
            "location write gate: Redis unavailable, degraded decision",
            extra={"driver_id": str(driver_id), "path": path, "error": str(exc)},
        )
        _metric_inc(_GATE_FAILED_METRIC)
        allow = _degraded_should_write(str(driver_id), unthrottled_before)
        _metric_inc(
            _WRITE_METRIC,
            {"path": path, "outcome": "written" if allow else "throttled"},
        )
        return allow

    if acquired:
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "written"})
        return True

    # A marker write for this driver already landed inside the window. Skip it
    # when the flag is on, or when this caller was already throttled before the
    # gate existed (shadow mode must not hand such a path a write it would not
    # previously have made).
    if not unthrottled_before or await _gate_enabled():
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "throttled"})
        return False

    _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "shadow_throttled"})
    return True
