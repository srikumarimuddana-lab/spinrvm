import { buildLocationChoiceMessage } from '@shared/utils/aiLocationMessages';

// Must match the backend's _BRACKETED_COORDS pattern (backend/ai/pii.py) —
// the scrubber only preserves pairs in exactly this shape.
const BRACKETED_COORDS = /\[-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+\]/;

describe('buildLocationChoiceMessage', () => {
  const candidate = {
    name: 'Canadian Tire',
    address: '655 Albert St, Regina, SK S4R 2P4, Canada',
    lat: 50.440786,
    lng: -104.618023,
  };

  it('embeds the candidate coordinates at 5 decimal places', () => {
    const msg = buildLocationChoiceMessage(candidate, 'dropoff');
    expect(msg).toBe(
      'Use 655 Albert St, Regina, SK S4R 2P4, Canada [50.44079,-104.61802] as my dropoff.',
    );
  });

  it('matches the backend bracketed-coordinate pattern the PII scrubber preserves', () => {
    expect(buildLocationChoiceMessage(candidate, 'dropoff')).toMatch(BRACKETED_COORDS);
  });

  it.each([
    ['pickup', ' as my pickup.'],
    ['dropoff', ' as my dropoff.'],
  ] as const)('suffixes the %s role', (role, suffix) => {
    expect(buildLocationChoiceMessage(candidate, role)).toContain(suffix);
  });

  it('omits the role suffix when the role is unknown', () => {
    expect(buildLocationChoiceMessage(candidate, null)).toMatch(/\]\.$/);
    expect(buildLocationChoiceMessage(candidate, undefined)).not.toContain('as my');
  });

  it('prefers the address over the name as the label', () => {
    const msg = buildLocationChoiceMessage(candidate, 'dropoff');
    expect(msg).toContain('655 Albert St');
    expect(msg).not.toContain('Canadian Tire');
  });

  it('falls back to the name when there is no address', () => {
    const msg = buildLocationChoiceMessage({ ...candidate, address: null }, 'pickup');
    expect(msg).toBe('Use Canadian Tire [50.44079,-104.61802] as my pickup.');
  });

  it('returns null when the candidate has no usable label', () => {
    expect(buildLocationChoiceMessage({ ...candidate, address: null, name: null }, 'dropoff')).toBeNull();
    expect(buildLocationChoiceMessage({ ...candidate, address: '', name: undefined }, 'dropoff')).toBeNull();
  });
});
