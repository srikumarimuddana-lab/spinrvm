-- Rollback: DROP INDEX IF EXISTS idx_disputes_user_id, idx_disputes_user_status,
--           idx_promo_applications_user_id, idx_promo_applications_promo_user,
--           idx_promo_applications_created, idx_payouts_status_created,
--           idx_payouts_driver_id;

-- ── disputes ────────────────────────────────────────────────────────────────
-- routes/disputes.py: get_rows("disputes", {"user_id": ...}, order="created_at")
-- routes/admin/support.py: get_rows("disputes", {"status": ...}, order="created_at")
-- Existing indexes cover status, created_at, ride_id — but not user_id.
CREATE INDEX IF NOT EXISTS idx_disputes_user_id
    ON disputes (user_id);

CREATE INDEX IF NOT EXISTS idx_disputes_user_status
    ON disputes (user_id, status);

-- ── promo_applications ──────────────────────────────────────────────────────
-- Table was created outside migrations; has zero indexes.
-- routes/promotions.py: count_documents("promo_applications", {"promo_id": ..., "user_id": ...})
-- routes/promotions.py: get_rows("promo_applications", {"user_id": ...})
-- routes/admin/promotions.py: get_rows("promo_applications", {"promo_id": ...}, order="created_at")
CREATE INDEX IF NOT EXISTS idx_promo_applications_user_id
    ON promo_applications (user_id);

CREATE INDEX IF NOT EXISTS idx_promo_applications_promo_user
    ON promo_applications (promo_id, user_id);

CREATE INDEX IF NOT EXISTS idx_promo_applications_created
    ON promo_applications (created_at DESC);

-- ── payouts ─────────────────────────────────────────────────────────────────
-- admin/rides.py: get_rows("payouts", {"status": ...}, order="created_at", desc=True)
-- admin/rides.py: get_rows("payouts", {}, limit=10000)  (stats endpoint)
CREATE INDEX IF NOT EXISTS idx_payouts_status_created
    ON payouts (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payouts_driver_id
    ON payouts (driver_id);

NOTIFY pgrst, 'reload schema';
