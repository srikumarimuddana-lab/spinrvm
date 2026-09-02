-- 401_dispatch_claim_batch.sql
--
-- C50 Phase 2 (T12) — batch driver-claim + offer-insert + insurance-period
-- RPC for the direct-pool dispatch path (dispatch_direct_pool_enabled,
-- flag OFF by default — see migration 400). Nothing calls this function
-- until Phase 2's T13 wires backend/repositories/dispatch_pool.py's
-- claim_batch() to it, and T13 only calls it when the flag is on. This
-- migration changes zero production behavior by itself.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz);
--
-- ============================================================================
-- Corrections made against the CURRENT code (not the plan doc's paraphrase)
-- ============================================================================
--
-- 1. ID TYPES ARE text, NOT uuid.
--    The plan doc's T12 signature reads
--    `dispatch_claim_batch(p_ride_id uuid, p_driver_ids uuid[], ...)`.
--    Read against backend/supabase_schema.sql: drivers.id, rides.id, and
--    users.id are all `TEXT PRIMARY KEY` (values happen to be UUID-shaped
--    strings, minted by the app, but the COLUMN TYPE is text). Every
--    existing SECURITY DEFINER RPC that takes a driver/ride id
--    (record_insurance_period_transition — migration 253 — takes
--    `p_driver_id text, p_ride_id text`) uses text for the same reason.
--    Declaring this function's parameters as `uuid` would make every call
--    from claim_driver_atomic-style callers (which pass Python `str`
--    values straight from a `text` column) either fail to bind or,
--    worse, silently succeed via an implicit cast while being wrong for
--    any id that isn't valid UUID syntax (there is no guarantee every
--    `drivers.id` is — it's an app-generated TEXT PK, not a `uuid` column
--    with a `gen_random_uuid()` default). Fixed to `text` / `text[]` here.
--
-- 2. SIGNATURE EXTENDED with p_eta_seconds int[] (parallel array to
--    p_driver_ids, same order, same length — enforced below).
--    T12 also says the ride_offers insert must mirror matching.py's
--    `_build_offer_rows` (matching.py:132-150), whose ride_offers rows
--    carry a per-driver `eta_seconds` column sourced from
--    `claimed_drivers: list[tuple[driver, eta_sec]]` — a value computed
--    in Python (Distance-Matrix batch call or haversine fallback,
--    matching.py:826-854) with no SQL-side source of truth. Passed in
--    alongside p_driver_ids rather than guessed or left NULL. Python-side
--    ranking stays authoritative for both driver order AND each driver's
--    eta_seconds — this function does no ranking or ETA computation.
--
-- 3. RETURN SHAPE EXTENDED beyond "claimed drivers rows plus the inserted
--    ride_offers ids" to ALSO emit one row per ATTEMPTED-BUT-NOT-CLAIMED
--    driver (claim lost the race, or failed is_online/is_verified/status
--    revalidation). Reasoning, spelled out because this is a deliberate
--    departure from the plan doc's literal return-shape wording, not an
--    oversight:
--
--    T13 requires the Python side to call `invalidate_driver_cache` for
--    "every driver attempted" (Redis side effect, kept out of SQL by
--    design — see dispatch_pool.py's module docstring: no waiting on
--    Redis while holding a pooled transaction-mode connection). Today's
--    Python claim loop (matching.py:859-878) does this implicitly:
--    `claim_driver_atomic()` (driver_repo.py:269) calls
--    `invalidate_driver_cache` unconditionally at the START of every
--    attempt, before it even knows if the claim will succeed. For the
--    direct-pool caller to reproduce that "every attempted driver" set
--    faithfully, it needs the actual list of drivers this function
--    attempted — which, because the stopping condition
--    (`p_max_offers` successful claims) is only known by walking the
--    array inside this function, cannot be reconstructed correctly in
--    Python ahead of time. Returning ONLY the claimed rows would silently
--    under-invalidate the cache for every driver this function attempted
--    and rejected (lost the claim race, or failed revalidation) — those
--    driver rows DID change (is_available flipped false then back to
--    true, or availability_claimed_at got set then cleared) and a stale
--    cache entry for them is exactly the kind of bug the existing
--    invalidate-on-both-sides pattern (driver_repo.py:269,297) exists to
--    prevent.
--
--    So this function returns one row per driver it actually iterated to
--    (i.e. before hitting the p_max_offers stopping condition — a driver
--    never reached because the batch was already full is correctly NOT
--    "attempted", exactly mirroring the Python loop's
--    `if len(claimed_drivers) >= max_offers: break` check, which runs
--    BEFORE calling claim_driver_atomic for the next candidate). Each row
--    reports `claimed` (boolean) so the caller can both (a) invalidate
--    cache for every returned driver_id and (b) filter to `claimed = true`
--    for the actual offer batch. `driver_row` / `ride_offer_id` are only
--    populated when `claimed = true`.
--
-- ============================================================================
-- Semantics — a translation of the existing Python claim loop, not a redesign
-- ============================================================================
--
-- Per driver in p_driver_ids, in the GIVEN order (no re-ranking), UNTIL
-- p_max_offers successful claims have been made:
--   1. `UPDATE drivers SET is_available = false, availability_claimed_at =
--      now() WHERE id = $1 AND is_available = true RETURNING *` — the exact
--      predicate verified against
--      backend/repositories/driver_repo.py:271-286 (claim_driver_atomic)
--      at the time this migration was written. 0 rows matched => driver
--      was claimed by a concurrent caller, or was never available =>
--      emit an unclaimed row (driver_row/ride_offer_id NULL), continue.
--   2. Revalidate `is_online AND is_verified AND status = 'active'` on the
--      just-claimed row — the exact check verified against
--      backend/routes/rides/matching.py:875 at the time this migration was
--      written. On failure: release (`is_available = true,
--      availability_claimed_at = NULL` — matches
--      driver_repo.py:144-160's set_driver_available(..., True) release
--      path exactly, including clearing the claim stamp), emit an
--      unclaimed row, continue.
--   3. Insert one `ride_offers` row mirroring `_build_offer_rows`
--      (matching.py:132-150) column-for-column: ride_id, driver_id,
--      status='pending', eta_seconds, offered_at, expires_at.
--   4. Call `record_insurance_period_transition(driver_id, 2, ride_id)`
--      (migration 253) IN THIS SAME TRANSACTION — Period 2 (TNC primary
--      commercial, en route to pickup) opens at claim time per CLAUDE.md.
--      Not gated on its return value, matching matching.py:923-924, which
--      calls this unconditionally without branching on the result
--      (status ok/noop/race).
--   5. Emit a claimed row (driver_row as jsonb, ride_offer_id).
--
-- driver_row is jsonb (the full post-update `drivers` row), not a typed
-- positional column list: `drivers` gains columns over time (20+
-- migrations in this repo do exactly that) and a positional TABLE(...)
-- return here would silently go stale every time `drivers` changes. The
-- Python caller (dispatch_pool.claim_batch, T13) treats it the same way
-- it already treats a postgrest response row: a dict.
--
-- ============================================================================
-- OPEN QUESTION — not fully resolved, flagged rather than guessed
-- ============================================================================
-- record_insurance_period_transition (migration 253) computes its
-- `started_at` from the plain `now()` function, which in PostgreSQL
-- returns the SAME value for every call within one transaction (it is the
-- transaction start timestamp, not wall-clock time — clock_timestamp()
-- would differ). Today, each claimed driver's Period-2 transition is a
-- SEPARATE PostgREST RPC call (matching.py:923-924's loop), i.e. a
-- separate transaction per driver, so their `driver_insurance_periods.
-- started_at` values naturally differ by the real (sub-millisecond to
-- low-millisecond) gap between those calls. Because T12 requires this
-- RPC to do all claims for a batch in ONE transaction, every driver
-- claimed in the same dispatch_claim_batch call will get an IDENTICAL
-- started_at (the shared transaction timestamp) once this path is
-- enabled. This is arguably MORE correct for a batch-offer model (all
-- drivers were dispatched as one atomic operation), but it is a real,
-- deliberate change in audit-trail granularity from today's behavior,
-- and record_insurance_period_transition (migration 253) cannot be
-- edited to use clock_timestamp() instead without violating the
-- append-only migration rule. Flagging for Divya / Kiran rather than
-- deciding unilaterally that the granularity change is acceptable for a
-- regulatory (SGI / Saskatchewan Transportation Act) audit trail.
--
-- ============================================================================
-- Conventions
-- ============================================================================
-- SECURITY DEFINER + pinned search_path — matches migrations 203, 253, 354.
-- Grants follow the pattern verified in
-- 354_revoke_public_execute_on_security_definer_fns.sql: Postgres grants
-- EXECUTE to PUBLIC on CREATE FUNCTION by default, and 354's sweep only
-- caught functions that existed AT THE TIME IT RAN — it does not
-- retroactively cover functions created later, so this migration must
-- close that gap for itself explicitly (per 354's own instruction: "If a
-- future function legitimately needs anon or authenticated EXECUTE, grant
-- it explicitly in that function's own migration AFTER this one").
--
-- This supersedes match_and_claim_driver (migrations 77/80 — dead code,
-- zero callers per Phase 0's T2 findings). Those files are NOT edited
-- here (append-only rule); this migration simply never calls them.
--
-- Forward-compatible: new function only, no table rewrite. Safe to run
-- against live traffic. No RLS changes (no new table).

CREATE OR REPLACE FUNCTION dispatch_claim_batch(
    p_ride_id       text,
    p_driver_ids    text[],
    p_eta_seconds   int[],
    p_max_offers    int,
    p_offered_at    timestamptz,
    p_expires_at    timestamptz
)
RETURNS TABLE(
    driver_id       text,
    claimed         boolean,
    driver_row      jsonb,
    ride_offer_id   uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_n             int;
    v_i             int;
    v_driver_id     text;
    v_eta           int;
    v_driver_row    drivers%ROWTYPE;
    v_offer_id      uuid;
    v_claimed_count int := 0;
BEGIN
    v_n := COALESCE(array_length(p_driver_ids, 1), 0);

    -- Defensive parity check: a misaligned parallel array would silently
    -- attach the wrong ETA to the wrong driver's ride_offers row. Fail
    -- loud rather than guess, per CLAUDE.md's "do not silently swallow
    -- errors" rule — doubly so on a path that writes regulatory audit
    -- rows.
    IF COALESCE(array_length(p_eta_seconds, 1), 0) <> v_n THEN
        RAISE EXCEPTION
            'dispatch_claim_batch: p_driver_ids (%) and p_eta_seconds (%) length mismatch',
            v_n, COALESCE(array_length(p_eta_seconds, 1), 0);
    END IF;

    IF p_max_offers <= 0 OR v_n = 0 THEN
        RETURN;
    END IF;

    FOR v_i IN 1..v_n LOOP
        -- Same stopping condition as matching.py:860-861
        -- (`if len(claimed_drivers) >= max_offers: break`), checked BEFORE
        -- attempting the next candidate — a driver never reached this way
        -- is correctly not "attempted" (no cache invalidation owed for it).
        EXIT WHEN v_claimed_count >= p_max_offers;

        v_driver_id := p_driver_ids[v_i];
        v_eta       := p_eta_seconds[v_i];

        -- Step 1: atomic claim — identical predicate to
        -- driver_repo.py:claim_driver_atomic. The single-statement
        -- UPDATE ... WHERE is_available = true is already atomic in
        -- Postgres; a concurrent caller racing the same driver_id either
        -- wins this UPDATE or gets 0 rows here.
        UPDATE drivers
        SET is_available = false,
            availability_claimed_at = now()
        WHERE id = v_driver_id
          AND is_available = true
        RETURNING * INTO v_driver_row;

        IF NOT FOUND THEN
            -- Already claimed by someone else, or never available.
            driver_id     := v_driver_id;
            claimed       := false;
            driver_row    := NULL;
            ride_offer_id := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 2: revalidate the FULL eligibility set on the freshly
        -- claimed row (matching.py:875) — is_available alone (checked by
        -- the UPDATE's WHERE clause) is not sufficient; an admin could
        -- have suspended/unverified the driver between the candidate
        -- read and this claim.
        IF NOT (
            v_driver_row.is_online
            AND v_driver_row.is_verified
            AND v_driver_row.status = 'active'
        ) THEN
            -- Release — mirrors set_driver_available(driver_id, True)
            -- exactly, including clearing availability_claimed_at so the
            -- orphan-claim reaper (migration 157) doesn't later treat
            -- this as a stuck claim.
            UPDATE drivers
            SET is_available = true,
                availability_claimed_at = NULL
            WHERE id = v_driver_row.id;

            driver_id     := v_driver_row.id;
            claimed       := false;
            driver_row    := NULL;
            ride_offer_id := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 3: ride_offers insert — mirrors _build_offer_rows
        -- (matching.py:132-150) column-for-column.
        INSERT INTO ride_offers (
            ride_id, driver_id, status, eta_seconds, offered_at, expires_at
        ) VALUES (
            p_ride_id, v_driver_row.id, 'pending', v_eta, p_offered_at, p_expires_at
        )
        RETURNING id INTO v_offer_id;

        -- Step 4: insurance Period 2 transition, same transaction as the
        -- claim + offer insert (T12's atomicity requirement — see the
        -- OPEN QUESTION note above on started_at granularity). Not
        -- gated on the return value, matching matching.py:923-924.
        PERFORM record_insurance_period_transition(v_driver_row.id, 2::smallint, p_ride_id);

        v_claimed_count := v_claimed_count + 1;

        driver_id     := v_driver_row.id;
        claimed       := true;
        driver_row    := to_jsonb(v_driver_row);
        ride_offer_id := v_offer_id;
        RETURN NEXT;
    END LOOP;

    RETURN;
END;
$$;

COMMENT ON FUNCTION dispatch_claim_batch IS
    'C50 Phase 2 (T12): atomic batch driver-claim + ride_offers insert + '
    'insurance-period-2 transition for the direct-pool dispatch path. '
    'Returns one row per ATTEMPTED driver (claimed=true/false), not just '
    'successes, so the Python caller can invalidate_driver_cache for every '
    'attempted driver (see the migration header for why). Dark until '
    'dispatch_direct_pool_enabled (migration 400) is true AND matching.py '
    '(T13) calls it. Supersedes dead code match_and_claim_driver '
    '(migrations 77/80). Created in migration 401.';

-- Lock down EXECUTE the same way 354's sweep locks down every other
-- SECURITY DEFINER function in public: only service_role may call this.
-- This is a money/safety/compliance-adjacent RPC (claims drivers,
-- writes ride_offers, writes the regulatory insurance-period audit
-- table) with no internal auth guard of its own — the grant model IS
-- the access control, exactly the class of gap 354 was written to close.
REVOKE EXECUTE ON FUNCTION dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz)
    TO service_role;
