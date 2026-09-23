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

This proves database transaction behavior for those RPCs only. It does not
exercise the application payment/webhook handler, Stripe delivery semantics,
the accept/cancel race, production Postgres, or Redis. Run with the disposable
Postgres setup documented in `backend/tests/rls/conftest.py`; compilation and
collection alone do not count as PostgreSQL execution.
