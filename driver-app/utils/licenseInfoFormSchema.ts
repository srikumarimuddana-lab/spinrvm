import { z } from 'zod';

/**
 * app/driver/(tabs)/profile.tsx's licence self-serve entry modal —
 * ACTION_ITEMS.md B14 (driver-facing self-serve replacement for the parked
 * manual admin-backfill approach). Follows the predicate + zod pattern
 * established by vehicleInfoFormSchema.ts / driverProfileSchema.ts under
 * B39, rather than hand-rolled inline checks.
 *
 * Valid SK licence classes accepted for rideshare (see backend
 * routes/drivers/status.py's go_online eligibility re-check): "5" or "5A".
 * A driver on any other class can still submit here — the backend's own
 * go_online check (flag-gated, off by default) is the actual gate; this
 * form only stops obviously-empty input from reaching the API.
 */

export interface LicenseInfoFormField {
  licenseNumber: string;
  licenseClass: string;
}

/** Non-empty, trimmed licence number. */
export function isLicenseNumberValid(licenseNumber: string): boolean {
  return licenseNumber.trim().length > 0;
}

/** Non-empty, trimmed licence class (e.g. "5", "5A"). */
export function isLicenseClassValid(licenseClass: string): boolean {
  return licenseClass.trim().length > 0;
}

export const licenseInfoFormSchema = z.object({
  licenseNumber: z.string().refine(isLicenseNumberValid, {
    message: 'Please enter your driver’s licence number.',
  }),
  licenseClass: z.string().refine(isLicenseClassValid, {
    message: 'Please enter your driver’s licence class (e.g. 5 or 5A).',
  }),
});

export type LicenseInfoFormInput = z.infer<typeof licenseInfoFormSchema>;

export function isLicenseInfoFormValid(form: LicenseInfoFormField): boolean {
  return licenseInfoFormSchema.safeParse(form).success;
}

/** First failing field's message, or null if the form is valid. */
export function getLicenseInfoFormError(form: LicenseInfoFormField): string | null {
  const result = licenseInfoFormSchema.safeParse(form);
  if (result.success) return null;
  return result.error.issues[0]?.message ?? 'Please check your licence details.';
}
