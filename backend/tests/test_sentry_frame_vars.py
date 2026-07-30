"""Sentry must not egress stack-frame locals, JSON-body PII, or split coordinates.

Two layers are under test and they are deliberately tested separately:

1. ``include_local_variables=False`` in ``server.py`` — the real control. Asserted
   as a real ``sentry_sdk`` round-trip through a fake transport, because the option
   only has meaning in the SDK's own serialization path.
2. ``sentry_scrub._scrub_frames`` / key-name redaction — defense-in-depth for a
   re-enabled option or a non-capture_exception path.

The round-trip form matters. A test that only calls ``scrub_event`` on a
hand-built dict cannot see the thing that makes this bug subtle: Sentry stringifies
values *before* ``before_send`` runs, so a float ``pickup_lat = 52.1332`` arrives as
``'52.1332'`` in one key and ``'-106.67'`` in another, and no coordinate pattern in
``ai/pii.py`` matches either half alone.
"""

from unittest.mock import MagicMock

import pytest
import sentry_sdk

from utils.sentry_scrub import _key_is_sensitive, _scrub_frames, scrub_event


def test_sentry_sdk_is_the_real_module_not_a_session_stub():
    """Guard: these are PIPEDA egress tests and must never silently no-op.

    Several test modules install permanent ``sys.modules`` MagicMock stubs at import
    time (see the comment in test_admin_security.py). If ``sentry_sdk`` is one of
    them, every round-trip assertion below still *passes* while capturing nothing —
    which is exactly what happened: this file was green in isolation and red only in
    the full suite. Fail loudly instead of trusting a green tick.
    """
    assert not isinstance(sentry_sdk, MagicMock), (
        "sentry_sdk has been replaced by a session-wide MagicMock stub, so the "
        "frame-var egress tests below cannot actually verify anything. Find the "
        "test module that stubs sys.modules['sentry_sdk'] and stop it."
    )
    assert hasattr(sentry_sdk, "VERSION"), "sentry_sdk does not look like the real module"
    assert callable(getattr(sentry_sdk, "capture_exception", None))

# Realistic values — a Saskatoon coordinate pair and a rider identity.
PICKUP_LAT = 52.1332
PICKUP_LNG = -106.6700
RIDER_PHONE = "+13065551234"
RIDER_EMAIL = "jane.doe@example.com"
RIDER_NAME = "Jane Doe"


class _FakeTransport(sentry_sdk.transport.Transport):
    """Collects the events that would have left the process."""

    def __init__(self):
        super().__init__({"dsn": "https://x@o0.ingest.sentry.io/0"})
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            payload = getattr(item, "payload", None)
            data = getattr(payload, "json", None)
            if isinstance(data, dict):
                self.events.append(data)

    def capture_event(self, event):
        self.events.append(event)

    def flush(self, *args, **kwargs):
        pass

    def kill(self):
        pass


_UNSCRUBBED = object()  # sentinel: run the raw SDK with no before_send hook


def _capture_booking_failure(before_send=_UNSCRUBBED, **init_kwargs):
    """Raise from a frame holding rider PII in locals; return what egressed.

    Uses ``isolation_scope()`` + ``set_client()`` rather than ``sentry_sdk.init()``
    so the fake transport never becomes the process-wide client and leaks into other
    tests. ``default_integrations=False`` keeps the event free of ambient noise
    (stdlib logging, excepthook) so the assertions below are about this exception
    only.

    ``before_send`` defaults to *no hook*, so tests measure raw SDK behaviour unless
    they explicitly opt into our scrubber. That split is the point: it keeps the
    "does the option contain the leak" question separate from "does our scrubber
    contain the leak".
    """
    transport = _FakeTransport()
    client = sentry_sdk.Client(
        dsn="https://x@o0.ingest.sentry.io/0",
        transport=transport,
        send_default_pii=False,
        auto_enabling_integrations=False,
        default_integrations=False,
        **({} if before_send is _UNSCRUBBED else {"before_send": before_send}),
        **init_kwargs,
    )

    def book_ride():
        pickup_lat = PICKUP_LAT
        pickup_lng = PICKUP_LNG
        rider_phone = RIDER_PHONE
        rider_email = RIDER_EMAIL
        rider_name = RIDER_NAME
        # Bound so the locals are live in the frame at raise time.
        assert pickup_lat and pickup_lng and rider_phone and rider_email and rider_name
        raise ValueError("booking failed")

    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        try:
            book_ride()
        except ValueError as exc:
            sentry_sdk.capture_exception(exc)
    client.flush()

    return transport.events


def _all_frame_vars(event):
    out = {}
    for val in event.get("exception", {}).get("values", []) or []:
        for frame in (val.get("stacktrace") or {}).get("frames", []) or []:
            if isinstance(frame.get("vars"), dict):
                out.update(frame["vars"])
    return out


def _flatten(value, acc=None):
    """Every string anywhere in the event, for a blanket leak assertion."""
    if acc is None:
        acc = []
    if isinstance(value, str):
        acc.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            acc.append(str(k))
            _flatten(v, acc)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _flatten(v, acc)
    else:
        acc.append(repr(value))
    return acc


