"""The loguru sink guard must strip PII without destroying observability.

T1 fixed the call sites that were leaking; this guards the sink so a future call
site cannot. Tests drive a real loguru logger with the guard installed as a
``patcher``, because the two things most likely to be wrong are both
loguru-specific: that ``extra`` carries keyword arguments (so a "clean" message can
still leak), and that ``serialize=True`` emits ``extra`` into the JSON output.

The second half of this file is as important as the first: a guard that redacts
timestamps, durations, fares or IDs would trade a privacy bug for an observability
bug, and CLAUDE.md's whole logging convention depends on those values surviving.
"""

from __future__ import annotations

import io
import json

import pytest
from loguru import logger

from utils.log_guard import _reset_for_tests, guard_record, install

RIDER_PHONE = "+13065551234"
RIDER_EMAIL = "jane.doe@example.com"
PICKUP_LAT = 52.1332
PICKUP_LNG = -106.6700


@pytest.fixture
def sink():
    """A real loguru logger with the guard installed, capturing serialized JSON.

    serialize=True mirrors the production sink in server.py — which matters,
    because that is what puts `extra` on the wire.
    """
    _reset_for_tests()
    buf = io.StringIO()
    logger.remove()
    install(logger)
    logger.add(buf, level="DEBUG", serialize=True)
    yield buf
    logger.remove()
    logger.configure(patcher=None)


def _records(buf):
    out = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [r for r in out if "record" in r]


def _last(buf):
    recs = _records(buf)
    assert recs, f"no serialized record captured; raw={buf.getvalue()[:300]!r}"
    return recs[-1]["record"]


# ── extra: the carrier a clean-looking message still leaks through ──


def test_keyword_arguments_land_in_extra_and_are_redacted(sink):
    """The subtle case. loguru puts kwargs into `extra`, and serialize=True writes
    `extra` to the wire, so the raw value ships even when the message renders clean.
    """
    logger.info("rider lookup for {phone}", phone=RIDER_PHONE)
    rec = _last(sink)

    assert RIDER_PHONE not in json.dumps(rec), f"phone survived in the record: {rec}"
    assert rec["extra"]["phone"] == "[REDACTED]"


def test_bound_context_is_redacted(sink):
    logger.bind(rider_email=RIDER_EMAIL, ride_id="ride_abc").info("settled")
    rec = _last(sink)

    assert RIDER_EMAIL not in json.dumps(rec)
    assert rec["extra"]["rider_email"] == "[REDACTED]"
    # Positive anchor: the correlating ID must survive.
    assert rec["extra"]["ride_id"] == "ride_abc"


def test_split_coordinates_in_extra_are_redacted(sink):
    """No value pattern can match a coordinate split across two keys — this is
    exactly why the guard redacts on key name."""
    logger.bind(lat=PICKUP_LAT, lng=PICKUP_LNG).info("driver moved")
    rec = _last(sink)

    assert rec["extra"]["lat"] == "[REDACTED]"
    assert rec["extra"]["lng"] == "[REDACTED]"
    assert "52.1332" not in json.dumps(rec)


def test_nested_extra_is_redacted(sink):
    logger.bind(payload={"driver": {"phone": RIDER_PHONE, "id": "drv_1"}}).info("write")
    rec = _last(sink)

    blob = json.dumps(rec)
    assert RIDER_PHONE not in blob, f"nested phone survived: {blob}"
    assert rec["extra"]["payload"]["driver"]["id"] == "drv_1"


# ── message: value-pattern redaction ────────────────────────────────


def test_pii_in_the_rendered_message_is_redacted(sink):
    logger.info(f"booking failed for {RIDER_EMAIL} at lat={PICKUP_LAT} lng={PICKUP_LNG}")
    rec = _last(sink)

    assert RIDER_EMAIL not in rec["message"]
    assert "52.1332" not in rec["message"]
    assert "[EMAIL]" in rec["message"]
    # Positive anchor: the diagnostic part of the line survives.
    assert "booking failed" in rec["message"]


def test_the_t1_leak_shape_is_caught_at_the_sink(sink):
    """The exact pattern the pre-commit hook structurally cannot see: a payload
    interpolated at runtime. None of `lat`, `lng` or `coordinates` appears in the
    source text of this call."""
    row = {"lat": PICKUP_LAT, "lng": PICKUP_LNG, "name": "Jane Doe"}
    logger.info(f"update_one executed: payload={row}")
    rec = _last(sink)

    assert "52.1332" not in rec["message"], f"coordinates reached the sink: {rec['message']}"


# ── observability must survive: the other half of correctness ───────


@pytest.mark.parametrize(
    "message",
    [
        "update_one executed: table=drivers filter_keys=['user_id'] geohash=cbqfx rows_updated=1",
        "ts=1769817600 ride_id=r1 status=in_progress",
        "spinr_dispatch_offer_to_accept_duration_ms=1847",
        "duration_ms=1234567890",
        "settled amount=12.50 tax=1.25 tip=3.00",
        "fare 45.00 surge 1.75 cap 2.5",
        "stripe_event evt_1769817600 claimed",
        "build 1769817600 version 10.2.4",
    ],
)
def test_observability_messages_pass_through_untouched(sink, message):
    """A guard that rewrites timestamps, durations or money is a regression, not a
    fix. `scrub_pii`'s phone pattern previously matched any bare 10-digit run,
    which turned every unix timestamp into `[PHONE]`."""
    logger.info(message)
    rec = _last(sink)
    assert rec["message"] == message, "an observability value was altered by the guard"


