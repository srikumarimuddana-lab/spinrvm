"""
DB-role-level RLS coverage for the ride distance/GPS integrity audit trail --
`ride_distance_integrity_events` (migration 246), `ride_distance_recomputes`
(242), and `ride_location_gap_events` (237, + 370's additive `status`-value
widening) -- ACTION_ITEMS.md C49.

Picked as this round's themed slice deliberately: all three back
fraud/dispute detection on ride billing *distance*, distinct from the money
ledger itself (`financial_events`, already covered in
test_money_and_safety_rls.py). `ride_distance_recomputes`' own migration
comment states its purpose explicitly: "so a rider/driver dispute or SGI
review can reconstruct exactly when and why a displayed distance changed" --
regulatory/dispute-audit stakes, the same caution-bias class as the
insurance-period audit tables covered last round.

Shape: all three ENABLE ROW LEVEL SECURITY, then define four EXPLICIT
`USING (false)` / `WITH CHECK (false)` policies `TO authenticated` for
SELECT/INSERT/UPDATE/DELETE. Unlike last round's admin-export-audit trio
(RLS's *implicit* default-deny -- zero policy at all), these are *explicit*
deny-all policies, but the observable behavior for anon/authenticated is
identical: SELECT/UPDATE/DELETE filter silently to zero rows (Postgres's
`USING (false)` default), INSERT raises `InsufficientPrivilege` (`WITH CHECK
(false)`). No policy exists for `anon` at all on any of the three, so anon
gets the same RLS implicit-default-deny. No admin-override policy exists on
any of the three either -- exercised explicitly below (parametrized over
`admin`/`super_admin`) rather than assumed, the same way the admin-export
round proved its own "no override" claim rather than stating it.

Real production write paths (confirmed by grep -- `utils/distance_integrity.py`,
`utils/route_finalizer.py`, `utils/route_gap_monitor.py`,
`utils/retention_purge.py` -- all through `db_supabase`, i.e. the
service-role client; no admin route or service file reads any of the three
directly, so today they are write-and-forget signal/audit streams with no
UI surface at all):

* `ride_distance_integrity_events` / `ride_distance_recomputes`: INSERT
  only. No call site anywhere issues an UPDATE or DELETE against either
  table -- genuinely dead DB-layer UPDATE/DELETE capability today, same
  "not a test gap" class as last round's un-exercised positive DELETE
  policies. BUT: unlike `audit_logs` (migrations 51/56/57) and
  `compliance_export_events` (263/285), which each got a dedicated
  anti-tamper trigger specifically because they're regulatory/security
  audit trails, neither of these two tables has any DB-level trigger
  preventing `service_role` (the only role able to write to them at all)
  from mutating or deleting a row directly, despite each migration's own
  comment asserting "event rows are immutable" / "audit rows are
  immutable" "on purpose". The guarantee today rests entirely on
  application-code discipline, not DB enforcement -- confirmed by direct
  reproduction below (`test_*_service_role_can_mutate_despite_immutable_comment`),
  which passes today (nothing is broken), it documents a real, previously
  unflagged gap relative to the enforcement pattern this codebase already
  uses elsewhere for tables making the same claim. Filed as ACTION_ITEMS.md
  C118 (new) -- not fixed here; adding a trigger is a production schema
  change with its own review, out of scope for a test-coverage-only PR.
* `ride_location_gap_events` is different in kind, and its own migration
  comment reflects that -- it never claims immutability, only that "all
  reads and mutations travel through...the service role". It has real,
  live UPDATE (`route_gap_monitor.py`'s `_resolve_open_gap_event`/
  `_close_orphaned_open_events`, transitioning `status` between
  `open`/`resolved`/`unresolved_at_completion`) and DELETE (migration
  238's `purge_trip_route_geometry()`, invoked daily by
  `retention_purge.py` at the 7-year trip-audit retention ceiling) write
  paths -- both exercised here as genuine positive cases, not dead
  capability.

Migration 238 (`trip_route_integrity_retention`) is deliberately NOT applied
by conftest.py: it ALTERs `ride_routes`, a table outside this harness's
build scope, and its only touch on `ride_location_gap_events` is a plain
index plus a reference inside `purge_trip_route_geometry()` -- neither an
RLS policy nor a CHECK constraint under test here. See conftest.py's own
comment on this block for the full reasoning.
"""

