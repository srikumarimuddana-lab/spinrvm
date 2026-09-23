"""High-risk SQL regressions for migration 448's durable claim identity.

The direct-pool fixture applies the exact core table DDL and migrations. These
tests intentionally call the SQL RPCs rather than simulating their state logic
in Python. They become runnable once migration 448 is included with the
dispatch branch's direct-pool fixture update.
"""

import pytest


def _enable_identity(cur):
    cur.execute("UPDATE settings SET dispatch_claim_identity_enabled = TRUE WHERE id = 'app_settings'")


def _driver(cur, driver_id):
    user_id = f"user-{driver_id}"
    phone = f"phone-{driver_id}"
    cur.execute(
        "INSERT INTO users (id, phone, role, is_driver) VALUES (%s, %s, 'driver', TRUE)",
        (user_id, phone),
    )
    cur.execute(
        """INSERT INTO drivers (id, user_id, name, phone, is_online, is_available, is_verified, status)
           VALUES (%s, %s, %s, %s, TRUE, TRUE, TRUE, 'active')""",
        (driver_id, user_id, driver_id, phone),
    )


def _rider(cur):
    cur.execute(
        "INSERT INTO users (id, phone, role, is_driver) VALUES ('identity-test-rider', 'identity-test-rider-phone', 'rider', FALSE)"
    )


def _ride(cur, ride_id, status="cancelled", driver_id=None):
    cur.execute(
        """INSERT INTO rides (id, rider_id, pickup_address, pickup_lat, pickup_lng,
                              dropoff_address, dropoff_lat, dropoff_lng, status, driver_id)
           VALUES (%s, 'identity-test-rider', 'pickup', 1, 1, 'dropoff', 2, 2, %s, %s)""",
        (ride_id, status, driver_id),
    )


def _claim(cur, driver_id):
    cur.execute("SELECT availability_claim_id FROM public.claim_driver_with_identity_v2(%s)", (driver_id,))
    row = cur.fetchone()
    assert row is not None and row[0] is not None
    return row[0]


def _offer(cur, ride_id, driver_id, status, claim_id):
    cur.execute(
        """INSERT INTO ride_offers (ride_id, driver_id, status, eta_seconds, offered_at, expires_at, responded_at, claim_id)
           VALUES (%s, %s, %s, 30, now(), now() + interval '10 minutes',
                   CASE WHEN %s IN ('accepted', 'cancelled', 'declined', 'expired') THEN now() ELSE NULL END,
                   %s)""",
        (ride_id, driver_id, status, status, claim_id),
    )


def _release(cur, driver_id, ride_id):
    cur.execute(
        "SELECT public.release_batch_offer_driver_and_close_period_v2(%s, %s)",
        (driver_id, ride_id),
    )
    return cur.fetchone()[0]


def _reap(cur, driver_id, claim_id):
    cur.execute(
        "SELECT public.reap_stale_driver_claim_v2(%s, %s)",
        (driver_id, claim_id),
    )
    return cur.fetchone()[0]


def test_flag_defaults_off_and_v2_rpcs_are_service_role_only(pg_cur):
    pg_cur.execute("UPDATE settings SET dispatch_claim_identity_enabled = FALSE WHERE id = 'app_settings'")
    pg_cur.execute("SELECT dispatch_claim_identity_enabled FROM settings WHERE id = 'app_settings'")
    assert pg_cur.fetchone()[0] is False
    for signature in (
        "public.claim_driver_with_identity_v2(text)",
        "public.dispatch_claim_batch_v2(text,text[],integer[],integer,timestamptz,timestamptz)",
        "public.release_batch_offer_driver_and_close_period_v2(text,text)",
        "public.reap_stale_driver_claim_v2(text,uuid)",
        "public.reap_stale_legacy_driver_claim_v2(text,timestamptz,timestamptz)",
    ):
        pg_cur.execute(
            """SELECT has_function_privilege('service_role', %s, 'EXECUTE'),
                      has_function_privilege('anon', %s, 'EXECUTE'),
                      has_function_privilege('authenticated', %s, 'EXECUTE')""",
            (signature, signature, signature),
        )
        assert pg_cur.fetchone() == (True, False, False), signature
    _driver(pg_cur, "identity-flag-driver")
    with pytest.raises(Exception, match="disabled"):
        pg_cur.execute("SELECT * FROM public.claim_driver_with_identity_v2(%s)", ("identity-flag-driver",))


