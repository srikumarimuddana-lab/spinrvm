/**
 * walletStore / rideStore error-message hygiene — store `error` fields must
 * hold user-presentable strings from getApiErrorMessage(), never raw
 * transport noise like "JSON Parse error: Unexpected token" or
 * "Request failed with status code 500".
 *
 * Regression for the raw-`error.message` leak: the stores used to assign
 * `error.message` directly, so any screen rendering the store `error`
 * surfaced parser/Axios internals to the user.
 */

// Keep the real getApiErrorMessage (the behavior under test) while stubbing
// the network methods the stores call.
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  return {
    __esModule: true,
    ...actual,
    default: {
      get: jest.fn(),
      post: jest.fn(),
      put: jest.fn(),
      patch: jest.fn(),
      delete: jest.fn(),
    },
  };
});

jest.mock('../utils/crashlytics', () => ({
  __esModule: true,
  recordNonFatal: jest.fn(),
}));

import api from '@shared/api/client';
import { useWalletStore } from '../store/walletStore';

const mockedGet = api.get as jest.Mock;
const mockedPost = api.post as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  useWalletStore.setState({ error: null, walletLoading: false, isLoading: false });
});

describe('walletStore error messages', () => {
  it('fetchWallet failure with a JSON parse error stores the friendly fallback, not parser noise', async () => {
    mockedGet.mockRejectedValueOnce(new SyntaxError('JSON Parse error: Unexpected token <'));
    await useWalletStore.getState().fetchWallet();
    const { error, walletLoading } = useWalletStore.getState();
    expect(error).toBe('Could not load your wallet. Please try again.');
    expect(walletLoading).toBe(false);
  });

  it('topUp failure with a generic Axios-style message stores the friendly fallback', async () => {
    mockedPost.mockRejectedValueOnce(new Error('Request failed with status code 500'));
    await expect(useWalletStore.getState().topUp(25)).rejects.toThrow();
    expect(useWalletStore.getState().error).toBe('Top-up failed. Please try again.');
  });

  it('topUp failure surfaces the backend detail when one exists', async () => {
    mockedPost.mockRejectedValueOnce({
      response: { status: 400, data: { detail: 'Minimum top-up amount is $5.00.' } },
      message: 'Request failed with status code 400',
    });
    await expect(useWalletStore.getState().topUp(1)).rejects.toBeDefined();
    expect(useWalletStore.getState().error).toBe('Minimum top-up amount is $5.00.');
  });
});
