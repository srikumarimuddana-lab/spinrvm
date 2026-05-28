import React, { useEffect, useState, useMemo, useRef, useContext } from 'react';

// Straight-line ETA at urban speed — used during trip so we don't re-call
// the Directions API on every GPS ping (that would be ~60 calls / 15-min ride).
function _haversineEtaMin(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dlat = toRad(lat2 - lat1);
  const dlng = toRad(lng2 - lng1);
  const a =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dlng / 2) ** 2;
  const km = R * 2 * Math.asin(Math.sqrt(a));
  return Math.max(1, Math.round((km / 30) * 60)); // 30 km/h urban average
}
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  useWindowDimensions,
  Share,
  Linking,
  Platform,
  ActivityIndicator,
  BackHandler,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import BottomSheet, { BottomSheetScrollView } from '../components/SafeBottomSheet';
import { useAppResumeKey } from '../hooks/useAppResumeKey';
import { useRideStore } from '../store/rideStore';
import { RideStatus } from '../constants/rideStatus';
import api from '@shared/api/client';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import { SOSButton } from '@shared/components/SOSButton';
import { CarMarker } from '@shared/components/CarMarker';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { TrackBaseUrlContext } from './_layout';

function RideInProgressScreenContent() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const trackBaseUrl = useContext(TrackBaseUrlContext);
  const {
    currentRide, currentDriver, fetchRide, cancelRide, clearRide,
    triggerEmergency, isLoading, error, wsConnected,
    activeRideRouteCoords, lastEtaMin,
    setActiveRideRouteCoords, setLastEtaMin,
  } = useRideStore();
  // Seed ETA and route from store so this screen shows correct values
  // immediately even before the first Directions fetch completes — and
  // skips the fetch entirely if driver-arriving already retrieved the route.
  const [eta, setEta] = useState(lastEtaMin ?? 15);
  const [estimatedTime, setEstimatedTime] = useState('12:45 PM');
  const [currentLocation, setCurrentLocation] = useState('4th Avenue North');
  const [isSharingLocation, setIsSharingLocation] = useState(false);
  const [tripRouteCoords, setTripRouteCoords] = useState<any[]>(activeRideRouteCoords ?? []);

  // Source-of-truth rider bill. The API computes grand_total as the sum of
  // the fare_breakdown line items (see backend/routes/rides.py::
  // _sum_fare_breakdown), so this screen, the end-ride confirms, and the
  // ride-details receipt all read the same number. No client-side math.
  const riderBill = parseFloat(((currentRide as any)?.grand_total ?? '0').toString()) || 0;
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message?: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });
  const mapRef = React.useRef<MapView>(null);
  const bottomSheetRef = React.useRef<any>(null);
  const resumeKey = useAppResumeKey();

  const { height, width } = useWindowDimensions();
  const isLandscape = height < width;
  const isTablet = width >= 768;
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Responsive snap points: fewer / higher stops on landscape phones and tablets
  const snapPoints = React.useMemo(
    () => (isLandscape ? ['50%', '70%'] : ['30%', '50%', '70%']),
    [isLandscape]
  );

  useEffect(() => {
    // Snapshot props at effect entry — the closure shouldn't reach into
    // currentRide / currentDriver after an async state change.
    const ride = currentRide;
    const driver = currentDriver;
    const map = mapRef.current;
    if (!ride || !map) return;

    const isCoord = (n: unknown): n is number =>
      typeof n === 'number' && Number.isFinite(n);

    const dropLat = ride.dropoff_lat;
    const dropLng = ride.dropoff_lng;

    if (isCoord(driver?.lat) && isCoord(driver?.lng) && isCoord(dropLat) && isCoord(dropLng)) {
      map.fitToCoordinates(
        [
          { latitude: driver.lat, longitude: driver.lng },
          { latitude: dropLat, longitude: dropLng },
        ],
        {
          edgePadding: { top: 50, right: 50, bottom: 250, left: 50 },
          animated: true,
        }
      );
      return;
    }

    // No driver fix yet — center on pickup, fall back to dropoff.
    const centerLat = isCoord(ride.pickup_lat) ? ride.pickup_lat : dropLat;
    const centerLng = isCoord(ride.pickup_lng) ? ride.pickup_lng : dropLng;
    if (isCoord(centerLat) && isCoord(centerLng)) {
      map.animateToRegion({
        latitude: centerLat,
        longitude: centerLng,
        latitudeDelta: 0.05,
        longitudeDelta: 0.05,
      });
    }
  }, [currentRide, currentDriver?.lat, currentDriver?.lng]);

  useEffect(() => {
    if (!rideId) { router.replace('/(tabs)' as any); return; }
    fetchRide(rideId);
    // Suspend fallback poll while WebSocket is delivering updates in real-time.
    if (wsConnected) return;
    const interval = setInterval(() => fetchRide(rideId), 15000);
    return () => clearInterval(interval);
  }, [rideId, wsConnected]);

  useEffect(() => {
    // Calculate estimated arrival time
    const now = new Date();
    now.setMinutes(now.getMinutes() + eta);
    setEstimatedTime(now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
  }, [eta]);

  useEffect(() => {
    if (currentRide?.status === RideStatus.COMPLETED && rideId) {
      router.replace({ pathname: '/ride-completed', params: { rideId } });
    }
  }, [currentRide?.status]);

  // Stable objects keyed to ride ID so MapViewDirections only calls the
  // Directions API once per ride instead of once per driver GPS ping.
  const routeOrigin = useMemo(
    () =>
      currentRide
        ? { latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentRide?.id],
  );
  const routeDestination = useMemo(
    () =>
      currentRide
        ? { latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentRide?.id],
  );

  // Track whether the initial Directions fetch has completed so we can hand
  // off ETA ownership to the haversine estimator below.
  const routeFetchedRef = useRef(false);

  // If the store already has coords (from driver-arriving), mark fetch done
  // so haversine starts immediately rather than waiting for a re-fetch.
  useEffect(() => {
    if (activeRideRouteCoords && activeRideRouteCoords.length > 1) {
      routeFetchedRef.current = true;
    }
  }, []);

  // After route is known, update ETA from driver's live position via haversine
  // (no Maps API call) and persist it to the store as a fallback value.
  useEffect(() => {
    if (!routeFetchedRef.current) return;
    const dLat = currentDriver?.lat;
    const dLng = currentDriver?.lng;
    const dropLat = currentRide?.dropoff_lat;
    const dropLng = currentRide?.dropoff_lng;
    // Use != null (not !dLat) so coords of exactly 0 are treated as valid.
    if (dLat == null || dLng == null || dropLat == null || dropLng == null) return;
    const etaMin = _haversineEtaMin(dLat, dLng, dropLat, dropLng);
    setEta(etaMin);
    setLastEtaMin(etaMin);
  }, [currentDriver?.lat, currentDriver?.lng]);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      setConfirmSheet({
        visible: true,
        title: 'End ride early?',
        message: `Full fare of $${riderBill.toFixed(2)} applies. Your driver will continue.`,
        variant: 'warning',
        buttons: [
          {
            text: 'End & Pay Full Fare',
            style: 'destructive',
            onPress: async () => {
              try { await api.post(`/rides/${currentRide?.id}/complete`); }
              catch { showToast('Error', 'Could not end ride. Please try again.', 'danger'); }
              if (rideId) fetchRide(rideId);
            },
          },
          { text: 'Continue Ride', style: 'cancel' },
        ],
      });
      return true;
    });
    return () => sub.remove();
  }, [currentRide?.status]);

  const handleSafety = () => {
    setConfirmSheet({
      visible: true,
      title: 'Emergency',
      message: 'Are you sure you want to contact emergency services?',
      variant: 'danger',
      buttons: [
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => {
            if (rideId) void triggerEmergency(rideId as string).catch(() => {
              showToast('Alert Not Sent', "We couldn't reach Spinr's emergency service. Please call 911 directly.", 'danger');
            });
            Linking.openURL('tel:911');
          },
        },
        { text: 'Cancel', style: 'cancel' },
      ],
    });
  };

  const handleShareTrip = async () => {
    // Public tracking URL base is served by GET /settings → app_settings.track_base_url
    // so ops can rotate the domain without a mobile rebuild. Disable the share
    // action if the admin hasn't configured one yet.
    if (!trackBaseUrl) {
      showToast(
        'Tracking Not Configured',
        'Live trip tracking is not set up yet. Please contact support.',
        'warning',
      );
      return;
    }
    // Get share token from backend API
    let shareToken = rideId || 'demo';
    try {
      const shareRes = await api.get<{ share_token?: string }>(`/rides/${rideId}/share`);
      if (shareRes.data?.share_token) {
        shareToken = shareRes.data.share_token;
      }
    } catch {
      // Fall back to ride ID
    }

    const liveTrackingUrl = `${trackBaseUrl}/${shareToken}`;
    // Include the human-readable ride code so the recipient can quote it
    // to support without guessing at a truncated UUID.
    const rideRef = currentRide?.ride_code
      ? `🆔 RIDE CODE: ${currentRide.ride_code}\n`
      : '';
    const tripDetails = `
🚗 TRACK MY SPINR RIDE - LIVE LOCATION

👤 DRIVER: ${currentDriver?.name || 'Unknown'}
⭐ RATING: ${currentDriver?.rating || 'New'}

🚙 VEHICLE: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'}
📋 LICENSE PLATE: ${currentDriver?.license_plate || 'Pending'}

📍 CURRENT LOCATION: ${currentLocation}
📍 HEADING TO: ${currentRide?.dropoff_address || '1055 Canada Place'}

⏱️ ESTIMATED ARRIVAL: ${estimatedTime} (${eta} min left)

${rideRef}🔴 LIVE TRACKING LINK:
${liveTrackingUrl}

I've shared my live location with you for safety.
`.trim();

    try {
      await Share.share({
        message: tripDetails,
        title: 'Track My Spinr Ride',
      });
      setIsSharingLocation(true);
      showToast('Trip Shared!', 'Your live location is now being shared.', 'success');
    } catch {
      showToast('Share Failed', 'Unable to share trip details. Please try again.', 'warning');
    }
  };

  const handleCopyTrackingLink = async () => {
    if (!trackBaseUrl) {
      showToast(
        'Tracking Not Configured',
        'Live trip tracking is not set up yet. Please contact support.',
        'warning',
      );
      return;
    }
    let shareToken = rideId || 'demo';
    try {
      const shareRes = await api.get<{ share_token?: string }>(`/rides/${rideId}/share`);
      if (shareRes.data?.share_token) {
        shareToken = shareRes.data.share_token;
      }
    } catch {
      // Fall back to ride ID
    }
    const trackingLink = `${trackBaseUrl}/${shareToken}`;
    await Clipboard.setStringAsync(trackingLink);
    showToast('Copied!', 'Live tracking link copied to clipboard.', 'success');
  };

  const handleOpenTrackingView = () => {
    router.push({ pathname: '/ride-tracking-webview', params: { rideId: rideId as string } } as any);
  };

  // No free cancel during ride — rider pays full fare if they end early

  const handleLocation = () => {
    // Center on current location
  };

  const progressPercent = ((15 - eta) / 15) * 100;

  const panelContent = (
    <View>
      {/* ETA Hero */}
      <View style={styles.etaHero}>
        <View style={{ flex: 1 }}>
          <Text style={styles.etaLabel}>ARRIVING AT</Text>
          <Text style={styles.etaTime} allowFontScaling={false}>{estimatedTime}</Text>
        </View>
        <View style={styles.etaBadge}>
          <Text style={styles.etaBadgeNum} allowFontScaling={false}>{eta}</Text>
          <Text style={styles.etaBadgeUnit} allowFontScaling={false}>min</Text>
        </View>
      </View>

      {/* Progress Bar */}
      <View style={styles.progressContainer}>
        <View style={[styles.progressBar, { width: `${Math.min(progressPercent, 100)}%` }]} />
      </View>

      {/* Driver Card */}
      <View style={styles.driverCard}>
        <View style={styles.driverRow}>
          <View style={styles.driverAvatar}>
            <Ionicons name="person" size={26} color={colors.textDim} />
            {currentDriver?.rating && (
              <View style={styles.ratingPill}>
                <Ionicons name="star" size={9} color="#FFB800" />
                <Text style={styles.ratingPillText}>{currentDriver.rating}</Text>
              </View>
            )}
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
            <Text style={styles.driverMeta}>{currentDriver?.total_rides || 0} trips completed</Text>
          </View>
          <TouchableOpacity
            style={styles.msgIconBtn}
            onPress={() => router.push({ pathname: '/chat-driver', params: { rideId } } as any)}
            accessibilityLabel="Message driver"
            accessibilityRole="button"
            accessibilityHint="Open chat with your driver"
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Ionicons name="chatbubble" size={20} color={colors.primary} />
          </TouchableOpacity>
        </View>

        {/* Vehicle Info */}
        <View style={styles.vehicleBar}>
          <Ionicons name="car" size={16} color={colors.primary} />
          <Text style={styles.vehicleDetail}>
            {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
          </Text>
          <View style={styles.plateBadge}>
            <Text style={styles.plateNum}>{currentDriver?.license_plate || 'N/A'}</Text>
          </View>
        </View>
      </View>

      {/* Trip Route Card */}
      <View style={styles.tripCard}>
        <View style={styles.tripRow}>
          <View style={styles.tripDots}>
            <View style={[styles.tripDot, { backgroundColor: '#10B981' }]} />
            <View style={styles.tripConnector} />
            <View style={[styles.tripDot, { backgroundColor: colors.primary }]} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.tripRouteLabel}>PICKUP</Text>
            <Text style={styles.tripRouteAddr} numberOfLines={1}>{currentRide?.pickup_address || 'Pickup'}</Text>
            <View style={{ height: 20 }} />
            <Text style={styles.tripRouteLabel}>DESTINATION</Text>
            <Text style={styles.tripRouteAddr} numberOfLines={1}>{currentRide?.dropoff_address || 'Destination'}</Text>
          </View>
        </View>

        {/* Fare — the rider's bill, computed exactly the same way
            ride-details.tsx renders "You paid": sum of the server-supplied
            fare_breakdown line items. This guarantees the in-progress and
            ride-details pages always agree, regardless of whether
            grand_total is on the row or how total_fare has been mutated
            by recalc/promo paths. Falls back to grand_total / total_fare
            scalars only when fare_breakdown is empty. */}
        <View style={styles.fareRow}>
          <View style={styles.fareItem}>
            <Ionicons name="cash-outline" size={16} color={colors.textDim} />
            <Text style={styles.fareValue} allowFontScaling={false}>${riderBill.toFixed(2)}</Text>
            <Text style={styles.fareLabel}>Fare</Text>
          </View>
          <View style={styles.fareDivider} />
          <View style={styles.fareItem}>
            <Ionicons name="speedometer-outline" size={16} color={colors.textDim} />
            <Text style={styles.fareValue} allowFontScaling={false}>{(currentRide?.distance_km || 0).toFixed(1)} km</Text>
            <Text style={styles.fareLabel}>Distance</Text>
          </View>
          <View style={styles.fareDivider} />
          <View style={styles.fareItem}>
            <Ionicons name="time-outline" size={16} color={colors.textDim} />
            <Text style={styles.fareValue} allowFontScaling={false}>{eta} min</Text>
            <Text style={styles.fareLabel}>ETA</Text>
          </View>
        </View>
      </View>

      {/* Live Sharing */}
      {isSharingLocation && (
        <View style={styles.liveSharingBanner}>
          <View style={styles.liveIndicator}>
            <View style={styles.liveDot} />
            <Text style={styles.liveText}>LIVE</Text>
          </View>
          <Text style={styles.sharingText}>Location sharing active</Text>
          <TouchableOpacity
            onPress={handleCopyTrackingLink}
            accessibilityRole="button"
            accessibilityLabel="Copy live tracking link"
            hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
          >
            <Ionicons name="copy-outline" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>
      )}

      {/* Action Row */}
      <View style={styles.actionRow}>
        <TouchableOpacity
          style={styles.actionBtn}
          onPress={handleShareTrip}
          accessibilityLabel="Share trip"
          accessibilityRole="button"
        >
          <Ionicons name="share-outline" size={20} color={colors.text} />
          <Text style={styles.actionBtnText}>Share Trip</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.actionBtn} onPress={handleOpenTrackingView}>
          <Ionicons name="map-outline" size={20} color={colors.text} />
          <Text style={styles.actionBtnText}>Live Map</Text>
        </TouchableOpacity>
        <View style={styles.actionBtn}>
          <SOSButton rideId={rideId as string} onTrigger={triggerEmergency} />
          <Text style={styles.actionBtnText}>SOS</Text>
        </View>
        <TouchableOpacity style={styles.actionBtn} onPress={() => {
          setConfirmSheet({
            visible: true,
            title: 'End ride early?',
            message: `You will be charged the full agreed fare of $${riderBill.toFixed(2)}. This cannot be undone.`,
            variant: 'warning',
            buttons: [
              {
                text: 'End & Pay Full Fare', style: 'destructive',
                onPress: async () => {
                  try { await api.post(`/rides/${currentRide?.id}/complete`); }
                  catch { showToast('Error', 'Could not end ride. Please try again.', 'danger'); }
                  if (rideId) fetchRide(rideId);
                },
              },
              { text: 'Continue Ride', style: 'cancel' },
            ],
          });
        }}>
          <Ionicons name="stop-circle-outline" size={20} color={colors.textDim} />
          <Text style={styles.actionBtnText}>End Ride</Text>
        </TouchableOpacity>
      </View>

      {/* DEV CONTROLS */}
      {__DEV__ && currentRide && (
        <View style={styles.devBar}>
          <Text style={styles.devLabel}>DEV: {currentRide.status}</Text>
          <TouchableOpacity style={styles.devBtn} onPress={async () => {
            try { await api.post(`/drivers/rides/${currentRide.id}/complete`); }
            catch(e) { if (__DEV__) console.warn('dev complete:', e); }
            if (rideId) fetchRide(rideId);
          }}>
            <Text style={styles.devBtnText}>Complete Ride</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );

  return (
    <View style={[styles.container, isTablet && { flexDirection: 'row' as const }]}>
      {/* Map column — scopes floating overlays to the map area on tablet */}
      <View style={styles.mapColumn}>

      {/* Header Status */}
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.statusPill}>
          <View style={styles.greenDot} />
          <Text style={styles.statusText} allowFontScaling={false}>Ride Started - Enjoy your trip</Text>
        </View>
      </SafeAreaView>

      {/* SOS — floating top-right, always visible even when bottom sheet is collapsed */}
      <SafeAreaView edges={['top']} style={styles.sosOverlay} pointerEvents="box-none">
        <SOSButton rideId={rideId as string} onTrigger={triggerEmergency} size="small" />
      </SafeAreaView>

      {/* Map Area */}
      <View style={styles.mapContainer}>
        {isLoading && !currentRide ? (
          <View style={styles.mapPlaceholder}>
            <ActivityIndicator size="large" color="#EE2B2B" />
            <Text style={styles.mapPlaceholderText}>Loading ride…</Text>
          </View>
        ) : error && !currentRide ? (
          <View style={styles.mapPlaceholder}>
            <Ionicons name="alert-circle" size={48} color="#EF4444" />
            <Text style={styles.mapPlaceholderText}>Could not load ride</Text>
            <TouchableOpacity
              style={styles.retryBtn}
              onPress={() => rideId && fetchRide(rideId)}
            >
              <Text style={styles.retryBtnText}>Retry</Text>
            </TouchableOpacity>
          </View>
        ) : currentRide ? (
          <MapView
            {...({ ref: mapRef } as any)}
            provider={Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined}
            style={styles.map}
            initialRegion={{
              latitude: currentRide.pickup_lat,
              longitude: currentRide.pickup_lng,
              latitudeDelta: 0.05,
              longitudeDelta: 0.05,
            }}
            showsUserLocation
            showsMyLocationButton={false}
            userInterfaceStyle={isDark ? "dark" : "light"}
          >
            {/* Route: reuse coords from store if driver-arriving already fetched
                them. Only call Directions API when this is the first screen in
                the ride flow (store is empty). Either way, origin is the stable
                pickup location — never the live driver position — so MapViewDirections
                fires at most once per ride. */}
            {routeOrigin && routeDestination &&
              process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY &&
              activeRideRouteCoords === null && (
              <MapViewDirections
                origin={routeOrigin}
                destination={routeDestination}
                apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={(result: any) => {
                  if (!result.coordinates?.length) return;
                  routeFetchedRef.current = true;
                  const etaMin = Math.ceil(result.duration);
                  setEta(etaMin);
                  setLastEtaMin(etaMin);
                  setTripRouteCoords(result.coordinates);
                  setActiveRideRouteCoords(result.coordinates);
                  if (mapRef.current && result.coordinates?.length > 1) {
                    mapRef.current.fitToCoordinates(result.coordinates, {
                      edgePadding: { top: 80, right: 50, bottom: 280, left: 50 },
                      animated: true,
                    });
                  }
                }}
              />
            )}

            {/* Orange → Red gradient route */}
            {tripRouteCoords.length > 1 && (() => {
              const total = tripRouteCoords.length;
              const SEGS = 20;
              const chunk = Math.max(1, Math.floor(total / SEGS));
              const segments: { coords: any[]; color: string }[] = [];
              for (let i = 0; i < total - 1; i += chunk) {
                const end = Math.min(i + chunk + 1, total);
                const t = i / Math.max(total - 1, 1);
                const r = Math.round(255 + (238 - 255) * t);
                const g = Math.round(149 + (43 - 149) * t);
                const b = Math.round(0 + (43 - 0) * t);
                segments.push({ coords: tripRouteCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
              }
              return segments.map((seg, idx) => (
                <Polyline
                  key={`trip-seg-${idx}`}
                  coordinates={seg.coords}
                  strokeWidth={5}
                  strokeColor={seg.color}
                  lineCap="round"
                  lineJoin="round"
                />
              ));
            })()}

            {/* Pickup Marker (green) */}
            <Marker
              coordinate={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
              anchor={{ x: 0.5, y: 0.5 }}
            >
              <View style={styles.pickupMarker}>
                <Ionicons name="location" size={16} color="#FFF" />
              </View>
            </Marker>

            {/* Destination Marker (red) */}
            <Marker
              coordinate={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
              anchor={{ x: 0.5, y: 0.5 }}
            >
              <View style={styles.dropoffMarker}>
                <Ionicons name="flag" size={16} color="#FFF" />
              </View>
            </Marker>

            {/* Driver Car Marker */}
            {currentDriver?.lat != null && currentDriver?.lng != null && (
              <CarMarker
                coordinate={{ latitude: currentDriver.lat, longitude: currentDriver.lng }}
                heading={(currentDriver as any).heading}
                size={44}
                zIndex={100}
              />
            )}
          </MapView>
        ) : (
          <View style={styles.mapPlaceholder}>
            <Text style={styles.mapPlaceholderText}>Loading Map…</Text>
          </View>
        )}

        {/* Location button */}
        <TouchableOpacity
          style={styles.locationButton}
          onPress={handleLocation}
          accessibilityRole="button"
          accessibilityLabel="Center map on my location"
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="navigate" size={22} color={colors.text} />
        </TouchableOpacity>
      </View>

      </View>{/* end map column */}

      {/* Panel — BottomSheet on phone, side panel on tablet */}
      {isTablet ? (
        <SafeAreaView edges={['top', 'bottom', 'right']} style={styles.tabletPanel}>
          <ScrollView contentContainerStyle={styles.bottomSheetContent} showsVerticalScrollIndicator={false}>
            {panelContent}
          </ScrollView>
        </SafeAreaView>
      ) : (
        <BottomSheet
          key={resumeKey}
          ref={bottomSheetRef}
          index={1}
          snapPoints={snapPoints}
          enablePanDownToClose={false}
          enableOverDrag={false}
          backgroundStyle={styles.bottomSheetBackground}
          handleIndicatorStyle={styles.sheetHandleIndicator}
        >
          {/* @ts-ignore - gorhom/bottom-sheet v4 has a known children typing bug with React 18 */}
          <BottomSheetScrollView bounces={false} overScrollMode="never" contentContainerStyle={styles.bottomSheetContent}>
            {panelContent}
          </BottomSheetScrollView>
        </BottomSheet>
      )}
      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
      />
    </View>
  );
}

export default function RideInProgressScreen() {
  return (
    <ErrorBoundary>
      <RideInProgressScreenContent />
    </ErrorBoundary>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: '#E8E8E8' },
    mapColumn: { flex: 1, position: 'relative' },
    tabletPanel: {
      width: 380,
      backgroundColor: colors.surface,
      borderLeftWidth: 1,
      borderLeftColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: -4, height: 0 },
      shadowOpacity: 0.08,
      shadowRadius: 12,
      elevation: 8,
    },
    headerSafeArea: {
      position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10,
      alignItems: 'center', paddingTop: 8,
    },
    statusPill: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, paddingHorizontal: 20, paddingVertical: 12, borderRadius: 24,
      shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.1, shadowRadius: 4, elevation: 3,
    },
    greenDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#10B981', marginRight: 10 },
    statusText: { fontSize: 15, fontWeight: '600', color: colors.text },
    sosOverlay: {
      position: 'absolute', top: 0, right: 16, zIndex: 20,
      alignItems: 'flex-end', paddingTop: 8,
    },
    mapContainer: { flex: 1, position: 'relative' },
    map: { ...StyleSheet.absoluteFill },
    mapPlaceholder: { flex: 1, backgroundColor: colors.border, justifyContent: 'center', alignItems: 'center' },
    mapPlaceholderText: { marginTop: 12, fontSize: 15, fontWeight: '500', color: '#555' },
    retryBtn: { marginTop: 16, paddingHorizontal: 28, paddingVertical: 12, backgroundColor: '#EE2B2B', borderRadius: 24 },
    retryBtnText: { color: '#FFF', fontSize: 15, fontWeight: '700' },
    locationButton: {
      position: 'absolute', right: 16, bottom: 16,
      width: 48, height: 48, borderRadius: 24, backgroundColor: colors.surface,
      justifyContent: 'center', alignItems: 'center',
      elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.15, shadowRadius: 4,
    },
    bottomSheetBackground: { backgroundColor: colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24 },
    sheetHandleIndicator: { width: 40, backgroundColor: '#DDD' },
    bottomSheetContent: { paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40 },

    // ETA Hero
    etaHero: {
      flexDirection: 'row', alignItems: 'center', marginBottom: 12,
    },
    etaLabel: { fontSize: 11, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5, marginBottom: 2 },
    etaTime: { fontSize: 28, fontWeight: '800', color: colors.text },
    etaBadge: {
      width: 56, height: 56, borderRadius: 28,
      backgroundColor: colors.primary,
      justifyContent: 'center', alignItems: 'center',
    },
    etaBadgeNum: { fontSize: 20, fontWeight: '800', color: '#FFF', lineHeight: 22 },
    etaBadgeUnit: { fontSize: 10, fontWeight: '600', color: 'rgba(255,255,255,0.8)', marginTop: -2 },

    // Progress
    progressContainer: { height: 4, backgroundColor: colors.border, borderRadius: 2, marginBottom: 16 },
    progressBar: { height: 4, backgroundColor: colors.primary, borderRadius: 2 },

    // Driver Card
    driverCard: { backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 16, marginBottom: 14 },
    driverRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
    driverAvatar: {
      width: 48, height: 48, borderRadius: 24, backgroundColor: '#E8E8E8',
      justifyContent: 'center', alignItems: 'center', marginRight: 12, position: 'relative',
    },
    ratingPill: {
      position: 'absolute', bottom: -4, left: -2,
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, paddingHorizontal: 5, paddingVertical: 2, borderRadius: 8,
      shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.1, shadowRadius: 2,
    },
    ratingPillText: { fontSize: 10, fontWeight: '700', color: colors.text, marginLeft: 2 },
    driverName: { fontSize: 16, fontWeight: '700', color: colors.text },
    driverMeta: { fontSize: 12, color: colors.textDim, marginTop: 2 },
    msgIconBtn: {
      width: 44, height: 44, borderRadius: 22,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center', alignItems: 'center',
    },
    vehicleBar: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingTop: 12, borderTopWidth: 1, borderTopColor: '#ECECEC',
    },
    vehicleDetail: { flex: 1, fontSize: 13, fontWeight: '500', color: colors.textSecondary },
    plateBadge: {
      backgroundColor: colors.text, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4,
    },
    plateNum: { fontSize: 13, fontWeight: '800', color: colors.surface, letterSpacing: 1.5 },

    // Trip Card
    tripCard: { backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 16, marginBottom: 14 },
    tripRow: { flexDirection: 'row' },
    tripDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
    tripDot: { width: 10, height: 10, borderRadius: 5 },
    tripConnector: { width: 2, flex: 1, backgroundColor: '#DDD', marginVertical: 4 },
    tripRouteLabel: { fontSize: 10, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5, marginBottom: 2 },
    tripRouteAddr: { fontSize: 14, fontWeight: '500', color: colors.text },
    fareRow: {
      flexDirection: 'row', marginTop: 14, paddingTop: 14,
      borderTopWidth: 1, borderTopColor: '#ECECEC',
    },
    fareItem: { flex: 1, alignItems: 'center' },
    fareValue: { fontSize: 15, fontWeight: '700', color: colors.text, marginTop: 4 },
    fareLabel: { fontSize: 10, color: colors.textDim, marginTop: 2 },
    fareDivider: { width: 1, backgroundColor: '#ECECEC' },

    // Live Sharing
    liveSharingBanner: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: '#F0FFF4', paddingHorizontal: 14, paddingVertical: 10,
      borderRadius: 12, marginBottom: 14, borderWidth: 1, borderColor: '#D1FAE5',
    },
    liveIndicator: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: '#EF4444', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, marginRight: 10,
    },
    liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#FFF', marginRight: 4 },
    liveText: { fontSize: 10, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
    sharingText: { flex: 1, fontSize: 14, fontWeight: '500', color: '#059669' },

    // Action Row
    actionRow: { flexDirection: 'row', gap: 10, marginBottom: 8 },
    actionBtn: {
      flex: 1, alignItems: 'center', justifyContent: 'center', gap: 4,
      backgroundColor: colors.surfaceLight, paddingVertical: 14, borderRadius: 14,
    },
    actionBtnDanger: { backgroundColor: '#FEF2F2' },
    actionBtnText: { fontSize: 12, fontWeight: '600', color: colors.text },

    // Markers
    pickupMarker: {
      width: 32, height: 32, borderRadius: 16,
      backgroundColor: '#10B981', justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: '#FFF',
      elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.2, shadowRadius: 3,
    },
    dropoffMarker: {
      width: 32, height: 32, borderRadius: 16,
      backgroundColor: '#EF4444', justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: '#FFF',
      elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.2, shadowRadius: 3,
    },

    // Dev
    devBar: {
      flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8,
      marginTop: 16, padding: 12, backgroundColor: '#FEF3C7', borderRadius: 12,
      borderWidth: 1, borderColor: '#F59E0B',
    },
    devLabel: { fontSize: 11, fontWeight: '700', color: '#92400E', marginRight: 4 },
    devBtn: { backgroundColor: '#F59E0B', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
    devBtnText: { fontSize: 12, fontWeight: '700', color: '#FFF' },
  });
}
