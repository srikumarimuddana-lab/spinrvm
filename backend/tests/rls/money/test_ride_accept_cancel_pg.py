"""Race the actual guarded repository writes against one PostgreSQL ride row."""

import asyncio
from threading import Barrier
from types import SimpleNamespace

import psycopg2

try:
    from repositories import _base, driver_repo
except ImportError:  # pragma: no cover - package-style test invocation
    from backend.repositories import _base, driver_repo


class _RideUpdateClient:
    """Translate only the repository query methods used by these two writes."""

    def __init__(self, dsn, barrier):
        self.dsn = dsn
        self.barrier = barrier

    def table(self, name):
        assert name == "rides"
        return _RideUpdateQuery(self.dsn, self.barrier)


class _RideUpdateQuery:
    def __init__(self, dsn, barrier):
        self.dsn = dsn
        self.barrier = barrier
        self.values = None
        self.filters = {}

    def update(self, values):
        self.values = values
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def in_(self, column, values):
        self.filters[f"{column}__in"] = values
        return self

    def or_(self, clause):
        # This is the exact one-null-or-same-driver guard emitted by
        # driver_repo.claim_ride_atomic; reject any unexpected query shape.
        parts = clause.split(",")
        assert len(parts) == 2 and parts[0] == "driver_id.is.null"
        prefix = "driver_id.eq."
        assert parts[1].startswith(prefix)
        self.filters["driver_id__or_same"] = parts[1][len(prefix) :]
        return self

    def execute(self):
        # Both repository calls reach the DB together; row-level UPDATE locks
        # then decide which guarded transition lands first.
        allowed_filters = {"id", "status__in", "driver_id__or_same"}
        assert set(self.filters) <= allowed_filters
        assert {"id", "status__in"} <= set(self.filters)
        assert set(self.values) <= {"status", "driver_id", "driver_accepted_at", "updated_at"}
        self.barrier.wait(timeout=5)
        set_sql = ", ".join(f"{column} = %s" for column in self.values)
        where = ["id = %s"]
        params = list(self.values.values()) + [self.filters["id"]]
        if "status__in" in self.filters:
            where.append("status = ANY(%s)")
            params.append(self.filters["status__in"])
        if "driver_id__or_same" in self.filters:
            where.append("(driver_id IS NULL OR driver_id = %s)")
            params.append(self.filters["driver_id__or_same"])
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE rides SET {set_sql} WHERE {' AND '.join(where)} RETURNING id, status, driver_id",
                params,
            )
            rows = [dict(zip(("id", "status", "driver_id"), row)) for row in cur.fetchall()]
        return SimpleNamespace(data=rows)


def test_driver_accept_racing_rider_cancel_cannot_resurrect_ride(pg_conn, monkeypatch):
    ride_id, driver_id = "pg-accept-cancel-race", "driver-state-proof"
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM rides WHERE id = %s", (ride_id,))
        cur.execute(
            """INSERT INTO rides
                   (id, driver_id, pickup_address, pickup_lat, pickup_lng,
                    dropoff_address, dropoff_lat, dropoff_lng, status)
               VALUES (%s, %s, 'pickup', 0, 0, 'dropoff', 0, 0, 'driver_assigned')""",
            (ride_id, driver_id),
        )

    barrier = Barrier(2)
    client = _RideUpdateClient(pg_conn.dsn, barrier)
    monkeypatch.setattr(driver_repo, "supabase", client)
    monkeypatch.setattr(_base, "supabase", client)

    async def race():
        return await asyncio.gather(
            driver_repo.claim_ride_atomic(ride_id, driver_id),
            _base.update_one(
                "rides",
                {
                    "id": ride_id,
                    "status": {
                        "$in": [
                            "requested",
                            "searching",
                            "driver_assigned",
                            "driver_accepted",
                            "en_route",
                            "driver_arrived",
                        ]
                    },
                },
                {"status": "cancelled"},
                retry_policy="write",
            ),
        )

    accepted, cancelled = asyncio.run(race())
    assert accepted in (True, False)
    assert cancelled == {"id": ride_id, "status": "cancelled", "driver_id": driver_id}
    with pg_conn.cursor() as cur:
        cur.execute("SELECT status, driver_id FROM rides WHERE id = %s", (ride_id,))
        assert cur.fetchone() == ("cancelled", driver_id)
