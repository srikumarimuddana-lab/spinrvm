/**
 * app/become-driver.tsx -- the driver-onboarding wizard's validation,
 * extracted from `validateStep`'s two step-specific checks (personal
 * info requiredness; vehicle-year and vehicle-info-completeness rules)
 * and `handleSubmit`'s CRC-consent requiredness check, per
 * ACTION_ITEMS.md B39 (broader-sweep candidate: driver-app #1,
 * KYC onboarding -- doc expiry, vehicle age, vehicle-info completeness).
 * Each helper below is a byte-for-byte equivalent of the check it
 * replaces -- see the PR description for the before/after per call site.
 *
 * Compliance-critical: vehicle age feeds the Saskatchewan driver-
 * eligibility rule ("Vehicle < 10 years old"); the vehicle-year check
 * here is the client-side mirror of that rule (the backend remains
 * authoritative, same "not duplicated beyond this check" discipline as
 * `payoutFormsSchema.ts`'s GST/BN regex).
 */

/** Mirrors `validateStep`'s case-1 personal-info requiredness check. */
export function isPersonalStepValid(
  firstName: string,
  lastName: string,
  email: string,
  gender: string,
  serviceAreaId: string,
): boolean {
  return !!(firstName && lastName && email && gender && serviceAreaId);
}

/** Mirrors `validateStep`'s case-2 `hasVehicleInfo` computation. */
export function hasAnyVehicleInfo(
  vehicleMake: string,
  vehicleModel: string,
  vehicleColor: string,
  vehicleYear: string,
  licensePlate: string,
  vehicleVin: string,
  vehicleType: string,
): boolean {
  return !!(vehicleMake || vehicleModel || vehicleColor || vehicleYear || licensePlate || vehicleVin || vehicleType);
}

/**
 * Mirrors `validateStep`'s `vehicleYear && (isNaN(year) || year < currentYear - 9)`
 * check (inverted), where `year = parseInt(vehicleYear)`. An empty
 * `vehicleYear` is valid here (the check only runs `if (vehicleYear)` in
 * the original) -- the SK "vehicle < 10 years old" rule this mirrors is
 * enforced authoritatively server-side; this is the client-side early
 * warning.
 */
export function isVehicleYearValid(vehicleYear: string): boolean {
  if (!vehicleYear) return true;
  const year = parseInt(vehicleYear, 10);
  const currentYear = new Date().getFullYear();
  return !isNaN(year) && year >= currentYear - 9;
}

/** Mirrors `validateStep`'s `!vehicleMake || !vehicleModel || !licensePlate || !vehicleType` completeness check (inverted). */
export function isVehicleInfoComplete(
  vehicleMake: string,
  vehicleModel: string,
  licensePlate: string,
  vehicleType: string,
): boolean {
  return !!(vehicleMake && vehicleModel && licensePlate && vehicleType);
}

export type BecomeDriverVehicleStepInput = {
  vehicleMake: string;
  vehicleModel: string;
  vehicleColor: string;
  vehicleYear: string;
  licensePlate: string;
  vehicleVin: string;
  vehicleType: string;
};

/**
 * Runs the same checks as `validateStep`'s case-2 vehicle-info block, in
 * the same order, and returns the same `{ title, message }` pair the
 * original passed to `Alert.alert(...)` for the first failing check, or
 * null if the step is valid (either fully empty -- allowed as a skip --
 * or fully complete with a valid year).
 */
export function getVehicleStepError(input: BecomeDriverVehicleStepInput): { title: string; message: string } | null {
  const hasInfo = hasAnyVehicleInfo(
    input.vehicleMake,
    input.vehicleModel,
    input.vehicleColor,
    input.vehicleYear,
    input.licensePlate,
    input.vehicleVin,
    input.vehicleType,
  );
  if (!hasInfo) return null;
  if (!isVehicleYearValid(input.vehicleYear)) {
    return { title: 'Invalid Year', message: 'Vehicle must be 9 years old or newer.' };
  }
  if (!isVehicleInfoComplete(input.vehicleMake, input.vehicleModel, input.licensePlate, input.vehicleType)) {
    return { title: 'Incomplete Vehicle Info', message: 'Please complete all vehicle fields or use "Skip for now".' };
  }
  return null;
}

/** Mirrors `handleSubmit`'s `!crcConsentChecked` check (inverted). */
export function isCrcConsentValid(crcConsentChecked: boolean): boolean {
  return !!crcConsentChecked;
}
