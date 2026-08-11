/**
 * Thin wrapper around GET /users/emergency-contacts, shared by the driver
 * Safety shield/overlay (ACTION_ITEMS.md B16). See useHoldToConfirm.test.ts
 * for why this lives under driver-app/__tests__/ rather than
 * shared/hooks/__tests__/.
 *
 * Code under test: shared/hooks/useEmergencyContacts.ts
 */

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

import { renderHook, waitFor } from '@testing-library/react-native';
import apiClient from '@shared/api/client';
import { useEmergencyContacts } from '@shared/hooks/useEmergencyContacts';

const mockGet = apiClient.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

describe('useEmergencyContacts', () => {
  it('starts loading and resolves to the fetched contacts', async () => {
    mockGet.mockResolvedValue({
      data: { contacts: [{ id: 'ec-1', name: 'Mom', phone: '+13061112222', relationship: 'Parent' }] },
    });

    const { result } = renderHook(() => useEmergencyContacts());
    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(mockGet).toHaveBeenCalledWith('/users/emergency-contacts');
    expect(result.current.contacts).toEqual([{ id: 'ec-1', name: 'Mom' }]);
  });

  it('degrades to an empty array on fetch failure instead of throwing', async () => {
    mockGet.mockRejectedValue(new Error('network down'));

    const { result } = renderHook(() => useEmergencyContacts());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.contacts).toEqual([]);
  });

  it('resolves to an empty array when the backend returns no contacts', async () => {
    mockGet.mockResolvedValue({ data: { contacts: [] } });

    const { result } = renderHook(() => useEmergencyContacts());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.contacts).toEqual([]);
  });
});
