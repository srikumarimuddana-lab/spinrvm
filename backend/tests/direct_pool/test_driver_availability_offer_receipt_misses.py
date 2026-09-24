"""Receipt-based missed-offer counting (migration 461 part B, T6-2)."""

import pytest
from test_offer_decision_atomicity import _apply
from test_offer_decision_atomicity import offer_db as _offer_db  # noqa: F401 - pytest fixture
from test_offer_decision_atomicity_resolve import _expire_now, _offer, _resolve


@pytest.fixture()
def receipt_miss_db(_offer_db):  # noqa: F811
    cur = _offer_db
    _apply(cur, "461_offer_delivery_receipts.sql")
    _apply(cur, "461_offer_delivery_receipts.sql")
    return cur


def _receipt(cur, offer_id, claim_id, event="presented", app_state="active", user="u1", session="sess-1"):
    cur.execute(
        "SELECT public.record_offer_receipt(%s::uuid,%s::uuid,%s,%s,%s,'ws',%s,1000)",
        (offer_id, claim_id, user, session, event, app_state),
    )
    return cur.fetchone()[0]


def _expire_after_receipt(cur, offer_id):
    """Move the deadline into the past while keeping the receipt before it."""
    cur.execute(
        "UPDATE driver_offer_receipts SET received_at=clock_timestamp() - interval '5 seconds' WHERE offer_id=%s",
        (offer_id,),
    )
    _expire_now(cur, offer_id)


def _streak(cur, driver="d1"):
    cur.execute("SELECT offer_miss_streak FROM drivers WHERE id=%s", (driver,))
    return cur.fetchone()[0]


def test_seam_is_private(receipt_miss_db):
    cur = receipt_miss_db
    sig = "offer_expiry_counts_as_miss(ride_offers,drivers)"
    for role in ("authenticated", "anon", "service_role"):
        cur.execute(f"SELECT has_function_privilege('{role}', '{sig}', 'EXECUTE')")
        assert cur.fetchone()[0] is False


def test_presented_then_expire_twice_counts_one_miss(receipt_miss_db):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    assert _receipt(cur, o1, c1)["recorded"] is True
    _expire_after_receipt(cur, o1)
    first = _resolve(cur, o1, c1, "expire", f"expire:{o1}")
    assert first["outcome"] == "expired_nonresponse"
    assert first["miss_counted"] is True
    replay = _resolve(cur, o1, c1, "expire", f"expire:{o1}")
    assert replay["replayed"] is True
    other = _resolve(cur, o1, c1, "expire", f"reaper:{o1}")
    assert other["code"] == "OFFER_ALREADY_RESOLVED"
    assert _streak(cur) == 1


def test_no_receipt_is_delivery_unknown(receipt_miss_db):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    _expire_now(cur, o1)
    result = _resolve(cur, o1, c1, "expire", f"expire:{o1}")
    assert result["outcome"] == "expired_delivery_unknown"
    assert result["miss_counted"] is False
    assert _streak(cur) == 0


def test_received_only_is_delivery_unknown(receipt_miss_db):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    _receipt(cur, o1, c1, event="received", app_state="background")
    _expire_now(cur, o1)
    assert _resolve(cur, o1, c1, "expire", f"expire:{o1}")["outcome"] == "expired_delivery_unknown"


def test_presented_after_deadline_does_not_count(receipt_miss_db):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    _expire_now(cur, o1)
    late = _receipt(cur, o1, c1)
    assert late["recorded"] is True
    assert late["late"] is True
    result = _resolve(cur, o1, c1, "expire", f"expire:{o1}")
    assert result["outcome"] == "expired_delivery_unknown"
    assert _streak(cur) == 0


def test_epoch_change_after_presented_is_not_a_miss(receipt_miss_db):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    _receipt(cur, o1, c1)
    cur.execute("UPDATE drivers SET online_epoch=online_epoch+1 WHERE id='d1'")
    _expire_after_receipt(cur, o1)
    result = _resolve(cur, o1, c1, "expire", f"expire:{o1}")
    assert result["outcome"] == "expired_availability_changed"
    assert result["miss_counted"] is False


@pytest.mark.parametrize("action", ["accept", "decline"])
def test_decision_after_presented_is_not_a_miss(receipt_miss_db, action):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    _receipt(cur, o1, c1)
    result = _resolve(cur, o1, c1, action, f"{action}-1")
    assert result["code"] == "OK"
    assert result["miss_counted"] is False
    assert _streak(cur) == 0


def test_threshold_crossed_once_with_presented_receipts(receipt_miss_db):
    cur = receipt_miss_db
    outcomes = []
    for n in range(3):
        o, c = _offer(cur, "d1", ride_id=f"r{n}")
        _receipt(cur, o, c)
        _expire_after_receipt(cur, o)
        outcomes.append(_resolve(cur, o, c, "expire", f"expire:{o}", threshold=3))
    assert [r["miss_counted"] for r in outcomes] == [True, True, True]
    assert [r["paused"] for r in outcomes] == [False, False, True]
    cur.execute("SELECT is_online, availability_reason, offer_miss_streak FROM drivers WHERE id='d1'")
    assert cur.fetchone() == (False, "pause_misses", 0)


def test_snapshot_pending_offer_carries_envelope_fields(receipt_miss_db):
    cur = receipt_miss_db
    o1, c1 = _offer(cur, "d1")
    cur.execute("SELECT public.get_driver_availability_snapshot('u1')")
    snap = cur.fetchone()[0]
    offer = snap["pending_offer"]
    assert offer["id"] == o1
    assert offer["offer_id"] == o1
    assert offer["claim_id"] == c1
    assert offer["online_epoch"] == "5"
    assert "readiness_enforced" in snap
    assert "readiness_prompt_at" in snap
    assert "current_session_id" in snap
