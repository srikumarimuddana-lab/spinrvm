"""Shared database infrastructure for all repository modules.

Provides: Supabase client access, run_sync (async wrapper with circuit
breaker + retry), serialization helpers, row-level Redis cache, generic
CRUD functions (get_rows, update_one, etc.), and query filter builders.

Domain-specific repository modules import from here; db_supabase.py
re-exports everything so existing callers are unaffected.
"""

import asyncio
import json as _json
import os as _os
import random as _random
import re as _re
import time as _time
import traceback
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as _Enum
from typing import Any, Callable, Dict, List, Literal, Optional, TypeVar

try:
    import httpx as _httpx

    _HTTPX_TIMEOUT_EXC = _httpx.TimeoutException
    # NetworkError covers ConnectError/ReadError/WriteError/CloseError — transient
    # transport failures (incl. SSL "EOF occurred in violation of protocol") that
    # are safe to retry under read/idempotent_write policies.
    _HTTPX_NETWORK_EXC = _httpx.NetworkError
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore
    _HTTPX_TIMEOUT_EXC = None  # type: ignore
    _HTTPX_NETWORK_EXC = None  # type: ignore

try:
    from ..supabase_client import supabase  # type: ignore
except ImportError:
    from supabase_client import supabase  # type: ignore

try:
    from ..utils.deadline import deadline_exhausted as _deadline_exhausted  # type: ignore
    from ..utils.deadline import remaining_seconds as _remaining_seconds
    from ..utils.error_handling import DatabaseError, DuplicateRecordError, ServiceUnavailableException  # type: ignore
    from ..utils.metrics import inc as _metric_inc  # type: ignore
    from ..utils.metrics import set_gauge as _metric_gauge
    from ..utils.pii import geohash as _geohash  # type: ignore
    from ..utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr, redis_set  # type: ignore
except ImportError:
    from utils.deadline import deadline_exhausted as _deadline_exhausted  # type: ignore
    from utils.deadline import remaining_seconds as _remaining_seconds
    from utils.error_handling import DatabaseError, DuplicateRecordError, ServiceUnavailableException  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import set_gauge as _metric_gauge
    from utils.pii import geohash as _geohash  # type: ignore
    from utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr, redis_set  # type: ignore

from loguru import logger

T = TypeVar("T")


# ── Circuit Breaker ─────────────────────────────────────────────────


class _CircuitBreaker:
    """Half-open circuit breaker for the Supabase connection pool.

    Opens after FAILURE_THRESHOLD failures within WINDOW seconds.
    After OPEN_DURATION elapses, allows **exactly one** probe request
    through in half-open state; the next call is blocked until the
    probe resolves. This prevents a thundering-herd recovery where
    hundreds of queued requests all flood Supabase at the same moment
    it's coming back, re-tripping the breaker.

    Closes on first success in any state.
    """

    FAILURE_THRESHOLD = 5
    WINDOW = 30.0
    OPEN_DURATION = 60.0

    def __init__(self) -> None:
        self._state = "closed"
        self._failure_times: list = []
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def should_allow(self) -> bool:
        now = _time.monotonic()
        if self._state == "closed":
            return True
        if self._state == "open":
            if self._opened_at is not None and now - self._opened_at >= self.OPEN_DURATION:
                self._state = "half_open"
                self._probe_in_flight = True
                logger.info("[DB] Circuit breaker half-open — releasing one probe request")
                return True
            return False
        # half_open: only allow a probe if one is not already in flight.
        if not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        if self._state != "closed":
            logger.info(f"[DB] Circuit breaker CLOSED (was {self._state})")
        self._state = "closed"
        self._failure_times.clear()
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        now = _time.monotonic()
        self._failure_times = [t for t in self._failure_times if now - t < self.WINDOW]
        self._failure_times.append(now)
        if self._state == "closed" and len(self._failure_times) >= self.FAILURE_THRESHOLD:
            self._state = "open"
            self._opened_at = now
            self._probe_in_flight = False
            logger.error("[DB] Circuit breaker OPENED — raising ServiceUnavailableException on future calls")
        elif self._state == "half_open":
            self._state = "open"
            self._opened_at = now
            self._probe_in_flight = False
            logger.warning("[DB] Circuit breaker probe failed — back to OPEN")

    def release_probe(self) -> None:
        """Return a leaked half-open probe to OPEN.

        should_allow() sets _probe_in_flight=True when it grants the single
        half-open probe, and only record_success/record_failure clear it. If the
        probe request instead exits via a path that calls neither — the client
        deadline abort (wait_for timeout or the in-loop deadline pre-check) or
        the ValueError early-exit in run_sync — the flag would stay True forever,
        so should_allow() returns False for every future call and the whole API
        503s until the process restarts. Reverting to OPEN with a fresh
        OPEN_DURATION lets the breaker probe again later (a deadline-aborted
        probe tells us nothing good about DB health, so we wait, exactly like a
        failed probe). Guarded so it is a safe no-op on any non-probe call.
        """
        if self._state == "half_open" and self._probe_in_flight:
            self._state = "open"
            self._opened_at = _time.monotonic()
            self._probe_in_flight = False
            logger.warning("[DB] Circuit breaker half-open probe released (deadline/abort) — back to OPEN")


_breaker = _CircuitBreaker()


# ── DB thread pool ──────────────────────────────────────────────────
# Single executor for all run_sync work. DB_THREAD_POOL_SIZE is the one
# knob; DB_THREAD_POOL_MAX is honoured as a legacy fallback (it used to
# size a second, never-used executor that only fed the thread gauge —
# making both the gauge and the env var lie about real capacity, which
# was capped at 32 while ops believed 64).

