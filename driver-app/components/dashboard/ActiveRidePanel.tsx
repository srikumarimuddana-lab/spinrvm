import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
  Linking,
  Platform,
  BackHandler,
  ScrollView,
  Dimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../store/languageStore';
import { showAlert } from '../AlertDialog';

interface Rider {
  first_name?: string;
  last_name?: string;
  name?: string;
  rating?: number;
  phone?: string;
}

interface Ride {
  id: string;
  pickup_address: string;
  dropoff_address: string;
  pickup_lat: number;
  pickup_lng: number;
  dropoff_lat: number;
  dropoff_lng: number;
  total_fare?: number;
  driver_earnings?: number;
  distance_km?: number;
  duration_minutes?: number;
  pickup_otp?: string;
  status?: string;
}

interface DriverLocation {
  coords: { latitude: number; longitude: number };
}

interface ActiveRidePanelProps {
  rideState: 'navigating_to_pickup' | 'arrived_at_pickup' | 'trip_in_progress';
  ride: Ride | null;
  rider: Rider | null;
  driverLocation?: DriverLocation | null;
  isLoading: boolean;
  otpInput: string;
  setOtpInput: (value: string) => void;
  onVerifyOTP: (otp: string) => Promise<boolean>;
  onNavigate: (lat: number, lng: number, label: string) => void;
  onArriveAtPickup: () => void;
  onStartRide: () => void;
  onCompleteRide: () => void;
  onCancelRide: () => void;
  routeEtaMinutes?: number | null;
  routeDistanceKm?: number | null;
  slideUpAnim: Animated.Value;
  fadeAnim: Animated.Value;
  distanceToPickup?: number | null;
}

