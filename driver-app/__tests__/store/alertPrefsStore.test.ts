/**
 * Device-local alert prefs (Settings → Sound & Haptics).
 *
 * Regression for the audit finding that Sound Effects / Vibration were sent
 * to /notifications/preferences, whose model dropped them — they now persist
 * to AsyncStorage and gate the ride-offer tone loop and offer haptics.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useAlertPrefsStore } from '../../store/alertPrefsStore';

const getItem = AsyncStorage.getItem as jest.Mock;
const setItem = AsyncStorage.setItem as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  useAlertPrefsStore.setState({ soundEffects: true, vibration: true, isLoaded: false });
});

describe('alertPrefsStore', () => {
  it('defaults both prefs ON when nothing is stored', async () => {
    getItem.mockResolvedValue(null);
    await useAlertPrefsStore.getState().loadAlertPrefs();
    const s = useAlertPrefsStore.getState();
    expect(s.soundEffects).toBe(true);
    expect(s.vibration).toBe(true);
    expect(s.isLoaded).toBe(true);
  });

  it('hydrates a stored OFF for each pref independently', async () => {
    getItem.mockImplementation((key: string) =>
      Promise.resolve(key === '@spinr_alert_sound_effects' ? '0' : '1'),
    );
    await useAlertPrefsStore.getState().loadAlertPrefs();
    const s = useAlertPrefsStore.getState();
    expect(s.soundEffects).toBe(false);
    expect(s.vibration).toBe(true);
  });

  it('setSoundEffects updates state immediately and persists', async () => {
    await useAlertPrefsStore.getState().setSoundEffects(false);
    expect(useAlertPrefsStore.getState().soundEffects).toBe(false);
    expect(setItem).toHaveBeenCalledWith('@spinr_alert_sound_effects', '0');
  });

  it('setVibration updates state immediately and persists', async () => {
    await useAlertPrefsStore.getState().setVibration(false);
    expect(useAlertPrefsStore.getState().vibration).toBe(false);
    expect(setItem).toHaveBeenCalledWith('@spinr_alert_vibration', '0');
  });

  it('keeps the in-memory choice when persistence fails', async () => {
    setItem.mockRejectedValueOnce(new Error('disk full'));
    await useAlertPrefsStore.getState().setVibration(false);
    expect(useAlertPrefsStore.getState().vibration).toBe(false);
  });

  it('still marks loaded (with defaults) when storage read throws', async () => {
    getItem.mockRejectedValue(new Error('boom'));
    await useAlertPrefsStore.getState().loadAlertPrefs();
    const s = useAlertPrefsStore.getState();
    expect(s.isLoaded).toBe(true);
    expect(s.soundEffects).toBe(true);
  });
});
