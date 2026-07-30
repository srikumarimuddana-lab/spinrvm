"""Runtime PII guard for the loguru sinks (T2, defense-in-depth).

T1 and T1c fixed the *call sites* that were leaking. This guards the *sink*, so a
future call site cannot leak by accident. It is installed once in ``server.py`` as a
loguru ``patcher``, which means it covers both loguru sinks — the stderr JSON sink
and the loguru->Sentry bridge — from a single place.

Why a sink guard is needed at all: ``.claude/hooks/pre-commit``'s "PII in logs" step
is a six-pattern source-text denylist. It cannot see a runtime-interpolated payload
— ``logger.info(f"... payload={row}")`` contains none of the words it looks for, and
the values arrive at runtime inside a dict — so it reported "Clean" for the entire
life of the T1 leak. Source-text matching structurally cannot do this job.

Two mechanisms, deliberately different, because the two carriers fail differently:

* ``record["extra"]`` — redacted by KEY NAME. This is the higher-value half. The
  stderr sink runs with ``serialize=True``, which emits ``extra`` as JSON, and loguru
  puts *keyword arguments* into ``extra``: ``logger.info("msg {x}", x=phone)`` leaves
  the raw phone in ``extra`` even when the rendered message is clean. Key-name
  redaction also catches coordinates split across keys (``lat=``/``lng=``), which no
  value pattern can match.

* ``record["message"]`` — redacted by VALUE PATTERN, via ``ai.pii.scrub_pii``, and
  only after a cheap screen. Applied narrowly and reported loudly (see below),
  because rewriting log text is how you destroy observability.

**This guard reports, it does not silently launder.** Every redaction increments
``spinr_logging_pii_redacted_total`` and, once per call site, emits a warning naming
the module/function/line so the *call site* gets fixed. A guard that quietly cleans
up after bad log calls just makes them permanent.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Set, Tuple

try:
    from ..ai.pii import scrub_pii
    from .metrics import inc as _metric_inc
    from .pii import REDACTED, is_sensitive_key
except ImportError:  # pragma: no cover - dual import
    from ai.pii import scrub_pii  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.pii import REDACTED, is_sensitive_key  # type: ignore

# Cheap screen: the only things scrub_pii can match are an '@' (email), a
# decimal-degree pair (coords), a NANP phone shape, or a postal code. If none of
# those shapes is present we skip the full scrub entirely.
#
# Measured on this codebase's log lines: full scrub_pii costs ~23us on a typical
# 124-char line and ~436us on a 2.4KB one; this screen costs ~4.7us and ~92us
# respectively. The screen matters most exactly where the scrub is most expensive.
#
# Deliberately NOT screening on a bare 10-digit run: that is every unix timestamp,
# so it would fire on nearly every line and the screen would buy nothing.
_SCREEN = re.compile(
    r"@"  # email
    r"|-?\d{1,3}\.\d{2,}"  # decimal degrees (also matches money; a cheap over-match)
    r"|(?<![\d+])(?:\+\d{10}|[2-9]\d{9})"  # NANP phone shapes
    r"|\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"  # postal code
)

# Bound the walk so a pathological/deep `extra` cannot spin the guard on a hot path.
_MAX_DEPTH = 4

# Call sites already reported. A leak in a hot loop must not itself become a
# log flood — report once per (module, function, line), then stay quiet.
_reported: Set[Tuple[str, str, int]] = set()

_MAX_REPORTED = 512  # bound the set; a runaway variety of sites should not leak memory


def _redact_mapping(value: Any, depth: int = 0) -> Tuple[Any, bool]:
    """Redact sensitive keys in a nested structure. Returns (value, changed)."""
    if depth >= _MAX_DEPTH:
        return value, False
    changed = False
    try:
        if isinstance(value, dict):
            out: Dict[Any, Any] = {}
            for k, v in value.items():
                if is_sensitive_key(k):
                    out[k] = REDACTED
                    changed = True
                else:
                    nv, ch = _redact_mapping(v, depth + 1)
                    out[k] = nv
                    changed = changed or ch
            return out, changed
        if isinstance(value, (list, tuple)):
            items = []
            for v in value:
                nv, ch = _redact_mapping(v, depth + 1)
                items.append(nv)
                changed = changed or ch
            return type(value)(items), changed
    except Exception:  # noqa: BLE001 - a guard must never break logging
        return value, False
    return value, False


def _report(record: Any, what: str) -> None:
    """Count the redaction and name the call site once, so it gets fixed."""
    try:
        _metric_inc("spinr_logging_pii_redacted_total", {"carrier": what})
    except Exception:  # noqa: BLE001
        pass
    try:
        key = (record.get("name") or "?", (record.get("function") or "?"), int(record.get("line") or 0))
        if key in _reported or len(_reported) >= _MAX_REPORTED:
            return
        _reported.add(key)
        # print, not logger — emitting a log record from inside the patcher would
        # re-enter the patcher. stderr matches where the JSON sink writes.
        import sys

        print(
            f'{{"level":"WARNING","logger":"spinr.log_guard",'
            f'"message":"PII redacted from {what} at {key[0]}:{key[1]}:{key[2]} '
            f'— fix the call site; the sink guard is not a licence to log PII"}}',
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001
        return


def guard_record(record: Any) -> None:
    """loguru ``patcher``: strip PII from a record before any sink sees it.

    Mutates ``record`` in place. Must never raise — losing a log line to a privacy
    guard is worse than the leak it prevents, because it hides the failure that
    produced the line.
    """
    try:
        extra = record.get("extra")
        if isinstance(extra, dict) and extra:
            cleaned, changed = _redact_mapping(extra)
            if changed:
                record["extra"] = cleaned
                _report(record, "extra")

        message = record.get("message")
        if isinstance(message, str) and message and _SCREEN.search(message):
            scrubbed = scrub_pii(message)
            if scrubbed != message:
                record["message"] = scrubbed
                _report(record, "message")
    except Exception:  # noqa: BLE001 - never break logging
        return


def install(logger: Any) -> None:
    """Install the guard on a loguru logger.

    Uses ``configure(patcher=...)`` rather than a per-sink ``filter`` so one call
    covers every sink, including the loguru->Sentry bridge added later in
    ``server.py``. Call before adding sinks.
    """
    logger.configure(patcher=guard_record)


def _reset_for_tests() -> None:
    """Clear the once-per-call-site report memo (tests only)."""
    _reported.clear()
