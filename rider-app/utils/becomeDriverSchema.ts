/**
 * app/become-driver.tsx's `validateStep` -- validation extracted per
 * ACTION_ITEMS.md B39 (broader-sweep candidate: rider-app #2,
 * compliance/KYC tier). Pure extraction, byte-for-byte equivalent of
 * the checks it replaces -- no behavior change, including the
 * pre-existing asymmetry where only the vehicle-year check shows a
 * toast on failure; every other check silently blocks the "Next"
 * button (returns a falsy value) with no error message. That asymmetry
 * is preserved exactly, not "fixed" here -- adding a new toast where
 * none existed would be a behavior change on an already-shipped screen,
 * out of scope for a pure extraction.
 *
 * Vehicle-year check mirrors the SGI "vehicle < 10 years old" rule
 * documented in CLAUDE.md's Saskatchewan Regulatory section (driver
 * eligibility, enforced at onboarding).
 */

export interface DocStateLike {
  front?: string;
  back?: string;
  expiry?: string;
}

export interface RequirementLike {
  id: string;
  name: string;
  is_mandatory: boolean;
  requires_back_side: boolean;
}

/** Mirrors step 1's `firstName && lastName && email && city` truthy check. */
export function isPersonalStepValid(
  firstName: string,
  lastName: string,
  email: string,
  city: string,
): boolean {
  return !!(firstName && lastName && email && city);
}

/**
 * Mirrors step 2's `!vehicleYear || isNaN(year) || year < currentYear - 9`
 * check (inverted to a positive predicate). SGI's <10-year rule.
 */
export function isVehicleYearValid(vehicleYear: string): boolean {
  const year = parseInt(vehicleYear);
  const currentYear = new Date().getFullYear();
  return !(!vehicleYear || isNaN(year) || year < currentYear - 9);
}

/**
 * Mirrors step 2's `vehicleMake && vehicleModel && vehicleColor &&
 * licensePlate && vehicleVin && vehicleType` truthy check -- only
 * reached once `isVehicleYearValid` already passed.
 */
export function isVehicleDetailsValid(
  vehicleMake: string,
  vehicleModel: string,
  vehicleColor: string,
  licensePlate: string,
  vehicleVin: string,
  vehicleType: string,
): boolean {
  return !!(vehicleMake && vehicleModel && vehicleColor && licensePlate && vehicleVin && vehicleType);
}

/**
 * Mirrors step 3's `missing` array-building loop exactly: license
 * number, then each mandatory requirement's front/back/expiry
 * presence (expiry enforced only for "Driving License" and "Vehicle
 * Insurance", matching the original's hardcoded name check).
 */
export function getMissingDriverDocuments(
  licenseNumber: string,
  requirements: RequirementLike[],
  docs: Record<string, DocStateLike>,
): string[] {
  const missing: string[] = [];
  if (!licenseNumber) missing.push('Driver License Number');

  requirements.forEach((req) => {
    if (req.is_mandatory) {
      const uploaded = docs[req.id];
      if (!uploaded?.front) {
        missing.push(`${req.name} (Front)`);
      }
      if (req.requires_back_side && !uploaded?.back) {
        missing.push(`${req.name} (Back)`);
      }
      if (['Driving License', 'Vehicle Insurance'].includes(req.name) && !uploaded?.expiry) {
        missing.push(`${req.name} Expiry Date`);
      }
    }
  });

  return missing;
}
