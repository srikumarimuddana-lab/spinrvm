import { resolveCarMarkerVariant, VARIANT_SIZE_FACTOR } from '../carMarkerVariant';

describe('resolveCarMarkerVariant', () => {
    it('defaults to sedan when no type is given', () => {
        expect(resolveCarMarkerVariant()).toBe('sedan');
        expect(resolveCarMarkerVariant(null)).toBe('sedan');
        expect(resolveCarMarkerVariant('')).toBe('sedan');
    });

    it('maps sedan-like names to sedan', () => {
        for (const name of ['Sedan', 'sedan', 'Spinr Standard', 'Economy', 'Hatchback']) {
            expect(resolveCarMarkerVariant(name)).toBe('sedan');
        }
    });

    it('maps bigger-vehicle names to suv', () => {
        for (const name of ['SUV', 'Spinr XL', 'Minivan', 'Van', 'Crossover', 'WAV', '4x4']) {
            expect(resolveCarMarkerVariant(name)).toBe('suv');
        }
    });

    it('maps premium names to lux', () => {
        for (const name of ['Lux', 'Luxury', 'Spinr Black', 'Premium', 'Premier', 'Executive', 'Comfort']) {
            expect(resolveCarMarkerVariant(name)).toBe('lux');
        }
    });

    it('matches whole words only — no false positives from substrings', () => {
        // "axle" must not match the \bxl\b keyword, "vantage" must not match van.
        expect(resolveCarMarkerVariant('Axle')).toBe('sedan');
        expect(resolveCarMarkerVariant('Vantage')).toBe('sedan');
    });

    it('prefers suv when a name matches both classes', () => {
        // e.g. "Premium SUV" — the bigger silhouette wins so capacity reads right.
        expect(resolveCarMarkerVariant('Premium SUV')).toBe('suv');
    });

    it('has a size factor for every variant', () => {
        expect(VARIANT_SIZE_FACTOR.sedan).toBe(1);
        expect(VARIANT_SIZE_FACTOR.suv).toBeGreaterThan(1);
        expect(VARIANT_SIZE_FACTOR.lux).toBe(1);
    });
});