_DB_THREAD_POOL_SIZE = int(_os.environ.get("DB_THREAD_POOL_SIZE") or _os.environ.get("DB_THREAD_POOL_MAX") or "64")
_DB_EXECUTOR = _ThreadPoolExecutor(max_workers=_DB_THREAD_POOL_SIZE, thread_name_prefix="spinr-db")


def _record_db_queue_depth() -> None:
    queue = getattr(_DB_EXECUTOR, "_work_queue", None)
    if queue is not None:
        _metric_gauge("spinr_db_thread_pool_queue_depth", queue.qsize())


def get_db_pool_stats() -> dict:
    """Live DB thread-pool + circuit-breaker state, sampled on demand.

    Exists because the pool gauges are only written from inside ``run_sync``:
    ``spinr_db_thread_pool_threads`` / ``_max_workers`` are set on the SUCCESS
    path only (see the ``else`` branch below), so during an outage — exactly
    when someone is reading them — they hold whatever the last successful call
    left behind. Worse, if traffic stops entirely the queue-depth gauge freezes
    too, so a saturated-then-idle pool and a healthy pool look identical.

    Sampling the executor directly makes the reading independent of whether any
    query has run recently. The capacity watchdog polls this; it also refreshes
    the queue-depth gauge as a side effect so a /metrics scrape between two
    queries reports a current value rather than a stale one.

    Read-only: no DB call, no Redis call, no lock — safe to call from a loop on
    every replica.
    """
    queue = getattr(_DB_EXECUTOR, "_work_queue", None)
    queue_depth = queue.qsize() if queue is not None else 0

    _metric_gauge("spinr_db_thread_pool_queue_depth", queue_depth)

    return {
        "queue_depth": queue_depth,
        "threads": len(getattr(_DB_EXECUTOR, "_threads", ()) or ()),
        "max_workers": getattr(_DB_EXECUTOR, "_max_workers", _DB_THREAD_POOL_SIZE),
        "breaker_state": getattr(_breaker, "_state", "unknown"),
    }


# ── Error redaction ─────────────────────────────────────────────────
# Postgres puts real column values in its error text, and this module is the
# funnel every table's errors pass through. Redact before the string reaches a
# log line OR a DatabaseError/DuplicateRecordError detail — CLAUDE.md tells
# callers to log `e.details["original"]` when handling a DatabaseError, so the
# details dict is a log sink in practice, not just an internal field.

# Markers after which Postgres emits raw row/column data. Values have no fixed
# terminator and may contain arbitrary punctuation (including parens), so we
# truncate at the marker rather than trying to match balanced delimiters.
_PG_VALUE_MARKERS = ("Failing row contains", "DETAIL:")


def _redact_pg_error(exc_str: str) -> str:
    """Strip row/column values from a PostgREST/Postgres error string.

    Keeps the parts that make an error actionable — error class, constraint
    name, column name — and drops the parts that are user data:

    * ``Key (phone)=(+13065551234) already exists``
      → ``Key (phone)=(<redacted>) already exists``
    * ``... violates check constraint "x"  Failing row contains (Jane Doe, +1306..., 52.13, -106.67)``
      → ``... violates check constraint "x" <values redacted>``
    """
    if not exc_str:
        return exc_str
    # Column name is schema (safe, and the useful half); the value is not.
    redacted = _re.sub(r"Key \(([^)]*)\)=\([^)]*\)", r"Key (\1)=(<redacted>)", exc_str)
    cut = min((i for i in (redacted.find(m) for m in _PG_VALUE_MARKERS) if i != -1), default=-1)
    if cut != -1:
        redacted = redacted[:cut].rstrip() + " <values redacted>"
    return redacted


# ── Retry policy ────────────────────────────────────────────────────

RetryPolicy = Literal["read", "idempotent_write", "write"]
_BACKOFFS_BY_POLICY: Dict[str, list] = {
    "read": [0.5, 1.5],
    "idempotent_write": [0.75],
    "write": [],
}

_RETRY_BUDGET_PER_SEC = int(_os.environ.get("RETRY_BUDGET_PER_SEC", "50"))


async def _consume_retry_token() -> bool:
    """Reserve one retry from the global per-second budget."""
    if _RETRY_BUDGET_PER_SEC <= 0:
        return True
    try:
        second = int(_time.time())
        key = f"spinr:retry_budget:{second}"
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 5)
        return count <= _RETRY_BUDGET_PER_SEC
    except Exception as exc:
        logger.debug(f"[RETRY] Budget check failed (fail-open): {exc}")
        return True


def _jittered(delay: float) -> float:
    """Full jitter: delay * (0.5 + random())."""
    return delay * (0.5 + _random.random())


