import { useCallback } from 'react';
import { BackHandler, Platform } from 'react-native';
// Sourced from expo-router (which re-exports React Navigation's useFocusEffect)
// rather than @react-navigation/native directly: this hook lives in @shared,
// and the apps' tsconfig `paths` map resolves expo-router for shared code but
// not @react-navigation/native.
import { useFocusEffect } from 'expo-router';

/**
 * Android hardware back-button guard for a root/home screen.
 *
 * After login the auth screens (`/login` → `/otp`) are still below the home
 * screen in the navigator history: `index` replaces to `/login`, `/login`
 * pushes `/otp`, and `/otp` replaces itself with the home route — leaving
 * `[login, home]` on the stack. Without this guard, pressing the Android back
 * button on the home screen pops that history and dumps the user back on the
 * phone-number/login screen even though they are signed in.
 *
 * A home screen is a navigation root, so the correct Android behaviour is to
 * background the app (`exitApp`) rather than navigate backwards into the auth
 * flow. iOS has no hardware back button, so this is a no-op there.
 *
 * @param enabled Pass `false` to temporarily disable the guard (e.g. while a
 *   modal/bottom-sheet is open and should consume back itself).
 */
export function useExitOnBackPress(enabled: boolean = true): void {
  useFocusEffect(
    useCallback(() => {
      if (Platform.OS !== 'android' || !enabled) return undefined;

      const onBackPress = () => {
        // Returning true prevents React Navigation's default pop, which would
        // otherwise surface the login screen sitting beneath home.
        BackHandler.exitApp();
        return true;
      };

      const subscription = BackHandler.addEventListener(
        'hardwareBackPress',
        onBackPress,
      );
      return () => subscription.remove();
    }, [enabled]),
  );
}
