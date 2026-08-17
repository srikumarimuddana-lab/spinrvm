/**
 * Retry-wrapped POST /rides/{rideId}/emergency for the driver Safety
 * shield/overlay (ACTION_ITEMS.md B16). 3 attempts, 1000ms/2000ms backoff --
 * same policy as SOSButton.tsx, reimplemented locally so this hook is used
 * only by the new discreet-SOS components (see the file's own header
 * comment for why it must never wrap the legacy SOSButton).
 *
 * Code under test: driver-app/hooks/useDriverSafetyTrigger.ts
 */

import { renderHook } from '@testing-library/react-native';
import apiClient from '@shared/api/client';
import { useDriverSafetyTrigger } from '../useDriverSafetyTrigger';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn() },
}));

const mockPost = apiClient.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('useDriverSafetyTrigger', () => {
  it('resolves on the first attempt and passes through the contacts array', async () => {
    mockPost.mockResolvedValue({ data: { contacts: [{ id: 'ec-1', name: 'Mom', notified: true }] } });
    const { result } = renderHook(() => useDriverSafetyTrigger());

    const promise = result.current.trigger('ride-1', 52.1, -106.6);
    const resolved = await promise;

    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith('/rides/ride-1/emergency', {
      latitude: 52.1,
      longitude: -106.6,
      // Migration 315: sent so a retry after a lost response returns the
      // original incident instead of re-alerting the driver's contacts.
      // Value is generated per call, so match on shape not content.
      idempotency_key: expect.stringMatching(/^[a-z0-9]+-[a-z0-9]+$/),
    });
    expect(resolved).toEqual({ contacts: [{ id: 'ec-1', name: 'Mom', notified: true }] });
  });

  it('reuses ONE idempotency key across all retry attempts of a single trigger', async () => {
    // The key identifies the press, not the attempt. If it were regenerated
    // per attempt the backend would see three distinct alerts and the dedup
    // would never engage — which is the whole point of migration 315.
    mockPost
      .mockRejectedValueOnce(new Error('net'))
      .mockRejectedValueOnce(new Error('net'))
      .mockResolvedValueOnce({ data: { contacts: [] } });
    const { result } = renderHook(() => useDriverSafetyTrigger());

    const promise = result.current.trigger('ride-9');
    await jest.advanceTimersByTimeAsync(3000);
    await promise;

    expect(mockPost).toHaveBeenCalledTimes(3);
    const keys = mockPost.mock.calls.map((c: any[]) => c[1].idempotency_key);
    expect(new Set(keys).size).toBe(1);
  });

  it('retries with 1000ms/2000ms backoff and succeeds on the 3rd attempt', async () => {
    mockPost
      .mockRejectedValueOnce(new Error('502'))
      .mockRejectedValueOnce(new Error('502'))
      .mockResolvedValueOnce({ data: { contacts: [] } });
    const { result } = renderHook(() => useDriverSafetyTrigger());

    const promise = result.current.trigger('ride-1');

    // Attempt 1 fires immediately (before any advance); attempts 2 and 3
    // are gated behind the 1000ms/2000ms backoff delays.
    await Promise.resolve();
    await Promise.resolve();
    expect(mockPost).toHaveBeenCalledTimes(1);

    await jest.advanceTimersByTimeAsync(1000);
    expect(mockPost).toHaveBeenCalledTimes(2);

    await jest.advanceTimersByTimeAsync(2000);
    expect(mockPost).toHaveBeenCalledTimes(3);

    await expect(promise).resolves.toEqual({ contacts: [] });
  });

  it('rethrows after 3 consecutive failures', async () => {
    const finalError = new Error('still down');
    mockPost.mockRejectedValueOnce(new Error('1')).mockRejectedValueOnce(new Error('2')).mockRejectedValueOnce(finalError);
    const { result } = renderHook(() => useDriverSafetyTrigger());

    const promise = result.current.trigger('ride-1');
    const assertion = expect(promise).rejects.toBe(finalError);

    await jest.advanceTimersByTimeAsync(1000);
    await jest.advanceTimersByTimeAsync(2000);

    await assertion;
    expect(mockPost).toHaveBeenCalledTimes(3);
  });
});