def test_stale_cancel_offer_cannot_release_new_claim_or_accept_delayed_insert(pg_cur):
    _rider(pg_cur)
    _driver(pg_cur, "identity-stale-driver")
    _ride(pg_cur, "identity-stale-cancel")
    _enable_identity(pg_cur)
    old_claim = _claim(pg_cur, "identity-stale-driver")
    _offer(pg_cur, "identity-stale-cancel", "identity-stale-driver", "cancelled", old_claim)

    pg_cur.execute(
        "UPDATE drivers SET is_available = TRUE, availability_claim_id = NULL, availability_claimed_at = NULL WHERE id = %s",
        ("identity-stale-driver",),
    )
    new_claim = _claim(pg_cur, "identity-stale-driver")
    assert new_claim != old_claim
    assert _release(pg_cur, "identity-stale-driver", "identity-stale-cancel")["status"] == "claim_identity_mismatch"
    pg_cur.execute("SELECT availability_claim_id FROM drivers WHERE id = %s", ("identity-stale-driver",))
    assert pg_cur.fetchone()[0] == new_claim

    _ride(pg_cur, "identity-stale-delayed", "searching")
    with pytest.raises(Exception):
        _offer(pg_cur, "identity-stale-delayed", "identity-stale-driver", "pending", old_claim)
    pg_cur.execute("SELECT availability_claim_id FROM drivers WHERE id = %s", ("identity-stale-driver",))
    assert pg_cur.fetchone()[0] == new_claim


@pytest.mark.parametrize(
    ("period_ride", "period_claim"),
    [("identity-null-target", None), (None, "current")],
)
def test_cancel_release_fails_closed_on_null_p2_identity(pg_cur, period_ride, period_claim):
    _rider(pg_cur)
    _driver(pg_cur, "identity-null-driver")
    _ride(pg_cur, "identity-null-target")
    _enable_identity(pg_cur)
    claim_id = _claim(pg_cur, "identity-null-driver")
    _offer(pg_cur, "identity-null-target", "identity-null-driver", "cancelled", claim_id)
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id, period, ride_id, claim_id) VALUES (%s, 2, %s, %s)",
        ("identity-null-driver", period_ride, claim_id if period_claim == "current" else period_claim),
    )
    assert _release(pg_cur, "identity-null-driver", "identity-null-target")["status"] == "ownership_mismatch"
    pg_cur.execute("SELECT is_available, availability_claim_id FROM drivers WHERE id = %s", ("identity-null-driver",))
    assert pg_cur.fetchone() == (False, claim_id)


@pytest.mark.parametrize(
    ("period_ride", "period_claim", "expected_status"),
    [
        # A NULL-identity Period 2 on a cancelled ride is a leftover from a
        # crashed release, not someone else's claim: recovered (see below).
        (None, "current", "period_offer_identity_mismatch"),
    ],
)
def test_stale_reaper_fails_closed_on_null_p2_identity(pg_cur, period_ride, period_claim, expected_status):
    _rider(pg_cur)
    _driver(pg_cur, "identity-reap-null-driver")
    _ride(pg_cur, "identity-reap-null-target")
    _enable_identity(pg_cur)
    claim_id = _claim(pg_cur, "identity-reap-null-driver")
    _offer(pg_cur, "identity-reap-null-target", "identity-reap-null-driver", "cancelled", claim_id)
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id, period, ride_id, claim_id) VALUES (%s, 2, %s, %s)",
        ("identity-reap-null-driver", period_ride, claim_id if period_claim == "current" else period_claim),
    )
    pg_cur.execute(
        "UPDATE drivers SET availability_claimed_at = now() - interval '2 minutes' WHERE id = %s",
        ("identity-reap-null-driver",),
    )
    assert _reap(pg_cur, "identity-reap-null-driver", claim_id)["status"] == expected_status
    pg_cur.execute(
        "SELECT is_available, availability_claim_id FROM drivers WHERE id = %s", ("identity-reap-null-driver",)
    )
    assert pg_cur.fetchone() == (False, claim_id)


def test_missing_period_cancel_release_recovers_to_p1_without_fabricating_p2(pg_cur):
    _rider(pg_cur)
    _driver(pg_cur, "identity-no-period-driver")
    _ride(pg_cur, "identity-no-period-cancel")
    _enable_identity(pg_cur)
    claim_id = _claim(pg_cur, "identity-no-period-driver")
    _offer(pg_cur, "identity-no-period-cancel", "identity-no-period-driver", "cancelled", claim_id)

    result = _release(pg_cur, "identity-no-period-driver", "identity-no-period-cancel")
    assert result["status"] == "released"
    assert result["period_missing"] is True
    pg_cur.execute(
        "SELECT period, ride_id, claim_id FROM driver_insurance_periods WHERE driver_id = %s AND ended_at IS NULL",
        ("identity-no-period-driver",),
    )
    assert pg_cur.fetchall() == [(1, None, None)]


def test_missing_period_stale_reaper_recovers_to_p1_once_without_fabricating_p2(pg_cur):
    _driver(pg_cur, "identity-reaper-no-period-driver")
    _enable_identity(pg_cur)
    claim_id = _claim(pg_cur, "identity-reaper-no-period-driver")
    pg_cur.execute(
        "UPDATE drivers SET availability_claimed_at = now() - interval '2 minutes' WHERE id = %s",
        ("identity-reaper-no-period-driver",),
    )
    result = _reap(pg_cur, "identity-reaper-no-period-driver", claim_id)
    assert result["status"] == "released"
    assert result["period_missing"] is True
    pg_cur.execute(
        "SELECT period, ride_id, claim_id FROM driver_insurance_periods WHERE driver_id = %s AND ended_at IS NULL",
        ("identity-reaper-no-period-driver",),
    )
    assert pg_cur.fetchall() == [(1, None, None)]
    assert _reap(pg_cur, "identity-reaper-no-period-driver", claim_id)["status"] == "not_claimed"