// Haversine distance between two points in meters
function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export const ActiveRidePanel: React.FC<ActiveRidePanelProps> = ({
  rideState,
  ride,
  rider,
  driverLocation,
  isLoading,
  otpInput,
  setOtpInput,
  onVerifyOTP,
  onNavigate,
  onArriveAtPickup,
  onStartRide,
  onCompleteRide,
  onCancelRide,
  routeEtaMinutes,
  routeDistanceKm,
  slideUpAnim,
  fadeAnim,
  distanceToPickup,
}) => {
  // All hooks MUST be before any early return to avoid React ordering issues
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { t } = useLanguageStore();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const waitTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live distance tracking during trip
  const [liveDistanceKm, setLiveDistanceKm] = useState(0);
  const [hasLiveData, setHasLiveData] = useState(false);
  const lastLocRef = useRef<{ lat: number; lng: number } | null>(null);
  const jitterBufferRef = useRef(0);

  // PIN entry feedback state — spinner while verifying, shake + error on a
  // wrong code. Reset whenever the ride phase or ride id changes.
  const shakeAnim = useRef(new Animated.Value(0)).current;
  const [verifying, setVerifying] = useState(false);
  const [pinError, setPinError] = useState(false);

  // Reset distance when ride phase OR ride id changes — prevents stale
  // accumulation carrying over to a different ride if the panel is recycled.
  useEffect(() => {
    setLiveDistanceKm(0);
    setHasLiveData(false);
    lastLocRef.current = null;
    jitterBufferRef.current = 0;
    setVerifying(false);
    setPinError(false);
  }, [rideState, ride?.id]);

  // Accumulate distance from GPS updates during trip_in_progress.
  // Always advances the reference point so slow/crawl driving (deltas <10m)
  // isn't permanently filtered out. We accumulate deltas into a jitter
  // buffer and flush to the displayed total once cumulative movement
  // crosses 10m — captures real motion while rejecting stationary GPS noise.
  useEffect(() => {
    if (rideState !== 'trip_in_progress' || !driverLocation?.coords) return;
    const { latitude, longitude } = driverLocation.coords;
    const prev = lastLocRef.current;
    if (prev) {
      const delta = haversineM(prev.lat, prev.lng, latitude, longitude);
      jitterBufferRef.current += delta;
      if (jitterBufferRef.current > 10) {
        setLiveDistanceKm(d => d + jitterBufferRef.current / 1000);
        setHasLiveData(true);
        jitterBufferRef.current = 0;
      }
    }
    // Always advance the ref so consecutive small movements are measured
    // between the latest two points, not against a stale origin.
    lastLocRef.current = { lat: latitude, lng: longitude };
  }, [driverLocation, rideState]);

  useEffect(() => {
    if (rideState === 'arrived_at_pickup') {
      setWaitSeconds(0);
      const id = setInterval(() => setWaitSeconds(s => s + 1), 1000);
      waitTimerRef.current = id;
      return () => clearInterval(id);
    }
    if (waitTimerRef.current) {
      clearInterval(waitTimerRef.current);
      waitTimerRef.current = null;
    }
  }, [rideState]);

  // Hardware back during an active ride must never pop the screen away (that
  // would strand the driver on a blank map mid-trip). Instead of a dead no-op,
  // in the pre-trip phases it opens the same Cancel-Ride confirm the on-screen
  // link uses, so back feels responsive and gives a real exit path. During
  // trip_in_progress it's consumed silently — a started trip can't be cancelled.
  useEffect(() => {
    const onBack = () => {
      if (rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') {
        showAlert(
          t('activeRide.cancelRide'),
          t('activeRide.cancelRideWarning'),
          [
            { text: t('activeRide.keepRide'), style: 'cancel' },
            { text: t('activeRide.yesCancel'), style: 'destructive', onPress: onCancelRide },
          ],
        );
      }
      return true;
    };
    const sub = BackHandler.addEventListener('hardwareBackPress', onBack);
    return () => sub.remove();
  }, [rideState, onCancelRide, t]);

  if (!ride) return null;

  // ── PIN verification ────────────────────────────────────────
  const triggerShake = () => {
    Animated.sequence([
      Animated.timing(shakeAnim, { toValue: 10, duration: 50, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: -10, duration: 50, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: 6, duration: 50, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: -6, duration: 50, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: 0, duration: 50, useNativeDriver: true }),
    ]).start();
  };

  // Auto-submit when the 4th digit lands. On a wrong code: shake, surface an
  // inline error and clear the boxes so the driver can retry. On success the
  // store advances to trip_in_progress and this whole OTP card unmounts.
  const submitOtp = async (otp: string) => {
    setVerifying(true);
    setPinError(false);
    try {
      const ok = await onVerifyOTP(otp);
      if (!ok) {
        triggerShake();
        setPinError(true);
        setOtpInput('');
      }
    } finally {
      setVerifying(false);
    }
  };

  // ── Helpers ─────────────────────────────────────────────────
  const riderName = rider?.first_name
    ? `${rider.first_name}${rider.last_name ? ' ' + rider.last_name[0] + '.' : ''}`
    : rider?.name || t('activeRide.rider');

  const earnings = ride.driver_earnings ?? ride.total_fare ?? 0;
  const distKm = ride.distance_km ?? 0;
  const durMin = ride.duration_minutes ?? 0;

  const formatWait = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec.toString().padStart(2, '0')}s` : `${sec}s`;
  };

  const openMapsNavigation = (lat: number, lng: number, _label: string) => {
    // Use Google Maps web URL as primary — works on all devices regardless
    // of whether the native Google Maps app is installed. On devices WITH
    // the app installed, the web URL auto-redirects to the app. On devices
    // without it (or in Expo Go), it opens in the browser which still
    // provides turn-by-turn. The old `google.navigation:` scheme crashes
    // with "No Activity found to handle Intent" when the app isn't present.
    const googleUrl = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
    const appleUrl = `http://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`;
    const url = Platform.OS === 'ios' ? appleUrl : googleUrl;
    Linking.openURL(url).catch(() => Linking.openURL(googleUrl));
  };

  const showConfirm = (
    title: string, message: string,
    buttons: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>,
  ) => {
    showAlert(title, message, buttons);
  };

  // ── Status config ───────────────────────────────────────────
  // Build the status label with live ETA when available.
  // During navigating_to_pickup: "~X min to pickup (Y km)"
  // During trip_in_progress: "~X min to dropoff (Y km)"
  // During arrived_at_pickup: "Waiting · Xm XXs" (no ETA, driver is stationary)
  const etaSuffix =
    routeEtaMinutes != null && routeDistanceKm != null
      ? ` · ~${routeEtaMinutes} min (${routeDistanceKm} km)`
      : '';

  const statusMap = {
    navigating_to_pickup: {
      icon: 'navigate-circle' as const,
      label: `${t('activeRide.enRouteToPickup')}${etaSuffix}`,
      color: colors.primary,
    },
    arrived_at_pickup: {
      icon: 'time' as const,
      label: `${t('activeRide.waiting')} · ${formatWait(waitSeconds)}`,
      color: colors.warning,
    },
    trip_in_progress: {
      icon: 'car-sport' as const,
      label: `${t('activeRide.tripInProgress')}${etaSuffix}`,
      color: colors.success,
    },
  };
  const status = statusMap[rideState];

  // Give the sheet more room while the PIN keypad is showing so the full
  // keypad (incl. the 0 / delete row) is reachable instead of falling below
  // the fold. Other phases stay compact so the map stays visible.
  const maxPanelHeight =
    Dimensions.get('window').height * (rideState === 'arrived_at_pickup' ? 0.9 : 0.65);

  return (
    <Animated.View
      style={[
        styles.container,
        {
          transform: [{ translateY: slideUpAnim }],
          opacity: fadeAnim,
          maxHeight: maxPanelHeight,
        },
      ]}
    >
        {/* Drag handle */}
        <View style={styles.dragHandleContainer}>
          <View style={styles.dragHandle} />
        </View>
        <ScrollView
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          contentContainerStyle={{ paddingBottom: insets.bottom + 16 }}
        >
      {/* ── Status pill (floating) ──────────────────────────── */}
      <View style={styles.statusPill} accessibilityRole="text" accessibilityLabel={`${status.label}, earnings $${earnings.toFixed(2)}`}>
        <View style={[styles.statusIconBg, { backgroundColor: `${status.color}15` }]}>
          <Ionicons name={status.icon} size={16} color={status.color} />
        </View>
        <Text allowFontScaling={false} style={[styles.statusText, { color: status.color }]}>{status.label}</Text>
        <View style={{ flex: 1 }} />
        <Text style={styles.statusFare}>${earnings.toFixed(2)}</Text>
      </View>

      {/* ── Main card ───────────────────────────────────────── */}
      <View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 12) + 10 }]}>

        {/* ── Trip info row: earnings, distance, time ────── */}
        <View style={styles.tripInfoRow}>
          <View style={styles.tripInfoItem}>
            <Text style={styles.tripInfoValue}>${earnings.toFixed(2)}</Text>
            <Text allowFontScaling={false} style={styles.tripInfoLabel}>{t('activeRide.yourEarnings')}</Text>
          </View>
          <View style={styles.tripInfoDivider} />
          <View style={styles.tripInfoItem}>
            <Text style={styles.tripInfoValue}>
              {rideState === 'trip_in_progress' && hasLiveData
                ? `${liveDistanceKm.toFixed(1)} km`
                : `${distKm.toFixed(1)} km`}
            </Text>
            <Text allowFontScaling={false} style={styles.tripInfoLabel}>
              {rideState === 'trip_in_progress' && hasLiveData ? t('activeRide.traveled') : t('activeRide.distance')}
            </Text>
          </View>
          <View style={styles.tripInfoDivider} />
          <View style={styles.tripInfoItem}>
            <Text style={styles.tripInfoValue}>{durMin} min</Text>
            <Text allowFontScaling={false} style={styles.tripInfoLabel}>{t('activeRide.estTime')}</Text>
          </View>
        </View>

        {/* ── Rider info ─────────────────────────────────── */}
        <View style={styles.riderRow}>
          <View style={styles.riderAvatar}>
            <Ionicons name="person" size={20} color={colors.textDim} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.riderName}>{riderName}</Text>
            {rider?.rating ? (
              <View style={styles.ratingRow}>
                <Ionicons name="star" size={11} color={colors.warning} />
                <Text style={styles.ratingText}>{Number(rider.rating).toFixed(1)}</Text>
              </View>
            ) : null}
          </View>
          <TouchableOpacity
            style={styles.chatBtn}
            accessibilityRole="button"
            accessibilityLabel={`Message ${riderName}`}
            onPress={() => router.push(`/driver/chat?rideId=${ride.id}` as any)}
          >
            <Ionicons name="chatbubble-ellipses" size={18} color={colors.primary} />
          </TouchableOpacity>
        </View>

        {/* ── Route addresses ────────────────────────────── */}
        <View style={styles.routeCard}>
          <View style={styles.routeRow}>
            <View style={[styles.dot, { backgroundColor: colors.primary }]} />
            <View style={{ flex: 1 }}>
              <Text allowFontScaling={false} style={styles.routeLabel}>{t('rideOffer.pickup')}</Text>
              <Text style={styles.routeAddress} numberOfLines={2}>{ride.pickup_address}</Text>
            </View>
          </View>
          <View style={styles.routeLineContainer}>
            <View style={styles.routeLine} />
          </View>
          <View style={styles.routeRow}>
            <View style={[styles.dot, { backgroundColor: colors.success }]} />
            <View style={{ flex: 1 }}>
              <Text allowFontScaling={false} style={styles.routeLabel}>{t('rideOffer.dropoff')}</Text>
              <Text style={styles.routeAddress} numberOfLines={2}>{ride.dropoff_address}</Text>
            </View>
          </View>
        </View>

        {/* ── OTP section (arrived_at_pickup) ─────────────── */}
        {rideState === 'arrived_at_pickup' ? (
          <View style={styles.otpCard}>
            <View style={styles.otpHeader}>
              <Ionicons name="shield-checkmark" size={18} color={colors.primary} />
              <Text allowFontScaling={false} style={styles.otpTitle}>{t('activeRide.verifyRiderPin')}</Text>
            </View>
            <Text allowFontScaling={false} style={styles.otpSub}>{t('activeRide.askRiderForCode')}</Text>
            <Animated.View style={[styles.otpBoxRow, { transform: [{ translateX: shakeAnim }] }]}>
              {[0, 1, 2, 3].map(i => (
                <View
                  key={i}
                  style={[
                    styles.otpBox,
                    otpInput.length > i && styles.otpBoxFilled,
                    pinError && styles.otpBoxError,
                  ]}
                >
                  <Text style={styles.otpDigit}>{otpInput[i] || ''}</Text>
                </View>
              ))}
            </Animated.View>
            {verifying ? (
              <View style={styles.otpStatusRow}>
                <ActivityIndicator size="small" color={colors.primary} />
              </View>
            ) : pinError ? (
              <Text allowFontScaling={false} style={styles.otpErrorText}>{t('activeRide.incorrectPin')}</Text>
            ) : null}
            <View style={styles.keypad}>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, null, 0, 'del'].map((key, idx) => (
                <TouchableOpacity
                  key={idx}
                  style={[styles.kpBtn, key === null && { backgroundColor: 'transparent', elevation: 0 }]}
                  disabled={key === null || verifying}
                  activeOpacity={0.6}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                  accessibilityRole="button"
                  accessibilityLabel={key === 'del' ? 'Delete' : key !== null ? `${key}` : undefined}
                  onPress={() => {
                    if (verifying) return;
                    if (key === 'del') {
                      setPinError(false);
                      setOtpInput(otpInput.slice(0, -1));
                    } else if (key !== null && otpInput.length < 4) {
                      const next = otpInput + String(key);
                      setPinError(false);
                      setOtpInput(next);
                      if (next.length === 4) submitOtp(next);
                    }
                  }}
                >
                  {key === 'del' ? <Ionicons name="backspace-outline" size={20} color={colors.text} />
                    : key !== null ? <Text allowFontScaling={false} style={styles.kpText}>{key}</Text>
                    : null}
                </TouchableOpacity>
              ))}
            </View>
            <TouchableOpacity style={styles.skipBtn} onPress={onStartRide} disabled={isLoading} accessibilityRole="button" accessibilityLabel={t('activeRide.startWithoutPin')}>
              <Text style={styles.skipText}>{t('activeRide.startWithoutPin')}</Text>
            </TouchableOpacity>
          </View>
        ) : null}

        {/* ── Action buttons ──────────────────────────────── */}
        {rideState === 'navigating_to_pickup' ? (
          <View style={styles.actions}>
            <TouchableOpacity
              style={[styles.actionPrimary, { backgroundColor: colors.primary }]}
              onPress={() => openMapsNavigation(ride.pickup_lat, ride.pickup_lng, 'Pickup')}
              accessibilityRole="button"
              accessibilityLabel={t('activeRide.navigateToPickup')}
            >
              <Ionicons name="navigate" size={20} color="#fff" />
              <Text allowFontScaling={false} style={styles.actionPrimaryText}>{t('activeRide.navigateToPickup')}</Text>
            </TouchableOpacity>
            {(() => {
              const atPickup = distanceToPickup === null || distanceToPickup === undefined || distanceToPickup <= 150;
              return (
                <TouchableOpacity
                  style={[styles.actionSecondary, !atPickup && styles.actionSecondaryDisabled]}
                  onPress={onArriveAtPickup}
                  disabled={isLoading || !atPickup}
                  accessibilityRole="button"
                  accessibilityLabel={t('activeRide.arrivedAtPickup')}
                >
                  {isLoading ? <ActivityIndicator color={colors.primary} /> : (
                    <>
                      <Ionicons name="flag" size={18} color={colors.primary} />
                      <Text allowFontScaling={false} style={[styles.actionSecondaryText, { color: colors.primary }]}>
                        {distanceToPickup !== null && distanceToPickup !== undefined && distanceToPickup > 150 ? `${distanceToPickup}m` : t('activeRide.arrivedAtPickup')}
                      </Text>
                    </>
                  )}
                </TouchableOpacity>
              );
            })()}
          </View>
        ) : null}

        {rideState === 'trip_in_progress' ? (
          <View style={styles.actions}>
            <TouchableOpacity
              style={[styles.actionPrimary, { backgroundColor: colors.info }]}
              onPress={() => openMapsNavigation(ride.dropoff_lat, ride.dropoff_lng, 'Dropoff')}
              accessibilityRole="button"
              accessibilityLabel={t('activeRide.navigateToDropoff')}
            >
              <Ionicons name="navigate" size={20} color="#fff" />
              <Text allowFontScaling={false} style={styles.actionPrimaryText}>{t('activeRide.navigateToDropoff')}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.actionPrimary, { backgroundColor: colors.success }]}
              onPress={() => showConfirm(
                t('activeRide.completeTrip'),
                `${t('activeRide.endTripConfirm')} $${(ride.total_fare ?? 0).toFixed(2)}.`,
                [
                  { text: t('common.notYet'), style: 'cancel' },
                  { text: t('activeRide.complete'), onPress: onCompleteRide },
                ],
              )}
              disabled={isLoading}
              accessibilityRole="button"
              accessibilityLabel={t('activeRide.completeTrip')}
            >
              {isLoading ? <ActivityIndicator color="#fff" /> : (
                <>
                  <Ionicons name="checkmark-circle" size={20} color="#fff" />
                  <Text allowFontScaling={false} style={styles.actionPrimaryText}>{t('activeRide.completeTrip')}</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        ) : null}

        {/* ── Cancel link (pickup phases only) ────────────── */}
        {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') ? (
          <TouchableOpacity
            style={[styles.cancelBtn, isLoading && { opacity: 0.4 }]}
            disabled={isLoading}
            accessibilityRole="button"
            accessibilityLabel={t('activeRide.cancelRide')}
            onPress={() => showConfirm(
              t('activeRide.cancelRide'),
              t('activeRide.cancelRideWarning'),
              [
                { text: t('activeRide.keepRide'), style: 'cancel' },
                { text: t('activeRide.yesCancel'), style: 'destructive', onPress: onCancelRide },
              ],
            )}
          >
            <Text allowFontScaling={false} style={styles.cancelText}>Cancel Ride</Text>
          </TouchableOpacity>
        ) : null}
      </View>
        </ScrollView>

    </Animated.View>
  );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      zIndex: 100,
    },
    dragHandleContainer: {
      alignItems: 'center',
      paddingTop: 8,
      paddingBottom: 4,
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
    },
    dragHandle: {
      width: 36,
      height: 4,
      borderRadius: 2,
      backgroundColor: colors.border,
    },

    statusPill: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      paddingHorizontal: 14,
      paddingVertical: 10,
      borderRadius: 20,
      marginHorizontal: 16,
      marginBottom: 8,
      gap: 8,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.1,
      shadowRadius: 8,
      elevation: 6,
    },
    statusIconBg: {
      width: 30,
      height: 30,
      borderRadius: 15,
      justifyContent: 'center',
      alignItems: 'center',
    },
    statusText: { fontSize: 13, fontWeight: '700' },
    statusFare: { fontSize: 18, fontWeight: '900', color: colors.success },

    sheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 16,
      paddingTop: 16,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -3 },
      shadowOpacity: 0.08,
      shadowRadius: 10,
      elevation: 10,
    },

    tripInfoRow: {
      flexDirection: 'row',
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      paddingVertical: 14,
      paddingHorizontal: 10,
      marginBottom: 14,
      alignItems: 'center',
    },
    tripInfoItem: { flex: 1, alignItems: 'center' },
    tripInfoValue: { fontSize: 17, fontWeight: '800', color: colors.text, marginBottom: 2 },
    tripInfoLabel: { fontSize: 10, fontWeight: '600', color: colors.textDim, letterSpacing: 0.3 },
    tripInfoDivider: { width: 1, height: 28, backgroundColor: colors.border },

    riderRow: {
      flexDirection: 'row',
      alignItems: 'center',
      marginBottom: 14,
      gap: 12,
    },
    riderAvatar: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: colors.surfaceLight,
      justifyContent: 'center',
      alignItems: 'center',
    },
    riderName: { fontSize: 15, fontWeight: '700', color: colors.text },
    ratingRow: { flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 2 },
    ratingText: { fontSize: 12, fontWeight: '600', color: colors.textDim },
    chatBtn: {
      width: 38,
      height: 38,
      borderRadius: 19,
      borderWidth: 1.5,
      borderColor: colors.border,
      justifyContent: 'center',
      alignItems: 'center',
    },

    routeCard: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      padding: 14,
      marginBottom: 14,
    },
    routeRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
    dot: { width: 10, height: 10, borderRadius: 5, marginTop: 4 },
    routeLabel: { fontSize: 9, fontWeight: '800', color: colors.textDim, letterSpacing: 0.8, marginBottom: 2 },
    routeAddress: { fontSize: 13, fontWeight: '600', color: colors.text, lineHeight: 18 },
    routeLineContainer: { paddingLeft: 4, marginVertical: 4 },
    routeLine: { width: 2, height: 16, backgroundColor: colors.border, marginLeft: 3 },

    otpCard: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      padding: 16,
      marginBottom: 14,
      alignItems: 'center',
    },
    otpHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 },
    otpTitle: { fontSize: 14, fontWeight: '700', color: colors.text },
    otpSub: { fontSize: 12, color: colors.textDim, marginBottom: 14 },
    otpBoxRow: { flexDirection: 'row', gap: 8, marginBottom: 14, justifyContent: 'center' },
    otpBox: {
      width: '18%',
      maxWidth: 56,
      aspectRatio: 0.85,
      borderRadius: 14,
      backgroundColor: colors.surface,
      borderWidth: 2,
      borderColor: colors.border,
      justifyContent: 'center',
      alignItems: 'center',
    },
    otpBoxFilled: { borderColor: colors.primary, backgroundColor: `${colors.primary}08` },
    otpBoxError: { borderColor: colors.error, backgroundColor: `${colors.error}08` },
    otpStatusRow: { height: 20, justifyContent: 'center', alignItems: 'center', marginBottom: 8 },
    otpErrorText: { fontSize: 12, color: colors.error, fontWeight: '600', marginBottom: 8, textAlign: 'center' },
    otpDigit: { fontSize: 32, fontWeight: '800', color: colors.text },
    keypad: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      justifyContent: 'center',
      gap: 8,
      width: '100%',
      marginBottom: 8,
    },
    kpBtn: {
      width: '28%',
      aspectRatio: 1.4,
      minWidth: 64,
      minHeight: 64,
      backgroundColor: colors.surface,
      borderRadius: 12,
      justifyContent: 'center',
      alignItems: 'center',
      borderWidth: 1.5,
      borderColor: colors.border,
    },
    kpText: { fontSize: 26, fontWeight: '700', color: colors.text },
    skipBtn: { display: 'none' as any },
    skipText: { fontSize: 12, color: colors.textDim, fontWeight: '600' },

    actions: { gap: 8 },
    actionPrimary: {
      flexDirection: 'row',
      height: 50,
      borderRadius: 14,
      justifyContent: 'center',
      alignItems: 'center',
      gap: 8,
    },
    actionPrimaryText: { fontSize: 15, fontWeight: '700', color: '#fff' },
    actionSecondary: {
      flexDirection: 'row',
      height: 50,
      borderRadius: 14,
      justifyContent: 'center',
      alignItems: 'center',
      gap: 8,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1.5,
      borderColor: colors.border,
    },
    actionSecondaryText: { fontSize: 15, fontWeight: '700' },
    actionSecondaryDisabled: {
      opacity: 0.45,
    },

    cancelBtn: { paddingVertical: 12, alignItems: 'center', marginTop: 4 },
    cancelText: { fontSize: 13, fontWeight: '600', color: colors.error },
  });
}

export default ActiveRidePanel;