from __future__ import annotations

import uuid

import pytest

try:
    import psycopg2
except ImportError:  # pragma: no cover - guarded by conftest's skipif
    psycopg2 = None

from conftest import as_role

pytestmark = pytest.mark.rls


def _uuid() -> str:
    return str(uuid.uuid4())


def _seed_user(cur, user_id: str, role: str = "rider") -> None:
    cur.execute(
        "INSERT INTO users (id, phone, role) VALUES (%s, %s, %s)",
        (user_id, f"+1306555{user_id[-4:]}", role),
    )


def _seed_driver(cur, driver_id: str, user_id: str) -> None:
    cur.execute(
        "INSERT INTO drivers (id, user_id, name, phone) VALUES (%s, %s, %s, %s)",
        (driver_id, user_id, "Test Driver", "+13065550000"),
    )


def _seed_ride(cur, ride_id: str, rider_id: str, driver_id: str | None = None) -> None:
    cur.execute(
        """
        INSERT INTO rides
            (id, rider_id, driver_id, pickup_address, pickup_lat, pickup_lng,
             dropoff_address, dropoff_lat, dropoff_lng)
        VALUES (%s, %s, %s, 'A', 50.4, -104.6, 'B', 50.5, -104.7)
        """,
        (ride_id, rider_id, driver_id),
    )


def _seed_integrity_event(cur, event_id: str, ride_id: str, kind: str = "coverage_fallback") -> None:
    cur.execute(
        "INSERT INTO ride_distance_integrity_events (id, ride_id, kind) VALUES (%s, %s, %s)",
        (event_id, ride_id, kind),
    )


def _seed_recompute(cur, recompute_id: str, ride_id: str, new_distance_km: str = "12.500") -> None:
    cur.execute(
        "INSERT INTO ride_distance_recomputes (id, ride_id, route_revision, new_actual_distance_km) "
        "VALUES (%s, %s, 1, %s)",
        (recompute_id, ride_id, new_distance_km),
    )


def _seed_ride_for(cur, user_id: str, role: str) -> str:
    """Return a valid, seeded rider_id for a ride: the user itself if it's a
    rider, otherwise a freshly seeded separate rider row (an admin/
    super_admin user is not itself a valid rides.rider_id -- rides.rider_id
    has no special-case for staff roles)."""
    if role == "rider":
        return user_id
    other_rider = _uuid()
    _seed_user(cur, other_rider, role="rider")
    return other_rider


def _seed_gap_event(
    cur,
    event_id: str,
    ride_id: str,
    driver_id: str | None = None,
    status: str = "open",
) -> None:
    cur.execute(
        "INSERT INTO ride_location_gap_events "
        "(id, ride_id, driver_id, gap_started_at, threshold_seconds, gap_seconds, status) "
        "VALUES (%s, %s, %s, now(), 45, 60, %s)",
        (event_id, ride_id, driver_id, status),
    )


# --------------------------------------------------------------------------
# ride_distance_integrity_events
# --------------------------------------------------------------------------


def test_anon_selects_no_integrity_events(pg_cur):
    rider, event_id, ride_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_integrity_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["rider", "admin", "super_admin"])
