/**
 * Reads the directions_proxy_enabled dark-launch flag from GET /settings
 * (R7, docs/audit/ride-experience/ROADMAP.md). Fails closed to `false`
 * while loading and on any fetch error.
 *
 * Code under test: driver-app/hooks/useDirectionsProxyFlag.ts
 */

import { renderHook, waitFor } from '@testing-library/react-native';
import apiClient from '@shared/api/client';
import { useDirectionsProxyFlag } from '../useDirectionsProxyFlag';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

const mockGet = apiClient.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

describe('useDirectionsProxyFlag', () => {
  it('reads {enabled: false, loaded: false} initially (before the fetch resolves)', () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolves
    const { result } = renderHook(() => useDirectionsProxyFlag());
    expect(result.current).toEqual({ enabled: false, loaded: false });
  });

  it('resolves to {enabled: true, loaded: true} when the backend has the flag enabled', async () => {
    mockGet.mockResolvedValue({ data: { directions_proxy_enabled: true } });
    const { result } = renderHook(() => useDirectionsProxyFlag());

    await waitFor(() => expect(result.current).toEqual({ enabled: true, loaded: true }));
    expect(mockGet).toHaveBeenCalledWith('/settings');
  });

  it('resolves to {enabled: false, loaded: true} when the backend has the flag disabled', async () => {
    mockGet.mockResolvedValue({ data: { directions_proxy_enabled: false } });
    const { result } = renderHook(() => useDirectionsProxyFlag());

    await waitFor(() => expect(result.current).toEqual({ enabled: false, loaded: true }));
  });

  it('fails closed to {enabled: false, loaded: true} on a fetch error', async () => {
    mockGet.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useDirectionsProxyFlag());

    await waitFor(() => expect(result.current).toEqual({ enabled: false, loaded: true }));
  });
});