def _open_period(cur, driver_id):
    cur.execute(
        "SELECT period, ride_id, claim_id FROM driver_insurance_periods WHERE driver_id = %s AND ended_at IS NULL",
        (driver_id,),
    )
    return cur.fetchall()


def _age_claim(cur, driver_id):
    cur.execute(
        "UPDATE drivers SET availability_claimed_at = now() - interval '2 minutes' WHERE id = %s",
        (driver_id,),
    )


def _legacy_claim(cur, driver_id):
    cur.execute(
        """UPDATE drivers SET is_available = FALSE, availability_claimed_at = now() - interval '2 minutes'
           WHERE id = %s RETURNING availability_claimed_at""",
        (driver_id,),
    )
    return cur.fetchone()[0]


def _legacy_reap(cur, driver_id, claimed_at):
    cur.execute(
        "SELECT public.reap_stale_legacy_driver_claim_v2(%s, %s, now() - interval '90 seconds')",
        (driver_id, claimed_at),
    )
    return cur.fetchone()[0]


@pytest.mark.parametrize(
    ("period", "ride_status", "offer_status"),
    [
        (2, "cancelled", "cancelled"),  # rider cancelled; release crashed
        (2, "searching", "declined"),  # driver declined; ride still searching for others
        (2, "searching", "preempted"),  # driver lost the race
        (2, "cancelled", "accepted"),  # rider cancelled after the driver accepted
        (3, "completed", "accepted"),  # trip completed; release crashed
    ],
)
def test_v2_reaper_recovers_stale_period_left_by_crashed_release(pg_cur, period, ride_status, offer_status):
    _rider(pg_cur)
    _driver(pg_cur, "stale-v2-driver")
    _ride(pg_cur, "stale-v2-ride", ride_status)
    _enable_identity(pg_cur)
    claim_id = _claim(pg_cur, "stale-v2-driver")
    _offer(pg_cur, "stale-v2-ride", "stale-v2-driver", offer_status, None)
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id, period, ride_id) VALUES (%s, %s, %s)",
        ("stale-v2-driver", period, "stale-v2-ride"),
    )
    _age_claim(pg_cur, "stale-v2-driver")

    result = _reap(pg_cur, "stale-v2-driver", claim_id)

    assert result["status"] == "released"
    assert result["stale_period_closed"] is True
    assert _open_period(pg_cur, "stale-v2-driver") == [(1, None, None)]
    pg_cur.execute("SELECT is_available, availability_claim_id FROM drivers WHERE id = %s", ("stale-v2-driver",))
    assert pg_cur.fetchone() == (True, None)


@pytest.mark.parametrize(
    ("period", "ride_status", "offer_status"),
    [
        (2, "searching", "declined"),
        (2, "searching", "preempted"),
        (2, "cancelled", "accepted"),
        (3, "completed", "accepted"),
    ],
)
def test_legacy_reaper_recovers_stale_period_left_by_crashed_release(pg_cur, period, ride_status, offer_status):
    _rider(pg_cur)
    _driver(pg_cur, "stale-legacy-driver")
    _ride(pg_cur, "stale-legacy-ride", ride_status)
    _offer(pg_cur, "stale-legacy-ride", "stale-legacy-driver", offer_status, None)
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id, period, ride_id) VALUES (%s, %s, %s)",
        ("stale-legacy-driver", period, "stale-legacy-ride"),
    )
    claimed_at = _legacy_claim(pg_cur, "stale-legacy-driver")

    result = _legacy_reap(pg_cur, "stale-legacy-driver", claimed_at)

    assert result["status"] == "released"
    assert result["stale_period_closed"] is True
    assert _open_period(pg_cur, "stale-legacy-driver") == [(1, None, None)]
    pg_cur.execute("SELECT is_available FROM drivers WHERE id = %s", ("stale-legacy-driver",))
    assert pg_cur.fetchone() == (True,)


def test_reapers_still_refuse_a_driver_with_a_live_offer(pg_cur):
    _rider(pg_cur)
    _driver(pg_cur, "live-offer-driver")
    _ride(pg_cur, "live-offer-ride", "searching")
    _ride(pg_cur, "old-cancelled-ride", "cancelled")
    _offer(pg_cur, "live-offer-ride", "live-offer-driver", "pending", None)
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id, period, ride_id) VALUES (%s, 2, %s)",
        ("live-offer-driver", "old-cancelled-ride"),
    )
    claimed_at = _legacy_claim(pg_cur, "live-offer-driver")

    assert _legacy_reap(pg_cur, "live-offer-driver", claimed_at)["status"] == "offer_or_ride_active"
    pg_cur.execute("SELECT is_available FROM drivers WHERE id = %s", ("live-offer-driver",))
    assert pg_cur.fetchone() == (False,)
