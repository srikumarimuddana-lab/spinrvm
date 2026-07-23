-- Rollback: No destructive changes.  Re-running is safe (idempotent requeue).
--
-- Requeue all rides whose route reconstruction previously failed but which
-- have observed GPS evidence.  The improved reconstruction pipeline (adaptive
-- endpoint tolerances, Google Directions Tier 2 fallback, haversine Tier 3/4
-- interpolation) guarantees these will now complete successfully.
--
-- The existing route_finalizer_loop (15 s tick, 1 route per tick) picks these
-- up automatically.  Even 500 failed routes process sequentially over ~2 hours
-- with no thundering-herd risk.  finalize_route() is idempotent and never
-- touches fare columns — only display/audit geometry is recomputed.

UPDATE ride_routes
SET    processing_status     = 'pending',
       retry_count           = 0,
       next_retry_at         = NOW(),
       processing_claimed_at = NULL,
       finalized_at          = NULL
WHERE  route_quality->>'reconstruction_status' = 'failed'
  AND  processing_status IN ('incomplete', 'complete')
  AND  observed_segments IS NOT NULL
  AND  jsonb_array_length(observed_segments) > 0;
