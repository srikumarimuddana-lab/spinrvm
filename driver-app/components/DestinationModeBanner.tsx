import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { View, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';
import { Text } from '@shared/components/Text';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { FONT, SPACING } from '@shared/utils/responsive';
import { useLanguageStore } from '../store/languageStore';
import { showToast } from '../hooks/useToast';
import {
  type DestinationModeResponse,
  destinationExpiryMs,
  isDestinationActive,
  remainingParts,
} from '../utils/destinationModeState';

/**
 * DestinationModeBanner — persistent "Heading home" strip on the main driver
 * screen (C136 T2). A driver left destination mode on all day and got zero
 * offers because nothing on the map said the filter was on.
 *
 * - Fetches GET /drivers/destination on mount and on every screen focus
 *   (so returning from app/driver/destination-mode.tsx refreshes it).
 * - Visibility rules live in utils/destinationModeState.ts (isDestinationActive),
 *   including the old-backend fallback (no `active` / no expiry).
 * - Countdown re-renders every 30s and the banner hides itself once the expiry
 *   passes on the device clock.
 * - "Turn off" is one tap (no confirm — the goal is to make turning the
 *   filter off frictionless): DELETE /drivers/destination, hide on success,
 *   non-blocking error toast + keep banner on failure.
 * - Fetch failures (offline, 5xx) are silent: keep the last known state rather
 *   than toasting on every refocus. This is a read-only status hint; the
 *   authoritative filter is server-side, and the destination-mode screen
 *   itself surfaces load errors.
 */

export const DESTINATION_BANNER_TICK_MS = 30_000;

export function DestinationModeBanner() {
  const { colors } = useTheme();
  const { t } = useLanguageStore();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [data, setData] = useState<DestinationModeResponse | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [turningOff, setTurningOff] = useState(false);
  const mountedRef = useRef(true);
  // Monotonic request id: a slow GET that resolves after a newer GET or a
  // successful DELETE must not resurrect stale state.
  const reqSeqRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    try {
      const res = await api.get<DestinationModeResponse>('/drivers/destination');
      if (!mountedRef.current || seq !== reqSeqRef.current) return;
      if (res?.data) {
        setData(res.data);
        setNow(Date.now());
      }
    } catch {
      // Deliberately silent — see header comment. Keep last known state.
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      refresh();
    }, [refresh]),
  );

  const active = isDestinationActive(data, now);
  const expiryMs = destinationExpiryMs(data);

  // Countdown ticker — only runs while the banner is showing.
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), DESTINATION_BANNER_TICK_MS);
    return () => clearInterval(id);
  }, [active]);

  const handleTurnOff = useCallback(async () => {
    if (turningOff) return;
    setTurningOff(true);
    // Invalidate any in-flight GET so it can't re-show the banner.
    reqSeqRef.current += 1;
    try {
      await api.delete('/drivers/destination');
      if (!mountedRef.current) return;
      setData((prev) => ({
        ...(prev ?? { destination_address: null }),
        destination_mode: false,
        active: false,
        destination_expires_at: null,
      }));
    } catch {
      if (!mountedRef.current) return;
      showToast('error', t('destinationMode.bannerTurnOffFailedTitle'), t('destinationMode.bannerTurnOffFailedMsg'));
    } finally {
      if (mountedRef.current) setTurningOff(false);
    }
  }, [turningOff, t]);

  if (!active || !data) return null;

  const area = data.destination_address?.trim() || t('destinationMode.bannerFallbackArea');
  let countdown: string | null = null;
  if (expiryMs !== null) {
    const { h, m } = remainingParts(expiryMs, now);
    const time = h > 0
      ? t('destinationMode.durationHoursMinutes').replace('{{h}}', String(h)).replace('{{m}}', String(m))
      : t('destinationMode.durationMinutes').replace('{{m}}', String(m));
    countdown = t('destinationMode.bannerEndsIn').replace('{{time}}', time);
  }

  return (
    <View
      style={styles.container}
      testID="destination-mode-banner"
      accessible={false}
    >
      <View style={styles.iconWrap}>
        <Ionicons name="home" size={16} color={colors.surface} />
      </View>
      <View
        style={styles.textCol}
        accessible
        accessibilityLabel={
          t('destinationMode.bannerA11y').replace('{{area}}', area) + (countdown ? ` ${countdown}` : '')
        }
      >
        <Text style={styles.title} numberOfLines={1}>
          {t('destinationMode.bannerTitle')}
          {countdown ? <Text style={styles.countdown}>{` · ${countdown}`}</Text> : null}
        </Text>
        <Text style={styles.subtitle} numberOfLines={1} ellipsizeMode="tail">
          {t('destinationMode.bannerOnlyToward').replace('{{area}}', area)}
        </Text>
      </View>
      <TouchableOpacity
        style={styles.button}
        onPress={handleTurnOff}
        disabled={turningOff}
        accessibilityRole="button"
        accessibilityLabel={t('destinationMode.bannerTurnOffA11y')}
        accessibilityState={{ disabled: turningOff, busy: turningOff }}
        hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
        testID="destination-mode-banner-turn-off"
      >
        {turningOff ? (
          <ActivityIndicator size="small" color={colors.primary} />
        ) : (
          <Text style={styles.buttonText}>{t('destinationMode.bannerTurnOff')}</Text>
        )}
      </TouchableOpacity>
    </View>
  );
}

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    container: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      borderRadius: 14,
      borderLeftWidth: 4,
      borderLeftColor: colors.warning,
      paddingLeft: SPACING.sm,
      paddingRight: SPACING.xs,
      paddingVertical: SPACING.xs,
      minHeight: 56,
      // shadowColor omitted: RN's default is black, and there is no theme
      // shadow token (lint forbids hardcoded hex).
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      shadowRadius: 6,
      elevation: 6,
    },
    iconWrap: {
      width: 28,
      height: 28,
      borderRadius: 14,
      backgroundColor: colors.warning,
      alignItems: 'center',
      justifyContent: 'center',
      marginRight: SPACING.sm,
    },
    textCol: {
      flex: 1,
      minWidth: 0,
    },
    title: {
      color: colors.text,
      fontSize: FONT.bodySm,
      fontWeight: '700',
    },
    countdown: {
      color: colors.textDim,
      fontWeight: '600',
    },
    subtitle: {
      color: colors.textDim,
      fontSize: FONT.bodySm,
    },
    button: {
      minHeight: 44,
      minWidth: 72,
      paddingHorizontal: SPACING.sm,
      marginLeft: SPACING.xs,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: colors.primary,
      alignItems: 'center',
      justifyContent: 'center',
    },
    buttonText: {
      color: colors.primary,
      fontSize: FONT.bodySm,
      fontWeight: '700',
    },
  });

export default DestinationModeBanner;
