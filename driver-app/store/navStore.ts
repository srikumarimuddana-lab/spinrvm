import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

export type NavApp = 'default' | 'google' | 'waze';

const NAV_APP_KEY = '@spinr_nav_app';
const AUTO_NAVIGATE_KEY = '@spinr_auto_navigate';

// Auto-navigate is ON by default, so the stored value only has to encode the
// opt-OUT. Anything that isn't an explicit '0' — a missing key (driver never
// touched the setting) or a corrupt value — resolves to the default. Storing
// '1' explicitly keeps the key readable when inspecting AsyncStorage.
const AUTO_NAVIGATE_ON = '1';
const AUTO_NAVIGATE_OFF = '0';

function isNavApp(value: unknown): value is NavApp {
    return value === 'default' || value === 'google' || value === 'waze';
}

interface NavState {
    navApp: NavApp;
    /** Launch the driver's nav app automatically on accept and on trip start. */
    autoNavigate: boolean;
    isLoaded: boolean;
    setNavApp: (app: NavApp) => Promise<void>;
    setAutoNavigate: (enabled: boolean) => Promise<void>;
    loadNavApp: () => Promise<void>;
}

// Driver's turn-by-turn navigation preferences: which app to hand off to, and
// whether the hand-off happens automatically. Persisted to AsyncStorage so both
// choices survive app restarts and are honoured by ActiveRidePanel (see its
// launcher call and the auto-launch effect).
export const useNavStore = create<NavState>((set, get) => ({
    navApp: 'default',
    autoNavigate: true,
    isLoaded: false,

    setNavApp: async (app: NavApp) => {
        // Optimistically update the UI, then persist. A storage failure
        // shouldn't strand the in-memory choice for the current session.
        set({ navApp: app });
        try {
            await AsyncStorage.setItem(NAV_APP_KEY, app);
        } catch (error) {
            console.error('Failed to store navigation app preference:', error);
        }
    },

    setAutoNavigate: async (enabled: boolean) => {
        set({ autoNavigate: enabled });
        try {
            await AsyncStorage.setItem(
                AUTO_NAVIGATE_KEY,
                enabled ? AUTO_NAVIGATE_ON : AUTO_NAVIGATE_OFF,
            );
        } catch (error) {
            console.error('Failed to store auto-navigate preference:', error);
        }
    },

    loadNavApp: async () => {
        // Hydrates BOTH nav preferences despite the name — kept as `loadNavApp`
        // because every call site and test mock already refers to it.
        //
        // Hydrate once per session. ActiveRidePanel calls this on every mount,
        // and re-reading would race a setAutoNavigate that has updated state but
        // not yet finished its write: the stale read would silently flip a
        // just-made opt-out back on, and the next transition would launch Maps
        // at a driver whose toggle reads OFF.
        if (get().isLoaded) return;

        // The whole body is guarded, not just the two promises. AsyncStorage
        // throws *synchronously* when the native module is missing (a bare Expo
        // Go client, a broken prebuild) — `.catch()` on the returned promise
        // never sees that, so an unguarded read would reject loadNavApp, leave
        // `isLoaded` false forever, and take auto-navigation and the Settings
        // radio down with it. Degrade to defaults instead.
        let storedApp: string | null = null;
        let storedAuto: string | null = null;
        try {
            [storedApp, storedAuto] = await Promise.all([
                Promise.resolve(AsyncStorage.getItem(NAV_APP_KEY)).catch(() => null),
                Promise.resolve(AsyncStorage.getItem(AUTO_NAVIGATE_KEY)).catch(() => null),
            ]);
        } catch (error) {
            console.error('Failed to read navigation preferences:', error);
        }

        // `isLoaded` is what gates the auto-launch effect: firing before this
        // resolves would hand off to the wrong app, or fire at all for a driver
        // who opted out, because the in-memory values are still the defaults.
        const patch: Partial<NavState> = {
            isLoaded: true,
            autoNavigate: storedAuto !== AUTO_NAVIGATE_OFF,
        };
        if (isNavApp(storedApp)) patch.navApp = storedApp;
        set(patch as NavState);
    },
}));
