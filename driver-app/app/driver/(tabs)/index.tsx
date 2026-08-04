import React, { useState, useEffect, useRef, useMemo } from 'react';
import { View, Text, StyleSheet, Platform, Linking, Animated, TouchableOpacity, ActivityIndicator, AppState, Modal } from 'react-native';
import MapView, { Polygon, Heatmap, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { Ionicons } from '@expo/vector-icons';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useDriverStore, type OffRouteConfirmation } from '../../../store/driverStore';
import { useAuthStore } from '@shared/store/authStore';
import { useExitOnBackPress } from '@shared/hooks/useExitOnBackPress';
import { useVehicleTypeStore } from '@shared/store/vehicleTypeStore';
import {
  DriverTopBar,
  DriverIdlePanel,
  ActiveRidePanel,
  TripCompletedPanel,
  MapControls,
} from '../../../components/dashboard';
import { RideOfferPanel } from '../../../components/panels/RideOfferPanel';
import { useDriverDashboard } from '../../../hooks/useDriverDashboard';
import { CarMarker, resolveMarkerVariant, type CarMarkerVariant } from '../../../components/CarMarker';
import { SOSButton } from '@shared/components/SOSButton';
import { useLanguageStore } from '../../../store/languageStore';
import { showToast } from '../../../hooks/useToast';
import api, { isAppCheckTokenReady } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '@shared/api/queryClient';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';

// Use Google Maps on Android, Apple Maps (native) on iOS
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

