"""The Stripe claim helper against migration 22's real PostgreSQL primary key."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import psycopg2

try:
    from repositories import wallet_repo
except ImportError:  # pragma: no cover - package-style test invocation
    from backend.repositories import wallet_repo


class _StripeEventsPostgresClient:
    """Small adapter for the exact table chains used by claim_stripe_event."""

    def __init__(self, dsn):
        self.dsn = dsn

    def table(self, name):
        assert name == "stripe_events"
        return _StripeEventsQuery(self.dsn)


class _StripeEventsQuery:
    def __init__(self, dsn):
        self.dsn = dsn
        self.operation = None
        self.payload = None
        self.event_id = None

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def select(self, _columns):
        self.operation = "select"
        return self

    def eq(self, column, value):
        assert column == "event_id"
        self.event_id = value
        return self

    def limit(self, _count):
        return self

    def execute(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            if self.operation == "insert":
                cur.execute(
                    "INSERT INTO stripe_events (event_id, event_type, payload) VALUES (%s, %s, %s::jsonb)",
                    (
                        self.payload["event_id"],
                        self.payload["event_type"],
                        json.dumps(self.payload["payload"]),
                    ),
                )
                return SimpleNamespace(data=[])
            if self.operation == "select":
                cur.execute("SELECT processed_at FROM stripe_events WHERE event_id = %s LIMIT 1", (self.event_id,))
                row = cur.fetchone()
                return SimpleNamespace(data=[{"processed_at": row[0]}] if row else [])
        raise AssertionError(f"unexpected stripe_events operation: {self.operation!r}")


def test_duplicate_claim_against_stripe_events_primary_key(pg_conn, monkeypatch):
    monkeypatch.setattr(wallet_repo, "supabase", _StripeEventsPostgresClient(pg_conn.dsn))
    event_id = f"evt_{uuid4()}"

    async def race_claims():
        return await asyncio.gather(
            wallet_repo.claim_stripe_event(event_id, "payment_intent.succeeded", {"id": event_id}),
            wallet_repo.claim_stripe_event(event_id, "payment_intent.succeeded", {"id": event_id}),
        )

    outcomes = asyncio.run(race_claims())
    assert sorted(outcomes) == [False, True]
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM stripe_events WHERE event_id = %s", (event_id,))
        assert cur.fetchone()[0] == 1
