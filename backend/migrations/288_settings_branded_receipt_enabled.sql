-- 288_settings_branded_receipt_enabled.sql
--
-- Kill switch for the branded ride-receipt / Spinr Pass invoice shell.
--
-- Those two emails predate the shared layout in utils/email_layout.py and
-- shipped their own bespoke shell: the legacy #ee2b2b rather than the
-- documented brand red, the wordmark as <h1>Spinr</h1> text rather than the
-- real logo, a hardcoded company footer, and no plain-text alternative. The
-- retrofit puts them on the shared header/footer and reads the company name
-- and address from the Company Info card on the admin Settings page.
--
-- Defaults TRUE. The whole point of the retrofit is that a receipt should
-- carry the company details an operator actually configured, so shipping it
-- dark would leave the known-wrong version in front of riders. FALSE restores
-- the previous shell byte-for-byte — pinned by
-- tests/test_receipt_shell_snapshot.py — without a redeploy, which is the
-- right response if it renders badly in a real mail client. Email rendering is
-- the one thing here that automated tests genuinely cannot check.
--
-- SCOPE — the flag governs the WRAPPER ONLY:
--   • header band, logo, footer, brand colour, plain-text alternative
-- It does NOT gate, and cannot affect:
--   • the fare rows, the separate GST/PST line items, area fees, surge notice
--     or the grand total (utils/email_receipt._build_fare_rows)
--   • whether a receipt is sent at all (that is the existing send path)
--   • the attached PDF's own fare table
-- A receipt is a tax-bearing document; putting its content behind a display
-- switch would be the wrong shape entirely.
--
-- NOT NULL DEFAULT true is safe with traffic in flight: no table rewrite on
-- PG 11+, existing rows backfill to the retrofit being on, and old replicas
-- that never write the column keep working. schemas.AppSettings defaults it
-- true as well, so behaviour is correct whether or not this migration has run.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS branded_receipt_enabled;
--   (Safe at any time: the schema default keeps the code reading `true`. To
--   turn the retrofit OFF, set the column false rather than dropping it.)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS branded_receipt_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.settings.branded_receipt_enabled IS
    'Renders the ride receipt and Spinr Pass invoice with the shared branded shell (real logo, brand red, company name/address from settings, plain-text alternative). false restores the previous bespoke shell without a redeploy. Governs presentation only — never the fare rows, GST/PST line items or totals.';
