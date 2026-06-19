/**
 * Spinr Theme System
 *
 * Provides system-aware dark/light mode with optional user override.
 * Persisted via AsyncStorage — no API calls, no network dependency.
 *
 * Usage:
 *   // In _layout.tsx:
 *   <ThemeProvider><App /></ThemeProvider>
 *
 *   // In any screen / component:
 *   const { colors, isDark, setTheme } = useTheme();
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useMemo,
  type ReactNode,
} from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { lightColors, darkColors, type ThemeColors, type ColorScheme } from './index';

const THEME_STORAGE_KEY = '@spinr:theme_pref';

// ── Context shape ────────────────────────────────────────────────────────────
type ThemeContextValue = {
  /** Active color palette — always resolved (never 'system'). */
  colors: ThemeColors;
  /** True when the resolved palette is dark. */
  isDark: boolean;
  /**
   * The stored user preference.
   * 'system' means follow the OS; 'light' / 'dark' override it.
   */
  colorScheme: ColorScheme;
  /**
   * Persist a new theme preference.
   * Pure local write — zero API calls.
   */
  setTheme: (scheme: ColorScheme) => void;
};

const ThemeContext = createContext<ThemeContextValue>({
  colors: lightColors,
  isDark: false,
  colorScheme: 'system',
  setTheme: () => {},
});

// ── Provider ─────────────────────────────────────────────────────────────────
type ThemeProviderProps = { children: ReactNode };

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [pref, setPref] = useState<ColorScheme>('system');
  const [hydrated, setHydrated] = useState(false);

  // Restore persisted preference on mount.
  useEffect(() => {
    AsyncStorage.getItem(THEME_STORAGE_KEY)
      .then((stored) => {
        if (stored === 'light' || stored === 'dark' || stored === 'system') {
          setPref(stored);
        }
      })
      .catch(() => {
        // AsyncStorage failure is non-fatal — fall back to 'system'.
      })
      .finally(() => setHydrated(true));
  }, []);

  const setTheme = useCallback((scheme: ColorScheme) => {
    setPref(scheme);
    AsyncStorage.setItem(THEME_STORAGE_KEY, scheme).catch(() => {});
  }, []);

  // Dark mode is OPT-IN: the app stays light unless the user explicitly turns
  // on Settings → Dark Mode (which persists pref='dark'). We intentionally do
  // NOT follow the OS dark setting ('system' → light) because dark-mode theming
  // is not yet complete across every screen/input — auto-following the device
  // would throw users into half-themed screens (white-on-white inputs). Flip
  // the 'system' branch back to `systemScheme === 'dark'` once the dark audit
  // is done to restore OS-follow behaviour.
  const isDark = useMemo(() => pref === 'dark', [pref]);

  const colors = isDark ? darkColors : lightColors;

  const value = useMemo<ThemeContextValue>(
    () => ({ colors, isDark, colorScheme: pref, setTheme }),
    [colors, isDark, pref, setTheme]
  );

  // Render children regardless of hydration state so there is no flash on
  // first paint — the system default is correct until AsyncStorage resolves.
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

// ── Hook ─────────────────────────────────────────────────────────────────────
export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
