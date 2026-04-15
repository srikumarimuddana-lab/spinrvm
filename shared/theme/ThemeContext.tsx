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
import { useColorScheme } from 'react-native';
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
  const systemScheme = useColorScheme(); // 'light' | 'dark' | null | undefined
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

  const isDark = useMemo(() => {
    if (pref === 'dark') return true;
    if (pref === 'light') return false;
    return systemScheme === 'dark';
  }, [pref, systemScheme]);

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
