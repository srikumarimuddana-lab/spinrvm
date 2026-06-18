import React from 'react';
import { Stack } from 'expo-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '@shared/api/queryClient';

/**
 * Driver stack — wraps the tab group so detail screens (settings, payout,
 * referral, notifications, chat, addresses, etc.) push onto a real stack
 * instead of living as hidden siblings inside the Tabs navigator.
 *
 * Before this file was rewritten, every sub-screen was registered with
 * `<Tabs.Screen href={null}>` in the Tabs layout — which caused back
 * presses from "Profile → Settings → Payout" to jump to the first tab
 * (Drive) instead of popping the previous detail screen. Tabs navigators
 * don't keep a push stack between hidden siblings.
 *
 * With this Stack wrapper:
 *   - `(tabs)` hosts the 5 real tabs and is the initial screen.
 *   - Every detail screen is a Stack.Screen, so `router.push('/driver/X')`
 *     truly pushes, and the back button pops back to the previous screen
 *     (the tab you came from, preserved in place).
 *
 * URLs are unchanged because `(tabs)` is a route group — it does not appear
 * in the URL path. `/driver/settings` still points at this file's sibling.
 */
/**
 * Deep-link anchor: when the app is opened via a deep link to a detail screen
 * (e.g. Stripe Connect returns to spinr-driver://driver/payout), expo-router
 * otherwise rebuilds the stack with only the matched route, leaving the back
 * button with nothing to pop — the payout screen looked "stuck". Anchoring on
 * `(tabs)` makes the tab home the base of the stack for any deep-linked detail
 * screen, so back always pops to the tabs.
 */
export const unstable_settings = {
  initialRouteName: '(tabs)',
};

export default function DriverStackLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <Stack
        screenOptions={{
          headerShown: false,
          animation: 'slide_from_right',
        }}
      >
        <Stack.Screen name="(tabs)" options={{ animation: 'fade' }} />
        <Stack.Screen name="settings" />
        <Stack.Screen name="notifications" />
        <Stack.Screen name="payout" />
        <Stack.Screen name="payout-history" />
        <Stack.Screen name="stripe-onboarding" />
        <Stack.Screen name="tax-documents" />
        <Stack.Screen name="referral" />
        <Stack.Screen name="addresses" />
        <Stack.Screen name="subscription" />
        <Stack.Screen name="quests" />
        <Stack.Screen name="emergency-contacts" />
        <Stack.Screen name="faq" />
        <Stack.Screen name="chat" />
        <Stack.Screen name="ride-detail" />
      </Stack>
    </QueryClientProvider>
  );
}
