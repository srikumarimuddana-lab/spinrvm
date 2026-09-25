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
import React, { useCallback } from 'react';
import { useRouter } from 'expo-router';
import ConfirmSheet from './ConfirmSheet';
import { useTranslation } from '../i18n';
import { useNoDriversStore, prepareRebookDraft } from '../store/noDriversStore';

export function NoDriversSheetHost() {
  const router = useRouter();
  const { t } = useTranslation();
  const prompt = useNoDriversStore((s) => s.prompt);
  const dismiss = useNoDriversStore((s) => s.dismiss);

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
