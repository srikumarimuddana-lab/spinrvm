import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
  Linking,
  PanResponder,
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
import { useNavStore } from '../../store/navStore';
import { showAlert } from '../AlertDialog';
import CancelReasonSheet from '../CancelReasonSheet';

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
  onCancelRide: (reason?: string) => void;
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
  const { navApp, loadNavApp } = useNavStore();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [reasonVisible, setReasonVisible] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const waitTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live distance tracking during trip
  const [liveDistanceKm, setLiveDistanceKm] = useState(0);
  const [hasLiveData, setHasLiveData] = useState(false);
  const lastLocRef = useRef<{ lat: number; lng: number } | null>(null);
  const jitterBufferRef = useRef(0);

  // PIN entry feedback state — spinner while verifying, shake + error on a
  // wrong code. Reset whenever the ride phase or ride id changes.
  // react-hooks/refs ("Cannot access refs during render"): standard
  // Animated.Value driver idiom (Animated mutates it imperatively outside
  // the render cycle via .timing() below and in the OTP shake sequence).
  // Verified safe — grepped this file for `.current =`: no reassignment of
  // shakeAnim anywhere, so a discarded/replayed render always reads the
  // same instance.
  // eslint-disable-next-line react-hooks/refs
  const shakeAnim = useRef(new Animated.Value(0)).current;
  const [verifying, setVerifying] = useState(false);
  const [pinError, setPinError] = useState(false);

  // ── Draggable sheet ─────────────────────────────────────────
  // The grab area (handle + status header) owns the pan gesture so dragging
  // never fights the content ScrollView. dragY is the sheet's offset from
  // fully-open (0) down to the collapsed peek, where only the grab area
  // stays visible above the home indicator. Composed with the parent's
  // slideUpAnim entry animation via Animated.add (both native-driven).
  // react-hooks/refs: same verified-safe Animated.Value driver idiom as
  // shakeAnim above — mutated only via Animated.spring()/.setValue() in the
  // PanResponder gesture handlers below, never reassigned via `.current =`.
  // eslint-disable-next-line react-hooks/refs
  const dragY = useRef(new Animated.Value(0)).current;
  const dragOffsetRef = useRef(0);
  const sheetHeightRef = useRef(0);
  const grabHeightRef = useRef(0);
  const insetsBottomRef = useRef(insets.bottom);
  useEffect(() => {
    insetsBottomRef.current = insets.bottom;
  }, [insets.bottom]);
  const [isCollapsed, setIsCollapsed] = useState(false);

  const collapsedOffsetRef = useRef(() =>
    Math.max(0, sheetHeightRef.current - grabHeightRef.current - insetsBottomRef.current),
  );

  const snapTo = (offset: number) => {
    dragOffsetRef.current = offset;
    setIsCollapsed(offset > 0);
    Animated.spring(dragY, {
      toValue: offset,
      useNativeDriver: true,
      tension: 90,
      friction: 13,
    }).start();
  };
  const snapToRef = useRef(snapTo);
  useEffect(() => {
    snapToRef.current = snapTo;
  });

  // react-hooks/refs: PanResponder.create(...) is created once (useRef's
  // initializer only runs on first render) and its `.panHandlers` are read
  // in JSX below — the textbook "stable gesture handler ref" case. Verified
  // safe — grepped this file for `.current =`: panResponder is never
  // reassigned after creation.
  const panResponder = useRef(
    // eslint-disable-next-line react-hooks/refs
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: (_e, g) =>
        Math.abs(g.dy) > 4 && Math.abs(g.dy) > Math.abs(g.dx),
      onPanResponderMove: (_e, g) => {
        const next = Math.min(
          Math.max(dragOffsetRef.current + g.dy, 0),
          collapsedOffsetRef.current(),
        );
        dragY.setValue(next);
      },
      onPanResponderRelease: (_e, g) => {
        const max = collapsedOffsetRef.current();
        if (Math.abs(g.dy) < 6 && Math.abs(g.vy) < 0.3) {
          // Tap on the grab area: always settle open (expands when collapsed).
          snapToRef.current(0);
          return;
        }
        // Project the gesture forward by its velocity, then snap to the
        // nearer detent — a quick flick collapses/expands without a full drag.
        const projected = dragOffsetRef.current + g.dy + g.vy * 150;
        snapToRef.current(projected > max / 2 ? max : 0);
      },
      onPanResponderTerminate: () => {
        snapToRef.current(dragOffsetRef.current);
      },
    }),
  ).current;

  // Hydrate the driver's saved navigation-app choice once so the
  // navigate buttons launch the right app even on a cold start.
  useEffect(() => {
    loadNavApp();
  }, []);

  // Phase changes always re-open the sheet — the PIN keypad or the new
  // action buttons must never appear while the sheet is collapsed.
  useEffect(() => {
    snapToRef.current(0);
  }, [rideState]);

  // Reset distance when ride phase OR ride id changes — prevents stale
  // accumulation carrying over to a different ride if the panel is recycled.
  // Local display-only accumulator (not the fare-settlement distance, which
  // is computed server-side) — doesn't feed back into rideState/ride.id.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
      // Local wait-timer display, doesn't feed back into rideState.
      // eslint-disable-next-line react-hooks/set-state-in-effect
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
            { text: t('activeRide.yesCancel'), style: 'destructive', onPress: () => setReasonVisible(true) },
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

  const openMapsNavigation = async (lat: number, lng: number, _label: string) => {
    // The Google Maps web URL is the universal fallback — it works on every
    // device whether or not a native app is installed. On devices WITH the
    // Google Maps app it auto-redirects; without it (or in Expo Go) it opens
    // the browser which still provides turn-by-turn.
    const googleWebUrl = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
    const appleUrl = `http://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`;
    // If the chosen app isn't installed, fall back to the phone's built-in
    // maps (Apple Maps on iOS, Google Maps web on Android) — always present
    // and always gives driving directions.
    const defaultUrl = Platform.OS === 'ios' ? appleUrl : googleWebUrl;

    // Try a dedicated app's deep link, falling back when it isn't installed.
    // canOpenURL is the reliable cross-platform check: on iOS the `waze` and
    // `comgooglemaps` schemes are whitelisted in app.config's
    // LSApplicationQueriesSchemes, so canOpenURL returns false (not a system
    // error) when the app is absent; on Android it reflects installed intents.
    const openWithFallback = async (appUrl: string) => {
      try {
        if (await Linking.canOpenURL(appUrl)) {
          await Linking.openURL(appUrl);
          return;
        }
      } catch {
        // canOpenURL/openURL threw — fall through to the default maps app.
      }
      Linking.openURL(defaultUrl).catch(() => Linking.openURL(googleWebUrl));
    };

    // Honour the driver's saved choice (Settings → Navigation).
    if (navApp === 'waze') {
      await openWithFallback(`waze://?ll=${lat},${lng}&navigate=yes`);
      return;
    }
    if (navApp === 'google') {
      await openWithFallback(`comgooglemaps://?daddr=${lat},${lng}&directionsmode=driving`);
      return;
    }

    // 'default' — use the platform's native maps app.
    Linking.openURL(defaultUrl).catch(() => Linking.openURL(googleWebUrl));
  };

  const showConfirm = (
    title: string, message: string,
    buttons: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[],
  ) => {
    showAlert(title, message, buttons);
  };

  // ── Status config ───────────────────────────────────────────
  // Status renders as a two-line header: bold phase label on top, live
  // detail underneath (ETA/distance, wait timer, or traveled distance).
  // Live route ETA wins; the booking estimate is the fallback so the line
  // is never empty.
  const etaLine =
    routeEtaMinutes != null && routeDistanceKm != null
      ? `~${routeEtaMinutes} min · ${routeDistanceKm} km`
      : `~${durMin} min · ${distKm.toFixed(1)} km`;

  // Palette is deliberately restricted to red / green / white / grey:
  // grey-charcoal = neutral phase + navigation, red = attention (waiting,
  // cancel, destination), green = money + advancing the trip.
  const statusMap = {
    navigating_to_pickup: {
      icon: 'navigate-circle' as const,
      label: t('activeRide.enRouteToPickup'),
      sub: etaLine,
      color: colors.text,
    },
    arrived_at_pickup: {
      icon: 'time' as const,
      label: t('activeRide.waiting'),
      sub: formatWait(waitSeconds),
      color: colors.error,
    },
    trip_in_progress: {
      icon: 'car-sport' as const,
      label: t('activeRide.tripInProgress'),
      sub: hasLiveData
        ? `${etaLine} · ${liveDistanceKm.toFixed(1)} km ${t('activeRide.traveled').toLowerCase()}`
        : etaLine,
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
      onLayout={e => { sheetHeightRef.current = e.nativeEvent.layout.height; }}
      style={[
        styles.container,
        {
          transform: [{ translateY: Animated.add(slideUpAnim, dragY) }],
          opacity: fadeAnim,
          maxHeight: maxPanelHeight,
        },
      ]}
    >
        {/* ── Grab area: drag handle + status header. Stays visible as the
            collapsed peek; owns the pan gesture. ─────────────── */}
        <View
          // eslint-disable-next-line react-hooks/refs -- stable PanResponder gesture handler ref, see note above declaration
          {...panResponder.panHandlers}
          onLayout={e => { grabHeightRef.current = e.nativeEvent.layout.height; }}
          accessibilityRole="button"
          accessibilityLabel={isCollapsed ? 'Expand ride details' : 'Collapse ride details'}
          accessibilityHint="Drag down to minimize the ride panel, drag up or tap to expand it"
        >
          <View style={styles.dragHandleContainer}>
            <View style={styles.dragHandle} />
          </View>

          {/* ── Header: phase status + live detail on the left, earnings on
              the right. Doubles as the collapsed-peek summary. ── */}
          <View
            style={styles.headerRow}
            accessibilityRole="text"
            accessibilityLabel={`${status.label}, ${status.sub}, earnings $${earnings.toFixed(2)}`}
          >
            <View style={[styles.statusIconBg, { backgroundColor: `${status.color}18` }]}>
              <Ionicons name={status.icon} size={20} color={status.color} />
            </View>
            <View style={{ flex: 1 }}>
              <Text allowFontScaling={false} style={[styles.statusText, { color: status.color }]}>{status.label}</Text>
              <Text allowFontScaling={false} style={styles.statusSub}>{status.sub}</Text>
            </View>
            <View style={styles.earningsBox}>
              <Text style={styles.earningsValue}>${earnings.toFixed(2)}</Text>
              <Text allowFontScaling={false} style={styles.earningsLabel}>{t('activeRide.yourEarnings')}</Text>
            </View>
          </View>
        </View>

        <ScrollView
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          scrollEnabled={!isCollapsed}
          contentContainerStyle={{ paddingBottom: insets.bottom + 16 }}
        >
      {/* ── Main card ───────────────────────────────────────── */}
      <View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 12) + 10 }]}>

        {/* ── Trip card: rider + route in one card so the sheet reads as a
            single unit instead of stacked fragments. Route is hidden during
            PIN entry to keep the keypad the hero. ─────────── */}
        <View style={styles.tripCard}>
          <View style={styles.riderRow}>
            <View style={styles.riderAvatar}>
              <Text allowFontScaling={false} style={styles.riderAvatarText}>
                {riderName.charAt(0).toUpperCase()}
              </Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.riderName}>{riderName}</Text>
              {rider?.rating ? (
                <View style={styles.ratingRow}>
                  <Ionicons name="star" size={11} color={colors.text} />
                  <Text style={styles.ratingText}>{Number(rider.rating).toFixed(1)}</Text>
                </View>
              ) : null}
            </View>
            {rider?.phone ? (
              <TouchableOpacity
                style={[styles.contactBtn, { backgroundColor: colors.successBg }]}
                accessibilityRole="button"
                accessibilityLabel={`Call ${riderName}`}
                onPress={() => Linking.openURL(`tel:${rider.phone}`)}
              >
                <Ionicons name="call" size={18} color={colors.success} />
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity
              style={[styles.contactBtn, styles.contactBtnNeutral]}
              accessibilityRole="button"
              accessibilityLabel={`Message ${riderName}`}
              onPress={() => router.push(`/driver/chat?rideId=${ride.id}` as any)}
            >
              <Ionicons name="chatbubble-ellipses" size={18} color={colors.text} />
            </TouchableOpacity>
          </View>

          {rideState !== 'arrived_at_pickup' && (
          <>
            <View style={styles.cardDivider} />
            <View style={styles.routeRow}>
              <View style={[styles.dot, { backgroundColor: colors.success }]} />
              <View style={{ flex: 1 }}>
                <Text allowFontScaling={false} style={styles.routeLabel}>{t('rideOffer.pickup')}</Text>
                <Text style={styles.routeAddress} numberOfLines={2}>{ride.pickup_address}</Text>
              </View>
            </View>
            <View style={styles.routeLineContainer}>
              <View style={styles.routeLine} />
            </View>
            <View style={styles.routeRow}>
              <View style={styles.destSquare} />
              <View style={{ flex: 1 }}>
                <Text allowFontScaling={false} style={styles.routeLabel}>{t('rideOffer.dropoff')}</Text>
                <Text style={styles.routeAddress} numberOfLines={2}>{ride.dropoff_address}</Text>
              </View>
            </View>
          </>
          )}
        </View>

        {/* ── Rider's note / meeting instructions (pickup phases) ─ */}
        {!!(ride as any).rider_notes && (rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') ? (
          <View style={styles.noteBanner}>
            <Ionicons name="chatbubble-ellipses-outline" size={16} color={colors.primary} style={{ marginTop: 1 }} />
            <View style={{ flex: 1 }}>
              <Text allowFontScaling={false} style={styles.noteLabel}>Rider note</Text>
              <Text style={styles.noteText}>{(ride as any).rider_notes}</Text>
            </View>
          </View>
        ) : null}

        {/* ── Quiet ride preference (whole trip) ──────────── */}
        {!!(ride as any).quiet_mode ? (
          <View style={styles.quietBanner}>
            <Ionicons name="volume-mute" size={16} color="#8B5CF6" style={{ marginTop: 1 }} />
            <View style={{ flex: 1 }}>
              <Text allowFontScaling={false} style={styles.quietLabel}>Quiet ride requested</Text>
              <Text style={styles.quietText}>The rider prefers minimal conversation.</Text>
            </View>
          </View>
        ) : null}

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

        {/* ── Action buttons. Consistent color language across all phases:
            charcoal = open navigation, green (success) = advance the
            trip, red = cancel link only. ──────────────────── */}
        {rideState === 'navigating_to_pickup' ? (
          <View style={styles.actions}>
            <TouchableOpacity
              style={[styles.actionPrimary, styles.actionNeutral]}
              onPress={() => openMapsNavigation((ride as any).pickup_nav_lat ?? ride.pickup_lat, (ride as any).pickup_nav_lng ?? ride.pickup_lng, 'Pickup')}
              accessibilityRole="button"
              accessibilityLabel={t('activeRide.navigateToPickup')}
            >
              <Ionicons name="navigate" size={20} color={colors.surface} />
              <Text allowFontScaling={false} style={[styles.actionPrimaryText, styles.actionNeutralText]}>{t('activeRide.navigateToPickup')}</Text>
            </TouchableOpacity>
            {(() => {
              const atPickup = distanceToPickup === null || distanceToPickup === undefined || distanceToPickup <= 150;
              return (
                <TouchableOpacity
                  style={atPickup
                    ? [styles.actionPrimary, { backgroundColor: colors.success }]
                    : [styles.actionSecondary, styles.actionSecondaryDisabled]}
                  onPress={onArriveAtPickup}
                  disabled={isLoading || !atPickup}
                  accessibilityRole="button"
                  accessibilityLabel={t('activeRide.arrivedAtPickup')}
                >
                  {isLoading ? <ActivityIndicator color={atPickup ? '#fff' : colors.textDim} /> : (
                    <>
                      <Ionicons name="flag" size={18} color={atPickup ? '#fff' : colors.textDim} />
                      <Text
                        allowFontScaling={false}
                        style={atPickup ? styles.actionPrimaryText : [styles.actionSecondaryText, { color: colors.textDim }]}
                      >
                        {!atPickup && distanceToPickup !== null && distanceToPickup !== undefined
                          ? `${t('activeRide.arrivedAtPickup')} · ${distanceToPickup}m`
                          : t('activeRide.arrivedAtPickup')}
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
              style={[styles.actionPrimary, styles.actionNeutral]}
              onPress={() => openMapsNavigation(ride.dropoff_lat, ride.dropoff_lng, 'Dropoff')}
              accessibilityRole="button"
              accessibilityLabel={t('activeRide.navigateToDropoff')}
            >
              <Ionicons name="navigate" size={20} color={colors.surface} />
              <Text allowFontScaling={false} style={[styles.actionPrimaryText, styles.actionNeutralText]}>{t('activeRide.navigateToDropoff')}</Text>
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
                { text: t('activeRide.yesCancel'), style: 'destructive', onPress: () => setReasonVisible(true) },
              ],
            )}
          >
            <Text allowFontScaling={false} style={styles.cancelText}>Cancel Ride</Text>
          </TouchableOpacity>
        ) : null}
      </View>
        </ScrollView>

      <CancelReasonSheet
        visible={reasonVisible}
        message={t('activeRide.cancelRideWarning')}
        onConfirm={(reason) => onCancelRide(reason)}
        onClose={() => setReasonVisible(false)}
      />
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
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -3 },
      shadowOpacity: 0.08,
      shadowRadius: 10,
      elevation: 10,
    },
    dragHandleContainer: {
      alignItems: 'center',
      paddingTop: 8,
      paddingBottom: 4,
    },
    dragHandle: {
      width: 36,
      height: 4,
      borderRadius: 2,
      backgroundColor: colors.border,
    },

    headerRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingHorizontal: 16,
      paddingBottom: 12,
    },
    statusIconBg: {
      width: 38,
      height: 38,
      borderRadius: 19,
      justifyContent: 'center',
      alignItems: 'center',
    },
    statusText: { fontSize: 15, fontWeight: '800' },
    statusSub: { fontSize: 12, fontWeight: '600', color: colors.textDim, marginTop: 1, fontVariant: ['tabular-nums'] },
    earningsBox: {
      alignItems: 'center',
      backgroundColor: colors.successBg,
      borderRadius: 14,
      paddingHorizontal: 12,
      paddingVertical: 6,
    },
    earningsValue: { fontSize: 19, fontWeight: '900', color: colors.success, fontVariant: ['tabular-nums'] },
    earningsLabel: { fontSize: 9, fontWeight: '700', color: colors.textDim, letterSpacing: 0.4, textTransform: 'uppercase' },

    sheet: {
      paddingHorizontal: 16,
      paddingTop: 2,
    },

    tripCard: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 18,
      borderWidth: 1,
      borderColor: colors.border,
      padding: 14,
      marginBottom: 12,
    },
    cardDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 12,
    },

    riderRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
    },
    riderAvatar: {
      width: 42,
      height: 42,
      borderRadius: 21,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center',
      alignItems: 'center',
    },
    riderAvatarText: { fontSize: 18, fontWeight: '800', color: colors.primary },
    riderName: { fontSize: 15, fontWeight: '700', color: colors.text },
    noteBanner: {
      flexDirection: 'row',
      gap: 8,
      alignItems: 'flex-start',
      backgroundColor: colors.primary + '14',
      borderRadius: 12,
      paddingVertical: 10,
      paddingHorizontal: 12,
      marginBottom: 12,
    },
    noteLabel: { fontSize: 11, fontWeight: '700', color: colors.primary, marginBottom: 2 },
    noteText: { fontSize: 14, color: colors.text, lineHeight: 19 },
    quietBanner: {
      flexDirection: 'row',
      gap: 8,
      alignItems: 'flex-start',
      backgroundColor: '#8B5CF614',
      borderRadius: 12,
      paddingVertical: 10,
      paddingHorizontal: 12,
      marginBottom: 12,
    },
    quietLabel: { fontSize: 11, fontWeight: '700', color: '#8B5CF6', marginBottom: 2 },
    quietText: { fontSize: 14, color: colors.text, lineHeight: 19 },
    ratingRow: { flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 2 },
    ratingText: { fontSize: 12, fontWeight: '600', color: colors.textDim },
    contactBtn: {
      width: 40,
      height: 40,
      borderRadius: 20,
      justifyContent: 'center',
      alignItems: 'center',
    },
    contactBtnNeutral: {
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
    },

    routeRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
    dot: { width: 10, height: 10, borderRadius: 5, marginTop: 4 },
    destSquare: { width: 10, height: 10, borderRadius: 2, marginTop: 4, backgroundColor: colors.error },
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
      height: 52,
      borderRadius: 16,
      justifyContent: 'center',
      alignItems: 'center',
      gap: 8,
    },
    actionPrimaryText: { fontSize: 15, fontWeight: '700', color: '#fff' },
    // Charcoal in light mode, white in dark mode — the "open external
    // navigation" action, distinct from green trip-advancing actions.
    actionNeutral: { backgroundColor: colors.text },
    actionNeutralText: { color: colors.surface },
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
