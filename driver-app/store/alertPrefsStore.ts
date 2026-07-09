import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

const SOUND_KEY = '@spinr_alert_sound_effects';
const VIBRATION_KEY = '@spinr_alert_vibration';

interface AlertPrefsState {
    soundEffects: boolean;
    vibration: boolean;
    isLoaded: boolean;
    setSoundEffects: (on: boolean) => Promise<void>;
    setVibration: (on: boolean) => Promise<void>;
    loadAlertPrefs: () => Promise<void>;
}

// Device-local alert preferences (Settings → Sound & Haptics). These control
// device behaviours (ride-offer tone loop, haptic feedback), so they live in
// AsyncStorage rather than notification_preferences — a driver muting one
// phone shouldn't mute their other device. Consumers read via
// useAlertPrefsStore.getState() so a stale render can't replay a muted tone.
export const useAlertPrefsStore = create<AlertPrefsState>((set) => ({
    soundEffects: true,
    vibration: true,
    isLoaded: false,

    setSoundEffects: async (on: boolean) => {
        // Optimistically update the UI, then persist. A storage failure
        // shouldn't strand the in-memory choice for the current session.
        set({ soundEffects: on });
        try {
            await AsyncStorage.setItem(SOUND_KEY, on ? '1' : '0');
        } catch (error) {
            console.error('Failed to store sound-effects preference:', error);
        }
    },

    setVibration: async (on: boolean) => {
        set({ vibration: on });
        try {
            await AsyncStorage.setItem(VIBRATION_KEY, on ? '1' : '0');
        } catch (error) {
            console.error('Failed to store vibration preference:', error);
        }
    },

    loadAlertPrefs: async () => {
        try {
            const [sound, vib] = await Promise.all([
                AsyncStorage.getItem(SOUND_KEY),
                AsyncStorage.getItem(VIBRATION_KEY),
            ]);
            set({
                // Absent key (never toggled) keeps the ON default.
                soundEffects: sound !== '0',
                vibration: vib !== '0',
                isLoaded: true,
            });
        } catch {
            set({ isLoaded: true }); // fall through to defaults
        }
    },
}));
