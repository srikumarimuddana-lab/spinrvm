/**
 * useSafetyPanelConfig — decides which Safety-panel rows can render.
 *
 * The rule under test everywhere: a row appears only when it can actually DO
 * something. No placeholders, and above all no dead 911 button.
 */
import { renderHook, waitFor } from '@testing-library/react-native';
import api from '@shared/api/client';
import { useSafetyPanelConfig, DEFAULT_EMERGENCY_NUMBER } from '@shared/hooks/useSafetyPanelConfig';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

const mockGet = api.get as jest.Mock;

// A square around (50.4, -104.6) — Regina-ish.
const SQUARE = [
  { lat: 50.0, lng: -105.0 },
  { lat: 51.0, lng: -105.0 },
  { lat: 51.0, lng: -104.0 },
  { lat: 50.0, lng: -104.0 },
];
const INSIDE: [number, number] = [50.4, -104.6];
const OUTSIDE: [number, number] = [43.6, -79.3]; // Toronto

function mockApi(areas: any[], settings: any = {}) {
  mockGet.mockImplementation((url: string) =>
    url.includes('/service-areas')
      ? Promise.resolve({ data: areas })
      : Promise.resolve({ data: settings }),
  );
}

beforeEach(() => jest.clearAllMocks());

describe('useSafetyPanelConfig', () => {
  it('surfaces a fully-populated authority (Calgary 311 shape) with its phone intact', async () => {
    mockApi([
      {
        id: 'a1',
        polygon: SQUARE,
        emergency_number: '911',
        safety_authority_name: 'City of Calgary 311',
        safety_authority_phone: '311',
        safety_authority_hours: '24/7',
      },
    ]);

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.authority).not.toBeNull());
    expect(result.current.authority!.name).toBe('City of Calgary 311');
    // A 3-digit service code must survive — a phone regex would eat this.
    expect(result.current.authority!.phone).toBe('311');
  });

  it('omits the phone for an SK-shaped area so the row renders as a link', async () => {
    mockApi([
      {
        id: 'a1',
        polygon: SQUARE,
        safety_authority_name: 'SGI',
        safety_authority_url: 'https://sgi.sk.ca',
        safety_authority_phone: '',
      },
    ]);

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.authority).not.toBeNull());
    expect(result.current.authority!.phone).toBeUndefined();
    expect(result.current.authority!.url).toBe('https://sgi.sk.ca');
  });

  it('hides the authority row entirely when no name is configured', async () => {
    mockApi([{ id: 'a1', polygon: SQUARE, safety_authority_phone: '311' }]);

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.loading).toBe(false));
    // A phone with no name is not something to put in front of someone in
    // distress — the row is suppressed rather than rendered half-blank.
    expect(result.current.authority).toBeNull();
  });

  it('hides the authority row when the user is outside every service area', async () => {
    mockApi([{ id: 'a1', polygon: SQUARE, safety_authority_name: 'SGI' }]);

    const { result } = renderHook(() => useSafetyPanelConfig(...OUTSIDE));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.authority).toBeNull();
  });

  it('NEVER yields a blank emergency number, even if both fetches fail', async () => {
    mockGet.mockRejectedValue(new Error('offline'));

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.emergencyNumber).toBe(DEFAULT_EMERGENCY_NUMBER);
  });

  it('falls back to 911 when the area has a blank emergency_number', async () => {
    mockApi([{ id: 'a1', polygon: SQUARE, emergency_number: '  ' }]);

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.emergencyNumber).toBe('911');
  });

  it('keeps already-shipped tiles visible when settings cannot be read', async () => {
    mockGet.mockRejectedValue(new Error('offline'));

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.showShareTrip).toBe(true);
    expect(result.current.showReportIssue).toBe(true);
  });

  it('respects an explicit admin opt-out of a tile', async () => {
    mockApi([{ id: 'a1', polygon: SQUARE }], { sos_show_share_trip: false });

    const { result } = renderHook(() => useSafetyPanelConfig(...INSIDE));

    await waitFor(() => expect(result.current.showShareTrip).toBe(false));
  });
});
