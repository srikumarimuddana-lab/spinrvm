-- 403_dispatch_claim_batch_v2.sql
--
-- Supersedes the BODY of 402_dispatch_claim_batch.sql (merged via #4873) with
-- the review fixes from #4883. 402 is left untouched (append-only rule,
-- migration-check.yml CHECK F); this file is a CREATE OR REPLACE of the same
-- signature, so applying 402 then 403 in order yields the fixed function and
-- a database that already ran 402 gets the fix on its next migration run.
-- Nothing calls the function while dispatch_direct_pool_enabled is false
-- (its default), so the window between the two files is inert.
--
-- What changed versus 402 (details in each section below and in
-- docs/change-log/2026-09-03-c50-phase2-direct-pool-claim-review-fixes.md):
--   * claim row selected with FOR UPDATE SKIP LOCKED (deadlock/convoy fix)
--   * SECURITY INVOKER + search_path = pg_catalog, public (was DEFINER with
--     public first)
--   * 399-style argument validation (NULL p_max_offers claimed every driver)
--   * ride_offers insert is ON CONFLICT ON CONSTRAINT ... DO NOTHING with a
--     release path (a re-offer used to abort the whole batch)
--   * new insurance_written return column (a failed Period-2 write used to be
--     a Postgres-side RAISE WARNING only)
--   * revalidation release clamps is_available to is_online
--
-- C50 Phase 2 (T12) — batch driver-claim + offer-insert + insurance-period
-- RPC for the direct-pool dispatch path (dispatch_direct_pool_enabled,
-- flag OFF by default — see migration 401). Nothing calls this function
-- until Phase 2's T13 wires backend/repositories/dispatch_pool.py's
-- claim_batch() to it, and T13 only calls it when the flag is on. This
-- migration changes zero production behavior by itself.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz);
--   (or re-apply 402's body if the pre-fix function is wanted back; nothing
--   calls either while the flag is off)
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
--    revalidation, or already held a ride_offers row for this ride).
--    Reasoning, spelled out because this is a deliberate departure from
--    the plan doc's literal return-shape wording, not an oversight:
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
-- 4. RETURN SHAPE ALSO CARRIES `insurance_written boolean` (review fix,
--    2026-09-03). The Period-2 write is best-effort (see step 4 below) and
--    a failure must not roll back the claim — but it also must not vanish.
--    On the PostgREST path a failed write is an ERROR log plus a
--    `spinr_insurance_period_write_failed_total` increment
--    (utils/insurance_periods.py); a plpgsql RAISE WARNING reaches only the
--    Postgres server log, which nothing in the app pipeline reads. The
--    flag reports the outcome per claimed driver so matching.py can emit
--    the same ERROR log and metric the PostgREST path does. It is NULL on
--    unclaimed rows.
--
-- ============================================================================
-- Semantics — a translation of the existing Python claim loop, not a redesign
-- ============================================================================
--
-- Per driver in p_driver_ids, in the GIVEN order (no re-ranking), UNTIL
-- p_max_offers successful claims have been made:
--   1. Claim: `UPDATE drivers SET is_available = false,
--      availability_claimed_at = now() WHERE id = $1 AND is_available =
--      true` — the exact predicate verified against
--      backend/repositories/driver_repo.py:271-286 (claim_driver_atomic).
--      The row is selected with FOR UPDATE SKIP LOCKED first (review fix,
--      2026-09-03 — see "Locking" below). 0 rows matched => driver was
--      claimed by a concurrent caller, is locked by a concurrent batch, or
--      was never available => emit an unclaimed row, continue.
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
--      status='pending', eta_seconds, offered_at, expires_at. The insert
--      is `ON CONFLICT ON CONSTRAINT ride_offers_ride_driver_uq DO NOTHING` (review fix,
--      2026-09-03): ride_offers_ride_driver_uq (migration 100) means a
--      driver re-ranked for a ride they already hold a row for (the Redis
--      offer_skip guard in matching.py is skipped when Redis is down)
--      would otherwise raise unique_violation and abort the WHOLE batch,
--      discarding every other driver's valid claim. On conflict the driver
--      is released exactly as in step 2 and reported unclaimed.
--   4. Call `record_insurance_period_transition(driver_id, 2, ride_id)`
--      (migration 253) IN THIS SAME TRANSACTION — Period 2 (TNC primary
--      commercial, en route to pickup) opens at claim time per CLAUDE.md.
--      Not gated on its return value, matching matching.py:923-924, which
--      calls this unconditionally without branching on the result
--      (status ok/noop/race). Wrapped in its own sub-transaction so a
--      failure sets insurance_written = false instead of aborting the batch.
--   5. Emit a claimed row (driver_row as jsonb, ride_offer_id,
--      insurance_written).
--
-- driver_row is jsonb (the full post-update `drivers` row), not a typed
-- positional column list: `drivers` gains columns over time (20+
-- migrations in this repo do exactly that) and a positional TABLE(...)
-- return here would silently go stale every time `drivers` changes. The
-- Python caller (dispatch_pool.claim_batch, T13) treats it the same way
-- it already treats a postgrest response row: a dict. It is consumed only
-- over the direct pool (never serialized through PostgREST), and the
-- caller reads only `id` and `user_id` from it.
--
-- ============================================================================
-- Locking (review fix, 2026-09-03)
-- ============================================================================
-- Today's Python loop issues each claim as its own PostgREST request, i.e.
-- its own transaction: a transaction holds at most ONE `drivers` row lock,
-- for microseconds, so two concurrent dispatches cannot deadlock. This
-- function claims up to p_max_offers drivers in ONE transaction and holds
-- every claimed row's lock until COMMIT. A bare `UPDATE ... WHERE id = $1`
-- would BLOCK on a row a concurrent batch already claimed, and two batches
-- ranking the same drivers in different orders (ranking is per-pickup ETA)
-- would deadlock: A holds d1 and waits on d2, B holds d2 and waits on d1 —
-- Postgres aborts one after deadlock_timeout (1 s) with 40P01, the caller
-- re-raises, and the ride waits a full retry backoff. The claim therefore
-- selects its row with `FOR UPDATE SKIP LOCKED` (the same construct
-- 399_transactional_outbox.sql's outbox_claim_batch uses): a row locked by
-- a concurrent batch reads as "lost the race" and the loop moves on, which
-- is already the documented and tested outcome for a concurrent claim.
--
-- ============================================================================
-- RESOLVED (Divya, Security & Compliance, C50 Phase 2 T15 review,
-- 2026-09-02): batching all claims for one dispatch attempt into a single
-- transaction, hence a single record_insurance_period_transition
-- started_at per attempt, is ACCEPTED as correct -- not a compliance gap.
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
-- claimed in the same dispatch_claim_batch call gets an IDENTICAL
-- started_at (the shared transaction timestamp) once this path is
-- enabled -- a real, deliberate change in audit-trail granularity from
-- today's behavior.
--
-- Divya's sign-off (compliance owner, SGI / Saskatchewan Transportation
-- Act audit-trail charter): this is MORE accurate, not less, for a
-- batch-offer dispatch model -- all drivers genuinely were offered at the
-- same instant of one atomic operation, so today's serial-transaction
-- timestamps encode an artifact of *implementation* (Python loop
-- latency), not a real-world distinction a regulator would care about.
-- Sub-millisecond timestamp granularity between DIFFERENT drivers was
-- never a regulatory requirement -- SGI/the Act care about which period a
-- driver was in and for how long, not microsecond ordering between two
-- different drivers' offers. Ordering WITHIN one driver's own history
-- (what an incident investigation actually keys on: driver_id,
-- started_at DESC) is unaffected -- each driver still gets exactly one
-- distinct, chronologically correct row per their own transition.
-- Empirically verified during review: a forced mid-call failure
-- (mismatched array lengths) leaves ZERO partial driver_insurance_periods
-- rows for that attempt -- Postgres's transactional atomicity means a
-- failed batch leaves no audit trail at all, which is correct (no offer
-- was actually extended, so no Period-2 row should exist). Not a blocker
-- for shipping this migration flag-off.
--
-- ============================================================================
-- Conventions
-- ============================================================================
-- SECURITY INVOKER + `search_path = pg_catalog, public` — matches
-- 399_transactional_outbox.sql's outbox_claim_batch, the structurally
-- identical batch-claim precedent (review fix, 2026-09-03; the first cut
-- was SECURITY DEFINER with `public, pg_catalog`). DEFINER buys nothing
-- here: the only caller connects as service_role, which already owns full
-- access to drivers / ride_offers, and the nested
-- record_insurance_period_transition call is itself SECURITY DEFINER with
-- EXECUTE granted to service_role (migration 354). DEFINER with `public`
-- searched before `pg_catalog` was the CVE-2018-1058 shape: any role with
-- CREATE on public could shadow now()/to_jsonb() and run it as the owner.
-- This function moves no money or credits, so migrations/CLAUDE.md's
-- "money-touching functions must be SECURITY DEFINER" rule does not apply.
--
-- Grants follow the pattern verified in
-- 354_revoke_public_execute_on_security_definer_fns.sql: Postgres grants
-- EXECUTE to PUBLIC on CREATE FUNCTION by default, and 354's sweep only
-- covers SECURITY DEFINER functions that existed AT THE TIME IT RAN — it
-- covers neither later functions nor INVOKER ones, so this migration must
-- close that gap for itself explicitly (per 354's own instruction: "If a
-- future function legitimately needs anon or authenticated EXECUTE, grant
-- it explicitly in that function's own migration AFTER this one").
--
-- This supersedes match_and_claim_driver (migrations 77/80 — dead code,
-- zero callers per Phase 0's T2 findings). Those files are NOT edited
-- here (append-only rule); this migration simply never calls them.
--
-- Argument validation mirrors 399: NULL or out-of-range control arguments
-- RAISE rather than silently widening the batch (a NULL p_max_offers made
-- both the early-return guard and the loop's EXIT WHEN evaluate to NULL,
-- i.e. never fire — the function would have claimed EVERY driver in the
-- array). The upper bound (50) is a sanity ceiling well above
-- service_areas.max_simultaneous_offers' hard cap (10, matching.py), not a
-- policy value.
--
-- Forward-compatible: new function only, no table rewrite. Safe to run
-- against live traffic. No RLS changes (no new table). The trailing
-- NOTIFY pgrst is not required while only the direct pool calls this
-- function (PostgREST never sees it); it is kept for parity with 399 so a
-- future PostgREST RPC caller does not hit a stale schema cache.

-- The return shape gains a column, and CREATE OR REPLACE cannot change an
-- existing function's OUT-parameter row type ("cannot change return type of
-- existing function"), so the 402 version is dropped first. Safe against
-- in-flight traffic: nothing calls this function while
-- dispatch_direct_pool_enabled is false (its default), and psycopg holds no
-- prepared statement against it (prepare_threshold=None). Re-runnable.
DROP FUNCTION IF EXISTS public.dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz);

CREATE FUNCTION public.dispatch_claim_batch(
    p_ride_id       text,
    p_driver_ids    text[],
    p_eta_seconds   int[],
    p_max_offers    int,
    p_offered_at    timestamptz,
    p_expires_at    timestamptz
)
RETURNS TABLE(
    driver_id         text,
    claimed           boolean,
    driver_row        jsonb,
    ride_offer_id     uuid,
    insurance_written boolean
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_n                 int;
    v_i                 int;
    v_driver_id         text;
    v_eta               int;
    v_driver_row        drivers%ROWTYPE;
    v_offer_id          uuid;
    v_claimed_count     int := 0;
    v_insurance_written boolean;
BEGIN
    -- Argument validation (399-style): fail loud on anything that would
    -- otherwise widen the batch or write an un-reapable offer.
    IF p_ride_id IS NULL OR p_ride_id = '' THEN
        RAISE EXCEPTION 'dispatch_claim_batch: p_ride_id is required';
    END IF;
    IF p_max_offers IS NULL OR p_max_offers < 1 OR p_max_offers > 50 THEN
        RAISE EXCEPTION 'dispatch_claim_batch: p_max_offers must be between 1 and 50 (got %)', p_max_offers;
    END IF;
    IF p_offered_at IS NULL OR p_expires_at IS NULL OR p_expires_at <= p_offered_at THEN
        -- A NULL expires_at would produce a pending offer the durable
        -- offer-expiry reaper (WHERE expires_at < now()) can never expire.
        RAISE EXCEPTION 'dispatch_claim_batch: p_offered_at/p_expires_at must be non-NULL with expires_at > offered_at';
    END IF;

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

    IF v_n = 0 THEN
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

        IF v_driver_id IS NULL THEN
            -- A NULL element cannot be claimed or cache-invalidated; skip it
            -- without emitting a row (nothing to invalidate).
            CONTINUE;
        END IF;

        -- Step 1: atomic claim — identical predicate to
        -- driver_repo.py:claim_driver_atomic. The row is locked with
        -- FOR UPDATE SKIP LOCKED first so a row a concurrent batch already
        -- holds reads as "lost the race" instead of blocking (see the
        -- Locking section above). A concurrent caller racing the same
        -- driver_id either wins this UPDATE or gets 0 rows here.
        UPDATE drivers AS d
        SET is_available = false,
            availability_claimed_at = now()
        FROM (
            SELECT c.id
            FROM drivers AS c
            WHERE c.id = v_driver_id
              AND c.is_available = true
            FOR UPDATE OF c SKIP LOCKED
        ) AS locked
        WHERE d.id = locked.id
        RETURNING d.* INTO v_driver_row;

        IF NOT FOUND THEN
            -- Already claimed by someone else, locked by a concurrent
            -- batch, or never available.
            driver_id         := v_driver_id;
            claimed           := false;
            driver_row        := NULL;
            ride_offer_id     := NULL;
            insurance_written := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 2: revalidate the FULL eligibility set on the freshly
        -- claimed row (matching.py:875) — is_available alone (checked by
        -- the UPDATE's WHERE clause) is not sufficient; an admin could
        -- have suspended/unverified the driver between the candidate
        -- read and this claim.
        IF NOT (
            COALESCE(v_driver_row.is_online, false)
            AND COALESCE(v_driver_row.is_verified, false)
            AND v_driver_row.status = 'active'
        ) THEN
            -- Release — mirrors set_driver_available(driver_id, True)
            -- exactly, including clearing availability_claimed_at so the
            -- orphan-claim reaper (migration 157) doesn't later treat
            -- this as a stuck claim. The is_available => is_online clamp
            -- (driver_repo.py:157-171) is applied: an offline driver is
            -- never marked available.
            UPDATE drivers
            SET is_available = COALESCE(v_driver_row.is_online, false),
                availability_claimed_at = NULL
            WHERE id = v_driver_row.id;

            driver_id         := v_driver_row.id;
            claimed           := false;
            driver_row        := NULL;
            ride_offer_id     := NULL;
            insurance_written := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 3: ride_offers insert — mirrors _build_offer_rows
        -- (matching.py:132-150) column-for-column. ON CONFLICT: this
        -- driver already holds a ride_offers row for this ride (a re-offer
        -- after decline/expiry that the Redis offer_skip guard did not
        -- catch). Do not abort the batch; release the driver and report
        -- them unclaimed, exactly as a failed revalidation does.
        v_offer_id := NULL;
        INSERT INTO ride_offers (
            ride_id, driver_id, status, eta_seconds, offered_at, expires_at
        ) VALUES (
            p_ride_id, v_driver_row.id, 'pending', v_eta, p_offered_at, p_expires_at
        )
        -- Named constraint, not a column list: inside plpgsql the output
        -- column `driver_id` is also a variable, so `ON CONFLICT (ride_id,
        -- driver_id)` is rejected as ambiguous (caught by the local psql run).
        ON CONFLICT ON CONSTRAINT ride_offers_ride_driver_uq DO NOTHING
        RETURNING id INTO v_offer_id;

        IF NOT FOUND OR v_offer_id IS NULL THEN
            UPDATE drivers
            SET is_available = true,
                availability_claimed_at = NULL
            WHERE id = v_driver_row.id;

            driver_id         := v_driver_row.id;
            claimed           := false;
            driver_row        := NULL;
            ride_offer_id     := NULL;
            insurance_written := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 4: insurance Period 2 transition, same transaction as the
        -- claim + offer insert (T12's atomicity requirement — see the
        -- RESOLVED note above on started_at granularity). Not gated on the
        -- return value, matching matching.py:923-924.
        --
        -- FIX (Surya, C50 Phase 2 T15 adversarial review, 2026-09-02):
        -- the Python compliance-write wrapper (utils/insurance_periods.py's
        -- record_period_transition) deliberately swallows ANY exception
        -- from this RPC ("a missed audit row is preferable to blocking the
        -- driver state machine" — its own module docstring) so a hiccup on
        -- the insurance write never affects the claim or the offer. A bare
        -- `PERFORM` here would NOT have that property: any error other than
        -- the unique_violation record_insurance_period_transition already
        -- catches internally (deadlock, statement timeout, a future schema
        -- change) would propagate out of the PERFORM and abort this WHOLE
        -- dispatch_claim_batch transaction — rolling back every claim and
        -- every ride_offers insert in the batch, not just this driver's,
        -- over a best-effort compliance write. That is a materially worse
        -- failure mode than today's (an insurance-table blip could block
        -- ALL dispatch for a ride instead of zero), so it is wrapped in its
        -- own sub-transaction via a nested block. On failure the claim and
        -- ride_offers row for this driver stand regardless, and the outcome
        -- is reported to the caller via insurance_written = false so the
        -- application logs at ERROR and increments
        -- spinr_insurance_period_write_failed_total exactly as the
        -- PostgREST path does (review fix, 2026-09-03 — a RAISE WARNING
        -- alone reaches only the Postgres server log). The WARNING is kept
        -- as a secondary trace in the server log.
        v_insurance_written := true;
        BEGIN
            PERFORM record_insurance_period_transition(v_driver_row.id, 2::smallint, p_ride_id);
        EXCEPTION WHEN OTHERS THEN
            v_insurance_written := false;
            RAISE WARNING
                'dispatch_claim_batch: insurance-period-2 write failed for driver % ride % (claim and offer stand) — %',
                v_driver_row.id, p_ride_id, SQLERRM;
        END;

        v_claimed_count := v_claimed_count + 1;

        driver_id         := v_driver_row.id;
        claimed           := true;
        driver_row        := to_jsonb(v_driver_row);
        ride_offer_id     := v_offer_id;
        insurance_written := v_insurance_written;
        RETURN NEXT;
    END LOOP;

    RETURN;
END;
$$;

COMMENT ON FUNCTION public.dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz) IS
    'C50 Phase 2 (T12): atomic batch driver-claim + ride_offers insert + '
    'insurance-period-2 transition for the direct-pool dispatch path. '
    'Returns one row per ATTEMPTED driver (claimed=true/false), not just '
    'successes, so the Python caller can invalidate_driver_cache for every '
    'attempted driver (see the migration header for why); insurance_written '
    'reports the best-effort Period-2 write per claimed driver. Claims with '
    'FOR UPDATE SKIP LOCKED so concurrent batches cannot deadlock. Dark until '
    'dispatch_direct_pool_enabled (migration 401) is true AND matching.py '
    '(T13) calls it. Supersedes dead code match_and_claim_driver '
    '(migrations 77/80). Created in migration 402; body superseded by 403.';

-- Lock down EXECUTE. 354's sweep covers only SECURITY DEFINER functions that
-- existed when it ran; this INVOKER function must carry its own block (same
-- three-statement form as 399). This is a safety/compliance-adjacent RPC
-- (claims drivers, writes ride_offers, writes the regulatory
-- insurance-period audit table) with no internal auth guard of its own —
-- the grant model IS the access control, exactly the class of gap 354 was
-- written to close.
REVOKE ALL ON FUNCTION public.dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.dispatch_claim_batch(text, text[], int[], int, timestamptz, timestamptz) TO service_role;

NOTIFY pgrst, 'reload schema';
