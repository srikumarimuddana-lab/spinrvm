/**
 * Driver navigation preferences (Settings → Navigation).
 *
 * `navApp` picks which turn-by-turn app the hand-off targets; `autoNavigate`
 * decides whether that hand-off fires on its own when the driver accepts a ride
 * and again when the trip starts. Auto-navigate ships default-ON, so the stored
 * value only ever encodes the opt-out — the cases below pin that asymmetry down,
 * because a default that silently flips to OFF would strand the feature and a
 * corrupt value that reads as OFF would strand a driver who never opted out.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useNavStore } from '../../store/navStore';

const getItem = AsyncStorage.getItem as jest.Mock;
const setItem = AsyncStorage.setItem as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  useNavStore.setState({ navApp: 'default', autoNavigate: true, isLoaded: false });
});

describe('navStore', () => {
  it('defaults autoNavigate ON and navApp to default when nothing is stored', async () => {
    getItem.mockResolvedValue(null);
    await useNavStore.getState().loadNavApp();
    const s = useNavStore.getState();
    expect(s.autoNavigate).toBe(true);
    expect(s.navApp).toBe('default');
    expect(s.isLoaded).toBe(true);
  });

  it('hydrates a stored auto-navigate opt-out', async () => {
    getItem.mockImplementation((key: string) =>
      Promise.resolve(key === '@spinr_auto_navigate' ? '0' : null),
    );
    await useNavStore.getState().loadNavApp();
    expect(useNavStore.getState().autoNavigate).toBe(false);
  });

  it('hydrates both preferences together', async () => {
    getItem.mockImplementation((key: string) =>
      Promise.resolve(key === '@spinr_nav_app' ? 'waze' : '1'),
    );
    await useNavStore.getState().loadNavApp();
    const s = useNavStore.getState();
    expect(s.navApp).toBe('waze');
    expect(s.autoNavigate).toBe(true);
  });

  it('falls back to default-ON for a corrupt auto-navigate value', async () => {
    getItem.mockImplementation((key: string) =>
      Promise.resolve(key === '@spinr_auto_navigate' ? 'yes-please' : null),
    );
    await useNavStore.getState().loadNavApp();
    expect(useNavStore.getState().autoNavigate).toBe(true);
  });

  it('ignores a corrupt navApp value rather than overwriting the current choice', async () => {
    useNavStore.setState({ navApp: 'waze' });
    getItem.mockImplementation((key: string) =>
      Promise.resolve(key === '@spinr_nav_app' ? 'tomtom' : null),
    );
    await useNavStore.getState().loadNavApp();
    expect(useNavStore.getState().navApp).toBe('waze');
  });

  it('still hydrates autoNavigate when the navApp read rejects', async () => {
    getItem.mockImplementation((key: string) =>
      key === '@spinr_nav_app'
        ? Promise.reject(new Error('storage unavailable'))
        : Promise.resolve('0'),
    );
    await useNavStore.getState().loadNavApp();
    const s = useNavStore.getState();
    expect(s.autoNavigate).toBe(false);
    expect(s.isLoaded).toBe(true);
  });

  it('hydrates once, so a mount-time read cannot revert a just-made opt-out', async () => {
    // ActiveRidePanel calls loadNavApp on every mount. setAutoNavigate updates
    // state before its write finishes, so a second read landing in that gap
    // would flip the driver's opt-out back on and launch Maps at them.
    getItem.mockResolvedValue(null);
    await useNavStore.getState().loadNavApp();
    expect(useNavStore.getState().autoNavigate).toBe(true);

    await useNavStore.getState().setAutoNavigate(false);
    getItem.mockResolvedValue(null); // storage write not yet visible
    await useNavStore.getState().loadNavApp();
    expect(useNavStore.getState().autoNavigate).toBe(false);
  });

  it('degrades to defaults when AsyncStorage throws synchronously', async () => {
    // "NativeModule: AsyncStorage is null" on a bare Expo Go client or a broken
    // prebuild throws before a promise exists, so a per-key .catch never sees
    // it. Leaving isLoaded false would silently disable auto-navigation for the
    // whole session and stop Settings hydrating the saved nav app.
    getItem.mockImplementation(() => { throw new Error('NativeModule: AsyncStorage is null'); });
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    await expect(useNavStore.getState().loadNavApp()).resolves.toBeUndefined();
    const st = useNavStore.getState();
    expect(st.isLoaded).toBe(true);
    expect(st.autoNavigate).toBe(true);
    expect(st.navApp).toBe('default');
    spy.mockRestore();
  });

  it('setAutoNavigate updates state immediately and persists', async () => {
    await useNavStore.getState().setAutoNavigate(false);
    expect(useNavStore.getState().autoNavigate).toBe(false);
    expect(setItem).toHaveBeenCalledWith('@spinr_auto_navigate', '0');
  });

  it('keeps the in-memory choice when persisting autoNavigate fails', async () => {
    setItem.mockRejectedValueOnce(new Error('disk full'));
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    await useNavStore.getState().setAutoNavigate(false);
    expect(useNavStore.getState().autoNavigate).toBe(false);
    spy.mockRestore();
  });

  it('setNavApp updates state immediately and persists', async () => {
    await useNavStore.getState().setNavApp('google');
    expect(useNavStore.getState().navApp).toBe('google');
    expect(setItem).toHaveBeenCalledWith('@spinr_nav_app', 'google');
  });
});
