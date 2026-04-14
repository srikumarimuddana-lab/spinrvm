import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
  Linking,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';

const ACCENT = SpinrConfig.theme.colors.primary;

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
  onVerifyOTP: (otp: string) => void;
  onNavigate: (lat: number, lng: number, label: string) => void;
  onArriveAtPickup: () => void;
  onStartRide: () => void;
  onCompleteRide: () => void;
  onCancelRide: () => void;
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
  slideUpAnim,
  fadeAnim,
  distanceToPickup,
}) => {
  // All hooks MUST be before any early return to avoid React ordering issues
  const [waitSeconds, setWaitSeconds] = useState(0);
  const waitTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [alertVisible, setAlertVisible] = useState(false);
  const [alertCfg, setAlertCfg] = useState({
    title: '', message: '', variant: 'info' as 'info' | 'warning' | 'danger' | 'success',
    buttons: [] as Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>,
  });

  // Live distance tracking during trip
  const [liveDistanceKm, setLiveDistanceKm] = useState(0);
  const [hasLiveData, setHasLiveData] = useState(false);
  const lastLocRef = useRef<{ lat: number; lng: number } | null>(null);
  const jitterBufferRef = useRef(0);

  // Reset distance when ride phase OR ride id changes — prevents stale
  // accumulation carrying over to a different ride if the panel is recycled.
  useEffect(() => {
    setLiveDistanceKm(0);
    setHasLiveData(false);
    lastLocRef.current = null;
    jitterBufferRef.current = 0;
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

  if (!ride) return null;

  // ── Helpers ─────────────────────────────────────────────────
  const riderName = rider?.first_name
    ? `${rider.first_name}${rider.last_name ? ' ' + rider.last_name[0] + '.' : ''}`
    : rider?.name || 'Rider';

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

  const showAlert = (
    title: string, message: string,
    variant: 'info' | 'warning' | 'danger' | 'success',
    buttons: typeof alertCfg.buttons
  ) => {
    setAlertCfg({ title, message, variant, buttons });
    setAlertVisible(true);
  };

  // ── Status config ───────────────────────────────────────────
  const statusMap = {
    navigating_to_pickup: { icon: 'navigate-circle' as const, label: 'En Route to Pickup', color: ACCENT },
    arrived_at_pickup: { icon: 'time' as const, label: `Waiting · ${formatWait(waitSeconds)}`, color: '#F59E0B' },
    trip_in_progress: { icon: 'car-sport' as const, label: 'Trip in Progress', color: '#22C55E' },
  };
  const status = statusMap[rideState];

  return (
    <Animated.View
      style={[styles.container, { transform: [{ translateY: slideUpAnim }], opacity: fadeAnim }]}
    >
      {/* ── Status pill (floating) ──────────────────────────── */}
      <View style={styles.statusPill}>
        <View style={[styles.statusIconBg, { backgroundColor: `${status.color}15` }]}>
          <Ionicons name={status.icon} size={16} color={status.color} />
        </View>
        <Text style={[styles.statusText, { color: status.color }]}>{status.label}</Text>
        <View style={{ flex: 1 }} />
        <Text style={styles.statusFare}>${earnings.toFixed(2)}</Text>
      </View>

      {/* ── Main card ───────────────────────────────────────── */}
      <View style={styles.sheet}>

        {/* ── Trip info row: earnings, distance, time ────── */}
        <View style={styles.tripInfoRow}>
          <View style={styles.tripInfoItem}>
            <Text style={styles.tripInfoValue}>${earnings.toFixed(2)}</Text>
            <Text style={styles.tripInfoLabel}>Your Earnings</Text>
          </View>
          <View style={styles.tripInfoDivider} />
          <View style={styles.tripInfoItem}>
            <Text style={styles.tripInfoValue}>
              {rideState === 'trip_in_progress' && hasLiveData
                ? `${liveDistanceKm.toFixed(1)} km`
                : `${distKm.toFixed(1)} km`}
            </Text>
            <Text style={styles.tripInfoLabel}>
              {rideState === 'trip_in_progress' && hasLiveData ? 'Traveled' : 'Distance'}
            </Text>
          </View>
          <View style={styles.tripInfoDivider} />
          <View style={styles.tripInfoItem}>
            <Text style={styles.tripInfoValue}>{durMin} min</Text>
            <Text style={styles.tripInfoLabel}>Est. Time</Text>
          </View>
        </View>

        {/* ── Rider info ─────────────────────────────────── */}
        <View style={styles.riderRow}>
          <View style={styles.riderAvatar}>
            <Ionicons name="person" size={20} color="#999" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.riderName}>{riderName}</Text>
            {rider?.rating ? (
              <View style={styles.ratingRow}>
                <Ionicons name="star" size={11} color="#F59E0B" />
                <Text style={styles.ratingText}>{Number(rider.rating).toFixed(1)}</Text>
              </View>
            ) : null}
          </View>
          <TouchableOpacity style={styles.chatBtn}>
            <Ionicons name="chatbubble-ellipses" size={18} color={ACCENT} />
          </TouchableOpacity>
        </View>

        {/* ── Route addresses ────────────────────────────── */}
        <View style={styles.routeCard}>
          <View style={styles.routeRow}>
            <View style={[styles.dot, { backgroundColor: ACCENT }]} />
            <View style={{ flex: 1 }}>
              <Text style={styles.routeLabel}>PICKUP</Text>
              <Text style={styles.routeAddress} numberOfLines={2}>{ride.pickup_address}</Text>
            </View>
          </View>
          <View style={styles.routeLineContainer}>
            <View style={styles.routeLine} />
          </View>
          <View style={styles.routeRow}>
            <View style={[styles.dot, { backgroundColor: '#22C55E' }]} />
            <View style={{ flex: 1 }}>
              <Text style={styles.routeLabel}>DROPOFF</Text>
              <Text style={styles.routeAddress} numberOfLines={2}>{ride.dropoff_address}</Text>
            </View>
          </View>
        </View>

        {/* ── OTP section (arrived_at_pickup) ─────────────── */}
        {rideState === 'arrived_at_pickup' ? (
          <View style={styles.otpCard}>
            <View style={styles.otpHeader}>
              <Ionicons name="shield-checkmark" size={18} color={ACCENT} />
              <Text style={styles.otpTitle}>Verify Rider's PIN</Text>
            </View>
            <Text style={styles.otpSub}>Ask rider for their 4-digit code</Text>
            <View style={styles.otpBoxRow}>
              {[0, 1, 2, 3].map(i => (
                <View key={i} style={[styles.otpBox, otpInput.length > i && styles.otpBoxFilled]}>
                  <Text style={styles.otpDigit}>{otpInput[i] || ''}</Text>
                </View>
              ))}
            </View>
            <View style={styles.keypad}>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, null, 0, 'del'].map((key, idx) => (
                <TouchableOpacity
                  key={idx}
                  style={[styles.kpBtn, key === null && { backgroundColor: 'transparent', elevation: 0 }]}
                  disabled={key === null}
                  activeOpacity={0.6}
                  onPress={() => {
                    if (key === 'del') setOtpInput(otpInput.slice(0, -1));
                    else if (key !== null && otpInput.length < 4) {
                      const next = otpInput + String(key);
                      setOtpInput(next);
                      if (next.length === 4) onVerifyOTP(next);
                    }
                  }}
                >
                  {key === 'del' ? <Ionicons name="backspace-outline" size={20} color="#333" />
                    : key !== null ? <Text style={styles.kpText}>{key}</Text>
                    : null}
                </TouchableOpacity>
              ))}
            </View>

          </View>
        ) : null}

        {/* ── Action buttons ──────────────────────────────── */}
        {rideState === 'navigating_to_pickup' ? (
          <View style={styles.actions}>
            <TouchableOpacity
              style={[styles.actionPrimary, { backgroundColor: ACCENT }]}
              onPress={() => openMapsNavigation(ride.pickup_lat, ride.pickup_lng, 'Pickup')}
            >
              <Ionicons name="navigate" size={20} color="#fff" />
              <Text style={styles.actionPrimaryText}>Navigate to Pickup</Text>
            </TouchableOpacity>
            {(() => {
              const atPickup = distanceToPickup === null || distanceToPickup === undefined || distanceToPickup <= 150;
              return (
                <TouchableOpacity
                  style={[styles.actionSecondary, !atPickup && styles.actionSecondaryDisabled]}
                  onPress={onArriveAtPickup}
                  disabled={isLoading || !atPickup}
                >
                  {isLoading ? <ActivityIndicator color={ACCENT} /> : (
                    <>
                      <Ionicons name="flag" size={18} color={ACCENT} />
                      <Text style={[styles.actionSecondaryText, { color: ACCENT }]}>
                        {distanceToPickup !== null && distanceToPickup !== undefined && distanceToPickup > 150 ? `${distanceToPickup}m away` : "I've Arrived at Pickup"}
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
              style={[styles.actionPrimary, { backgroundColor: '#3B82F6' }]}
              onPress={() => openMapsNavigation(ride.dropoff_lat, ride.dropoff_lng, 'Dropoff')}
            >
              <Ionicons name="navigate" size={20} color="#fff" />
              <Text style={styles.actionPrimaryText}>Navigate to Dropoff</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.actionPrimary, { backgroundColor: '#22C55E' }]}
              onPress={() => showAlert(
                'Complete Trip',
                `End this trip? Rider will be charged $${(ride.total_fare ?? 0).toFixed(2)}.`,
                'success',
                [
                  { text: 'Not Yet', style: 'cancel' },
                  { text: 'Complete', onPress: onCompleteRide },
                ],
              )}
              disabled={isLoading}
            >
              {isLoading ? <ActivityIndicator color="#fff" /> : (
                <>
                  <Ionicons name="checkmark-circle" size={20} color="#fff" />
                  <Text style={styles.actionPrimaryText}>Complete Trip</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        ) : null}

        {/* ── Cancel link (pickup phases only) ────────────── */}
        {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') ? (
          <TouchableOpacity
            style={styles.cancelBtn}
            onPress={() => showAlert(
              'Cancel Ride?',
              'Frequent cancellations may affect your rating.',
              'warning',
              [
                { text: 'Keep Ride', style: 'cancel' },
                { text: 'Yes, Cancel', style: 'destructive', onPress: onCancelRide },
              ],
            )}
          >
            <Text style={styles.cancelText}>Cancel Ride</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      <CustomAlert
        visible={alertVisible}
        title={alertCfg.title}
        message={alertCfg.message}
        variant={alertCfg.variant}
        buttons={alertCfg.buttons.length > 0 ? alertCfg.buttons : [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertVisible(false)}
      />
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    zIndex: 100,
  },

  // Status pill
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
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
  statusFare: { fontSize: 18, fontWeight: '900', color: '#22C55E' },

  // Sheet
  sheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: Platform.OS === 'ios' ? 38 : 22,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.08,
    shadowRadius: 10,
    elevation: 10,
  },

  // Trip info row
  tripInfoRow: {
    flexDirection: 'row',
    backgroundColor: '#F8F9FA',
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 10,
    marginBottom: 14,
    alignItems: 'center',
  },
  tripInfoItem: { flex: 1, alignItems: 'center' },
  tripInfoValue: { fontSize: 17, fontWeight: '800', color: '#1A1A1A', marginBottom: 2 },
  tripInfoLabel: { fontSize: 10, fontWeight: '600', color: '#999', letterSpacing: 0.3 },
  tripInfoDivider: { width: 1, height: 28, backgroundColor: '#E5E5E5' },

  // Rider row
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
    backgroundColor: '#F0F0F0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  riderName: { fontSize: 15, fontWeight: '700', color: '#1A1A1A' },
  ratingRow: { flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 2 },
  ratingText: { fontSize: 12, fontWeight: '600', color: '#999' },
  chatBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    borderWidth: 1.5,
    borderColor: '#F0F0F0',
    justifyContent: 'center',
    alignItems: 'center',
  },

  // Route card
  routeCard: {
    backgroundColor: '#F8F9FA',
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
  },
  routeRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  dot: { width: 10, height: 10, borderRadius: 5, marginTop: 4 },
  routeLabel: { fontSize: 9, fontWeight: '800', color: '#999', letterSpacing: 0.8, marginBottom: 2 },
  routeAddress: { fontSize: 13, fontWeight: '600', color: '#1A1A1A', lineHeight: 18 },
  routeLineContainer: { paddingLeft: 4, marginVertical: 4 },
  routeLine: { width: 2, height: 16, backgroundColor: '#DDD', marginLeft: 3 },

  // OTP
  otpCard: {
    backgroundColor: '#F8F9FA',
    borderRadius: 14,
    padding: 16,
    marginBottom: 14,
    alignItems: 'center',
  },
  otpHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 },
  otpTitle: { fontSize: 14, fontWeight: '700', color: '#1A1A1A' },
  otpSub: { fontSize: 12, color: '#999', marginBottom: 14 },
  otpBoxRow: { flexDirection: 'row', gap: 8, marginBottom: 14 },
  otpBox: {
    width: 56,
    height: 66,
    borderRadius: 14,
    backgroundColor: '#fff',
    borderWidth: 2,
    borderColor: '#E5E5E5',
    justifyContent: 'center',
    alignItems: 'center',
  },
  otpBoxFilled: { borderColor: ACCENT, backgroundColor: `${ACCENT}08` },
  otpDigit: { fontSize: 32, fontWeight: '800', color: '#1A1A1A' },
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
    backgroundColor: '#fff',
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1.5,
    borderColor: '#F0F0F0',
  },
  kpText: { fontSize: 26, fontWeight: '700', color: '#1A1A1A' },
  skipBtn: { display: 'none' as any },
  skipText: { fontSize: 12, color: '#999', fontWeight: '600' },

  // Actions
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
    backgroundColor: '#F8F9FA',
    borderWidth: 1.5,
    borderColor: '#F0F0F0',
  },
  actionSecondaryText: { fontSize: 15, fontWeight: '700' },
  actionSecondaryDisabled: {
    opacity: 0.45,
  },

  // Cancel
  cancelBtn: { paddingVertical: 12, alignItems: 'center', marginTop: 4 },
  cancelText: { fontSize: 13, fontWeight: '600', color: '#EF4444' },
});

export default ActiveRidePanel;