# ── Layer 1: the SDK option is the containment ──────────────────────


def test_locals_leak_when_the_option_is_left_at_its_default():
    """Characterizes the bug, so the fix below is provably not a no-op.

    If a future sentry-sdk changes the default to False this test fails, which is
    the correct outcome: it means the risk profile changed and the comment in
    server.py explaining why we set it explicitly needs revisiting.
    """
    events = _capture_booking_failure(include_local_variables=True)
    assert events, "expected an event to egress"
    frame_vars = _all_frame_vars(events[0])

    assert frame_vars, "expected frame vars with the option on — SDK behaviour changed"
    # This is what was reaching Sentry in production, verbatim.
    assert "52.1332" in str(frame_vars.get("pickup_lat"))
    assert "-106.67" in str(frame_vars.get("pickup_lng"))
    assert RIDER_PHONE in str(frame_vars.get("rider_phone"))
    assert RIDER_EMAIL in str(frame_vars.get("rider_email"))
    assert RIDER_NAME in str(frame_vars.get("rider_name"))


def test_scrubber_contains_locals_through_the_real_sdk_if_the_option_is_re_enabled():
    """Defense-in-depth, exercised through the SDK's own serialization path.

    This is the layer that catches a future regression where someone flips
    ``include_local_variables`` back on. Asserted here rather than only against a
    hand-built dict, because the split-coordinate shape this must handle is
    something the SDK creates, not the test.
    """
    events = _capture_booking_failure(include_local_variables=True, before_send=scrub_event)
    assert events, "expected an event to egress"
    frame_vars = _all_frame_vars(events[0])
    assert frame_vars, "frame vars should still be present, just redacted"

    blob = "\n".join(_flatten(events[0]))
    for secret in (RIDER_PHONE, RIDER_EMAIL, RIDER_NAME, "52.1332", "-106.67"):
        assert secret not in blob, f"{secret!r} survived the scrubber: {blob[:600]!r}"


def test_include_local_variables_false_strips_every_frame_var():
    """The actual fix: with the option off, no frame carries vars at all."""
    events = _capture_booking_failure(include_local_variables=False)
    assert events, "expected an event to egress"
    event = events[0]

    frame_vars = _all_frame_vars(event)
    assert frame_vars == {}, f"frame vars survived the option: {frame_vars!r}"

    # And nothing anywhere else in the event carries the values either.
    blob = "\n".join(_flatten(event))
    for secret in (RIDER_PHONE, RIDER_EMAIL, RIDER_NAME, "52.1332", "-106.67"):
        assert secret not in blob, f"{secret!r} egressed in the event: {blob[:600]!r}"


def test_the_exception_message_still_egresses():
    """Containment must not cost us the actual error — this would be a useless fix
    if it stripped the diagnostic payload along with the PII."""
    events = _capture_booking_failure(include_local_variables=False)
    event = events[0]
    values = event.get("exception", {}).get("values", [])
    assert values, "exception payload was dropped"
    assert "booking failed" in str(values[-1].get("value"))
    assert values[-1].get("type") == "ValueError"
    # The traceback itself must survive — only the locals go.
    frames = (values[-1].get("stacktrace") or {}).get("frames", [])
    assert any(f.get("function") == "book_ride" for f in frames), "traceback frames were lost"


# ── Layer 2: defense-in-depth if locals come back ───────────────────


def test_scrub_frames_redacts_split_coordinates_that_patterns_cannot_match():
    """The subtle case: lat and lng in separate keys.

    ``scrub_pii`` needs them adjacent in one string, so value-only scrubbing
    leaves both halves intact. Key-name redaction is what closes this.
    """
    exc = {
        "values": [
            {
                "value": "booking failed",
                "stacktrace": {
                    "frames": [
                        {
                            "function": "book_ride",
                            "vars": {
                                "pickup_lat": "52.1332",
                                "pickup_lng": "-106.67",
                                "rider_name": "'Jane Doe'",
                                "ride_id": "'ride_abc123'",
                            },
                        }
                    ]
                },
            }
        ]
    }
    _scrub_frames(exc)
    got = exc["values"][0]["stacktrace"]["frames"][0]["vars"]

    assert got["pickup_lat"] == "[REDACTED]"
    assert got["pickup_lng"] == "[REDACTED]"
    # Names are not regex-matchable, so the key is the only signal.
    assert got["rider_name"] == "[REDACTED]"
    # Positive anchor: the ID that makes the event triageable is preserved.
    assert got["ride_id"] == "'ride_abc123'", "ride_id must survive — it is not PII"


def test_scrub_event_reaches_frame_vars_end_to_end():
    """Wired into scrub_event, not just importable."""
    event = {
        "exception": {
            "values": [
                {
                    "value": "boom",
                    "stacktrace": {"frames": [{"vars": {"rider_phone": RIDER_PHONE, "attempt": "3"}}]},
                }
            ]
        }
    }
    out = scrub_event(event)
    got = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert RIDER_PHONE not in str(got), f"phone survived scrub_event: {got!r}"
    assert got["attempt"] == "3", "non-PII locals must be preserved for debugging"


