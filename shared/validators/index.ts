/**
 * Shared validators used across rider-app, driver-app, and admin-dashboard.
 *
 * Rationale:
 * - Before: identical validation logic duplicated across 5+ files in each app
 * - After: single source of truth, consistent behavior, testable in isolation
 *
 * Each validator returns a discriminated union `ValidationResult` so callers
 * can get both a boolean and a user-facing reason without throwing.
 */

export type ValidationResult =
  | { valid: true }
  | { valid: false; reason: string };

/**
 * Validates a North American phone number.
 * Accepts common user-entered formats (with spaces, dashes, parens, + prefix).
 *
 * @param input Raw phone number string as entered by user
 * @returns ValidationResult with reason if invalid
 *
 * @example
 *   validatePhone("(306) 555-1234") // { valid: true }
 *   validatePhone("+13065551234")   // { valid: true }
 *   validatePhone("555")            // { valid: false, reason: "..." }
 */
export function validatePhone(input: string): ValidationResult {
  if (!input || typeof input !== "string") {
    return { valid: false, reason: "Phone number is required" };
  }
  const digits = input.replace(/\D/g, "");
  if (digits.length < 10) {
    return { valid: false, reason: "Phone number must be at least 10 digits" };
  }
  if (digits.length > 15) {
    return { valid: false, reason: "Phone number is too long" };
  }
  return { valid: true };
}

/**
 * Normalizes a phone number to E.164 format (assumes North American if no country code).
 * Use this before sending to the backend.
 *
 * @param input Raw phone number string
 * @returns E.164-formatted string (e.g., "+13065551234") or null if invalid
 */
export function normalizePhone(input: string): string | null {
  const result = validatePhone(input);
  if (!result.valid) return null;
  const digits = input.replace(/\D/g, "");
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
  return `+${digits}`;
}

/**
 * Validates an email address.
 * Note: This is a permissive regex for UX; backend must still validate per RFC 5322.
 */
export function validateEmail(input: string): ValidationResult {
  if (!input || typeof input !== "string") {
    return { valid: false, reason: "Email is required" };
  }
  const trimmed = input.trim();
  if (trimmed.length === 0) {
    return { valid: false, reason: "Email is required" };
  }
  if (trimmed.length > 254) {
    return { valid: false, reason: "Email is too long" };
  }
  // Basic shape check: local@domain.tld with at least one char on each side
  // and a TLD of 2+ chars. This rejects a@b.c which the legacy regex accepted.
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  if (!re.test(trimmed)) {
    return { valid: false, reason: "Enter a valid email address" };
  }
  return { valid: true };
}

/**
 * Validates a latitude value (-90 to 90).
 */
export function validateLatitude(lat: unknown): ValidationResult {
  const n = typeof lat === "number" ? lat : Number(lat);
  if (!Number.isFinite(n)) {
    return { valid: false, reason: "Latitude must be a number" };
  }
  if (n < -90 || n > 90) {
    return { valid: false, reason: "Latitude must be between -90 and 90" };
  }
  return { valid: true };
}

/**
 * Validates a longitude value (-180 to 180).
 */
export function validateLongitude(lng: unknown): ValidationResult {
  const n = typeof lng === "number" ? lng : Number(lng);
  if (!Number.isFinite(n)) {
    return { valid: false, reason: "Longitude must be a number" };
  }
  if (n < -180 || n > 180) {
    return { valid: false, reason: "Longitude must be between -180 and 180" };
  }
  return { valid: true };
}

/**
 * Validates a coordinate pair.
 * Rejects NaN, Infinity, and out-of-bounds values.
 */
export function validateCoordinates(
  lat: unknown,
  lng: unknown
): ValidationResult {
  const latResult = validateLatitude(lat);
  if (!latResult.valid) return latResult;
  const lngResult = validateLongitude(lng);
  if (!lngResult.valid) return lngResult;
  return { valid: true };
}

/**
 * Strip all non-digit characters. Used for phone number UI masking.
 */
export function digitsOnly(input: string): string {
  return (input || "").replace(/\D/g, "");
}