async def run_sync(
    func: Callable[[], T],
    retry_policy: RetryPolicy = "read",
) -> T:
    """Run a synchronous Supabase call in a thread and retry on transient
    HTTP/2 connection errors.

    Retry behaviour is policy-gated:
      - read: 3 attempts, exponential backoff 500ms→1500ms with jitter.
      - idempotent_write: 2 attempts, 750ms with jitter.
      - write: 1 attempt, no retry.
    """
    remaining = _remaining_seconds()
    if remaining is not None and remaining <= 0:
        _metric_inc("spinr_db_calls_rejected_total", {"reason": "deadline_exhausted"})
        raise ServiceUnavailableException("database")

    if not _breaker.should_allow():
        _metric_inc("spinr_db_calls_rejected_total", {"reason": "circuit_open"})
        raise ServiceUnavailableException("database")

    loop = asyncio.get_running_loop()
    backoffs = _BACKOFFS_BY_POLICY.get(retry_policy, _BACKOFFS_BY_POLICY["read"])
    last_exc: Exception | None = None
    last_exc_transient = False
    _metric_inc("spinr_db_calls_total", {"policy": retry_policy})

    for attempt in range(len(backoffs) + 1):
        try:
            remaining = _remaining_seconds()
            if remaining is not None and remaining <= 0:
                _metric_inc("spinr_db_calls_rejected_total", {"reason": "deadline_exhausted"})
                _breaker.release_probe()
                raise ServiceUnavailableException("database")

            future = loop.run_in_executor(_DB_EXECUTOR, func)  # type: ignore
            _record_db_queue_depth()
            try:
                if remaining is None:
                    result = await future
                else:
                    result = await asyncio.wait_for(future, timeout=remaining)
            except TimeoutError:
                if future.done() and not future.cancelled():
                    raise
                future.cancel()
                _metric_inc("spinr_db_calls_rejected_total", {"reason": "deadline_timeout"})
                logger.error("[DB] Executor wait exceeded the request deadline")
                _breaker.release_probe()
                raise ServiceUnavailableException("database") from None
            finally:
                _record_db_queue_depth()

            _breaker.record_success()
            _metric_gauge("spinr_db_circuit_state", 0, {"state": "closed"})
            _metric_gauge("spinr_db_thread_pool_threads", len(_DB_EXECUTOR._threads))
            _metric_gauge("spinr_db_thread_pool_max_workers", _DB_EXECUTOR._max_workers)
            return result
        except ServiceUnavailableException:
            raise
        except Exception as exc:
            if isinstance(exc, ValueError):
                _breaker.release_probe()
                raise
            last_exc = exc
            exc_name = type(exc).__name__
            exc_str = str(exc)
            is_conn_terminated = "ConnectionTerminated" in exc_name or "ConnectionTerminated" in exc_str
            is_remote_disconnect = (
                "RemoteProtocolError" in exc_name or "Server disconnected" in exc_str or "ConnectionClosed" in exc_name
            )
            is_timeout = _HTTPX_TIMEOUT_EXC is not None and isinstance(exc, _HTTPX_TIMEOUT_EXC)
            is_h2_stream_race = isinstance(exc, KeyError) and "http2" in traceback.format_exc().lower()
            # httpx NetworkError family: ConnectError/ReadError/WriteError/CloseError.
            # Covers SSL "EOF occurred in violation of protocol" (WriteError) seen when
            # a pooled TLS connection is reused after the server closed it (e.g. cold start).
            is_network_error = _HTTPX_NETWORK_EXC is not None and isinstance(exc, _HTTPX_NETWORK_EXC)
            is_transient = (
                is_conn_terminated or is_remote_disconnect or is_timeout or is_h2_stream_race or is_network_error
            )
            last_exc_transient = is_transient

            if not is_transient:
                break

            if attempt < len(backoffs):
                next_backoff = backoffs[attempt]
                if _deadline_exhausted(now_margin_seconds=next_backoff):
                    _metric_inc(
                        "spinr_db_retry_skipped_total",
                        {"reason": "deadline_exhausted", "policy": retry_policy},
                    )
                    logger.warning(
                        f"Supabase transient failure ({exc_name}) — client deadline exhausted, "
                        f"skipping {next_backoff}s retry"
                    )
                    break

                if not await _consume_retry_token():
                    _metric_inc(
                        "spinr_db_retry_skipped_total",
                        {"reason": "budget_exhausted", "policy": retry_policy},
                    )
                    logger.warning(
                        f"Supabase transient failure ({exc_name}) — retry budget exhausted, "
                        f"returning 503 instead of retrying"
                    )
                    break

                delay = _jittered(next_backoff)
                _metric_inc(
                    "spinr_db_retry_total",
                    {"policy": retry_policy, "reason": exc_name},
                )
                logger.warning(
                    f"Supabase transient failure ({exc_name}) on attempt {attempt + 1}, retrying in {delay:.2f}s: {exc}"
                )
                await asyncio.sleep(delay)
                continue

            logger.error(f"Supabase transient failure ({exc_name}) exhausted retries: {exc}")

    if last_exc_transient:
        _breaker.record_failure()
    else:
        # The server responded: application-level errors (duplicate keys,
        # FK/CHECK violations, RLS denials, bad requests) prove connectivity
        # is healthy and must NOT count against the breaker. Recording them
        # as failures let the multi-replica duplicate-claim idempotency
        # pattern (expected 23505 bursts, e.g. the daily onboarding-reminder
        # scan) open the breaker and 503 the entire API.
        _breaker.record_success()
    _metric_gauge(
        "spinr_db_circuit_state",
        1 if _breaker._state == "open" else (0.5 if _breaker._state == "half_open" else 0),
        {"state": _breaker._state},
    )
    assert last_exc is not None
    raw_exc_str = str(last_exc)
    exc_str = _redact_pg_error(raw_exc_str)
    exc_name = type(last_exc).__name__
    exc_str_lower = raw_exc_str.lower()
    if "duplicate key" in exc_str_lower or "unique constraint" in exc_str_lower or "23505" in exc_str_lower:
        _metric_inc("spinr_db_errors_total", {"kind": "duplicate_key"})
        raise DuplicateRecordError(details={"original": exc_str}) from last_exc
    _metric_inc("spinr_db_errors_total", {"kind": "database_error"})
    logger.error(f"[DB] Supabase call failed ({exc_name}): {exc_str}")
    raise DatabaseError(details={"original": exc_str, "exception_type": exc_name}) from last_exc


# ── Serialization helpers ───────────────────────────────────────────


