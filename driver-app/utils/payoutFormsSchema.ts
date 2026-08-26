import { z } from 'zod';

/**
 * app/driver/payout.tsx -- GST/BN and SIN validation, extracted from three
 * inline regex/length checks (handleSaveGst, the `gstOnFile` checklist
 * flag, and handleSaveSin) per ACTION_ITEMS.md B39. Each helper below is a
 * byte-for-byte equivalent of the check it replaces -- see the PR
 * description for the before/after per call site.
 */

// CRA Business Number: 9 digits, optionally followed by a program-account
// suffix (e.g. RT0001). Mirrors the backend's own format check; not
// duplicated beyond this regex -- the server stays authoritative.
export const gstBnSchema = z.string().regex(/^\d{9}(RT\d{4})?$/);

/**
 * handleSaveGst's accept condition: an EMPTY cleaned value is allowed (it
 * clears gst_bn on save); a non-empty value must match the CRA BN/program-
 * account format. `cleaned` is expected pre-stripped of whitespace by the
 * caller, matching the existing `gstNumber.replace(/\s/g, '')` call site.
 */
export function isGstBnValid(cleaned: string): boolean {
  return !cleaned || gstBnSchema.safeParse(cleaned).success;
}

/**
 * The setup-checklist "gstOnFile" display flag: normalizes (strip
 * whitespace, uppercase) and requires a full match -- unlike isGstBnValid,
 * an empty value is NOT on file. Kept separate because it's a different
 * predicate (checklist "is this step done" vs. form "can I save this"),
 * not a duplicate of the same rule.
 */
export function isGstBnOnFile(value: string | null | undefined): boolean {
  return gstBnSchema.safeParse((value ?? '').replace(/\s/g, '').toUpperCase()).success;
}

// SIN: exactly 9 digits, after non-digit characters are stripped by the
// caller (sinInput.replace(/\D/g, '')). The checksum/leading-digit rule is
// server-side only (backend/utils/sin.py) -- deliberately not duplicated
// here, per this item's own note that the server remains authoritative.
export const sinDigitsSchema = z.string().length(9);

export function isSinValid(cleaned: string): boolean {
  return sinDigitsSchema.safeParse(cleaned).success;
}
