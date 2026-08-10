-- 289_driver_sin_encrypted.sql
-- Purpose: collect and hold each driver's SIN ourselves, encrypted, because
--          Stripe will not give it back.
--
--          Spinr has never stored a SIN. The design assumed Stripe Connect
--          held it and `POST /admin/drivers/{id}/reveal-sin` could read it at
--          T4A time. That is impossible: `individual.id_number` is WRITE-ONLY
--          on Stripe Connect — it exists only as a request parameter and is
--          never a response field, so the reveal endpoint has always returned
--          an error. Stripe exposes only booleans (`id_number_provided`,
--          `ssn_last_4_provided`); no digits, ever. Stripe's tax-form product
--          files US 1099s and does not file Canadian T4A.
--
--          Net effect: without this migration Spinr cannot file T4A slips at
--          all, because no system it can read holds the number.
--
-- ─────────────────────────────────────────────────────────────────────────
-- PRIVACY — READ BEFORE USING THESE COLUMNS
--
-- A SIN is among the most sensitive identifiers Spinr will ever hold.
-- CLAUDE.md forbids government IDs in logs, Sentry events and analytics
-- payloads without qualification. That applies to every column below.
--
--   sin              NOT the number. A `vault.secrets` UUID, exactly like
--                    `drivers.license_number` (migration 32). Plaintext is
--                    held by pgsodium under the `drivers_pii_key`; the app
--                    writes through the `encrypt_driver_pii` RPC and can only
--                    read back through `decrypt_driver_pii`.
--
--                    No application code decrypts this column today, by
--                    design. Collection and disclosure are separate
--                    decisions, and only collection has been made. Any future
--                    read path must be super_admin-gated and audited, like
--                    the existing reveal endpoint.
--
--   sin_last4        Plaintext, 4 digits. The ONLY part ever displayed. Its
--                    purpose is to let a driver confirm which number is on
--                    file, and an admin confirm T4A readiness, without
--                    decrypting anything. Four digits alone do not identify a
--                    person and match Spinr's existing `phone_last4` /
--                    `license_number_last4` precedent.
--
--   sin_collected_at When the driver supplied it. Drives "who is still
--                    missing a SIN" reporting ahead of the T4A deadline.
--
-- There is deliberately NO index on `sin` or `sin_last4`. Nothing looks a
-- driver up by their SIN; an index would only create another copy of the
-- value in the physical layout and another surface to leak.
--
-- ─────────────────────────────────────────────────────────────────────────
-- ROLLBACK
--
--   ALTER TABLE public.drivers DROP COLUMN IF EXISTS sin;
--   ALTER TABLE public.drivers DROP COLUMN IF EXISTS sin_last4;
--   ALTER TABLE public.drivers DROP COLUMN IF EXISTS sin_collected_at;
--
-- Dropping `sin` orphans rows in `vault.secrets` — it does NOT delete the
-- ciphertext. A rollback intended to erase collected SINs must also remove
-- those secrets:
--
--   DELETE FROM vault.secrets WHERE id IN (
--     SELECT sin::uuid FROM public.drivers WHERE sin IS NOT NULL
--   );
--
-- Run that BEFORE dropping the column, or the ids are lost and the ciphertext
-- is unreachable but retained — which is a PIPEDA problem, not a clean
-- rollback.
--
-- Purely additive: three nullable columns, no default, no backfill, no
-- rewrite of existing rows. Nothing reads them until application code ships,
-- so this is safe to apply ahead of the deploy.
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS sin              TEXT,
    ADD COLUMN IF NOT EXISTS sin_last4        TEXT,
    ADD COLUMN IF NOT EXISTS sin_collected_at TIMESTAMPTZ;

COMMENT ON COLUMN public.drivers.sin IS
    'vault.secrets UUID for the driver SIN (pgsodium, drivers_pii_key) — NEVER the number itself. Write via encrypt_driver_pii RPC. No application code decrypts this; any future read path must be super_admin-gated and audited.';

COMMENT ON COLUMN public.drivers.sin_last4 IS
    'Last 4 digits of the SIN, plaintext. The only part ever displayed, so on-file state is visible without a decrypt.';

COMMENT ON COLUMN public.drivers.sin_collected_at IS
    'When the driver supplied their SIN. Drives pre-deadline T4A readiness reporting.';

-- `sin_last4` is displayed, so a malformed value would be rendered to an
-- admin or a driver. The application validates the full SIN (9 digits +
-- Luhn) before it ever gets here; this is the backstop for any writer that
-- bypasses it — including a manual UPDATE during an incident.
ALTER TABLE public.drivers
    DROP CONSTRAINT IF EXISTS drivers_sin_last4_format;

ALTER TABLE public.drivers
    ADD CONSTRAINT drivers_sin_last4_format
    CHECK (sin_last4 IS NULL OR sin_last4 ~ '^[0-9]{4}$')
    NOT VALID;

-- NOT VALID above, VALIDATE separately: the ADD then takes only a brief lock
-- instead of scanning every driver row while dispatch is reading the table.
-- Every existing row has sin_last4 IS NULL and passes trivially.
ALTER TABLE public.drivers
    VALIDATE CONSTRAINT drivers_sin_last4_format;
