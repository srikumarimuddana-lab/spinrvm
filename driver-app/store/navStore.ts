import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

export type NavApp = 'default' | 'google' | 'waze';

const NAV_APP_KEY = '@spinr_nav_app';

function isNavApp(value: unknown): value is NavApp {
    return value === 'default' || value === 'google' || value === 'waze';
}

interface NavState {
    navApp: NavApp;
    isLoaded: boolean;
    setNavApp: (app: NavApp) => Promise<void>;
    loadNavApp: () => Promise<void>;
}

// Driver's preferred turn-by-turn app for the "Navigate to Pickup/Dropoff"
// buttons. Persisted to AsyncStorage so the choice survives app restarts and
// is honoured by ActiveRidePanel's launcher (see openMapsNavigation).
export const useNavStore = create<NavState>((set) => ({
    navApp: 'default',
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

    loadNavApp: async () => {
        try {
            const stored = await AsyncStorage.getItem(NAV_APP_KEY);
            if (isNavApp(stored)) {
                set({ navApp: stored, isLoaded: true });
                return;
            }
        } catch {
            // fall through to default
        }
        set({ isLoaded: true });
    },
}));
