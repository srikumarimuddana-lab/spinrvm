-- 171_venues_fifo_queue.sql
--
-- Purpose (ADR-008): opt-in FIFO ("first in, first out") dispatch for airport-
-- style venues. A terminal venue gets fifo_enabled = true; one or more feeder-lot
-- venues point at it via queue_for_venue_id. Drivers waiting in ANY feeder lot of
-- a terminal share one wait-ordered line keyed by the TERMINAL id, so a driver in
-- the cell-phone lot and one in the overflow lot interleave by arrival time.
--
-- Two surfaces of state:
--   * venues.fifo_enabled        — marks a terminal as FIFO (pickup-detection venue)
--   * venues.queue_for_venue_id  — on a feeder-lot venue, the terminal it feeds
--   * drivers.zone_venue_id      — the terminal a driver is currently queued for
--   * drivers.zone_entered_at    — when they entered the queue (ORDER BY this ASC)
--
-- Nothing changes until an admin sets fifo_enabled on a venue; dispatch reads the
-- new path behind that per-venue flag (see services/dispatch_service.py), so this
-- migration is inert on apply.
--
-- FORWARD-COMPATIBLE / lock-safety:
--   * All four columns are added NULLABLE / with a constant DEFAULT → metadata-only
--     adds with NO table rewrite (true for DEFAULT false / DEFAULT NULL on PG11+;
--     no volatile default). NB: "no rewrite" still briefly takes ACCESS EXCLUSIVE
--     to flip the catalog — negligible here because venues/drivers ALTERs below
--     are catalog-only, but do NOT read this as "lock-free" if copying onto a
--     pattern that scans rows.
--   * drivers.zone_venue_id is intentionally NOT a foreign key: it is transient
--     queue state cleared on every zone exit, and a validated FK to venues would
--     take a SHARE ROW EXCLUSIVE lock + scan on the high-traffic drivers table.
--     Integrity is enforced in the dispatch service, which only ever writes a
--     terminal id it just resolved from venues.
--   * venues.queue_for_venue_id IS a self-FK with ON DELETE SET NULL: deleting a
--     terminal detaches its feeder lots rather than orphaning them. (App layer
--     must treat a lot whose queue_for_venue_id became NULL as plain non-FIFO,
--     not an error.) The FK validation takes SHARE ROW EXCLUSIVE on venues —
--     trivial only because venues is a tiny admin-managed config table (O(1) rows).
--   * The CHECK guard is added WITHOUT NOT VALID, so it scans+validates existing
--     rows under ACCESS EXCLUSIVE — instant ONLY because venues is O(1) rows and
--     every existing row is (fifo_enabled=false, queue_for_venue_id=NULL). This is
--     NOT the safe pattern for a hot table (there, use NOT VALID + VALIDATE later).
--   * The drivers FIFO-ordering index is PARTIAL (WHERE zone_venue_id IS NOT NULL)
--     so it only holds the handful of currently-queued drivers, and is built
--     CONCURRENTLY (drivers is high-traffic, never take ACCESS EXCLUSIVE). The
--     migrate runner detects CONCURRENTLY and applies the whole file in autocommit
--     — so the venues ALTERs above run as separate, individually-committed steps
--     (no cross-statement atomicity). A mid-file failure leaves a partial schema;
--     the rollback block below reverses it by hand (DROP INDEX CONCURRENTLY first).
--
-- RLS: no new tables. venues and drivers already have RLS enabled; new columns
-- inherit it. Both are backend/service-role only — no anon/authenticated policies.
--
-- ROLLBACK (on paper):
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_fifo_queue;
--   DROP INDEX IF EXISTS idx_venues_queue_for;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS zone_entered_at;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS zone_venue_id;
--   ALTER TABLE venues  DROP CONSTRAINT IF EXISTS venues_fifo_lot_exclusive;
--   ALTER TABLE venues  DROP COLUMN IF EXISTS queue_for_venue_id;
--   ALTER TABLE venues  DROP COLUMN IF EXISTS fifo_enabled;

-- --- venues: terminal flag + feeder-lot link -------------------------------

ALTER TABLE venues ADD COLUMN IF NOT EXISTS fifo_enabled boolean NOT NULL DEFAULT false;

ALTER TABLE venues ADD COLUMN IF NOT EXISTS queue_for_venue_id uuid
    REFERENCES venues (id) ON DELETE SET NULL;

-- A venue is EITHER a terminal (fifo_enabled, no queue_for) OR a feeder lot
-- (queue_for set, not itself fifo) — never both, and never points at itself.
ALTER TABLE venues DROP CONSTRAINT IF EXISTS venues_fifo_lot_exclusive;
ALTER TABLE venues ADD CONSTRAINT venues_fifo_lot_exclusive CHECK (
    queue_for_venue_id IS NULL
    OR (NOT fifo_enabled AND queue_for_venue_id <> id)
);

-- Resolve a lot -> its terminal, and list a terminal's lots in admin.
CREATE INDEX IF NOT EXISTS idx_venues_queue_for
    ON venues (queue_for_venue_id)
    WHERE queue_for_venue_id IS NOT NULL;

COMMENT ON COLUMN venues.fifo_enabled IS
    'ADR-008: true on a terminal venue → dispatch orders drivers FIFO by zone_entered_at instead of by ETA.';
COMMENT ON COLUMN venues.queue_for_venue_id IS
    'ADR-008: on a feeder-lot venue, the terminal venue its waiting drivers queue for (shared line keyed by the terminal id).';

-- --- drivers: per-driver queue position ------------------------------------

ALTER TABLE drivers ADD COLUMN IF NOT EXISTS zone_venue_id uuid;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS zone_entered_at timestamptz;

COMMENT ON COLUMN drivers.zone_venue_id IS
    'ADR-008: TERMINAL venue this driver is currently queued for (stamped on entering any of its feeder lots). NULL = not in a FIFO queue. Transient, not FK.';
COMMENT ON COLUMN drivers.zone_entered_at IS
    'ADR-008: when the driver entered the current FIFO queue; dispatch orders by this ASC (longest wait first).';

-- FIFO ordering query: WHERE zone_venue_id = <terminal> ORDER BY zone_entered_at.
-- Partial so it only indexes drivers actually in a queue. CONCURRENTLY because
-- drivers is high-traffic.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_fifo_queue
    ON drivers (zone_venue_id, zone_entered_at)
    WHERE zone_venue_id IS NOT NULL;
