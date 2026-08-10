"""The loguru stderr sink must not render local variable values into tracebacks.

Sibling of test_sentry_frame_vars.py, for the other egress path. Sentry is
covered by ``include_local_variables=False``; the stderr JSON sink in
``server.py`` is covered by ``diagnose=False``.

loguru defaults ``diagnose`` to **True**, which annotates every traceback frame
with the values of the locals and arguments in scope::

    settle_fare(rider_email="rider@example.com", amount=Decimal("42.50"))
    └ <the rider row, with phone and address>

``utils/log_guard.py`` cannot catch this. The guard scrubs ``record["message"]``
and ``record["extra"]``; the annotated frames are rendered by the *sink* from
``record["exception"]``, downstream of both. So the control has to be the sink
option itself, and it is asserted here as a real round trip through a loguru
sink rather than by reading the argument back — the option only means anything
in loguru's own rendering path.

``backtrace=True`` is deliberately kept: the stack is what makes a payment or
dispatch error actionable. It is the variable *values* that must never be logged
(CLAUDE.md → Compliance (PIPEDA), "What can NEVER appear in logs").
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger

pytestmark = pytest.mark.unit

RIDER_EMAIL = "rider@example.com"
RIDER_PHONE = "+13065551234"


def _boom(rider_email: str, rider_phone: str, amount: str) -> None:
    """A stand-in for a settlement helper called with real PII arguments."""
    raise ValueError("stripe unavailable")


def test_loguru_logger_is_the_real_object_not_a_session_stub():
    """Guard: three test modules replace loguru.logger with a MagicMock.

    test_p3_loop_jitter_metrics.py, test_p3_ws_broadcast.py and
    test_ws_health.py each run ``sys.modules["loguru"].logger = MagicMock()``
    at import time, permanently. If that has happened before this module is
    imported, every ``logger.add`` below is a no-op on a mock and the
    assertions still pass having rendered nothing — the same silent-vacuity
    trap test_sentry_frame_vars.py documents for sentry_sdk. Fail loudly.
    """
    assert not isinstance(logger, MagicMock)
    assert callable(getattr(logger, "add", None))
    assert callable(getattr(logger, "opt", None))


@pytest.fixture(autouse=True)
def _own_the_handler_set():
    """Take exclusive control of loguru's global state for each test.

    An additive `logger.add(...)` / `logger.remove(id)` is not enough. Other
    modules mutate the same global logger from several directions —
    test_log_guard.py's fixture calls a bare `logger.remove()` (drops every
    handler) and `logger.configure(patcher=...)`, and three test modules
    replace `loguru.logger` with a MagicMock at import time. An earlier version
    of this file used the additive form: it passed alone and in several
    subsets, failed in two full-suite runs, and passed in a third with no code
    change. A test that exists to prove a PIPEDA control is in place must not
    be the flakiest thing in the suite.

    So: clear every handler and any inherited patcher, run with only our sink,
    then restore a plain stderr handler (matching what test_log_guard.py
    already leaves behind, so this is no more disruptive than the existing
    convention).
    """
    logger.remove()
    logger.configure(patcher=None)
    yield
    logger.remove()
    logger.configure(patcher=None)
    logger.add(sys.stderr, level="INFO")


def _capture(**sink_kwargs) -> str:
    stream = io.StringIO()
    sink_id = logger.add(stream, level="INFO", format="{message}", **sink_kwargs)
    try:
        try:
            _boom(RIDER_EMAIL, RIDER_PHONE, "42.50")
        except Exception as exc:
            logger.opt(exception=True).error("settlement failed: {}", exc)
    finally:
        logger.remove(sink_id)
    return stream.getvalue()


def test_diagnose_false_keeps_pii_argument_values_out_of_the_traceback():
    rendered = _capture(backtrace=True, diagnose=False)
    assert "stripe unavailable" in rendered, "the error itself must still be logged"
    assert "Traceback" in rendered, "backtrace=True must keep the stack"
    assert RIDER_EMAIL not in rendered
    assert RIDER_PHONE not in rendered


def test_diagnose_true_would_leak_them():
    """Non-vacuity guard: proves the assertion above is testing the real control.

    If loguru ever changes what `diagnose` does, this fails and the test above
    stops being meaningful silently.
    """
    rendered = _capture(backtrace=True, diagnose=True)
    assert RIDER_EMAIL in rendered or RIDER_PHONE in rendered, (
        "diagnose=True no longer annotates frames with values — the diagnose=False "
        "assertion above may now be passing for the wrong reason"
    )


def test_server_stderr_sink_is_configured_with_diagnose_false():
    """The control has to be set where the real sink is added, not just be settable."""
    source = (Path(__file__).resolve().parent.parent / "server.py").read_text()
    add_call = re.search(r"logger\.add\(\s*sys\.stderr,.*?\n\)", source, re.DOTALL)
    assert add_call, "could not find the stderr logger.add(...) call in server.py"
    body = add_call.group(0)
    assert "diagnose=False" in body, "stderr sink must pass diagnose=False (PIPEDA)"
    assert "backtrace=True" in body, "stderr sink should keep the stack for triage"
