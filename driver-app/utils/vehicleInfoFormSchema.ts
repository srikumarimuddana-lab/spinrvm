import { z } from 'zod';

/**
 * app/vehicle-info.tsx's inline `isFormValid` + submit-time
 * `parseInt(form.vehicle_year) || 0` computation, extracted per
 * ACTION_ITEMS.md B39 (broader-sweep candidate: driver-app #2).
 *
 * BUG FIX (not a pure byte-for-byte extraction): the original
 * `isFormValid` only checked `form.vehicle_year.trim()` was non-empty --
 * it never validated the value actually parses as a number. A driver
 * typing a non-numeric year (e.g. "abc") passed `isFormValid` (non-empty
 * string) and reached `handleSubmit`, where `parseInt(form.vehicle_year)
 * || 0` silently coerced the unparseable value to `0` and sent
 * `vehicle_year: 0` to the backend -- a garbage value written to the
 * driver's vehicle record with no error shown, no rejection, and no
 * signal to the driver that their input was ignored. This extraction
 * closes that gap by requiring the year to actually parse as a finite
 * integer before the form is considered valid, matching CLAUDE.md's
 * "do not silently swallow errors" convention (a client-side analogue --
 * this is a form-validation gate, not a DB/auth/payment call, but the
 * same principle: don't silently substitute a fallback that masks bad
 * input).
 */

export interface VehicleInfoFormField {
  vehicleTypeId: string;
  vehicleMake: string;
  vehicleModel: string;
  vehicleYear: string;
  licensePlate: string;
}

// Individual predicates are exported so future call sites (e.g. per-field
// error UI) can reuse them independently, matching the pattern used by
// profileSetupSchema.ts.

/** Mirrors the original `form.vehicle_type_id` truthy check exactly. */
export function isVehicleTypeSelected(vehicleTypeId: string): boolean {
  return !!vehicleTypeId;
}

/** Mirrors the original `form.vehicle_make.trim()` check exactly. */
export function isVehicleMakeValid(vehicleMake: string): boolean {
  return vehicleMake.trim().length > 0;
}

/** Mirrors the original `form.vehicle_model.trim()` check exactly. */
export function isVehicleModelValid(vehicleModel: string): boolean {
  return vehicleModel.trim().length > 0;
}

/**
 * BUG FIX: the original check was `form.vehicle_year.trim()` (non-empty
 * string only). This additionally requires the trimmed value to parse as
 * a finite integer, so a non-numeric year no longer silently reaches
 * `parseInt(...) || 0` at submit time.
 */
export function isVehicleYearValid(vehicleYear: string): boolean {
  const trimmed = vehicleYear.trim();
  if (!trimmed) return false;
  return Number.isFinite(parseInt(trimmed, 10));
}

/** Mirrors the original `form.license_plate.trim()` check exactly. */
export function isLicensePlateValid(licensePlate: string): boolean {
  return licensePlate.trim().length > 0;
}

export const vehicleInfoFormSchema = z.object({
  vehicleTypeId: z.string().refine(isVehicleTypeSelected, {
    message: 'Please select a vehicle type.',
  }),
  vehicleMake: z.string().refine(isVehicleMakeValid, {
    message: 'Please enter your vehicle make.',
  }),
  vehicleModel: z.string().refine(isVehicleModelValid, {
    message: 'Please enter your vehicle model.',
  }),
  vehicleYear: z.string().refine(isVehicleYearValid, {
    message: 'Please enter a valid vehicle year.',
  }),
  licensePlate: z.string().refine(isLicensePlateValid, {
    message: 'Please enter your license plate.',
  }),
});

export type VehicleInfoFormInput = z.infer<typeof vehicleInfoFormSchema>;

/**
 * Drop-in replacement for the screen's old inline `isFormValid`:
 * `form.vehicle_type_id && form.vehicle_make.trim() &&
 * form.vehicle_model.trim() && form.vehicle_year.trim() &&
 * form.license_plate.trim()` -- with the vehicle-year bug fix above.
 */
export function isVehicleInfoFormValid(form: VehicleInfoFormField): boolean {
  return vehicleInfoFormSchema.safeParse(form).success;
}

/**
 * Computes the vehicle_year value sent to the API. Mirrors the original
 * `parseInt(form.vehicle_year) || 0` for every input `isVehicleInfoFormValid`
 * already accepts; the `|| 0` fallback here is unreachable in practice
 * once the submit button is gated behind `isVehicleInfoFormValid` (kept
 * only as a defensive fallback, matching the original's own fallback
 * shape).
 */
export function getVehicleYearValue(vehicleYear: string): number {
  const parsed = parseInt(vehicleYear.trim(), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}
