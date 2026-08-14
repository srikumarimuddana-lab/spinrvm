/**
 * Reads the driver_discreet_sos_enabled dark-launch flag from GET /settings
 * (ACTION_ITEMS.md B16). Fails closed to `false` while loading and on any
 * fetch error.
 *
 * Code under test: driver-app/hooks/useDriverDiscreetSosFlag.ts
 */

import { renderHook, waitFor } from '@testing-library/react-native';
import apiClient from '@shared/api/client';
import { useDriverDiscreetSosFlag } from '../useDriverDiscreetSosFlag';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

const mockGet = apiClient.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

describe('useDriverDiscreetSosFlag', () => {
  it('reads false initially (before the fetch resolves)', () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolves
    const { result } = renderHook(() => useDriverDiscreetSosFlag());
    expect(result.current).toBe(false);
  });

  it('resolves to true when the backend has the flag enabled', async () => {
    mockGet.mockResolvedValue({ data: { driver_discreet_sos_enabled: true } });
    const { result } = renderHook(() => useDriverDiscreetSosFlag());

    await waitFor(() => expect(result.current).toBe(true));
    expect(mockGet).toHaveBeenCalledWith('/settings');
  });

  it('resolves to false when the backend has the flag disabled', async () => {
    mockGet.mockResolvedValue({ data: { driver_discreet_sos_enabled: false } });
    const { result } = renderHook(() => useDriverDiscreetSosFlag());

    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });

  it('fails closed to false on a fetch error', async () => {
    mockGet.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useDriverDiscreetSosFlag());

    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });
});