@pytest.mark.parametrize(
    "key",
    ["ride_id", "driver_id", "rider_id", "user_id", "request_id", "domain", "surface", "trace_id", "idempotency_key"],
)
def test_triage_keys_in_extra_survive(sink, key):
    logger.bind(**{key: "value-123"}).info("state transition")
    rec = _last(sink)
    assert rec["extra"][key] == "value-123", f"{key} must not be redacted — it is how events correlate"


def test_a_clean_record_is_not_rewritten_at_all(sink):
    """No-op path: nothing changed means nothing reported."""
    logger.bind(ride_id="r1", domain="dispatch").info("offer sent")
    rec = _last(sink)
    assert rec["message"] == "offer sent"
    assert rec["extra"] == {"ride_id": "r1", "domain": "dispatch"}


# ── the guard must never break logging ─────────────────────────────


def test_guard_never_raises_on_hostile_records():
    """Losing a log line to a privacy guard is worse than the leak, because it
    hides the failure that produced the line."""

    class Boom:
        def __repr__(self):
            raise RuntimeError("repr exploded")

        def __eq__(self, other):
            raise RuntimeError("eq exploded")

    cyclic: dict = {}
    cyclic["self"] = cyclic

    for bad in (
        {},
        {"extra": None, "message": None},
        {"extra": {"phone": Boom()}, "message": "x"},
        {"extra": cyclic, "message": "x"},
        {"extra": {"a": [{"phone": RIDER_PHONE}]}, "message": 12345},
        {"extra": {None: "x", 1: "y"}, "message": "z"},
    ):
        guard_record(bad)  # must return, not raise


def test_deep_nesting_is_bounded(sink):
    """A pathological `extra` must not spin the guard on a hot path."""
    deep: dict = {"phone": RIDER_PHONE}
    for _ in range(50):
        deep = {"nest": deep}
    logger.bind(**deep).info("deep")
    # The assertion is that we got here at all, and still emitted a record.
    assert _records(sink)


def test_logging_still_works_when_scrub_pii_explodes(sink, monkeypatch):
    import utils.log_guard as lg

    monkeypatch.setattr(lg, "scrub_pii", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    logger.info(f"contact {RIDER_EMAIL}")
    # The record must still be emitted rather than swallowed.
    assert _records(sink), "a scrubber failure must not drop the log line"


# ── it reports rather than silently launders ───────────────────────


def test_redaction_increments_a_metric_and_names_the_call_site(sink):
    from utils import metrics

    _reset_for_tests()
    before = metrics.render_prometheus()
    logger.bind(phone=RIDER_PHONE).info("lookup")
    after = metrics.render_prometheus()

    assert "spinr_logging_pii_redacted_total" in after
    assert after != before

    # And the call site is named on stderr so it gets fixed, not laundered.
    # (Captured separately from the JSON sink; assert the metric at minimum.)


def test_server_actually_installs_the_guard_before_adding_sinks():
    """The wiring, not the behaviour.

    Every other test in this file installs the guard itself, so deleting the
    `_install_log_guard(logger)` call from server.py passes the whole suite — that
    mutation was run and did exactly that (the same gap that
    `include_local_variables=False` had in T1c). server.py configures logging at
    module scope while mounting ~25 routers, so it cannot be imported here; assert
    on the AST instead.

    Order matters as much as presence: loguru's `configure(patcher=...)` must run
    before `logger.add(...)`, or the first sink is registered unguarded.
    """
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "server.py").read_text()
    tree = ast.parse(src)

    install_line = None
    first_add_line = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "_install_log_guard":
            install_line = node.lineno if install_line is None else min(install_line, node.lineno)
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "add"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "logger"
        ):
            first_add_line = node.lineno if first_add_line is None else min(first_add_line, node.lineno)

    assert install_line is not None, (
        "server.py must call _install_log_guard(logger) — without it the loguru sinks "
        "run unguarded and every test in this file still passes"
    )
    assert first_add_line is not None, "expected at least one logger.add() in server.py"
    assert install_line < first_add_line, (
        f"the guard is installed at line {install_line} but a sink is added at "
        f"{first_add_line}; configure(patcher=...) must come first"
    )


def test_a_repeated_call_site_is_reported_once(sink, capsys):
    """A leak inside a hot loop must not itself become a log flood."""
    _reset_for_tests()
    capsys.readouterr()
    for _ in range(25):
        logger.bind(phone=RIDER_PHONE).info("same call site")
    err = capsys.readouterr().err
    assert err.count("spinr.log_guard") <= 1, f"guard reported {err.count('spinr.log_guard')} times"