def _serialize_for_api(data: Any) -> Any:
    """Recursively prepare a payload for Supabase/PostgREST JSON encoding."""
    if isinstance(data, dict):
        return {k: _serialize_for_api(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_serialize_for_api(v) for v in data]
    if isinstance(data, (datetime, date)):
        return data.isoformat()
    if isinstance(data, Decimal):
        return str(data)
    return data


def _single_row_from_res(res: Any) -> Optional[Dict[str, Any]]:
    if not res:
        return None
    data = None
    if isinstance(res, dict):
        data = res.get("data")
    else:
        data = getattr(res, "data", None)
    if not data:
        return None
    if isinstance(data, list):
        return data[0] if len(data) > 0 else None
    return data


def _rows_from_res(res: Any) -> List[Dict[str, Any]]:
    if not res:
        return []
    data = None
    if isinstance(res, dict):
        data = res.get("data")
    else:
        data = getattr(res, "data", None)
    return data or []


# ── Row-level Redis Cache ───────────────────────────────────────────

_USER_CACHE_TTL_SECONDS = 30
_DRIVER_CACHE_TTL_SECONDS = 30
_DRIVER_BY_USER_CACHE_TTL_SECONDS = 30
_NEGATIVE_CACHE_SENTINEL = "__none__"


def _user_cache_key(user_id: str) -> str:
    return f"cache:user:{user_id}"


def _driver_cache_key(driver_id: str) -> str:
    return f"cache:driver:{driver_id}"


def _driver_by_user_cache_key(user_id: str) -> str:
    return f"cache:driver:by_user:{user_id}"


def _metric_prefix_for_key(key: str) -> str:
    """Map a cache key to a stable low-cardinality prefix label."""
    parts = key.split(":", 3)
    if len(parts) < 2:
        return "unknown"
    if len(parts) >= 3 and parts[1] == "driver" and parts[2] == "by_user":
        return "driver_by_user"
    return parts[1]


async def _read_cached_row(key: str) -> Optional[Dict[str, Any]]:
    """Return the cached row, or None on miss / corrupt entry."""
    prefix = _metric_prefix_for_key(key)
    try:
        raw = await redis_get(key)
    except Exception as exc:
        _metric_inc("spinr_cache_error_total", {"prefix": prefix, "op": "get"})
        logger.debug(f"[CACHE] redis_get failed for {key}: {exc}")
        return None
    if raw is None:
        _metric_inc("spinr_cache_miss_total", {"prefix": prefix})
        return None
    if raw == _NEGATIVE_CACHE_SENTINEL:
        _metric_inc("spinr_cache_hit_total", {"prefix": prefix, "kind": "negative"})
        return {}
    try:
        value = _json.loads(raw)
        _metric_inc("spinr_cache_hit_total", {"prefix": prefix, "kind": "positive"})
        return value
    except Exception:
        _metric_inc("spinr_cache_error_total", {"prefix": prefix, "op": "decode"})
        try:
            await redis_delete(key)
        except Exception:
            # loguru formats with str.format, not %-interpolation, and takes
            # `exception=`, not `exc_info=`. The previous stdlib-style call
            # emitted a literal "%s" and no traceback at all.
            logger.opt(exception=True).warning(f"Failed to evict corrupt cache key {key}")
        return None


async def _write_cached_row(key: str, value: Optional[Dict[str, Any]], ttl: int) -> None:
    try:
        if value is None:
            await redis_set(key, _NEGATIVE_CACHE_SENTINEL, ttl=ttl)
        else:
            await redis_set(key, _json.dumps(value, default=str), ttl=ttl)
    except Exception as exc:
        logger.debug(f"[CACHE] redis_set failed for {key}: {exc}")


async def invalidate_user_cache(user_id: Optional[str]) -> None:
    """Drop the cached users row. Safe to call with None."""
    if not user_id:
        return
    try:
        await redis_delete(_user_cache_key(user_id))
    except Exception as exc:
        logger.debug(f"[CACHE] Failed to invalidate user cache {user_id}: {exc}")


async def invalidate_driver_cache(driver_id: Optional[str] = None, user_id: Optional[str] = None) -> None:
    """Drop the cached drivers row (and the by-user index)."""
    try:
        if driver_id:
            await redis_delete(_driver_cache_key(driver_id))
        if user_id:
            await redis_delete(_driver_by_user_cache_key(user_id))
    except Exception as exc:
        logger.debug(f"[CACHE] Failed to invalidate driver cache d={driver_id} u={user_id}: {exc}")


async def _pre_invalidate_for_table(
    table: str,
    filters: Optional[Dict[str, Any]],
) -> None:
    """Pre-write cache eviction to close read-between-write-and-delete races."""
    if not isinstance(filters, dict):
        return
    if table == "users":
        await invalidate_user_cache(filters.get("id"))
    elif table == "drivers":
        await invalidate_driver_cache(
            driver_id=filters.get("id"),
            user_id=filters.get("user_id"),
        )


# ── Query / Filter Helpers ──────────────────────────────────────────


def _postgrest_pattern(value: str) -> str:
    """Escape PostgREST wildcard characters in user input."""
    return str(value).replace("*", r"\*").replace(",", r"\,").replace("(", r"\(").replace(")", r"\)")


def _escape_like(value: str) -> str:
    r"""Escape SQL LIKE/ILIKE wildcards in user input (C6).

    Without this, a user-supplied ``%`` or ``_`` in a $regex search acts as a
    wildcard (over-match) and ``%`` allows a cheap ReDoS-style scan. Escape the
    escape char first, then the two wildcards. Postgres LIKE treats ``\`` as the
    default ESCAPE, so ``\%`` / ``\_`` match a literal percent / underscore.
    """
    return str(value).replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _unwrap_enum(v: Any) -> Any:
    """Return the .value of Enum instances so PostgREST sees plain strings."""
    return v.value if isinstance(v, _Enum) else v


_OR_VALUE_NEEDS_QUOTE = _re.compile(r'[,()"\\]')


def _postgrest_or_value(value: Any) -> str:
    r"""Render one scalar as a PostgREST value inside an ``or=(...)`` group.

    Inside an or-group PostgREST splits terms on ``,`` and closes the group on
    ``)``, so a value containing either silently truncates or corrupts the
    *whole* clause rather than just its own term. Such values are double-quoted
    (with ``"`` and ``\`` backslash-escaped, the only escaping PostgREST honours
    inside a quoted value). Values with no reserved character are emitted bare
    so the generated clause stays byte-identical to what callers relied on
    before quoting existed — timestamps (``:``) and emails (``.``) parse fine
    unquoted because PostgREST splits ``col.op.value`` on the first two dots
    only.
    """
    s = str(_unwrap_enum(value))
    if _OR_VALUE_NEEDS_QUOTE.search(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


# Operators `_apply_filters` knows how to compile. `$options` is a modifier on
# `$regex`, not an operator of its own. Anything outside this set raises rather
# than being silently ignored — see the comment in _apply_filters.
_SUPPORTED_FILTER_OPS = frozenset(
    {"$in", "$nin", "$gt", "$gte", "$lt", "$lte", "$ne", "$eq", "$notnull", "$regex", "$options"}
)


def _build_or_clause_term(col: str, val: Any) -> Optional[str]:
    """Convert one {col: predicate} pair into a PostgREST or_() leaf term.

    Returns None ONLY for an empty ``$in`` list, which matches no rows and so
    contributes nothing to an OR. Every other unsupported predicate raises:
    returning None for them (the previous behaviour) made `_build_or_clause`
    drop the term silently, which *widens* the result set — and for an ``$or``
    used as an update/delete filter would widen it to the entire table. A
    predicate this builder cannot express is a programming error and must
    surface loudly rather than change what the query matches.
    """
    if isinstance(val, dict):
        # Each branch below returns ONE leaf, so a multi-operator predicate
        # (e.g. a two-sided range) would emit only its first operator and
        # silently widen the OR — the same class of bug `_apply_filters` had.
        # PostgREST can nest `and(...)` inside `or(...)`, but that spelling is
        # unverified here, so this raises rather than guessing: loud beats a
        # query that quietly matches more rows.
        _ops = set(val) - {"$options"}
        if len(_ops) > 1:
            raise ValueError(
                f"$or term {col!r}: multiple operators {sorted(_ops)} in one predicate are not supported "
                "as a single or() leaf — split them into separate $and/$or terms rather than letting "
                "one be dropped (a dropped leaf WIDENS the match)."
            )
        if "$regex" in val:
            op = "ilike" if val.get("$options") == "i" else "like"
            # Escape LIKE wildcards before PostgREST-escaping, mirroring the
            # non-$or path in _apply_filters (C6). Without this a `%` typed into
            # an admin search box matches every row, and `_` matches any char.
            return f"{col}.{op}.*{_postgrest_pattern(_escape_like(val['$regex']))}*"
        if "$in" in val:
            values = val["$in"]
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"$or term {col!r}: $in expects a list/tuple, got {type(values).__name__}: {values!r}")
            if not values:
                return None
            return f"{col}.in.({','.join(_postgrest_or_value(v) for v in values)})"
        if "$ne" in val:
            return f"{col}.neq.{_postgrest_or_value(val['$ne'])}"
        if "$gt" in val:
            return f"{col}.gt.{_postgrest_or_value(val['$gt'])}"
        if "$gte" in val:
            return f"{col}.gte.{_postgrest_or_value(val['$gte'])}"
        if "$lt" in val:
            return f"{col}.lt.{_postgrest_or_value(val['$lt'])}"
        if "$lte" in val:
            return f"{col}.lte.{_postgrest_or_value(val['$lte'])}"
        if "$notnull" in val:
            return f"{col}.not.is.null" if val["$notnull"] else f"{col}.is.null"
        raise ValueError(
            f"$or term {col!r}: unsupported predicate {val!r} — cannot be expressed as a PostgREST or() leaf"
        )
    if val is None:
        return f"{col}.is.null"
    return f"{col}.eq.{_postgrest_or_value(val)}"


def _build_or_clause(clauses: List[Dict[str, Any]]) -> str:
    """Flatten a list of {col: predicate} dicts into a PostgREST or_() string."""
    parts: List[str] = []
    for clause in clauses or []:
        for col, val in clause.items():
            term = _build_or_clause_term(col, val)
            if term is not None:
                parts.append(term)
    return ",".join(parts)


def _apply_filters(q, filters: Optional[Dict[str, Any]]):
    if not filters:
        return q
    if not isinstance(filters, dict):
        # A non-dict filter (almost always a bare id string passed where a
        # {"id": ...} dict was expected) would otherwise blow up inside
        # supabase-py as the opaque "'str' object has no attribute 'items'".
        # Surface it loudly with the offending value so the caller is obvious.
        raise TypeError(f"_apply_filters expected a dict of filters, got {type(filters).__name__}: {filters!r}")
    for k, v in filters.items():
        if k == "$or" and isinstance(v, list):
            clause = _build_or_clause(v)
            if clause:
                q = q.or_(clause)
            elif v:
                # Every leaf collapsed to "matches nothing" (empty $in lists), so
                # the OR as a whole matches nothing — but applying no filter would
                # instead match the ENTIRE table, and on an update/delete would
                # write it. Callers must guard the empty case themselves.
                raise ValueError(
                    f"$or filter {v!r} produced no PostgREST terms; every leaf matches nothing. "
                    "Guard the empty case in the caller rather than issuing an unfiltered query."
                )
            continue
        if k == "$and" and isinstance(v, list):
            for sub in v:
                q = _apply_filters(q, sub)
            continue
        if isinstance(v, dict):
            # EVERY operator in the dict is applied, not just the first match.
            # This was an if/elif chain, so a two-sided range —
            # {"$gte": start, "$lt": end}, the ordinary way to express "inside
            # this period" — silently compiled to the lower bound ALONE. The
            # query then returned everything from `start` onward, and because
            # nothing signalled the drop it read as correct data.
            #
            # That produced real money errors: every weekly/monthly driver
            # statement summed payouts from its period start to the present
            # (so all periods reported the same total), and the T4A year
            # windows pulled later years into an earlier slip, over-reporting
            # income to the CRA. Some callers had already worked around it by
            # hand with $and of single-operator dicts
            # (routes/admin/analytics.py) — that workaround is now unnecessary
            # but stays correct.
            #
            # A predicate this compiler does not recognise still raises rather
            # than being ignored, for the same reason the $or builder raises:
            # a dropped predicate WIDENS the result set, and _apply_filters is
            # shared with update/delete.
            unknown = set(v) - _SUPPORTED_FILTER_OPS
            if unknown:
                raise ValueError(
                    f"filter {k!r}: unsupported operator(s) {sorted(unknown)} in {v!r}. "
                    "Add it to _apply_filters rather than letting it be dropped — "
                    "a silently ignored predicate matches MORE rows, not fewer."
                )
            if "$in" in v:
                if not isinstance(v["$in"], (list, tuple)):
                    raise ValueError(f"filter {k!r}: $in expects a list/tuple, got {v['$in']!r}")
                q = q.in_(k, [_unwrap_enum(x) for x in v["$in"]])
            if "$eq" in v:
                # Explicit equality, distinct from the bare `{col: value}` form
                # below: a bare `None` value compiles to `IS NULL` (needed for
                # nullable columns), but some columns are NOT NULL with a
                # non-null default (e.g. `legacy_import_metadata JSONB NOT
                # NULL DEFAULT '{}'::jsonb`) where "no data" is represented by
                # that default value, not SQL NULL — `{col: None}` against
                # such a column is unsatisfiable and silently matches zero
                # rows instead of raising (found live via A26,
                # docs/audit/2026-08-11-driver-rider-migration-audit.md).
                # `{col: {"$eq": <value>}}` lets a caller filter on the exact
                # value (including a dict/list, e.g. `{}`) without that trap.
                q = q.eq(k, _unwrap_enum(v["$eq"]))
            if "$gt" in v:
                q = q.gt(k, _unwrap_enum(v["$gt"]))
            if "$gte" in v:
                q = q.gte(k, _unwrap_enum(v["$gte"]))
            if "$lt" in v:
                q = q.lt(k, _unwrap_enum(v["$lt"]))
            if "$lte" in v:
                q = q.lte(k, _unwrap_enum(v["$lte"]))
            if "$ne" in v:
                q = q.neq(k, _unwrap_enum(v["$ne"]))
            if "$notnull" in v:
                # SQL `<> NULL` never matches; PostgREST needs `not.is.null`.
                # Lets callers filter server-side instead of scanning every row
                # and dropping the nulls in Python (e.g. users with a
                # referral_code_used). {"$notnull": False} mirrors {col: None}.
                if v["$notnull"]:
                    q = q.not_.is_(k, "null")
                else:
                    q = q.is_(k, "null")
            if "$nin" in v:
                if not isinstance(v["$nin"], (list, tuple)):
                    raise ValueError(f"filter {k!r}: $nin expects a list/tuple, got {v['$nin']!r}")
                q = q.not_.in_(k, [_unwrap_enum(x) for x in v["$nin"]])
            if "$regex" in v:
                # Escape LIKE wildcards in user input so `%`/`_` can't over-match
                # or be used as a cheap scan vector (C6).
                pattern = f"%{_escape_like(v['$regex'])}%"
                if v.get("$options") == "i":
                    q = q.ilike(k, pattern)
                else:
                    q = q.like(k, pattern)
        elif v is None:
            # SQL `= NULL` never matches; PostgREST needs `is.null`. Without
            # this, a {"driver_id": None} filter silently matches zero rows.
            q = q.is_(k, "null")
        else:
            q = q.eq(k, v.value if isinstance(v, _Enum) else v)
    return q


# ── Generic CRUD ────────────────────────────────────────────────────


def _write_skipped(op: str, table: str) -> None:
    """Log a write that never reached a database because the client is absent.

    Every write helper below returns a benign empty value when ``supabase`` is
    falsy — ``None`` or ``[]`` — WITHOUT raising. That is what makes this class
    of failure invisible: the caller sees a normal return, there is no
    exception to catch, and code that treats "no exception" as "written"
    reports success for a write that never happened. services/ledger_service.py
    hit exactly this and had to add its own guard before it could tell a
    written tax-ledger row from an unwritten one.

    ERROR, not warning: CLAUDE.md is explicit that a DB error must never be
    logged at warning and continued past. This IS a DB error — the write is
    lost — it simply arrives without an exception attached.

    Why these still do not RAISE (the question `update_one` deferred):
    core/lifespan.py raises on a falsy client when ENV == production, so
    Uvicorn never serves traffic in that state and production cannot reach
    here. Below production it deliberately warns and boots so local work
    without Supabase is possible — and raising from every write helper would
    destroy exactly that affordance. Loud and honest beats fatal here; a money
    path that needs more than a log should check the client itself, as
    ledger_service now does.
    """
    logger.error(f"{op}({table}): supabase client is not configured — WRITE SKIPPED, no data was persisted")


async def get_rows(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    order: Optional[str] = None,
    desc: bool = False,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    columns: str = "*",
):
    if not supabase:
        return []

    def _fn():
        q = supabase.table(table).select(columns)
        q = _apply_filters(q, filters)
        if order:
            q = q.order(order, desc=desc)
        if limit is not None and offset is not None:
            q = q.range(offset, offset + limit - 1)
        elif limit:
            q = q.limit(limit)
        elif offset is not None:
            q = q.offset(offset)
        return _rows_from_res(q.execute())

    return await run_sync(_fn)


async def count_documents(table: str, filters: Optional[Dict[str, Any]] = None, id_column: str = "id") -> int:
    """Count rows matching filters. ``id_column`` must name a real column on
    ``table`` — most tables use the default "id", but a few (e.g.
    marketing_preferences) key on something else (user_id) and have no id
    column at all."""
    if not supabase:
        return 0

    def _fn():
        q = supabase.table(table).select(id_column, count="exact")
        q = _apply_filters(q, filters)
        q = q.limit(1)
        res = q.execute()
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return 0

    return await run_sync(_fn)


async def find_one(table: str, filters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return the first row matching filters, or None."""
    rows = await get_rows(table, filters, limit=1)
    return rows[0] if rows else None


async def insert_one(table: str, doc: Dict[str, Any]):
    if not supabase:
        _write_skipped("insert_one", table)
        return None
    if not isinstance(doc, dict):
        raise TypeError(f"insert_one({table!r}) doc must be a dict, got {type(doc).__name__}: {doc!r}")
    doc = _serialize_for_api(doc)
    result = await run_sync(lambda: _single_row_from_res(supabase.table(table).insert(doc).execute()))
    # A freshly inserted drivers/users row must evict any negative-cache
    # sentinel (e.g. a cached "no driver for this user_id" empty-dict) so the
    # new row is visible on the very next authenticated request instead of only
    # after the cache TTL expires. update_one/delete already do this; insert did
    # not, which left a just-registered driver looking like is_driver=false.
    if table == "drivers":
        await invalidate_driver_cache(driver_id=doc.get("id"), user_id=doc.get("user_id"))
    elif table == "users":
        await invalidate_user_cache(doc.get("id"))
    return result


async def insert_many(table: str, docs: List[Dict[str, Any]]):
    """Bulk insert using Supabase's native batch insert (single round-trip)."""
    if not supabase:
        # Note the split from `not docs`: an empty batch is a legitimate no-op,
        # an absent client is a lost write. They used to share one branch.
        _write_skipped("insert_many", table)
        return []
    if not docs:
        return []
    _bad = next((d for d in docs if not isinstance(d, dict)), None)
    if _bad is not None:
        raise TypeError(f"insert_many({table!r}) every doc must be a dict, got {type(_bad).__name__}: {_bad!r}")
    serialized = [_serialize_for_api(d) for d in docs]
    return await run_sync(lambda: _rows_from_res(supabase.table(table).insert(serialized).execute()))


async def insert_many_ignore_conflicts(
    table: str, docs: List[Dict[str, Any]], on_conflict: str
) -> List[Dict[str, Any]]:
    """Upsert a batch once, retaining the first row for each conflict key."""
    if not docs:
        return []
    if not isinstance(on_conflict, str) or not on_conflict.strip():
        raise ValueError("on_conflict is required")
    if any(not isinstance(doc, dict) for doc in docs):
        raise TypeError(f"insert_many_ignore_conflicts({table!r}) requires dict rows")
    if not supabase:
        _write_skipped("insert_many_ignore_conflicts", table)
        return []

    rows = [_serialize_for_api(doc) for doc in docs]
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table(table).upsert(rows, on_conflict=on_conflict, ignore_duplicates=True).execute()
        ),
        retry_policy="idempotent_write",
    )


