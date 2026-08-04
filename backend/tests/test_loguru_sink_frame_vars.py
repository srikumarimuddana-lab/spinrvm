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
from pathlib import Path

import pytest
from loguru import logger

pytestmark = pytest.mark.unit

RIDER_EMAIL = "rider@example.com"
RIDER_PHONE = "+13065551234"


def _boom(rider_email: str, rider_phone: str, amount: str) -> None:
    """A stand-in for a settlement helper called with real PII arguments."""
    raise ValueError("stripe unavailable")


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
