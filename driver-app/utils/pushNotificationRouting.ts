import type { useRouter } from 'expo-router';

/**
 * DV-9: route a tapped push notification to the correct in-app screen.
 * new_ride_assignment → driver home (offer panel hydrates from AsyncStorage);
 * everything else → notifications inbox as the safe fallback so the driver
 * can read the notification content rather than landing on a random screen.
 *
 * Shared by all of app/_layout.tsx's tap-detection listeners — both
 * expo-notifications' (only fires for the one notification it schedules
 * itself, e.g. DriverIdlePanel.tsx's welcome nudge) and Firebase's own
 * onNotificationOpenedApp/getInitialNotification (fires for real FCM pushes,
 * which RNFirebase — not expo-notifications — actually receives; see C97
 * rec #6, ACTION_ITEMS.md, for why). Extracted to its own dependency-free
 * module so it's unit-testable without importing _layout.tsx's heavy
 * module-level side effects (fonts, splash screen, Sentry, LogRocket, …).
 */
export function routePushNotificationTap(
  router: Pick<ReturnType<typeof useRouter>, 'push'>,
  data: Record<string, any>,
) {
  if (data?.type === 'new_ride_assignment') {
    router.push('/driver/' as any);
  } else if (data?.type === 'chat_message' && data?.ride_id) {
    router.push(`/driver/chat?rideId=${data.ride_id}` as any);
  } else if ((data?.type === 'lost_and_found' || data?.type === 'lost_and_found_message') && data?.case_id) {
    router.push({ pathname: '/driver/lost-and-found-chat', params: { caseId: data.case_id } } as any);
  } else if (data?.type === 'license_backfill_prompt') {
    // ACTION_ITEMS.md B14 self-serve nudge — send the driver straight
    // to the Profile tab, where the missing-licence banner (see
    // app/driver/(tabs)/profile.tsx) opens the entry modal.
    router.push('/driver/profile' as any);
  } else {
    router.push('/driver/notifications' as any);
  }
}
