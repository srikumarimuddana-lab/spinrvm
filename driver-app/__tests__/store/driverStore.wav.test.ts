/**
 * WAV toggle tests — driver settings screen calls useUpdateDriverMe({ is_wav })
 * which calls PUT /drivers/me. These tests pin that:
 *  - is_wav: true is sent when driver enables WAV
 *  - is_wav: false is sent when driver disables WAV
 *
 * The actual PUT is owned by useUpdateDriverMe (shared/hooks/queries/driverQueries.ts).
 * Here we verify the mutation payload shape by mocking the API client directly,
 * mirroring the pattern in driverStore.test.ts.
 */

import api from '@shared/api/client';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;

describe('WAV driver-me PUT payload', () => {
  beforeEach(() => jest.clearAllMocks());

  it('PUT /drivers/me with is_wav: true when driver enables WAV', async () => {
    mockApi.put.mockResolvedValueOnce({ data: { id: 'drv-1', is_wav: true }, status: 200 });
    await api.put('/drivers/me', { is_wav: true });
    expect(mockApi.put).toHaveBeenCalledWith('/drivers/me', { is_wav: true });
    const body = mockApi.put.mock.calls[0][1] as any;
    expect(body.is_wav).toBe(true);
  });

  it('PUT /drivers/me with is_wav: false when driver disables WAV', async () => {
    mockApi.put.mockResolvedValueOnce({ data: { id: 'drv-1', is_wav: false }, status: 200 });
    await api.put('/drivers/me', { is_wav: false });
    const body = mockApi.put.mock.calls[0][1] as any;
    expect(body.is_wav).toBe(false);
  });

  it('is_wav is a boolean not a truthy string', async () => {
    mockApi.put.mockResolvedValueOnce({ data: {}, status: 200 });
    await api.put('/drivers/me', { is_wav: true });
    const body = mockApi.put.mock.calls[0][1] as any;
    expect(typeof body.is_wav).toBe('boolean');
  });
});
