-- Migration 244: Store vehicle_vin as plaintext at rest (masked in UI)
--
-- Reverses P2-5 for the VIN ONLY. license_number stays vault-encrypted.
--
-- Rationale: operational request — admins need to read VINs directly in the
-- dashboard and exports. VIN is reclassified from "encrypt-at-rest" PII to
-- "mask-in-UI" PII (handled like phone/plate: shown masked, revealed with the
-- Show PII toggle). This is a deliberate privacy-posture change; a DB/backup
-- leak now exposes VINs in plaintext, which vault encryption previously
-- prevented. license_number remains encrypted at rest.
--
-- What this does: any drivers.vehicle_vin that currently holds a vault.secrets
-- UUID is decrypted back into plaintext in the column. decrypt_driver_pii()
-- returns non-UUID input unchanged (it casts to uuid and catches the failure),
-- so rows that already store plaintext (legacy / pre-encryption) are left as-is
-- and re-running this migration is safe (idempotent). A 17-char VIN is never a
-- valid UUID, so a plaintext VIN is never re-decrypted into garbage.
--
-- Forward-compatible: decrypt_driver_pii() passes plaintext through untouched,
-- so reads keep working whether or not the application still decrypts VIN
-- during the deploy window. Deploy this together with the code that drops
-- vehicle_vin from _VAULT_PII_FIELDS so new writes store plaintext.
--
-- Scale note: this is a single UPDATE over the drivers table (modest row count
-- for a single-province launch). If the driver table later grows into the
-- millions, convert this to a batched backfill before running.
--
-- Rollback (on paper, no down-migration file): re-encrypt by calling
-- encrypt_driver_pii(vehicle_vin) per row and re-adding vehicle_vin to
-- _VAULT_PII_FIELDS, or restore from PITR. Re-encryption mints new vault
-- secrets, so it is not a clean inverse.

UPDATE drivers
   SET vehicle_vin = decrypt_driver_pii(vehicle_vin)
 WHERE vehicle_vin IS NOT NULL
   AND vehicle_vin <> '';
