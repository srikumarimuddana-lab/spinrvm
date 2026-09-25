/**
 * shared/hooks/usePlacesAutocomplete.ts — the additive `error` / `searched`
 * fields (C136 follow-up). A failed request (429 / 5xx / network) must be
 * distinguishable from a successful search with zero predictions, and
 * neither may be reported during the debounce window.
 *
 * Lives in driver-app because shared/ has no jest runner of its own; the
 * hook is imported for real (only the API client is mocked).
 */
import { renderHook, act } from '@testing-library/react-native';

import { usePlacesAutocomplete } from '@shared/hooks/usePlacesAutocomplete';

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));
jest.mock('@shared/utils/placesSession', () => ({ newPlacesSessionToken: () => 'tok-1' }));

beforeEach(() => {
  jest.useFakeTimers();
  mockApiGet.mockReset();
});
afterEach(() => {
  jest.useRealTimers();
});

async function settle() {
  await act(async () => {
    jest.advanceTimersByTime(350);
    await Promise.resolve();
    await Promise.resolve();
  });
}

it('reports searched with no error when the search succeeds with zero results', async () => {
  mockApiGet.mockResolvedValue({ data: { predictions: [] } });
  const { result } = renderHook(() => usePlacesAutocomplete('nowhere st', null));
  expect(result.current.searched).toBe(false);
  expect(result.current.error).toBeNull();
  await settle();
  expect(result.current.predictions).toEqual([]);
  expect(result.current.searched).toBe(true);
  expect(result.current.error).toBeNull();
});

it.each([
  ['429 rate limit', Object.assign(new Error('Too many requests'), { name: 'RateLimitError' })],
  ['5xx', Object.assign(new Error('Request failed with status code 502'), { response: { status: 502 } })],
  ['network', new Error('Network Error')],
])('reports error=unavailable (not an empty search) on %s', async (_label, err) => {
  mockApiGet.mockRejectedValue(err);
  const { result } = renderHook(() => usePlacesAutocomplete('123 main', null));
  await settle();
  expect(result.current.error).toBe('unavailable');
  expect(result.current.searched).toBe(false);
  expect(result.current.predictions).toEqual([]);
});

it('passes the location bias to the proxy and clears error on the next input', async () => {
  mockApiGet.mockRejectedValueOnce(new Error('Network Error'));
  const { result, rerender } = renderHook(
    ({ q }: { q: string }) => usePlacesAutocomplete(q, { lat: 52.13, lng: -106.67, radiusMeters: 50000 }),
    { initialProps: { q: '123 main' } },
  );
  await settle();
  expect(result.current.error).toBe('unavailable');
  const url = mockApiGet.mock.calls[0][0] as string;
  expect(url).toContain('location=52.1300%2C-106.6700');
  expect(url).toContain('radius=50000');

  mockApiGet.mockResolvedValueOnce({ data: { predictions: [{ place_id: 'p1', description: '123 Main St' }] } });
  rerender({ q: '123 main st' });
  expect(result.current.error).toBeNull();
  await settle();
  expect(result.current.predictions).toHaveLength(1);
  expect(result.current.searched).toBe(true);
});
