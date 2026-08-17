// Jest resolves every `@shared/*` import here (jest.config.js moduleNameMapper),
// so anything a unit under test imports from shared/ needs a stub in this tree.
// The real store pulls SecureStore, the API client and Firebase in at module
// scope — none of which a headless unit test wants.
//
// Hand-rolled rather than importing zustand so `useAuthStore.setState({...})`
// works in tests the same way it does in app code, without a store subscription
// leaking between cases. Call `useAuthStore.__reset()` in beforeEach.
const DEFAULTS = {
  user: null,
  driver: null,
  token: null,
  refreshToken: null,
  tokenExpiresAt: null,
  isInitialized: false,
  isLoading: false,
  sessionRecoverable: false,
};

let state = { ...DEFAULTS };
const listeners = new Set();

function useAuthStore(selector) {
  return selector ? selector(state) : state;
}

useAuthStore.getState = () => state;
useAuthStore.setState = (partial) => {
  state = { ...state, ...(typeof partial === 'function' ? partial(state) : partial) };
  listeners.forEach((l) => l(state));
};
useAuthStore.subscribe = (l) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
/** Test-only: back to defaults, with a fresh `initialize` spy. */
useAuthStore.__reset = () => {
  state = { ...DEFAULTS, initialize: jest.fn(() => Promise.resolve()) };
  listeners.clear();
};

useAuthStore.__reset();

module.exports = { __esModule: true, useAuthStore, default: useAuthStore };