def test_authenticated_selects_no_integrity_events_regardless_of_role(pg_cur, role):
    """No admin-override policy exists -- proved, not assumed, for admin and
    super_admin alongside a plain rider."""
    user_id, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id, role=role)
    _seed_ride(pg_cur, ride_id, _seed_ride_for(pg_cur, user_id, role))
    _seed_integrity_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_insert_integrity_event(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_integrity_event(pg_cur, _uuid(), ride_id)


def test_authenticated_cannot_insert_integrity_event(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_integrity_event(pg_cur, _uuid(), ride_id)


def test_authenticated_cannot_update_integrity_event(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_integrity_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute(
        "UPDATE ride_distance_integrity_events SET kind = 'booked_distance_suspect' WHERE id = %s", (event_id,)
    )
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_integrity_event(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_integrity_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("DELETE FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_and_select_integrity_event(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_integrity_event(pg_cur, event_id, ride_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT id FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [event_id]


@pytest.mark.parametrize(
    "kind",
    [
        "booked_distance_suspect",
        "coverage_fallback",
        "measured_below_straight_line",
        "trip_window_compressed",
        "quote_measured_divergence",
    ],
)
def test_integrity_event_accepts_each_known_kind(pg_cur, kind):
    """Parity check between the DB CHECK constraint and
    `utils/distance_integrity.py`'s own `VALID_KINDS` frozenset -- both
    must agree on the same five values, or a kind the Python guard accepts
    would fail at the DB layer (or vice versa)."""
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_integrity_event(pg_cur, _uuid(), ride_id, kind=kind)
    assert pg_cur.rowcount == 1


def test_integrity_event_rejects_unknown_kind(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _seed_integrity_event(pg_cur, _uuid(), ride_id, kind="not_a_real_kind")


def test_integrity_event_service_role_can_mutate_despite_immutable_comment(pg_cur):
    """ACTION_ITEMS.md C118: no anti-tamper trigger exists for this table
    (unlike audit_logs/compliance_export_events), so service_role -- the
    only role that can touch this table at all -- can UPDATE and DELETE a
    row directly at the DB layer, contradicting the migration's own
    "event rows are immutable" comment. No production code path does this
    today (see module docstring); this test documents today's real DB
    behavior, it does not fix it."""
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_integrity_event(pg_cur, event_id, ride_id)
    pg_cur.execute(
        "UPDATE ride_distance_integrity_events SET kind = 'booked_distance_suspect' WHERE id = %s", (event_id,)
    )
    assert pg_cur.rowcount == 1
    pg_cur.execute("DELETE FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 1


# --------------------------------------------------------------------------
# ride_distance_recomputes
# --------------------------------------------------------------------------


def test_anon_selects_no_recomputes(pg_cur):
    rider, ride_id, recompute_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_recompute(pg_cur, recompute_id, ride_id)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM ride_distance_recomputes WHERE id = %s", (recompute_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["rider", "admin", "super_admin"])
def test_authenticated_selects_no_recomputes_regardless_of_role(pg_cur, role):
    user_id, ride_id, recompute_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id, role=role)
    _seed_ride(pg_cur, ride_id, _seed_ride_for(pg_cur, user_id, role))
    _seed_recompute(pg_cur, recompute_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM ride_distance_recomputes WHERE id = %s", (recompute_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_insert_recompute(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_recompute(pg_cur, _uuid(), ride_id)


def test_authenticated_cannot_insert_recompute(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_recompute(pg_cur, _uuid(), ride_id)


def test_authenticated_cannot_update_recompute(pg_cur):
    rider, ride_id, recompute_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_recompute(pg_cur, recompute_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("UPDATE ride_distance_recomputes SET new_actual_distance_km = 99.999 WHERE id = %s", (recompute_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_recompute(pg_cur):
    rider, ride_id, recompute_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_recompute(pg_cur, recompute_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("DELETE FROM ride_distance_recomputes WHERE id = %s", (recompute_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_and_select_recompute(pg_cur):
    rider, ride_id, recompute_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_recompute(pg_cur, recompute_id, ride_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT id FROM ride_distance_recomputes WHERE id = %s", (recompute_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [recompute_id]


def test_recompute_rejects_negative_distance(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _seed_recompute(pg_cur, _uuid(), ride_id, new_distance_km="-1.000")


def test_recompute_service_role_can_mutate_despite_immutable_comment(pg_cur):
    """ACTION_ITEMS.md C118 -- same finding as
    test_integrity_event_service_role_can_mutate_despite_immutable_comment
    above, second table: no trigger backs the "audit rows are immutable"
    comment against service_role. Documents current behavior; not a fix."""
    rider, ride_id, recompute_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_recompute(pg_cur, recompute_id, ride_id)
    pg_cur.execute("UPDATE ride_distance_recomputes SET new_actual_distance_km = 1.000 WHERE id = %s", (recompute_id,))
    assert pg_cur.rowcount == 1
    pg_cur.execute("DELETE FROM ride_distance_recomputes WHERE id = %s", (recompute_id,))
    assert pg_cur.rowcount == 1


# --------------------------------------------------------------------------
# ride_location_gap_events
# --------------------------------------------------------------------------


def test_anon_selects_no_gap_events(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM ride_location_gap_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["rider", "admin", "super_admin"])
def test_authenticated_selects_no_gap_events_regardless_of_role(pg_cur, role):
    user_id, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id, role=role)
    _seed_ride(pg_cur, ride_id, _seed_ride_for(pg_cur, user_id, role))
    _seed_gap_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM ride_location_gap_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_insert_gap_event(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_gap_event(pg_cur, _uuid(), ride_id)


def test_authenticated_cannot_insert_gap_event(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_gap_event(pg_cur, _uuid(), ride_id)


def test_authenticated_cannot_update_gap_event(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("UPDATE ride_location_gap_events SET status = 'resolved' WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_gap_event(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("DELETE FROM ride_location_gap_events WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_and_select_gap_event(pg_cur):
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT id FROM ride_location_gap_events WHERE id = %s", (event_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [event_id]


def test_service_role_can_resolve_open_gap_event(pg_cur):
    """Real production path: route_gap_monitor.py's `_resolve_open_gap_event`
    transitions status open -> resolved via the service-role client."""
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id, status="open")
    pg_cur.execute(
        "UPDATE ride_location_gap_events SET status = 'resolved', gap_resolved_at = now() "
        "WHERE id = %s AND status = 'open'",
        (event_id,),
    )
    assert pg_cur.rowcount == 1


def test_service_role_can_delete_gap_event(pg_cur):
    """Real production path: purge_trip_route_geometry() (migration 238,
    invoked daily by retention_purge.py) hard-deletes gap events past the
    7-year trip-audit retention ceiling."""
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id)
    pg_cur.execute("DELETE FROM ride_location_gap_events WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 1


def test_gap_event_rejects_unknown_status(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _seed_gap_event(pg_cur, _uuid(), ride_id, status="not_a_real_status")


def test_gap_event_accepts_unresolved_at_completion_status(pg_cur):
    """Regression guard for migration 370: 'unresolved_at_completion' must
    be a valid status (route_gap_monitor.py's _close_orphaned_open_events
    uses it) -- proves 370 was actually applied on top of 237's original
    CHECK, not just 237 alone."""
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, _uuid(), ride_id, status="unresolved_at_completion")
    assert pg_cur.rowcount == 1


def test_gap_event_unique_index_blocks_duplicate_open(pg_cur):
    """route_gap_monitor.py's own comment on _open_gap_event: 'Unique index
    makes replay safe.' A second insert for the same (ride_id,
    gap_started_at) pair must fail -- proving the monitor's replay-safety
    guarantee actually holds at the DB layer, not just in the comment."""
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    pg_cur.execute(
        "INSERT INTO ride_location_gap_events "
        "(id, ride_id, gap_started_at, threshold_seconds, gap_seconds, status) "
        "VALUES (%s, %s, '2026-01-01T00:00:00Z', 45, 60, 'open')",
        (_uuid(), ride_id),
    )
    assert pg_cur.rowcount == 1
    with pytest.raises(psycopg2.errors.UniqueViolation):
        pg_cur.execute(
            "INSERT INTO ride_location_gap_events "
            "(id, ride_id, gap_started_at, threshold_seconds, gap_seconds, status) "
            "VALUES (%s, %s, '2026-01-01T00:00:00Z', 45, 60, 'open')",
            (_uuid(), ride_id),
        )


def test_gap_event_driver_id_optional(pg_cur):
    """driver_id is nullable (a gap can be detected before a driver is
    assigned or after unassignment) -- confirms the FK doesn't require it."""
    rider, ride_id, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_ride(pg_cur, ride_id, rider)
    _seed_gap_event(pg_cur, event_id, ride_id, driver_id=None)
    assert pg_cur.rowcount == 1


def test_gap_event_driver_id_references_real_driver(pg_cur):
    """Complements test_gap_event_driver_id_optional above with the other
    real production shape: route_gap_monitor.py always sets driver_id from
    the ride's own assigned driver when one exists -- confirms the FK
    accepts a valid drivers.id, not just NULL."""
    rider, driver_user, driver_id, ride_id, event_id = _uuid(), _uuid(), _uuid(), _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_user(pg_cur, rider)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    _seed_ride(pg_cur, ride_id, rider, driver_id=driver_id)
    _seed_gap_event(pg_cur, event_id, ride_id, driver_id=driver_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT driver_id FROM ride_location_gap_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchone()[0] == driver_id
