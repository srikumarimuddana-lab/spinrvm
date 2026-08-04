import React, { useState, useRef, useEffect } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Alert, Animated,
  Linking, Vibration,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getSOSLocation } from '../utils/sosLocation';

interface SOSButtonProps {
  rideId?: string;
  onTrigger: (rideId: string, lat?: number, lng?: number) => Promise<void>;
  size?: 'small' | 'large';
  /**
   * Discreet mode — the driver-side design from sketch 011 ("Discreet Hold
   * Shield"). Defaults false, so every existing (rider) call site is
   * unchanged.
   *
   * The design question that produced it was "can a driver call for help with
   * one hand while driving?", and the loud variant was rejected outright:
   * "a full-screen red alarm is visible to any passenger in the back seat —
   * the exact scenario where a driver most needs help." So in discreet mode
   * the alert is sent without anything a passenger could read over the
   * driver's shoulder: no red flash, no native modal, no alarm buzz.
   *
   * Sketch 010 deliberately chose a *different* design for riders — the
   * rider's threat model doesn't require silence — which is why this is an
   * opt-in prop rather than a change to the shared default.
   */
  discreet?: boolean;
}

/**
 * SOS Emergency Button — long press to activate.
 * 1. Vibrates device
 * 2. Calls backend emergency endpoint (notifies admin + emergency contacts)
 * 3. Only shows success + 911 prompt AFTER backend confirms (fail-safe)
 * 4. On network failure: shows retry dialog AND button stays in FAILED state
 *    until the alert is confirmed by the backend — a dismissed alert does NOT
 *    reset the button to idle so the driver always knows the alert was not sent.
 * 5. While in FAILED state a single tap (no hold) retries immediately.
 *
 * In `discreet` mode every user-visible confirmation above is replaced by a
 * small dark toast instead of a native Alert, and the hold is longer so a
 * bump against the phone can't fire it. What does NOT change: the alert is
 * still only reported as sent after a real backend 200, the failed state is
 * still persistent, and 911 is still one tap away (see DISCREET_TOAST_MS).
 */
const SOS_HOLD_MS = 1200;
// Sketch 011 specifies a 3s hold for the discreet path. Longer than the
// standard 1.2s on purpose: the discreet button gives no loud feedback while
// filling, so it needs a bigger margin against an accidental press from a
// phone jostling in a mount.
const SOS_DISCREET_HOLD_MS = 3000;
// R-P0-1 spec: 3 total attempts with 1 s / 2 s backoff between them
// before declaring failure. The button stays amber until a backend 200.
const SOS_MAX_ATTEMPTS = 3;
const SOS_RETRY_DELAYS_MS = [1000, 2000];
// How long a *successful* discreet toast stays up: long enough to be read and
// tapped (it carries the "call 911" affordance the suppressed native Alert
// normally would), short enough not to linger as something a passenger
// notices. Failure toasts ignore this and persist — see showToast.
const DISCREET_TOAST_MS = 8000;