def test_request_body_with_split_coordinates_is_redacted():
    """`max_request_body_size` defaults to 'medium' and — verified against the SDK
    — request bodies are NOT gated by send_default_pii (only cookies and sensitive
    headers are). So a booking POST body reaches before_send, with its coordinates
    already split into separate keys."""
    event = {
        "request": {
            "url": "https://api-spinr.spinr.ca/rides",
            "data": {
                "pickup_lat": "52.1332",
                "pickup_lng": "-106.67",
                "rider_phone": RIDER_PHONE,
                "ride_type": "standard",
            },
        }
    }
    out = scrub_event(event)
    data = out["request"]["data"]

    assert data["pickup_lat"] == "[REDACTED]"
    assert data["pickup_lng"] == "[REDACTED]"
    assert RIDER_PHONE not in str(data)
    # Positive anchors: the debuggable parts of the request survive.
    assert data["ride_type"] == "standard"
    assert out["request"]["url"].endswith("/rides")


@pytest.mark.parametrize(
    "key",
    ["pickup_lat", "lng", "rider_phone", "email", "home_address", "postal_code", "vehicle_vin", "auth_token", "cvv"],
)
def test_sensitive_keys_are_detected(key):
    assert _key_is_sensitive(key), f"{key!r} should be treated as sensitive"


@pytest.mark.parametrize(
    "key",
    ["ride_id", "driver_id", "rider_id", "user_id", "request_id", "filename", "attempt", "status", "ride_type"],
)
def test_triage_keys_are_not_redacted(key):
    """Over-redaction is its own failure — these are how an event gets triaged."""
    assert not _key_is_sensitive(key), f"{key!r} must not be redacted"


# ── The wiring itself ───────────────────────────────────────────────
# Without these, deleting include_local_variables=False from server.py passes the
# whole behavioural suite above, because those tests configure their own client.
# That mutation was run and did exactly that, which is why these exist.


def test_pipeda_options_pin_the_controls():
    from utils.sentry_scrub import pipeda_sentry_options, scrub_breadcrumb

    opts = pipeda_sentry_options()
    assert opts["include_local_variables"] is False
    assert opts["send_default_pii"] is False
    assert opts["before_send"] is scrub_event
    assert opts["before_breadcrumb"] is scrub_breadcrumb


def test_server_init_uses_the_pipeda_options_and_does_not_override_them():
    """AST assertion on the one call that cannot be reached without importing the app.

    Deliberately structural rather than textual: it resolves the actual
    ``sentry_sdk.init`` call node and checks that (a) it spreads
    ``pipeda_sentry_options()`` and (b) no explicit keyword re-enables a control the
    builder switched off. A trailing ``include_local_variables=True`` after the
    spread would silently win at runtime, and no behavioural test can see it.
    """
    import ast
    import pathlib

    server_src = pathlib.Path(__file__).resolve().parent.parent / "server.py"
    tree = ast.parse(server_src.read_text())

    init_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "init"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sentry_sdk"
    ]
    assert len(init_calls) == 1, f"expected exactly one sentry_sdk.init call, found {len(init_calls)}"
    call = init_calls[0]

    spreads = [
        kw
        for kw in call.keywords
        if kw.arg is None and isinstance(kw.value, ast.Call) and getattr(kw.value.func, "id", None) == "pipeda_sentry_options"
    ]
    assert spreads, "sentry_sdk.init must spread pipeda_sentry_options() — the PIPEDA controls live there"

    explicit = {kw.arg for kw in call.keywords if kw.arg}
    for guarded in ("include_local_variables", "send_default_pii", "before_send", "before_breadcrumb"):
        assert guarded not in explicit, (
            f"{guarded} is set explicitly in server.py; it would override "
            f"pipeda_sentry_options() and silently defeat the control"
        )


# ── The no-drop invariant ───────────────────────────────────────────


def test_scrubbing_never_drops_or_mangles_a_malformed_event():
    """sentry_scrub's hard rule: never drop an event because scrubbing failed."""
    for bad in (
        {"exception": None},
        {"exception": {"values": None}},
        {"exception": {"values": [None, "str", 42]}},
        {"exception": {"values": [{"stacktrace": {"frames": [None, {"vars": None}]}}]}},
        {"exception": {"values": [{"stacktrace": "not-a-dict"}]}},
        {"request": {"data": None}},
        {},
    ):
        out = scrub_event(dict(bad))
        assert out is not None, f"event dropped for input {bad!r}"
        assert out.get("tags", {}).get("surface") == "backend"


def test_scrub_frames_tolerates_a_cyclic_frame_var():
    """A self-referencing local must not spin the scrubber."""
    cycle = {}
    cycle["self"] = cycle
    exc = {"values": [{"stacktrace": {"frames": [{"vars": {"state": cycle}}]}}]}
    _scrub_frames(exc)  # must return, not recurse forever
