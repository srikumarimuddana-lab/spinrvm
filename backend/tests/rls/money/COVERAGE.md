# Corporate money PostgreSQL race coverage

`test_money_rpc_races.py` exercises the shipped corporate wallet and allowance
RPC definitions against the disposable PostgreSQL database from
`backend/tests/rls/conftest.py`. The opt-in fixture adds migrations 19, 214,
297, 319, and 376 in order; it does not change the shared RLS bootstrap.

The tests cover two different rides debiting one wallet concurrently, two
allowance-backed debits competing for one remaining cap, and a replay of one
ride debit. Assertions check final balances, allowance usage, paired ledger
rows, and replay deduplication. Calls name the migration-376 idempotency
parameter explicitly so PostgreSQL selects the 11-argument overload while the
older overload remains installed.

`test_stripe_claim_pg.py` also drives the actual `wallet_repo.claim_stripe_event`
helper through a narrow adapter to the disposable database's `stripe_events`
table from migration 22. The adapter uses the table's real primary key to
resolve concurrent duplicate claims.

`test_ride_accept_cancel_pg.py` races the actual `driver_repo.claim_ride_atomic`
and `_base.update_one` guarded writes against the shipped rides table. Its
narrow adapter translates only those repository query chains to conditional
PostgreSQL updates; this verifies the row-lock outcome and final state, not
PostgREST or the full HTTP cancellation flow.

This proves database transaction behavior for those RPCs and the Stripe event
claim primitive only. The Stripe case does not prove application webhook
business-processing idempotency or delivery semantics. The race cases do not
prove production Postgres, Supabase/PostgREST behavior, or Redis. Run with the
disposable Postgres setup documented in `backend/tests/rls/conftest.py`;
compilation and collection alone do not count as PostgreSQL execution.