// Distance in metres between two lat/lng pairs. Used by the Directions
// refresh ticker to decide whether the driver has moved far enough to
// warrant a new API call.
function haversineMeters(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function DriverDashboard() {
  const { colors, isDark } = useTheme();
  const insets = useSafeAreaInsets();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Home is a navigation root: the Android back button must background the app,
  // not pop back into the login/OTP screens that sit beneath it in history.
  useExitOnBackPress();

  // Driver + user live on the shared auth store — useDriverStore does not
  // hold these fields, so reading them from there always returned null and
  // left the GO button permanently disabled.
  const driverData = useAuthStore(s => s.driver);
  const user = useAuthStore(s => s.user);
  const {
    rideState,
    incomingRide,
    activeRide,
    completedRide,
    countdownSeconds,
    configuredCountdownSeconds,
    setCountdown,
    acceptRide,
    declineRide,
    arriveAtPickup,
    verifyOTP,
    startRide,
    completeRide,
    cancelRide,
    resetRideState,
    clearError,
    earnings,
    rateRider,
  } = useDriverStore();

  const isCancellingRide = useDriverStore((s) => s.isCancellingRide);
  const [completionConfirmationVisible, setCompletionConfirmationVisible] = useState(false);
  const [completionConfirmationRideId, setCompletionConfirmationRideId] = useState<string | null>(null);

  const requestRideCompletion = async (confirmation?: OffRouteConfirmation) => {
    const rideId = confirmation ? completionConfirmationRideId : activeRide?.ride.id;
    if (!rideId) return;

    const result = await completeRide(rideId, confirmation);
    if (result.confirmationRequired) {
      setCompletionConfirmationRideId(rideId);
      setCompletionConfirmationVisible(true);
      return;
    }
    setCompletionConfirmationVisible(false);
    setCompletionConfirmationRideId(null);
  };

  // Own-car marker, admin-configured per vehicle type. Resolved from the
  // shared vehicleTypeStore (synced once per app open by the root layout) —
  // no per-screen fetch.
  const ownVehicleType = useVehicleTypeStore(
    (s) => s.byId[(driverData?.vehicle_type_id as string) ?? ''],
  );
  const markerVariant: CarMarkerVariant = resolveMarkerVariant(
    ownVehicleType?.marker_variant,
    ownVehicleType?.name,
  );
  const markerImageUri = ownVehicleType?.marker_image_url;

  const { t } = useLanguageStore();

  const {
    isOnline,
    connectionState,
    location,
    locationStatus,
    otpInput,
    setOtpInput,
    toggleOnline,
    openNavigation,
    mapRef,
    currentRegionRef,
    pulseAnim,
    slideUpAnim,
    fadeAnim,
    wsError,
    wsLatency,
    refreshLocation,
  } = useDriverDashboard();

  // Unread notification count — fetched on mount, refreshed every 60s
  const [unreadNotifCount, setUnreadNotifCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const fetchUnread = async () => {
      // /api/v1/notifications is App-Check-enforced in prod; polling before the
      // App Check token is minted 401s. Skip until ready — the 60s interval
      // retries, so the badge loads once App Check is up.
      if (!(await isAppCheckTokenReady())) return;
      api.get('/notifications?limit=1').then((res: any) => {
        if (!cancelled) setUnreadNotifCount(res.data?.unread_count ?? 0);
      }).catch(() => {});
    };
    fetchUnread();
    const timer = setInterval(fetchUnread, 60 * 1000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  // Driver discreet-SOS rollout flag (ACTION_ITEMS.md B16), from the public
  // /settings projection. Fetched once on mount — deliberately NOT at SOS
  // time, since that would put a network round trip on the one path that must
  // not have one. Defaults false, and a failed fetch leaves it false, so the
  // fail-safe direction is "driver keeps the standard SOS button" rather than
  // "driver gets no SOS".
  const [discreetSosEnabled, setDiscreetSosEnabled] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.get<{ driver_sos_discreet_enabled?: boolean }>('/settings')
      .then((res: any) => {
        if (!cancelled) setDiscreetSosEnabled(!!res?.data?.driver_sos_discreet_enabled);
      })
      .catch((e: any) => console.warn('[index] settings fetch failed:', e?.message ?? e));
    return () => { cancelled = true; };
  }, []);

  // Surge multiplier for the driver's service area — fetched on mount and
  // refreshed every 2 minutes (matching the surge engine interval).
  const [surgeMultiplier, setSurgeMultiplier] = useState<number>(1.0);
  useEffect(() => {
    let cancelled = false;
    const fetchSurge = async () => {
      try {
        const res = await api.get<Array<{ id: string; surge_multiplier?: number; surge_active?: boolean }>>('/service-areas');
        if (cancelled) return;
        const areaId = driverData?.service_area_id;
        const areas: Array<{ id: string; surge_multiplier?: number; surge_active?: boolean }> = res.data || [];
        const myArea = areaId ? areas.find((a) => a.id === areaId) : areas[0];
        if (myArea?.surge_active && typeof myArea.surge_multiplier === 'number') {
          setSurgeMultiplier(myArea.surge_multiplier);
        } else {
          setSurgeMultiplier(1.0);
        }
      } catch {
        // surge badge is informational — silently ignore fetch failures
      }
    };
    fetchSurge();
    const timer = setInterval(fetchSurge, 2 * 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [driverData?.service_area_id]);

  // Route polyline coordinates for active rides
  const [routeCoords, setRouteCoords] = useState<{ latitude: number; longitude: number }[]>([]);
  // True when the Google Directions API call failed (no network, quota,
  // invalid key, etc.). When set, the offer panel renders a dashed
  // straight-line polyline from origin to destination so the driver
  // still sees pickup → dropoff geometry instead of an empty map.
  const [directionsFailed, setDirectionsFailed] = useState(false);

  // MapView remount key. Bumped when a ride ends so Android's Google Maps
  // native layer fully drops leftover polyline overlays and the CarMarker
  // re-snapshots cleanly. Without this, after rating/Done the driver sees
  // a ghost polyline near the destination and a blank car marker.
  const [mapKey, setMapKey] = useState(0);
  const prevRideStateRef = useRef(rideState);
  useEffect(() => {
    const prev = prevRideStateRef.current;
    if (rideState === 'idle' && prev !== 'idle') {
      setMapKey((k) => k + 1);
    }
    prevRideStateRef.current = rideState;
  }, [rideState]);

  // Live ETA from Google Directions — refreshed every 60s, with a stationary
  // skip so a parked driver doesn't burn API calls when nothing has changed.
  const [routeEtaMinutes, setRouteEtaMinutes] = useState<number | null>(null);
  const [routeDistanceKm, setRouteDistanceKm] = useState<number | null>(null);

  // Last origin actually fetched + a mirror of the live driver location, both
  // held in refs so the interval callback sees fresh values without
  // re-subscribing on every render.
  const lastDirectionsFetchRef = useRef<{ lat: number; lng: number; ts: number } | null>(null);
  const currentLocationRef = useRef<{ lat: number; lng: number } | null>(null);
  useEffect(() => {
    if (location?.coords?.latitude != null && location?.coords?.longitude != null) {
      currentLocationRef.current = { lat: location.coords.latitude, lng: location.coords.longitude };
    }
  }, [location]);

  // Force MapViewDirections to re-compute by changing a key prop. Only
  // runs for navigating_to_pickup (driver → pickup needs live Directions).
  // ride_offered and trip_in_progress use the saved planned_route_polyline
  // instead — zero Directions API calls for those phases.
  const [directionsKey, setDirectionsKey] = useState(0);

  // True while the backend's OSRM live-route poll is delivering routes. When
  // OSRM is healthy we suppress Google Directions entirely (render + 60s
  // refresh ticker) — OSRM already redraws driver → destination every 20s at
  // zero metered cost. Flips back false if a live-route fetch fails so Google
  // resumes as the fallback. Mirrored in a ref so the interval callback sees
  // the current value without re-subscribing.
  const [osrmRouteActive, setOsrmRouteActive] = useState(false);
  const osrmRouteActiveRef = useRef(false);
  useEffect(() => { osrmRouteActiveRef.current = osrmRouteActive; }, [osrmRouteActive]);

  useEffect(() => {
    if (rideState !== 'navigating_to_pickup') return;
    const interval = setInterval(() => {
      if (osrmRouteActiveRef.current) return;
      const now = Date.now();
      const last = lastDirectionsFetchRef.current;
      const cur = currentLocationRef.current;
      if (last && cur) {
        const movedMeters = haversineMeters(last.lat, last.lng, cur.lat, cur.lng);
        const elapsedMs = now - last.ts;
        if (movedMeters < 100 && elapsedMs < 5 * 60_000) return;
      }
      setDirectionsKey((k) => k + 1);
    }, 60000);
    return () => clearInterval(interval);
  }, [rideState]);

  // Demand heatmap — controlled by admin per service area
  const [heatmapPoints, setHeatmapPoints] = useState<{ latitude: number; longitude: number; weight: number }[]>([]);

  // Fetch heatmap data when idle (backend returns empty if admin disabled it)
  useEffect(() => {
    if (rideState !== 'idle') {
      setHeatmapPoints([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<{ enabled: boolean; points?: number[][] }>('/drivers/demand-heatmap');
        if (cancelled) return;
        if (!res.data.enabled) {
          setHeatmapPoints([]);
          return;
        }
        const pts = (res.data.points || []).map((p: number[]) => ({
          latitude: p[0],
          longitude: p[1],
          weight: p[2] || 1,
        }));
        setHeatmapPoints(pts);
      } catch (e) {
        console.log('Heatmap fetch error:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [rideState]);

  const [countdown, setCountdownState] = useState(countdownSeconds);

  // Countdown timer — starts on every rideState transition into
  // 'ride_offered'. Previously this was gated on `countdown > 0`, but on
  // the render where rideState flipped to 'ride_offered' the local
  // `countdown` was still the stale 0 from the previous render (the
  // sync-from-store effect hadn't fired yet), so the gate failed and the
  // interval never started — the number stuck at 15 until the offer
  // auto-declined in the background.
  useEffect(() => {
    if (rideState !== 'ride_offered') return;
    // Re-seed local state from the store so we always start from the
    // configured countdown when a fresh offer arrives.
    setCountdownState(countdownSeconds);
    const interval = setInterval(() => {
      setCountdownState((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          setCountdown(0);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
    // `countdownSeconds` is read at effect-start only; we intentionally
    // don't re-run on every tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rideState]);

  // Clear route + ETA when ride state changes (new phase = new route).
  // Stable boolean: true when the store already holds a saved polyline for the
  // current ride. Declared here (before the useEffect) because dep arrays are
  // evaluated at call time — can't reference a variable that's declared later
  // in the function body. Uses incomingRide / activeRide directly rather than
  // the `ride` alias (which is declared further down the component) for the
  // same reason. The effect re-fires when this transitions false → true, which
  // covers cold-start fetches and hydration-then-freshen sequences where
  // rideState stays unchanged but the ride object gains polyline data.
  const _hasRidePolyline = (() => {
    const poly =
      incomingRide?.planned_route_polyline ??
      (activeRide?.ride as any)?.planned_route_polyline ??
      (activeRide?.ride as any)?.route_polyline;
    return Array.isArray(poly) && poly.length >= 2;
  })();

  // For ride_offered and trip_in_progress, reuse the saved polyline from
  // ride creation instead of calling the Directions API — the planned
  // route (pickup → dropoff) is already computed and stored server-side.
  // This eliminates ~90% of Directions API calls during active rides.
  // Additionally, when transitioning back to idle (ride cancelled / completed),
  // re-center the map on the driver's current location — otherwise the map
  // stays framed on the pickup/dropoff bounding box from the previous ride
  // and the driver marker ends up off-screen.
  useEffect(() => {
    setDirectionsFailed(false);
    setRouteEtaMinutes(null);
    setRouteDistanceKm(null);
    setDirectionsKey(0);
    setOsrmRouteActive(false);

    const savedPoly = (ride as any)?.planned_route_polyline || (ride as any)?.route_polyline;
    const canUseSaved =
      Array.isArray(savedPoly) &&
      savedPoly.length >= 2 &&
      (rideState === 'ride_offered' || rideState === 'trip_in_progress');

    if (canUseSaved) {
      const coords = savedPoly
        .filter((p: any) => Array.isArray(p) && p.length >= 2)
        .map((p: any) => ({ latitude: p[0], longitude: p[1] }));
      if (coords.length >= 2) {
        setRouteCoords(coords);
        if (mapRef.current) {
          mapRef.current.fitToCoordinates(coords, {
            edgePadding: { top: 100, right: 60, bottom: 300, left: 60 },
            animated: true,
          });
        }
      } else {
        setRouteCoords([]);
      }
    } else {
      setRouteCoords([]);
    }

    if (rideState === 'idle' && mapRef.current && location?.coords) {
      mapRef.current.animateToRegion(
        {
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
          latitudeDelta: 0.04,
          longitudeDelta: 0.04,
        },
        600
      );
    }
    // location intentionally excluded — we only want to re-center on state
    // transition, not on every GPS tick.
    // _hasRidePolyline: re-fires when polyline data arrives after a cold-start
    // fetch or a hydration-then-freshen (rideState unchanged, ride updates).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rideState, _hasRidePolyline]);

  // OSRM live route + ETA. Google stays the map canvas; during active phases we
  // poll the backend's OSRM route from the driver's live position to the current
  // destination and draw that road-matched line + live ETA, replacing the static
  // planned polyline. Falls back to the saved polyline / existing ETA if OSRM is
  // unreachable. Avoids per-ride Google Directions calls.
  useEffect(() => {
    const rid = activeRide?.ride?.id;
    if (!rid || !['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress'].includes(rideState)) return;
    let cancelled = false;
    const fetchLiveRoute = async () => {
      try {
        const { data } = await api.get<{
          polyline: [number, number][];
          eta_seconds: number | null;
          distance_km: number | null;
        }>(`/rides/${rid}/live-route`);
        if (cancelled || !data) return;
        if (Array.isArray(data.polyline) && data.polyline.length > 1) {
          setRouteCoords(data.polyline.map((p) => ({ latitude: p[0], longitude: p[1] })));
          setOsrmRouteActive(true);
        } else {
          setOsrmRouteActive(false);
          if (rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') {
            setRouteCoords([]);
          }
        }
        if (typeof data.eta_seconds === 'number' && data.eta_seconds > 0) {
          setRouteEtaMinutes(Math.max(1, Math.ceil(data.eta_seconds / 60)));
        }
        if (typeof data.distance_km === 'number' && data.distance_km > 0) {
          setRouteDistanceKm(data.distance_km);
        }
      } catch {
        // OSRM unavailable — let Google Directions resume as the live-route
        // fallback. Pre-pickup must never retain pickup -> dropoff geometry.
        if (!cancelled) {
          setOsrmRouteActive(false);
          if (rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') {
            setRouteCoords([]);
          }
        }
      }
    };
    fetchLiveRoute();
    const id = setInterval(fetchLiveRoute, 20000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRide?.ride?.id, rideState]);

  // When the app comes back to the foreground, arm a one-shot re-center
  // for the next location update. `initialRegion` above is one-shot, so
  // without this the map stays pinned to whatever fix was set before the
  // user backgrounded — e.g. they left home, opened the app at work, and
  // the map still shows home until the next app restart. Skipped during
  // active rides so the ride camera framing isn't disrupted.
  const pendingRecenterRef = useRef(false);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') pendingRecenterRef.current = true;
    });
    return () => sub.remove();
  }, []);
  useEffect(() => {
    if (!pendingRecenterRef.current) return;
    if (!location?.coords || !mapRef.current) return;
    if (rideState !== 'idle') return;
    pendingRecenterRef.current = false;
    mapRef.current.animateToRegion?.(
      {
        latitude: location.coords.latitude,
        longitude: location.coords.longitude,
        latitudeDelta: 0.04,
        longitudeDelta: 0.04,
      },
      400
    );
  }, [location, rideState]);

  // Error handling
  useEffect(() => {
    const { error } = useDriverStore.getState();
    if (error) {
      showToast('error', 'Something Went Wrong', error || 'Please try again.');
      clearError();
    }
  }, [useDriverStore.getState().error]);

  // Ride pickup/dropoff markers. Memoised so the countdown ticking
  // every second during `ride_offered` doesn't rebuild the marker
  // elements (new `coordinate` object refs each render forced
  // react-native-maps to re-animate the markers, producing visible
  // flicker on the map).
  const ride = activeRide?.ride || incomingRide;
  const pickupLat = ride?.pickup_lat;
  const pickupLng = ride?.pickup_lng;
  const dropoffLat = ride?.dropoff_lat;
  const dropoffLng = ride?.dropoff_lng;

  // Stable pickup / dropoff point objects for the shared <RoutePins>. Memoised
  // so the countdown ticking every second during `ride_offered` doesn't hand
  // react-native-maps fresh coordinate refs each render (which forced the
  // markers to re-animate and flicker).
  const pickupPoint = useMemo(
    () => (pickupLat && pickupLng ? { latitude: pickupLat, longitude: pickupLng } : null),
    [pickupLat, pickupLng],
  );
  const dropoffPoint = useMemo(
    () => (dropoffLat && dropoffLng ? { latitude: dropoffLat, longitude: dropoffLng } : null),
    [dropoffLat, dropoffLng],
  );

  // Ride Offer Panel
  const renderRideOfferPanel = () => {
    if (!incomingRide) return null;
    // Timer-bar progress tracks remaining countdown as a fraction of the
    // configured max. Previously this was `/ 15` hardcoded, so bumping
    // ride_offer_timeout_seconds in backend settings would have left
    // the visual bar stuck past 100%.
    const maxCountdown = configuredCountdownSeconds || 15;
    const progress = Math.max(0, Math.min(1, countdown / maxCountdown));
    const fare = parseFloat(incomingRide.fare || '0').toFixed(2);

    // Haversine distance from driver → pickup so the offer shows how far
    // the driver has to travel to start the trip (not just the trip
    // distance, which is pickup → dropoff).
    let pickupDistanceKm: number | null = null;
    if (
      location?.coords?.latitude != null &&
      location?.coords?.longitude != null &&
      incomingRide.pickup_lat != null &&
      incomingRide.pickup_lng != null
    ) {
      const R = 6371; // km
      const toRad = (d: number) => (d * Math.PI) / 180;
      const dLat = toRad(incomingRide.pickup_lat - location.coords.latitude);
      const dLng = toRad(incomingRide.pickup_lng - location.coords.longitude);
      const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(location.coords.latitude)) *
          Math.cos(toRad(incomingRide.pickup_lat)) *
          Math.sin(dLng / 2) ** 2;
      pickupDistanceKm = R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    return (
      <View style={styles.rideOfferOverlay}>
        <View style={styles.rideOfferContent}>
          {/* Countdown timer bar at top */}
          <View style={styles.timerBarContainer}>
            <View style={[styles.timerBar, { width: `${progress * 100}%` }]} />
          </View>

          {/* Header: Countdown + Title */}
          <View style={styles.offerHeader}>
            <View style={styles.countdownCircle}>
              <Text style={styles.countdownText}>{countdown}</Text>
            </View>
            <View style={{ flex: 1, marginLeft: 14 }}>
              <Text style={styles.rideOfferTitle}>{t('rideOffer.newRideRequest')}</Text>
              <View style={styles.rideTypeBadge}>
                <Ionicons name="car-sport" size={12} color={colors.primary} />
                <Text style={styles.rideTypeText}>{t('rideOffer.standardRide')}</Text>
              </View>
            </View>
            <View style={styles.fareContainer}>
              <Text style={styles.fareLabel}>{t('rideOffer.fare')}</Text>
              <Text style={styles.fareAmount}>${fare}</Text>
            </View>
          </View>

          {/* Route: Pickup & Dropoff */}
          <View style={styles.routeContainer}>
            <View style={styles.routeIconColumn}>
              <View style={[styles.routeDot, { backgroundColor: colors.primary }]} />
              <View style={styles.routeLine} />
              <View style={[styles.routeDot, { backgroundColor: '#FF4757' }]} />
            </View>
            <View style={styles.routeDetails}>
              <View style={styles.routeRow}>
                <Text style={styles.routeLabel}>{t('rideOffer.pickup')}</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>
                  {incomingRide.pickup_address || t('rideOffer.pickupLocation')}
                </Text>
              </View>
              <View style={styles.routeDivider} />
              <View style={styles.routeRow}>
                <Text style={styles.routeLabel}>{t('rideOffer.dropoff')}</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>
                  {incomingRide.dropoff_address || t('rideOffer.dropoffLocation')}
                </Text>
              </View>
            </View>
          </View>

          {/* Trip info badges */}
          <View style={styles.tripInfoRow}>
            {pickupDistanceKm != null && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="walk-outline" size={14} color={colors.primary} />
                <Text style={styles.tripInfoText}>
                  {pickupDistanceKm < 1
                    ? `${Math.round(pickupDistanceKm * 1000)} m to pickup`
                    : `${pickupDistanceKm.toFixed(1)} km to pickup`}
                </Text>
              </View>
            )}
            {incomingRide.rider_name && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="person-outline" size={14} color={colors.primary} />
                <Text style={styles.tripInfoText}>{incomingRide.rider_name}</Text>
                {incomingRide.rider_rating && (
                  <>
                    <Ionicons name="star" size={12} color="#FFD700" />
                    <Text style={styles.tripInfoText}>{incomingRide.rider_rating.toFixed(1)}</Text>
                  </>
                )}
              </View>
            )}
          </View>

          {/* Accept / Decline buttons */}
          <View style={styles.offerActions}>
            <TouchableOpacity
              style={styles.declineBtn}
              onPress={() => declineRide(incomingRide.ride_id)}
              activeOpacity={0.8}
            >
              <Ionicons name="close-circle" size={24} color="#FF4757" />
              <Text style={styles.declineText}>{t('rideOffer.decline')}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.acceptBtn}
              onPress={() => acceptRide(incomingRide.ride_id)}
              activeOpacity={0.8}
            >
              <Ionicons name="checkmark-circle" size={24} color="#fff" />
              <Text style={styles.acceptText}>{t('rideOffer.acceptRide')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    );
  };

  if (!location?.coords) {
    // Permission denied or the GPS fix failed: an infinite spinner here gave
    // the driver no way out (and nothing reaches the backend, so no logs).
    if (locationStatus === 'denied' || locationStatus === 'unavailable') {
      const denied = locationStatus === 'denied';
      return (
        <View style={[styles.container, { justifyContent: 'center', alignItems: 'center', paddingHorizontal: 32 }]}>
          <Ionicons name={denied ? 'location-outline' : 'navigate-outline'} size={48} color={colors.primary} />
          <Text style={styles.locationFallbackTitle}>
            {t(denied ? 'home.locationDeniedTitle' : 'home.locationUnavailableTitle')}
          </Text>
          <Text style={styles.locationFallbackBody}>
            {t(denied ? 'home.locationDeniedBody' : 'home.locationUnavailableBody')}
          </Text>
          {denied && (
            <TouchableOpacity style={styles.locationFallbackBtn} onPress={() => Linking.openSettings()} activeOpacity={0.8}>
              <Text style={styles.locationFallbackBtnText}>{t('home.openSettings')}</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity
            style={[styles.locationFallbackBtn, denied && styles.locationFallbackBtnSecondary]}
            onPress={() => refreshLocation(true)}
            activeOpacity={0.8}
          >
            <Text style={[styles.locationFallbackBtnText, denied && styles.locationFallbackBtnTextSecondary]}>
              {t('home.retryLocation')}
            </Text>
          </TouchableOpacity>
        </View>
      );
    }
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={{ color: colors.text, marginTop: 12, fontSize: 15 }}>{t('home.gettingLocation')}</Text>
      </View>
    );
  }

  return (
    <View style={styles.container} collapsable={false}>
      {/* WS server error banner — non-blocking, clears on next good message */}
      {wsError && (
        <View style={styles.wsErrorBanner}>
          <Text style={styles.wsErrorText} allowFontScaling={false}>{wsError}</Text>
        </View>
      )}

      {/* Map */}
      <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
      <MapView
        key={mapKey}
        ref={mapRef}
        style={styles.map}
        provider={MAP_PROVIDER}
        userInterfaceStyle={isDark ? "dark" : "light"}
        initialRegion={{
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
          latitudeDelta: 0.04,
          longitudeDelta: 0.04,
        }}
        showsUserLocation={false}
        showsMyLocationButton={false}
        onRegionChange={(region) => {
          currentRegionRef.current = {
            latitudeDelta: region.latitudeDelta,
            longitudeDelta: region.longitudeDelta,
          };
        }}
      >
        {/* Driver car marker */}
        {location?.coords && (
          <CarMarker
            coordinate={{
              latitude: location.coords.latitude,
              longitude: location.coords.longitude,
            }}
            heading={location.coords.heading}
            isOnline={isOnline}
            variant={markerVariant}
            imageUri={markerImageUri}
          />
        )}
        <RoutePins pickup={pickupPoint} dropoff={dropoffPoint} />

        {/* Route polyline.
            - ride_offered / trip_in_progress: reuse planned_route_polyline
              saved at ride creation (pickup → dropoff). Zero API calls.
            - navigating_to_pickup / arrived_at_pickup: driver → pickup,
              needs a live Directions call (not in saved polyline).
            Snap the driver's GPS to a 3-decimal grid (~110 m) so small
            jitter doesn't refetch the Directions API. */}
        {ride && (rideState === 'ride_offered' || rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && (() => {
          const savedPoly = (ride as any)?.planned_route_polyline || (ride as any)?.route_polyline;
          const hasSavedRoute = Array.isArray(savedPoly) && savedPoly.length >= 2;
          // The saved route always describes pickup -> dropoff. Before pickup,
          // only a live route (OSRM or Directions) can describe driver -> pickup.
          const useSavedRoute = hasSavedRoute &&
            (rideState === 'ride_offered' || rideState === 'trip_in_progress');
          const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive;

          const driverLat = location?.coords?.latitude != null ? Math.round(location.coords.latitude * 1000) / 1000 : null;
          const driverLng = location?.coords?.longitude != null ? Math.round(location.coords.longitude * 1000) / 1000 : null;

          // Road-snapped pickup the driver can actually reach (fallback to pin).
          const pNavLat = (ride as any).pickup_nav_lat ?? ride.pickup_lat;
          const pNavLng = (ride as any).pickup_nav_lng ?? ride.pickup_lng;
          let origin: { latitude: number; longitude: number };
          let destination: { latitude: number; longitude: number };
          if (rideState === 'ride_offered') {
            origin = { latitude: pNavLat, longitude: pNavLng };
            destination = { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng };
          } else if (rideState === 'trip_in_progress') {
            origin = driverLat != null && driverLng != null
              ? { latitude: driverLat, longitude: driverLng }
              : { latitude: pNavLat, longitude: pNavLng };
            destination = { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng };
          } else {
            origin = driverLat != null && driverLng != null
              ? { latitude: driverLat, longitude: driverLng }
              : { latitude: pNavLat, longitude: pNavLng };
            destination = { latitude: pNavLat, longitude: pNavLng };
          }

          return (
            <React.Fragment key={`route-${rideState}`}>
              {needsDirections && (
              <MapViewDirections
                key={directionsKey}
                origin={origin}
                destination={destination}
                apikey={GOOGLE_MAPS_API_KEY}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={(result) => {
                  setRouteCoords(result.coordinates);
                  setDirectionsFailed(false);
                  if (result.duration != null) setRouteEtaMinutes(Math.round(result.duration));
                  if (result.distance != null) setRouteDistanceKm(Math.round(result.distance * 10) / 10);
                  lastDirectionsFetchRef.current = {
                    lat: origin.latitude,
                    lng: origin.longitude,
                    ts: Date.now(),
                  };
                  if (directionsKey === 0 && mapRef.current && result.coordinates?.length > 1) {
                    mapRef.current.fitToCoordinates(result.coordinates, {
                      edgePadding: { top: 100, right: 60, bottom: 300, left: 60 },
                      animated: true,
                    });
                  }
                }}
                onError={(err) => {
                  console.warn('Directions error — falling back to straight line:', err);
                  setRouteCoords([]);
                  setDirectionsFailed(true);
                  if (mapRef.current) {
                    mapRef.current.fitToCoordinates([origin, destination], {
                      edgePadding: { top: 100, right: 60, bottom: 300, left: 60 },
                      animated: true,
                    });
                  }
                }}
              />
              )}
              {/* THE uniform route line. Draws the REAL road coords already
                  built for this phase (saved planned polyline / OSRM live-route /
                  Directions onReady) as one orange→red gradient — same component,
                  same colours as every other map. Falls back to a straight
                  pickup→destination gradient only when Directions failed and no
                  real path resolved, so the driver still sees where they're
                  headed instead of a blank map. */}
              <RouteLine
                path={routeCoords}
                pickup={directionsFailed ? origin : null}
                destination={directionsFailed ? destination : null}
              />
            </React.Fragment>
          );
        })()}

        {/* Service area boundary polygon */}
        {(() => {
          const rawPoly: Array<{ lat: number; lng: number }> | null | undefined =
            rideState === 'ride_offered'
              ? (incomingRide as any)?.service_area_polygon
              : (activeRide as any)?.service_area_polygon;
          if (!rawPoly || rawPoly.length < 3) return null;
          return (
            <Polygon
              coordinates={rawPoly.map(p => ({ latitude: p.lat, longitude: p.lng }))}
              strokeColor="rgba(0,212,170,0.65)"
              fillColor="rgba(0,212,170,0.07)"
              strokeWidth={2}
            />
          );
        })()}

        {/* Demand heatmap overlay — admin-controlled per service area */}
        {heatmapPoints.length > 0 && Platform.OS !== 'web' && (
          <Heatmap
            points={heatmapPoints}
            radius={35}
            opacity={0.65}
            gradient={{
              colors: ['#00D4AA', '#FFD700', '#FF6B35', '#FF2D2D'],
              startPoints: [0.1, 0.4, 0.65, 0.9],
              colorMapSize: 256,
            }}
          />
        )}
      </MapView>
      </View>

      {/* Top Bar */}
      <DriverTopBar driverData={driverData ?? undefined} user={user ?? undefined} isOnline={isOnline} connectionState={connectionState} surgeMultiplier={surgeMultiplier} wsLatency={wsLatency} earnings={earnings} unreadNotifCount={unreadNotifCount} />

      {/* SOS Button — visible during active ride */}
      {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && activeRide?.ride?.id && (
        <View style={{ position: 'absolute', top: insets.top + 56, right: 16, zIndex: 50 }}>
          <SOSButton
            rideId={activeRide.ride.id}
            discreet={discreetSosEnabled}
            // Must NOT swallow the error. SOSButton decides between "Alert
            // Sent" and its persistent "Not Sent — call 911" state purely on
            // whether this promise rejects, so catching here made every
            // failed driver SOS report success — the exact false
            // confirmation the component was built to prevent. The rider
            // path (rideStore.triggerEmergency) rethrows for this reason;
            // this call site did not. Let it propagate.
            onTrigger={async (rideId, lat, lng) => {
              await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng });
            }}
          />
        </View>
      )}

      {/* Map Controls */}
      <MapControls
        mapRef={mapRef}
        location={location}
        currentRegionRef={currentRegionRef}
        onRecenter={() => refreshLocation(false)}
      />

      {/* Bottom Panels */}
      {rideState === 'idle' && (
        <DriverIdlePanel
          isOnline={isOnline}
          onToggleOnline={toggleOnline}
          pulseAnim={pulseAnim}
        />
      )}
      {rideState === 'ride_offered' && incomingRide && (
        <RideOfferPanel
          incomingRide={incomingRide as any}
          countdownSeconds={countdown}
          maxCountdown={configuredCountdownSeconds || 15}
          pickupDistanceKm={
            location?.coords?.latitude != null && incomingRide.pickup_lat != null
              ? (() => {
                  const R = 6371;
                  const toRad = (d: number) => (d * Math.PI) / 180;
                  const dLat = toRad(incomingRide.pickup_lat - location.coords.latitude);
                  const dLng = toRad(incomingRide.pickup_lng - location.coords.longitude);
                  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(location.coords.latitude)) * Math.cos(toRad(incomingRide.pickup_lat)) * Math.sin(dLng / 2) ** 2;
                  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
                })()
              : null
          }
          isLoading={false}
          onAccept={() => acceptRide(incomingRide.ride_id)}
          onDecline={() => declineRide(incomingRide.ride_id)}
        />
      )}
      {(rideState === 'navigating_to_pickup' ||
        rideState === 'arrived_at_pickup' ||
        rideState === 'trip_in_progress') && (
        <ActiveRidePanel
          rideState={rideState}
          ride={activeRide?.ride as unknown as any || null}
          rider={activeRide?.rider || null}
          driverLocation={location}
          isLoading={isCancellingRide}
          otpInput={otpInput}
          setOtpInput={setOtpInput}
          onVerifyOTP={(otp) => verifyOTP(activeRide!.ride.id, otp)}
          onNavigate={openNavigation}
          // Pass current coordinates so driverStore.arriveAtPickup can run
          // its 100m haversine geofence check. Without the coords the check
          // silently skips and drivers can mark "arrived" from anywhere.
          onArriveAtPickup={() => arriveAtPickup(
            activeRide!.ride.id,
            location?.coords.latitude,
            location?.coords.longitude,
          )}
          onStartRide={() => startRide(activeRide!.ride.id)}
          onCompleteRide={async () => {
            await requestRideCompletion();
          }}
          onCancelRide={(reason) => cancelRide(activeRide!.ride.id, reason)}
          routeEtaMinutes={routeEtaMinutes}
          routeDistanceKm={routeDistanceKm}
          slideUpAnim={slideUpAnim}
          fadeAnim={fadeAnim}
        />
      )}
      {rideState === 'trip_completed' && (
        <TripCompletedPanel
          completedRide={completedRide}
          onDone={resetRideState}
          onRateRider={rateRider}
        />
      )}
      <Modal
        transparent
        animationType="fade"
        visible={completionConfirmationVisible}
        onRequestClose={() => setCompletionConfirmationVisible(false)}
      >
        <View
          style={{
            flex: 1,
            justifyContent: 'center',
            padding: 24,
            backgroundColor: 'rgba(0, 0, 0, 0.55)',
          }}
        >
          <View style={{ backgroundColor: colors.surface, borderRadius: 20, padding: 20, gap: 12 }}>
            <Text style={{ color: colors.text, fontSize: 18, fontWeight: '700' }}>Confirm trip completion</Text>
            <Text style={{ color: colors.textDim, fontSize: 14, lineHeight: 20 }}>
              Your final location is more than 1 km from the requested destination. Select the reason before completing this trip.
            </Text>
            {([
              ['Rider asked to stop here', 'rider_requested_stop'],
              ['Destination changed', 'changed_destination'],
              ['Emergency', 'emergency'],
              ['Location unavailable', 'location_unavailable'],
            ] as const).map(([label, confirmation]) => (
              <TouchableOpacity
                key={confirmation}
                onPress={() => void requestRideCompletion(confirmation)}
                style={{ backgroundColor: colors.surfaceLight, borderRadius: 12, paddingVertical: 13, paddingHorizontal: 14 }}
              >
                <Text style={{ color: colors.text, fontSize: 15, fontWeight: '600' }}>{label}</Text>
              </TouchableOpacity>
            ))}
            <TouchableOpacity
              onPress={() => setCompletionConfirmationVisible(false)}
              style={{ alignItems: 'center', paddingVertical: 10 }}
            >
              <Text style={{ color: colors.textDim, fontSize: 15, fontWeight: '600' }}>Keep trip open</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    map: {
      ...StyleSheet.absoluteFill,
    },
    wsErrorBanner: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      backgroundColor: 'rgba(239,68,68,0.92)',
      paddingVertical: 6,
      paddingHorizontal: 16,
      zIndex: 200,
      alignItems: 'center',
    },
    wsErrorText: {
      color: '#fff',
      fontSize: 12,
      fontWeight: '600',
    },
    locationFallbackTitle: {
      color: colors.text,
      marginTop: 16,
      fontSize: 17,
      fontWeight: '700',
      textAlign: 'center',
    },
    locationFallbackBody: {
      color: colors.textDim,
      marginTop: 8,
      fontSize: 14,
      lineHeight: 20,
      textAlign: 'center',
    },
    locationFallbackBtn: {
      marginTop: 16,
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 12,
      paddingHorizontal: 32,
    },
    locationFallbackBtnSecondary: {
      backgroundColor: 'transparent',
      borderWidth: 1,
      borderColor: colors.border,
    },
    locationFallbackBtnText: {
      color: '#fff',
      fontSize: 15,
      fontWeight: '600',
    },
    locationFallbackBtnTextSecondary: {
      color: colors.text,
    },
    // ── Rich Ride Offer Panel ──
    rideOfferOverlay: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      justifyContent: 'flex-end',
    },
    rideOfferContent: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 28,
      borderTopRightRadius: 28,
      paddingBottom: 34,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -8 },
      shadowOpacity: 0.12,
      shadowRadius: 20,
      elevation: 20,
    },
    timerBarContainer: {
      height: 4,
      backgroundColor: colors.surfaceLight,
      borderTopLeftRadius: 28,
      borderTopRightRadius: 28,
      overflow: 'hidden',
    },
    timerBar: {
      height: '100%',
      backgroundColor: colors.primary,
      borderRadius: 4,
    },
    offerHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 20,
      paddingTop: 18,
      paddingBottom: 14,
    },
    countdownCircle: {
      width: 52,
      height: 52,
      borderRadius: 26,
      borderWidth: 3,
      borderColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: 'rgba(0,212,170,0.06)',
    },
    countdownText: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.primary,
    },
    rideOfferTitle: {
      fontSize: 18,
      fontWeight: '700',
      color: colors.text,
    },
    rideTypeBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      marginTop: 3,
    },
    rideTypeText: {
      fontSize: 12,
      color: colors.primary,
      fontWeight: '600',
    },
    fareContainer: {
      alignItems: 'flex-end',
    },
    fareLabel: {
      fontSize: 11,
      color: colors.textDim,
      fontWeight: '500',
    },
    fareAmount: {
      fontSize: 28,
      fontWeight: '800',
      color: colors.primary,
      letterSpacing: -1,
    },
    // Route display
    routeContainer: {
      flexDirection: 'row',
      marginHorizontal: 20,
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 14,
      marginBottom: 12,
    },
    routeIconColumn: {
      alignItems: 'center',
      width: 20,
      paddingTop: 4,
    },
    routeDot: {
      width: 10,
      height: 10,
      borderRadius: 5,
    },
    routeLine: {
      width: 2,
      flex: 1,
      backgroundColor: colors.border,
      marginVertical: 4,
    },
    routeDetails: {
      flex: 1,
      marginLeft: 12,
    },
    routeRow: {
      paddingVertical: 2,
    },
    routeLabel: {
      fontSize: 10,
      fontWeight: '700',
      color: colors.textDim,
      letterSpacing: 0.5,
      marginBottom: 2,
    },
    routeAddress: {
      fontSize: 14,
      fontWeight: '500',
      color: colors.text,
    },
    routeDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 8,
    },
    // Trip info badges
    tripInfoRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
      paddingHorizontal: 20,
      marginBottom: 16,
    },
    tripInfoBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      backgroundColor: '#F0FDF9',
      paddingHorizontal: 10,
      paddingVertical: 6,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: '#D1FAE5',
    },
    tripInfoText: {
      fontSize: 12,
      fontWeight: '600',
      color: colors.text,
    },
    // Action buttons
    offerActions: {
      flexDirection: 'row',
      gap: 12,
      paddingHorizontal: 20,
    },
    declineBtn: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: '#FEF2F2',
      borderRadius: 16,
      paddingVertical: 16,
      gap: 8,
      borderWidth: 1,
      borderColor: '#FECACA',
    },
    declineText: {
      fontSize: 15,
      fontWeight: '600',
      color: '#FF4757',
    },
    acceptBtn: {
      flex: 2,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.primary,
      borderRadius: 16,
      paddingVertical: 16,
      gap: 8,
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.3,
      shadowRadius: 8,
      elevation: 6,
    },
    acceptText: {
      fontSize: 16,
      fontWeight: '700',
      color: '#fff',
    },
  });
}

export default function DriverDashboardScreen() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <DriverDashboard />
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
