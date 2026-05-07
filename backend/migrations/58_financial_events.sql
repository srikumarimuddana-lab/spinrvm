-- 58_financial_events.sql
-- 20-3: Append-only ledger for every money event (ride charge, refund,
--       wallet top-up, chargeback, fare-split debit, driver payout).
--
-- Rollback plan:
--   DROP TABLE IF EXISTS financial_events;
--   DROP FUNCTION IF EXISTS _financial_events_immutable();
--
-- Forward-compatible: new table, no changes to existing tables.
-- RLS ensures no role can UPDATE or DELETE rows (append-only).
-- Required by CRA record-keeping and SOC2 CC9.1.

CREATE TABLE IF NOT EXISTS financial_events (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type   text        NOT NULL
                             CHECK (event_type IN (
                                 'stripe_charge',
                                 'stripe_refund',
                                 'stripe_dispute',
                                 'wallet_topup',
                                 'wallet_debit',
                                 'fare_settle',
                                 'fare_split_debit',
                                 'driver_payout',
                                 'tax_adjust'
                             )),
    user_id      text        NOT NULL REFERENCES users(id),
    ride_id      text        REFERENCES rides(id),
    -- Amount in cents (positive = credit/charge, negative = debit/refund)
    delta_cents  bigint      NOT NULL,
    -- External reference (e.g. Stripe PaymentIntent ID, payout transfer ID)
    ref          text,
    metadata     jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Query patterns:
--   "All events for this user, newest first" (rider history, CRA export)
CREATE INDEX IF NOT EXISTS financial_events_user_created
    ON financial_events (user_id, created_at DESC);

--   "All events of a given type in a date window" (reconciliation cron)
CREATE INDEX IF NOT EXISTS financial_events_type_created
    ON financial_events (event_type, created_at DESC);

--   "All events for a specific ride" (dispute investigation)
CREATE INDEX IF NOT EXISTS financial_events_ride_id
    ON financial_events (ride_id)
    WHERE ride_id IS NOT NULL;

-- Append-only RLS: allow INSERT and SELECT; block UPDATE and DELETE.
ALTER TABLE financial_events ENABLE ROW LEVEL SECURITY;

-- Backend service role bypasses RLS (by design). Frontend anon key only.
CREATE POLICY financial_events_insert ON financial_events
    FOR INSERT WITH CHECK (true);

CREATE POLICY financial_events_select ON financial_events
    FOR SELECT USING (
        user_id = auth.uid()::text
        OR (SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'
    );

-- No UPDATE or DELETE policies → those operations are denied by default.

-- Tamper-evidence trigger: raise at the DB level so no superuser mistake
-- can silently mutate a ledger row.
CREATE OR REPLACE FUNCTION _financial_events_immutable()
RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
    RAISE EXCEPTION
        'financial_events rows are append-only and cannot be modified or deleted';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'financial_events_no_mutate'
          AND tgrelid = 'financial_events'::regclass
    ) THEN
        CREATE TRIGGER financial_events_no_mutate
            BEFORE UPDATE OR DELETE ON financial_events
            FOR EACH ROW EXECUTE FUNCTION _financial_events_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE financial_events IS
    'Append-only money ledger. Rows are tamper-evident (UPDATE/DELETE blocked '
    'by trigger). Required by CRA record-keeping (7-year retention) and '
    'SOC2 CC9.1. Created in migration 58.';