export function SOSButton({ rideId, onTrigger, size = 'small', discreet = false }: SOSButtonProps) {
  const [triggered, setTriggered] = useState(false);
  const [sending, setSending] = useState(false);
  const [pressing, setPressing] = useState(false);
  // Persistent failure flag: stays true until the backend confirms the alert.
  // Dismissing the failure dialog does NOT clear it — the button stays amber
  // so the driver always has a visible reminder that the alert was NOT sent.
  const [failed, setFailed] = useState(false);
  // Discreet-mode confirmation. `null` = nothing showing. Replaces the native
  // Alert entirely in discreet mode; never used otherwise.
  const [toast, setToast] = useState<{ kind: 'sent' | 'failed'; text: string } | null>(null);
  const pulseAnim = useRef(new Animated.Value(1)).current;
  // Hold progress 0→1, drives the discreet ring fill. Kept separate from
  // pulseAnim because the ring is a width interpolation (no native driver)
  // while the pulse is a transform (native driver) — sharing one value would
  // force the pulse off the native driver for every rider too.
  const holdProgress = useRef(new Animated.Value(0)).current;
  const pressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const holdMs = discreet ? SOS_DISCREET_HOLD_MS : SOS_HOLD_MS;

  // A pending toast timer outliving the screen would call setState on an
  // unmounted component; the hold timer would fire an SOS for a screen the
  // user already left.
  useEffect(() => {
    return () => {
      if (pressTimer.current) clearTimeout(pressTimer.current);
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  const showToast = (kind: 'sent' | 'failed', text: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ kind, text });
    // A 'sent' toast auto-clears — the alert is out, and leaving it up is one
    // more thing for a passenger to notice. A 'failed' toast does NOT: it is
    // the only 911 affordance in discreet mode (the native Alert that carries
    // it is suppressed), and it mirrors the persistent amber button — the
    // component's existing rule is that a failed alert stays visible until
    // the backend actually confirms one. Auto-dismissing it would leave a
    // driver whose SOS failed with no way to reach 911 from this control.
    if (kind === 'sent') {
      toastTimer.current = setTimeout(() => setToast(null), DISCREET_TOAST_MS);
    }
  };

  const startPress = () => {
    setPressing(true);
    if (discreet) {
      // Ring fill, no pulse — a pulsing button draws the eye, which is the
      // one thing this mode exists to avoid.
      holdProgress.setValue(0);
      Animated.timing(holdProgress, {
        toValue: 1,
        duration: holdMs,
        useNativeDriver: false,
      }).start();
    } else {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 1.15, duration: 400, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 400, useNativeDriver: true }),
        ])
      ).start();
    }
    pressTimer.current = setTimeout(() => {
      triggerSOS();
    }, holdMs);
  };

  const endPress = () => {
    setPressing(false);
    pulseAnim.stopAnimation();
    pulseAnim.setValue(1);
    holdProgress.stopAnimation();
    holdProgress.setValue(0);
    if (pressTimer.current) {
      clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
  };

  const showSuccessAlert = () => {
    Alert.alert(
      '🚨 Emergency Alert Sent',
      'Your location has been shared with Spinr support and your emergency contacts.\n\nDo you want to call 911?',
      [
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: "I'm OK",
          style: 'cancel',
          onPress: () => setTriggered(false),
        },
      ]
    );
  };

  const showFailureAlert = (retry: () => void) => {
    Alert.alert(
      '⚠️ Alert Not Sent',
      'Could not reach Spinr. The button will stay red — tap it to retry.\n\nYou can call 911 directly right now.',
      [
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: 'Retry Now',
          onPress: retry,
        },
        // "Dismiss" intentionally does NOT reset the button — failed state
        // persists so the driver always knows the alert was not sent.
        { text: 'Dismiss', style: 'cancel' },
      ]
    );
  };

  const triggerSOS = async () => {
    // Clear any prior failure so the button transitions to "Sending…" visually.
    setFailed(false);
    setPressing(false);
    holdProgress.setValue(0);
    setSending(true);
    // The standard six-pulse pattern is an audible buzz — in a quiet car it
    // announces the press to whoever is sitting behind the driver. Discreet
    // mode gets one short tick instead: enough to confirm by feel, short
    // enough not to carry.
    Vibration.vibrate(discreet ? 40 : [0, 200, 100, 200, 100, 200]);

    // Hard-bounded lookup: fresh fix raced against a short deadline, then
    // last-known, then no coords — a GPS dead zone must never delay the alert.
    const { lat, lng } = await getSOSLocation();
    if (lat === undefined) {
      console.warn('[SOS] No location fix within deadline — sending alert without coordinates');
    }

    // Attempt backend call with two retries (3 total attempts) using
    // 1 s / 2 s backoff before declaring failure. Never treat missing
    // rideId as success — that would show "Alert Sent" without any
    // backend notification going out.
    let backendOk = false;
    if (rideId) {
      for (let attempt = 0; attempt < SOS_MAX_ATTEMPTS && !backendOk; attempt++) {
        if (attempt > 0) {
          const delay = SOS_RETRY_DELAYS_MS[attempt - 1] ?? SOS_RETRY_DELAYS_MS[SOS_RETRY_DELAYS_MS.length - 1];
          await new Promise<void>((r) => setTimeout(r, delay));
        }
        try {
          await onTrigger(rideId, lat, lng);
          backendOk = true;
        } catch {}
      }
    }

    setSending(false);

    if (!rideId) {
      // No active ride context — direct user to call 911 immediately.
      // Deliberately still a native Alert even in discreet mode: nothing was
      // sent and nothing will be, so the only useful action is calling 911
      // directly, and that is worth interrupting for. (Whether a rideless SOS
      // path should exist at all is ACTION_ITEMS.md B15(c) — undecided.)
      Alert.alert(
        'No Active Ride',
        'Emergency alert requires an active ride. Call 911 directly for immediate help.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Call 911', style: 'destructive', onPress: () => Linking.openURL('tel:911') },
        ],
      );
    } else if (backendOk) {
      setTriggered(true);
      if (discreet) {
        showToast('sent', 'Silent alert sent · tap to call 911');
      } else {
        showSuccessAlert();
      }
    } else {
      // Mark failed BEFORE showing the alert so the button is already amber
      // when the dialog appears. Dismissing the dialog leaves failed=true.
      setFailed(true);
      if (discreet) {
        // Still no modal — but the driver must learn the alert did not send,
        // so this toast does not auto-clear into silence: the button itself
        // stays amber underneath it, and tapping the toast dials 911.
        showToast('failed', 'Not sent · tap to call 911');
      } else {
        showFailureAlert(triggerSOS);
      }
    }
  };

  const isLarge = size === 'large';

  const iconName = triggered
    ? 'checkmark-circle'
    : sending
    ? 'hourglass'
    : failed
    ? 'alert-circle'
    : 'shield';

  const labelText = pressing
    ? 'Hold...'
    : sending
    ? 'Sending…'
    : triggered
    ? 'Alert Sent'
    : failed
    ? 'FAILED'
    : 'SOS';

  return (
    <Animated.View style={[{ transform: [{ scale: pressing && !discreet ? pulseAnim : 1 }] }]}>
      <TouchableOpacity
        style={[
          styles.btn,
          isLarge ? styles.btnLarge : styles.btnSmall,
          discreet && styles.btnDiscreet,
          triggered && (discreet ? styles.btnDiscreetTriggered : styles.btnTriggered),
          sending && (discreet ? styles.btnDiscreetSending : styles.btnSending),
          pressing && !discreet && styles.btnPressing,
          failed && styles.btnFailed,
        ]}
        // In failed state a single tap retries immediately — no hold required.
        onPressIn={sending || triggered ? undefined : (failed ? triggerSOS : startPress)}
        onPressOut={sending || triggered || failed ? undefined : endPress}
        activeOpacity={0.9}
        disabled={sending}
        accessibilityLabel={
          triggered
            ? 'Emergency alert sent'
            : sending
            ? 'Sending emergency alert'
            : failed
            ? 'Emergency alert failed — tap to retry'
            : 'Emergency SOS'
        }
        accessibilityRole="button"
        accessibilityHint={
          failed
            ? "Alert failed — double tap to retry"
            : triggered
            ? "Alert sent"
            : discreet
            ? `Hold ${SOS_DISCREET_HOLD_MS / 1000} seconds to send a silent SOS alert`
            : "Double tap to send SOS alert"
        }
        accessibilityState={{
          selected: triggered,
          busy: sending,
          disabled: sending,
        }}
      >
        <Ionicons name={iconName} size={isLarge ? 28 : 20} color={discreet ? '#E5E7EB' : '#FFF'} />
        {isLarge && <Text style={[styles.btnText, discreet && styles.btnTextDiscreet]}>{labelText}</Text>}
        {/* Hold progress. Discreet mode only — a thin bar under the icon
            rather than the standard pulse, so the driver can see the hold
            registering without the button drawing attention to itself. */}
        {discreet && pressing && (
          <View style={styles.ringTrack}>
            <Animated.View
              style={[
                styles.ringFill,
                {
                  width: holdProgress.interpolate({
                    inputRange: [0, 1],
                    outputRange: ['0%', '100%'],
                  }),
                },
              ]}
            />
          </View>
        )}
      </TouchableOpacity>
      {/* The standard hold hint is deliberately suppressed in discreet mode:
          "Hold for 3 seconds" on screen tells a passenger exactly what the
          driver is doing. */}
      {pressing && !discreet && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>Hold for {SOS_HOLD_MS / 1000} seconds</Text>
        </View>
      )}
      {failed && !sending && !discreet && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>Tap to retry</Text>
        </View>
      )}
      {/* Discreet confirmation. Small, dark, low-contrast — legible to the
          driver glancing down, unremarkable from the back seat. Tapping it
          dials 911: in discreet mode this is the ONLY 911 affordance, since
          the native Alert that normally carries it is suppressed. */}
      {discreet && toast && (
        <TouchableOpacity
          style={[styles.discreetToast, toast.kind === 'failed' && styles.discreetToastFailed]}
          onPress={() => Linking.openURL('tel:911')}
          accessibilityRole="button"
          accessibilityLabel={
            toast.kind === 'sent'
              ? 'Silent emergency alert sent. Double tap to call 911.'
              : 'Emergency alert was not sent. Double tap to call 911.'
          }
        >
          <Text style={styles.discreetToastText} numberOfLines={1}>{toast.text}</Text>
        </TouchableOpacity>
      )}
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  btn: {
    backgroundColor: '#DC2626',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 6,
    shadowColor: '#DC2626',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
  },
  btnSmall: {
    width: 44, height: 44, borderRadius: 22,
  },
  btnLarge: {
    width: 80, height: 80, borderRadius: 40,
    flexDirection: 'column', gap: 2,
  },
  btnPressing: {
    backgroundColor: '#B91C1C',
  },
  btnSending: {
    backgroundColor: '#D97706',
  },
  btnTriggered: {
    backgroundColor: '#10B981',
  },
  // Distinct amber style for failed state — clearly different from normal red.
  // Border draws attention even on dark backgrounds.
  btnFailed: {
    backgroundColor: '#92400E',
    borderWidth: 2,
    borderColor: '#FCD34D',
  },
  // ── Discreet mode (sketch 011) ────────────────────────────────────────
  // Muted slate, no coloured glow. Reads as a normal utility control next to
  // Call/Nav in the driver's action row rather than as an alarm. Note
  // btnFailed is intentionally NOT overridden — a failed alert is the one
  // state worth being conspicuous about, and by then the alert either sent
  // or it didn't, so discretion has already served its purpose.
  btnDiscreet: {
    backgroundColor: '#374151',
    shadowColor: '#000',
    shadowOpacity: 0.25,
    elevation: 3,
  },
  btnDiscreetSending: {
    backgroundColor: '#4B5563',
  },
  btnDiscreetTriggered: {
    backgroundColor: '#4B5563',
  },
  btnText: {
    color: '#FFF', fontSize: 12, fontWeight: '800', letterSpacing: 0.5,
  },
  btnTextDiscreet: {
    color: '#E5E7EB', fontWeight: '600',
  },
  holdHint: {
    position: 'absolute', bottom: -20, alignSelf: 'center',
    backgroundColor: 'rgba(0,0,0,0.7)', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  holdHintText: {
    color: '#FFF', fontSize: 10, fontWeight: '600',
  },
  ringTrack: {
    position: 'absolute', bottom: 6, left: 8, right: 8, height: 2,
    backgroundColor: 'rgba(255,255,255,0.18)', borderRadius: 1, overflow: 'hidden',
  },
  ringFill: {
    height: 2, backgroundColor: '#E5E7EB', borderRadius: 1,
  },
  discreetToast: {
    position: 'absolute', bottom: -30, alignSelf: 'center',
    backgroundColor: 'rgba(17,24,39,0.94)',
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6,
    borderWidth: StyleSheet.hairlineWidth, borderColor: 'rgba(255,255,255,0.12)',
    maxWidth: 220,
  },
  discreetToastFailed: {
    borderColor: '#FCD34D',
  },
  discreetToastText: {
    color: '#E5E7EB', fontSize: 10, fontWeight: '600',
  },
});

export default SOSButton;
