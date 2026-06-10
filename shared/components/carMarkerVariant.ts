/**
 * Maps an admin-defined vehicle type name (vehicle_types.name) to one of the
 * three top-down car marker art variants. Kept free of React Native imports
 * so it can be unit-tested in plain Node.
 */
export type CarMarkerVariant = 'sedan' | 'suv' | 'lux';

// Vehicle types are admin-defined rows, so the mapping is keyword-based
// rather than exact: any name that reads like a bigger vehicle gets the SUV
// art, premium/black-car names get the lux art, everything else the sedan.
const SUV_PATTERN = /\b(suv|xl|van|minivan|crossover|truck|wagon|4x4|wav)\b/;
const LUX_PATTERN = /\b(lux\w*|black|premium|premier|select|executive|elite|business|comfort)\b/;

export function resolveCarMarkerVariant(vehicleType?: string | null): CarMarkerVariant {
    if (!vehicleType) return 'sedan';
    const name = vehicleType.toLowerCase();
    if (SUV_PATTERN.test(name)) return 'suv';
    if (LUX_PATTERN.test(name)) return 'lux';
    return 'sedan';
}

// SUVs render slightly larger than the size prop so the class difference is
// visible at a glance (mirrors how Uber/Lyft show XL vehicles on the map).
export const VARIANT_SIZE_FACTOR: Record<CarMarkerVariant, number> = {
    sedan: 1,
    suv: 1.12,
    lux: 1,
};
