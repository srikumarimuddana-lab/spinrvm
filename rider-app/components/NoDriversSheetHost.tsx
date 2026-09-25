/**
 * "No drivers available right now" sheet, mounted once at the app root.
 *
 * Shown when the backend auto-cancels the ride on screen because no driver
 * accepted (store/noDriversStore.ts has the rules for when). The rider has
 * already been sent home underneath, so dismissing it leaves them where the
 * old flow did.
 *
 * Try again and Schedule for later both reopen ride-options with the same
 * trip and an empty quote, so the rider sees a fresh price before booking.
 * Neither books anything on its own.
 */
import React, { useCallback, useEffect } from 'react';
import { AccessibilityInfo } from 'react-native';
import { useRouter } from 'expo-router';
import ConfirmSheet from './ConfirmSheet';
import { useTranslation } from '../i18n';
import { useNoDriversStore, prepareRebookDraft } from '../store/noDriversStore';

export function NoDriversSheetHost() {
  const router = useRouter();
  const { t } = useTranslation();
  const prompt = useNoDriversStore((s) => s.prompt);
  const dismiss = useNoDriversStore((s) => s.dismiss);

  // The sheet opens on its own (WS, push, poll or resume), never from a tap,
  // so a screen reader gets no cue from focus. Announce it once per ride,
  // the same way Toast announces its unprompted messages.
  const promptRideId = prompt?.rideId;
  useEffect(() => {
    if (!promptRideId) return;
    AccessibilityInfo.announceForAccessibility(`${t('ride.no_drivers_title')}. ${t('ride.no_drivers_msg')}`);
    // t is stable for a render tree; the ride id is the trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptRideId]);

  const reopenBooking = useCallback((schedule: boolean) => {
    if (!prompt) return;
    prepareRebookDraft(prompt);
    if (schedule) useNoDriversStore.getState().requestScheduleOnArrival();
    router.push('/ride-options' as any);
  }, [prompt, router]);

  return (
    <ConfirmSheet
      visible={!!prompt}
      title={t('ride.no_drivers_title')}
      message={t('ride.no_drivers_msg')}
      variant="info"
      buttons={[
        { text: t('ride.no_drivers_try_again'), onPress: () => reopenBooking(false) },
        { text: t('ride.no_drivers_schedule'), onPress: () => reopenBooking(true) },
        { text: t('ride.no_drivers_dismiss'), style: 'cancel' },
      ]}
      onClose={dismiss}
    />
  );
}

export default NoDriversSheetHost;