def _log_safe_write(table: str, filters: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Describe a write for the log without emitting any value from it.

    PIPEDA: filter and payload *values* are never safe to log. A ``drivers``
    payload carries raw lat/lng, and the table's plaintext columns include
    ``name``, ``phone``, and ``vehicle_vin`` — all on CLAUDE.md's never-log
    list. So this emits key *names* only, plus a coarse geohash when the write
    carries coordinates.

    Deliberately an allowlist rather than a denylist: a new sensitive column
    added to any table is safe here by default. The previous denylist approach
    ("scrub lat/lng") is exactly why name/phone/VIN leaked for so long.

    Mirrors the pattern already used by ``driver_repo.set_driver_available``
    and ``ride_repo``'s insert path.
    """
    parts = [f"table={table}", f"filter_keys={sorted(filters)}", f"payload_keys={sorted(payload)}"]
    lat, lng = payload.get("lat"), payload.get("lng")
    if lat is not None and lng is not None:
        parts.append(f"geohash={_geohash(lat, lng)}")
    return " ".join(parts)


async def update_one(table: str, filters: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
    if not supabase:
        # This was the warn-and-continue that CLAUDE.md forbids, left in place
        # because insert_many, insert_many_ignore_conflicts, delete_many and
        # driver_repo.claim_driver_atomic all swallowed identically and fixing
        # one in isolation would have been a worse inconsistency. That
        # coordinated change is now done — every write helper routes through
        # _write_skipped, which explains why they log rather than raise.
        _write_skipped("update_one", table)
        return None

    await _pre_invalidate_for_table(table, filters)

    def _fn():
        update_data = update.get("$set", update)
        if not isinstance(update_data, dict):
            # supabase-py .update(<non-dict>) fails deep inside as the opaque
            # "'str' object has no attribute 'items'". Name the table and the
            # offending TYPE so the bad caller is identifiable — but never the
            # payload itself: this message is logged at _base's catch-all error
            # site and also rides into DatabaseError.details["original"], so a
            # repr here would leak the very values this module must not emit.
            raise TypeError(f"update_one({table!r}) payload must be a dict, got {type(update_data).__name__}")
        update_data = _serialize_for_api(update_data)

        if upsert:
            payload = {**filters, **update_data}
            res = supabase.table(table).upsert(payload).execute()
        else:
            q = supabase.table(table).update(update_data)
            q = _apply_filters(q, filters)
            res = q.execute()

        # PIPEDA: log the shape of the write and whether it landed — never the
        # values, and never res.data. PostgREST returns the full updated row by
        # default, which for `drivers` carries plaintext name, phone, and
        # vehicle_vin alongside raw lat/lng.
        #
        # This replaces a pair of [GO-ONLINE] debug lines that dumped both
        # `payload` and `res_data` for every write to `drivers`. They were
        # redundant as well as unsafe: routes/drivers/status.py already logs the
        # pre-write state, the post-write re-read, and the RLS/service-role
        # diagnosis for the silent-no-op case they were added to chase.
        _rows = getattr(res, "data", None) or []
        _n = len(_rows) if isinstance(_rows, list) else 0
        logger.info(f"update_one executed: {_log_safe_write(table, filters, update_data)} rows_updated={_n}")

        return _single_row_from_res(res)

    result = await run_sync(_fn)

    if table == "users":
        user_id = None
        if isinstance(result, dict):
            user_id = result.get("id")
        if not user_id:
            user_id = filters.get("id") if isinstance(filters, dict) else None
        await invalidate_user_cache(user_id)
    elif table == "drivers":
        driver_id = None
        user_id = None
        if isinstance(result, dict):
            driver_id = result.get("id")
            user_id = result.get("user_id")
        if isinstance(filters, dict):
            driver_id = driver_id or filters.get("id")
            user_id = user_id or filters.get("user_id")
        await invalidate_driver_cache(driver_id=driver_id, user_id=user_id)

    return result


async def delete_many(table: str, filters: Dict[str, Any]):
    if not supabase:
        _write_skipped("delete_many", table)
        return None

    await _pre_invalidate_for_table(table, filters)

    def _fn():
        q = supabase.table(table).delete()
        q = _apply_filters(q, filters)
        res = q.execute()
        return _rows_from_res(res)

    rows = await run_sync(_fn)

    if table == "users":
        for r in rows or []:
            await invalidate_user_cache(r.get("id") if isinstance(r, dict) else None)
        if isinstance(filters, dict) and filters.get("id"):
            await invalidate_user_cache(filters["id"])
    elif table == "drivers":
        for r in rows or []:
            if isinstance(r, dict):
                await invalidate_driver_cache(driver_id=r.get("id"), user_id=r.get("user_id"))
        if isinstance(filters, dict):
            await invalidate_driver_cache(driver_id=filters.get("id"), user_id=filters.get("user_id"))

    return rows


async def delete_one(table: str, filters: Dict[str, Any]):
    return await delete_many(table, filters)


async def rpc(func_name: str, params: Dict[str, Any]):
    if not supabase:
        # Named as a write even though an RPC can be read-only: the money-moving
        # Postgres functions (wallet_pay_for_ride, corporate_wallet_apply_delta,
        # settle_ride_card_payment) all come through an rpc call, so a silent
        # None here is a silently-unapplied wallet delta. A caller that must
        # distinguish "not called" from "returned NULL" checks the client
        # itself — repositories/ledger_repo.py raises SettleRpcUnavailable.
        _write_skipped("rpc", func_name)
        return None

    def _fn():
        res = supabase.rpc(func_name, params).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


# ── Health Check ────────────────────────────────────────────────────


async def ping() -> dict:
    """Liveness probe with latency and circuit breaker telemetry."""

    def _check():
        # Probe the `settings` table (single config row, id='app_settings').
        # NB: the table is `settings`; `app_settings` is the row id, not a table
        # — querying table("app_settings") 503s the /health readiness probe and
        # aborts rolling deploys (PGRST205: table not found).
        supabase.table("settings").select("id").limit(1).execute()

    t0 = _time.monotonic()
    try:
        await run_sync(_check)
        latency_ms = (_time.monotonic() - t0) * 1000
        _metric_gauge("spinr_db_ping_duration_ms", latency_ms)
        _metric_inc("spinr_db_ping_total", {"outcome": "success"})
        _metric_gauge(
            "spinr_db_circuit_state",
            {"closed": 0, "half_open": 0.5, "open": 1}.get(_breaker._state, 0),
            {"state": _breaker._state},
        )
        return {
            "ping_ms": round(latency_ms, 1),
            "circuit_state": _breaker._state,
        }
    except Exception as exc:
        latency_ms = (_time.monotonic() - t0) * 1000
        _metric_gauge("spinr_db_ping_duration_ms", latency_ms)
        _metric_inc("spinr_db_ping_total", {"outcome": "failed"})
        _metric_gauge(
            "spinr_db_circuit_state",
            {"closed": 0, "half_open": 0.5, "open": 1}.get(_breaker._state, 0),
            {"state": _breaker._state},
        )
        logger.error(f"[DB] ping failed in {latency_ms:.0f}ms, circuit={_breaker._state}: {exc}")
        raise DatabaseError(
            details={
                "original": str(exc),
                "ping_ms": round(latency_ms, 1),
                "circuit_state": _breaker._state,
            }
        ) from exc
