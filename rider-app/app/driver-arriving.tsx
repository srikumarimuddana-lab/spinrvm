import React, { useCallback, useContext, useEffect, useState, useMemo, useRef } from 'react';
import { TrackBaseUrlContext } from './_layout';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Share,
  Platform,
  ActivityIndicator,
  BackHandler,
  Animated,
  Easing,
  useWindowDimensions,
  Image,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Polygon, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';
import { useRideStore } from '../store/rideStore';
import { RideStatus } from '../constants/rideStatus';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import CancelReasonSheet from '../components/CancelReasonSheet';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SOSButton } from '@shared/components/SOSButton';
import { CarMarker } from '@shared/components/CarMarker';
import { FreeCancelTimer } from '../components/FreeCancelTimer';
import { useResponsive } from '@shared/utils/responsive';
import BottomSheet, { BottomSheetScrollView } from '../components/SafeBottomSheet';
import { useAppResumeKey } from '../hooks/useAppResumeKey';
import { useTranslation } from '../i18n';
import { getRideMapCoords } from '../utils/rideMapCoords';
import { useAnimatedValue } from '../hooks/useAnimatedValue';

function DriverArrivingScreenContent() {
  const { height: SCREEN_HEIGHT } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { sf } = useResponsive();
  const styles = useMemo(() => createStyles(colors, sf, insets), [colors, sf, insets]);
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  // Public tracking URL base is served at runtime by GET /settings →
  // app_settings.track_base_url. Same source as ride-in-progress.tsx so
  // ops can rotate the share domain without a mobile rebuild.
  const trackBaseUrl = useContext(TrackBaseUrlContext);
  const {
    currentRide, currentDriver, fetchRide, triggerEmergency,
    driverEtaSeconds, cancelRide, clearRide,
    activeRideRouteCoords, activeDriverRouteCoords,
    setActiveRideRouteCoords, setActiveDriverRouteCoords,
  } = useRideStore();
  const { t } = useTranslation();
  const mapRef = useRef<MapView>(null);
  const bottomSheetRef = useRef<any>(null);
  const snapPoints = useMemo(() => ['40%', '65%'], []);
  const resumeKey = useAppResumeKey();

  const [mapEtaMinutes, setMapEtaMinutes] = useState<number | null>(null);
  const [countdownSeconds, setCountdownSeconds] = useState<number | null>(null);
  const [serviceAreaPolygons, setServiceAreaPolygons] = useState<{ latitude: number; longitude: number }[][]>([]);
  useEffect(() => {
    api.get('/service-areas').then((res: any) => {
      const areas: any[] = res.data || [];
      const polys = areas
        .filter((a: any) => Array.isArray(a.polygon) && a.polygon.length >= 3)
        .map((a: any) => a.polygon.map((p: any) => ({ latitude: p.lat, longitude: p.lng })));
      setServiceAreaPolygons(polys);
    }).catch(() => {});
  }, []);
  // Seed from store so returning to this screen after a brief navigation
  // (e.g. opening chat) shows the already-fetched route immediately.
  const [driverRouteCoords, setDriverRouteCoords] = useState<any[]>(activeDriverRouteCoords ?? []);
  const [rideRouteCoords, setRideRouteCoords] = useState<any[]>(() => {
    if (activeRideRouteCoords && activeRideRouteCoords.length > 1) return activeRideRouteCoords;
    const savedPoly = (currentRide as any)?.planned_route_polyline || (currentRide as any)?.route_polyline;
    if (Array.isArray(savedPoly) && savedPoly.length >= 2) {
      const coords = savedPoly
        .filter((p: any) => Array.isArray(p) && p.length >= 2)
        .map((p: any) => ({ latitude: p[0], longitude: p[1] }));
      if (coords.length >= 2) return coords;
    }
    return [];
  });
  const [isCancelling, setIsCancelling] = useState(false);
  const [driverPhotoError, setDriverPhotoError] = useState(false);
  const cancelInitiatedRef = useRef(false);
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean; title: string; message?: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', variant: 'info' });
  const [reasonVisible, setReasonVisible] = useState(false);

  // Searching animation
  const pulseAnim = useAnimatedValue(0);
  useEffect(() => {
    // Rewritten from `!currentRide || currentRide.status === ...` to read
    // only `currentRide?.status` so this narrows to a single field instead
    // of needing the whole `currentRide` object as a dep (which updates on
    // every ride poll and would restart the pulse loop far more often than
    // intended). status == null covers the "no ride yet" case the same way
    // `!currentRide` did.
    const status = currentRide?.status;
    if (status == null || status === RideStatus.SEARCHING || status === RideStatus.DRIVER_ASSIGNED) {
      Animated.loop(
        Animated.timing(pulseAnim, { toValue: 1, duration: 1800, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ).start();
    }
    // pulseAnim is a stable Animated.Value (useAnimatedValue, created once
    // via useRef and never reassigned) — adding it doesn't change firing.
  }, [currentRide?.status, pulseAnim]);

  const rideCoords = useMemo(() => getRideMapCoords(currentRide), [currentRide]);
  const cancellationFee = (currentRide as any)?.cancellation_fee ?? 3.0;
  // Hoisted out of handleCancel so it can be a plain primitive in that
  // callback's useCallback deps below, instead of re-deriving from
  // `(currentRide as any)?.grand_total` inline (which the linter flags as
  // "a complex expression" when used directly in a dep array).
  const fare = parseFloat((currentRide as any)?.grand_total || currentRide?.total_fare || '0');

  // Capture the driver's position the first time valid coords arrive.
  // Latches on first non-null GPS so MapViewDirections only fetches once per
  // driver assignment. Reset when the driver changes (reassignment after a
  // cancel) so the new driver's route is fetched from scratch.
  const [driverOriginSnapshot, setDriverOriginSnapshot] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);
  const prevDriverIdRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const newId = currentDriver?.id;
    if (prevDriverIdRef.current !== undefined && prevDriverIdRef.current !== newId) {
      // Driver was reassigned — discard the cached route for the old driver.
      setDriverOriginSnapshot(null);
      setActiveDriverRouteCoords(null);
    }
    prevDriverIdRef.current = newId;
    // setActiveDriverRouteCoords is a zustand store action (stable reference).
  }, [currentDriver?.id, setActiveDriverRouteCoords]);
  useEffect(() => {
    if (
      driverOriginSnapshot === null &&
      currentDriver?.lat != null &&
      currentDriver?.lng != null
    ) {
      // Guarded by driverOriginSnapshot === null above. Now that
      // driverOriginSnapshot IS listed as a dep, setting it here causes one
      // extra invocation of this effect right after — on that run the guard
      // is false (driverOriginSnapshot is no longer null), so it's a no-op.
      // Still fires the real snapshot capture exactly once per driver
      // (reset to null on reassignment by the effect above) — same
      // behavior as before, just an honest dep array instead of a rule
      // suppression.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDriverOriginSnapshot({
        latitude: currentDriver.lat,
        longitude: currentDriver.lng,
      });
    }
  }, [currentDriver?.lat, currentDriver?.lng, driverOriginSnapshot]);

  // Stable pickup→dropoff refs — ride endpoints never change mid-ride.
  const rideRouteOrigin = useMemo(
    () =>
      rideCoords
        ? { latitude: rideCoords.pickupLat, longitude: rideCoords.pickupLng }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentRide?.id, rideCoords?.pickupLat, rideCoords?.pickupLng],
  );
  const rideRouteDestination = useMemo(
    () =>
      rideCoords
        ? { latitude: rideCoords.dropoffLat, longitude: rideCoords.dropoffLng }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentRide?.id, rideCoords?.dropoffLat, rideCoords?.dropoffLng],
  );
  const freeCancelWindowSeconds = (currentRide as any)?.free_cancel_window_seconds ?? 120;

  // ── ETA logic ──
  useEffect(() => {
    // Resets the countdown whenever the backend sends a fresh ETA;
    // countdownSeconds itself isn't a dep, so no loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (driverEtaSeconds !== null) setCountdownSeconds(driverEtaSeconds);
  }, [driverEtaSeconds]);

  useEffect(() => {
    if (countdownSeconds === null || countdownSeconds <= 0) return;
    const timer = setTimeout(() => setCountdownSeconds(s => (s !== null && s > 0 ? s - 1 : s)), 1000);
    return () => clearTimeout(timer);
  }, [countdownSeconds]);

  const etaLabel = useMemo(() => {
    if (countdownSeconds !== null) {
      if (countdownSeconds <= 30) return 'Arriving shortly';
      if (countdownSeconds < 120) return `${countdownSeconds}s`;
      return `${Math.ceil(countdownSeconds / 60)} min`;
    }
    if (mapEtaMinutes !== null) return `${mapEtaMinutes} min`;
    return null;
  }, [countdownSeconds, mapEtaMinutes]);

  const isSearching = !currentRide || currentRide.status === RideStatus.SEARCHING || currentRide.status === RideStatus.DRIVER_ASSIGNED;
  const hasDriver = currentRide?.status === RideStatus.DRIVER_ACCEPTED ||
    currentRide?.status === RideStatus.DRIVER_ARRIVED ||
    currentRide?.status === RideStatus.IN_PROGRESS;

  // ── Fetch ride on mount ──
  useEffect(() => {
    if (rideId) fetchRide(rideId);
    const interval = setInterval(() => {
      if (rideId && !cancelInitiatedRef.current) fetchRide(rideId);
    }, 5000);
    return () => clearInterval(interval);
    // fetchRide is a zustand store action (stable reference).
  }, [rideId, fetchRide]);

  // ── Status-based navigation ──
  useEffect(() => {
    // Rewritten to read only currentRide?.id/status (both now listed below)
    // instead of the whole currentRide object via an unnarrowed `!currentRide`
    // check — this object updates on every ride poll and would re-run this
    // navigation effect far more often than the status/id transitions it
    // actually cares about.
    if (currentRide?.id !== rideId) return;
    const status = currentRide?.status;
    if (status === RideStatus.DRIVER_ARRIVED) {
      router.replace({ pathname: '/driver-arrived', params: { rideId } });
    } else if (status === RideStatus.IN_PROGRESS) {
      router.replace({ pathname: '/ride-in-progress', params: { rideId } });
    } else if (status === 'completed') {
      router.replace({ pathname: '/ride-completed', params: { rideId } });
    } else if (status === 'cancelled') {
      clearRide();
      router.replace('/(tabs)' as any);
    }
    // clearRide is a zustand action (stable); router is expo-router's stable
    // singleton; rideId is the route param. Adding currentRide?.id closes a
    // real gap: previously this effect only re-checked on status changes, so
    // if the ride id changed (re-assignment) without status also changing in
    // the same tick, the id/rideId mismatch guard could momentarily use a
    // stale comparison.
  }, [currentRide?.status, currentRide?.id, rideId, router, clearRide]);

  // ── Map fitting ──
  useEffect(() => {
    // rideCoords is derived from currentRide via getRideMapCoords(), which
    // returns null whenever currentRide is null/falsy — so checking
    // rideCoords already implies currentRide exists; no need to also guard
    // on (or depend on) currentRide itself.
    if (rideCoords?.pickupLat != null && rideCoords?.pickupLng != null && mapRef.current) {
      if (currentDriver?.lat != null && currentDriver?.lng != null) {
        mapRef.current.fitToCoordinates(
          [
            { latitude: rideCoords.pickupLat, longitude: rideCoords.pickupLng },
            { latitude: currentDriver.lat, longitude: currentDriver.lng },
          ],
          { edgePadding: { top: 80, right: 50, bottom: SCREEN_HEIGHT * 0.5, left: 50 }, animated: true },
        );
      }
    }
    // SCREEN_HEIGHT is a primitive from useWindowDimensions() — adding it
    // means the map correctly refits on an orientation change too (a real,
    // if minor, improvement — previously a rotation wouldn't have re-fit the
    // bottom edge padding until some other dep also changed).
  }, [currentDriver?.lat, currentDriver?.lng, rideCoords?.pickupLat, rideCoords?.pickupLng, SCREEN_HEIGHT]);

  // ── Cancel handler (fixed: errors are caught, user stays on page) ──
  const performCancel = useCallback(async (reason?: string) => {
    cancelInitiatedRef.current = true;
    setIsCancelling(true);
    try {
      await cancelRide(reason);
      clearRide();
      router.replace('/(tabs)' as any);
    } catch (err) {
      cancelInitiatedRef.current = false;
      setIsCancelling(false);
      showToast('Cancel Failed', getApiErrorMessage(err, 'Could not cancel the ride. Please try again.'), 'danger');
    }
  }, [cancelRide, clearRide, router]);

  const handleCancel = useCallback(() => {
    const status = currentRide?.status;

    // A driver is involved → collect a reason first; plain search cancels skip it.
    const doCancel = () => setReasonVisible(true);

    if (status === RideStatus.IN_PROGRESS) {
      setConfirmSheet({
        visible: true, title: 'Ride in progress',
        message: `If you cancel now, you will be charged the full fare of $${fare.toFixed(2)}.`,
        variant: 'danger',
        buttons: [
          { text: `Cancel & Pay $${fare.toFixed(2)}`, style: 'destructive', onPress: doCancel },
          { text: 'Continue Ride', style: 'cancel' },
        ],
      });
    } else if (status === RideStatus.DRIVER_ARRIVED) {
      setConfirmSheet({
        visible: true, title: 'Driver is waiting',
        message: `A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`,
        variant: 'warning',
        buttons: [
          { text: `Cancel & Pay $${cancellationFee.toFixed(2)}`, style: 'destructive', onPress: doCancel },
          { text: 'Keep Ride', style: 'cancel' },
        ],
      });
    } else if (status === RideStatus.DRIVER_ACCEPTED) {
      setConfirmSheet({
        visible: true, title: 'Cancel ride?',
        message: 'Your driver is on the way. Cancel for free right now.',
        variant: 'warning',
        buttons: [
          { text: 'Cancel (Free)', style: 'destructive', onPress: doCancel },
          { text: 'Keep Ride', style: 'cancel' },
        ],
      });
    } else {
      setConfirmSheet({
        visible: true, title: 'Cancel search?',
        message: 'Stop looking for a driver? No charge.',
        variant: 'info',
        buttons: [
          { text: 'Cancel', onPress: () => performCancel() },
          { text: 'Keep searching', style: 'cancel' },
        ],
      });
    }
    // fare/cancellationFee are plain numbers derived from currentRide at
    // component scope (already used the same way elsewhere in this file);
    // setConfirmSheet is a setState setter (stable); performCancel is now a
    // useCallback below with its own correct deps.
  }, [currentRide?.status, fare, cancellationFee, performCancel]);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { handleCancel(); return true; });
    return () => sub.remove();
    // handleCancel is now a useCallback (see above) with its own correct
    // deps, so depending on it here keeps the back-button handler's dialog
    // text (fare/cancellationFee) fresh instead of the previous
    // [currentRide?.status]-only tracking, which could miss a fare/fee
    // change that didn't happen to coincide with a status change.
  }, [handleCancel]);

  // ── Handlers ──
  // No call handler: rider↔driver contact is chat-only — phone numbers are
  // never shared between parties (backend /call endpoint removed).
  const handleMessage = () => router.push({ pathname: '/chat-driver', params: { rideId } });

  const handleShareTrip = async () => {
    // Fail loud if ops hasn't configured a tracking URL yet — silently
    // sending recipients to a hardcoded spinr-track.app would mislead
    // them when the admin has moved tracking to a different domain.
    if (!trackBaseUrl) {
      showToast(
        'Tracking Not Configured',
        'Live trip tracking is not set up yet. Please contact support.',
        'warning',
      );
      return;
    }
    const trackLink = `${trackBaseUrl}/${rideId || 'demo'}`;
    const info = `🚗 SPINR RIDE\n\n👤 Driver: ${currentDriver?.name || 'Searching...'}\n🚙 ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || ''} ${currentDriver?.vehicle_model || ''}\n📋 Plate: ${currentDriver?.license_plate || 'Pending'}\n\n📍 From: ${currentRide?.pickup_address || ''}\n📍 To: ${currentRide?.dropoff_address || ''}\n⏱️ ETA: ${etaLabel || 'Calculating'}\n\nTrack: ${trackLink}`;
    try { await Share.share({ message: info.trim(), title: 'My Spinr Ride' }); } catch {}
  };

  const handleCopyDetails = async () => {
    const d = `Driver: ${currentDriver?.name || 'Searching'} | ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || ''} ${currentDriver?.vehicle_model || ''} | Plate: ${currentDriver?.license_plate || 'N/A'}`;
    await Clipboard.setStringAsync(d);
    showToast('Copied!', 'Driver details copied to clipboard.', 'success');
  };

  // ── Render ──
  return (
    <View style={styles.container}>
      {/* ═══ Full-screen map ═══ */}
      {currentRide && rideCoords ? (
        <MapView
          ref={mapRef}
          provider={Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined}
          style={StyleSheet.absoluteFill}
          initialRegion={{
            latitude: rideCoords.pickupLat, longitude: rideCoords.pickupLng,
            latitudeDelta: 0.02, longitudeDelta: 0.02,
          }}
        >
          {/* Driver → pickup route: origin is snapshotted on first driver fix so
              MapViewDirections only calls Directions API once per assigned driver.
              The car marker updates live; re-fetching the route on every GPS ping
              would cost ~$0.10 extra per pickup phase for no visible benefit since
              driverEtaSeconds from the backend already drives the ETA countdown. */}
          {/* Only fetch if we don't already have coords from a previous screen visit */}
          {driverOriginSnapshot && activeDriverRouteCoords === null && (
            <MapViewDirections
              origin={driverOriginSnapshot}
              destination={{ latitude: rideCoords.pickupLat, longitude: rideCoords.pickupLng }}
              apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || ''}
              strokeWidth={0} strokeColor="transparent"
              onReady={(r: any) => {
                if (!r.coordinates?.length) return;
                setMapEtaMinutes(Math.ceil(r.duration));
                // Intentionally NOT writing lastEtaMin here: that value is the
                // driver→pickup duration and must not seed the in-trip ETA on
                // the next screen (ride-in-progress reads lastEtaMin on mount).
                setDriverRouteCoords(r.coordinates);
                setActiveDriverRouteCoords(r.coordinates);
              }}
            />
          )}
          {/* Driver → pickup leg — same real coords, drawn via the shared gradient. */}
          <RouteLine path={driverRouteCoords} />

          {/* Pickup → dropoff route: use saved polyline or cached coords first;
              only call Directions API as a last resort. */}
          {rideRouteOrigin && rideRouteDestination && activeRideRouteCoords === null && rideRouteCoords.length < 2 && (
            <MapViewDirections
              origin={rideRouteOrigin}
              destination={rideRouteDestination}
              apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || ''}
              strokeWidth={0} strokeColor="transparent"
              onReady={(r: any) => {
                if (!r.coordinates?.length) return;
                setRideRouteCoords(r.coordinates);
                setActiveRideRouteCoords(r.coordinates);
              }}
            />
          )}
          {/* Pickup → dropoff route — real coords, shared gradient line + pins. */}
          <RouteLine
            path={rideRouteCoords}
            pickup={rideRouteOrigin}
            destination={rideRouteDestination}
          />
          <RoutePins
            pickup={{ latitude: rideCoords.pickupLat, longitude: rideCoords.pickupLng }}
            dropoff={{ latitude: rideCoords.dropoffLat, longitude: rideCoords.dropoffLng }}
          />

          {/* Driver car */}
          {currentDriver?.lat != null && currentDriver?.lng != null && (
            <CarMarker coordinate={{ latitude: currentDriver.lat, longitude: currentDriver.lng }}
              heading={(currentDriver as any).heading} size={44} zIndex={105} />
          )}
          {serviceAreaPolygons.map((coords, idx) => (
            <Polygon
              key={`sa-poly-${idx}`}
              coordinates={coords}
              strokeColor="rgba(0,212,170,0.65)"
              fillColor="rgba(0,212,170,0.07)"
              strokeWidth={2}
            />
          ))}
        </MapView>
      ) : (
        <View style={styles.mapPlaceholder}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>{currentRide ? 'Loading map...' : 'Loading ride...'}</Text>
        </View>
      )}

      {/* ═══ Floating header ═══ */}
      <View style={[styles.floatingHeader, { top: insets.top + 8 }]}>
        <TouchableOpacity style={styles.floatingBtn} onPress={handleCancel}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>

        {!isSearching && etaLabel && (
          <View style={styles.etaPill}>
            <View style={styles.greenDot} />
            <Text style={styles.etaText} allowFontScaling={false}>{etaLabel}</Text>
          </View>
        )}

        {!isSearching && <SOSButton rideId={rideId as string} onTrigger={triggerEmergency} t={t} />}
      </View>

      {/* ═══ Bottom sheet ═══ */}
      <BottomSheet
        key={resumeKey}
        ref={bottomSheetRef}
        index={0}
        snapPoints={snapPoints}
        backgroundStyle={styles.sheet}
        handleIndicatorStyle={styles.sheetHandle}
        enableOverDrag={false}
      >
        <BottomSheetScrollView
          bounces={false}
          overScrollMode="never"
          contentContainerStyle={{ paddingBottom: Math.max(insets.bottom, 16) + 8 }}>

          {/* ── SEARCHING STATE ── */}
          {isSearching && (
            <View style={styles.searchingSection}>
              {/* Animated pulse */}
              <Animated.View style={[styles.pulseRing, {
                opacity: pulseAnim.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0.6, 0.2, 0.6] }),
                transform: [{ scale: pulseAnim.interpolate({ inputRange: [0, 0.5, 1], outputRange: [1, 1.3, 1] }) }],
              }]} />
              <View style={styles.searchingIcon}>
                <Ionicons name="car" size={28} color={colors.primary} />
              </View>

              <Text style={styles.searchingTitle}>Looking for a driver</Text>
              <Text style={styles.searchingSubtitle}>
                This usually takes less than 2 minutes
              </Text>

              {/* Trip summary */}
              <View style={styles.tripSummary}>
                <View style={styles.tripDotLine}>
                  <View style={[styles.tripDot, { backgroundColor: '#10B981' }]} />
                  <View style={styles.tripConnector} />
                  <View style={[styles.tripDot, { backgroundColor: colors.primary }]} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.tripAddress} numberOfLines={1}>{currentRide?.pickup_address || 'Pickup'}</Text>
                  <View style={{ height: 16 }} />
                  <Text style={styles.tripAddress} numberOfLines={1}>{currentRide?.dropoff_address || 'Dropoff'}</Text>
                </View>
              </View>

              {/* Cancel button — prominent during searching */}
              <TouchableOpacity
                style={styles.cancelSearchBtn}
                onPress={handleCancel}
                disabled={isCancelling}
                activeOpacity={0.7}
              >
                {isCancelling ? (
                  <ActivityIndicator color={colors.primary} size="small" />
                ) : (
                  <Text style={styles.cancelSearchText}>Cancel search</Text>
                )}
              </TouchableOpacity>
            </View>
          )}

          {/* ── DRIVER ASSIGNED/ARRIVING STATE ── */}
          {hasDriver && (
            <>
              {/* Share trip banner */}
              <TouchableOpacity style={styles.shareBanner} onPress={handleShareTrip} activeOpacity={0.8}>
                <View style={styles.shareBannerIcon}>
                  <Ionicons name="shield-checkmark" size={18} color="#FFF" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.shareBannerTitle}>Share your trip</Text>
                  <Text style={styles.shareBannerSub}>Let someone track your live location</Text>
                </View>
                <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
              </TouchableOpacity>

              {/* Driver card */}
              <View style={styles.driverCard}>
                <View style={styles.driverCardHeader}>
                  <Text style={styles.driverCardLabel}>YOUR DRIVER</Text>
                  <TouchableOpacity onPress={handleCopyDetails} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                    <Ionicons name="copy-outline" size={16} color={colors.textDim} />
                  </TouchableOpacity>
                </View>

                <View style={styles.driverRow}>
                  <View style={styles.avatar}>
                    {currentDriver?.photo_url && !driverPhotoError ? (
                      <Image
                        source={{ uri: currentDriver.photo_url }}
                        style={styles.avatarImg}
                        resizeMode="cover"
                        onError={() => setDriverPhotoError(true)}
                      />
                    ) : (
                      <Ionicons name="person" size={24} color={colors.textDim} />
                    )}
                    <View style={styles.ratingBadge}>
                      <Ionicons name="star" size={9} color="#FFB800" />
                      <Text style={styles.ratingText}>{currentDriver?.rating || 'New'}</Text>
                    </View>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.driverName}>{currentDriver?.name || 'Driver'}</Text>
                    <Text style={styles.driverTrips}>{currentDriver?.total_rides || 0} trips</Text>
                  </View>
                </View>

                <View style={styles.vehicleSection}>
                  <View style={styles.vehicleRow}>
                    <Ionicons name="car" size={18} color={colors.primary} />
                    <Text style={styles.vehicleText}>
                      {currentDriver?.vehicle_color || ''} {currentDriver?.vehicle_make || ''} {currentDriver?.vehicle_model || ''}
                    </Text>
                  </View>
                  <View style={styles.plateBox}>
                    <Text style={styles.plateText} allowFontScaling={false}>{currentDriver?.license_plate || '---'}</Text>
                  </View>
                </View>
              </View>

              {/* Pickup PIN */}
              {currentRide?.pickup_otp && (
                <View style={styles.pinCard}>
                  <Text style={styles.pinLabel}>SHARE THIS PIN WITH YOUR DRIVER</Text>
                  <View style={styles.pinBoxes}>
                    {[0, 1, 2, 3].map(i => (
                      <View key={i} style={styles.pinBox}>
                        <Text style={styles.pinDigit} allowFontScaling={false}>
                          {currentRide.pickup_otp?.[i] || '•'}
                        </Text>
                      </View>
                    ))}
                  </View>
                </View>
              )}

              {/* Action buttons — Message, Share (no Call: chat-only contact) */}
              <View style={styles.actionRow}>
                <TouchableOpacity style={styles.actionBtnPrimary} onPress={handleMessage}>
                  <Ionicons name="chatbubble" size={18} color="#FFF" />
                  <Text style={styles.actionBtnPrimaryText}>Message</Text>
                </TouchableOpacity>

                <TouchableOpacity style={styles.actionBtnOutline} onPress={handleShareTrip}>
                  <Ionicons name="share-outline" size={20} color={colors.text} />
                </TouchableOpacity>
              </View>

              {/* Free cancel timer */}
              {(currentRide?.status === RideStatus.DRIVER_ACCEPTED ||
                currentRide?.status === RideStatus.DRIVER_ARRIVED) && (
                <FreeCancelTimer
                  driverAcceptedAt={(currentRide as any)?.driver_accepted_at}
                  rideStatus={currentRide?.status}
                  freeCancelWindowSeconds={freeCancelWindowSeconds}
                  cancellationFee={cancellationFee}
                  onExpire={() => showToast(
                    'Free cancel window closed',
                    `A $${cancellationFee.toFixed(2)} fee now applies if you cancel.`,
                    'warning',
                  )}
                />
              )}

              {/* Trip route */}
              <View style={styles.tripSummary}>
                <View style={styles.tripDotLine}>
                  <View style={[styles.tripDot, { backgroundColor: '#10B981' }]} />
                  <View style={styles.tripConnector} />
                  <View style={[styles.tripDot, { backgroundColor: colors.primary }]} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.tripAddress} numberOfLines={1}>{currentRide?.pickup_address}</Text>
                  <View style={{ height: 16 }} />
                  <Text style={styles.tripAddress} numberOfLines={1}>{currentRide?.dropoff_address}</Text>
                </View>
              </View>
            </>
          )}

          {/* DEV CONTROLS */}
          {__DEV__ && currentRide && (
            <View style={styles.devBar}>
              <Text style={styles.devLabel}>DEV: {currentRide.status}</Text>
              {currentRide.status === RideStatus.SEARCHING && (
                <TouchableOpacity style={styles.devBtn} onPress={async () => {
                  try { await useRideStore.getState().simulateDriverArrival(); } catch {}
                  if (rideId) fetchRide(rideId);
                }}><Text style={styles.devBtnText}>Assign</Text></TouchableOpacity>
              )}
              {(currentRide.status === RideStatus.DRIVER_ASSIGNED || currentRide.status === RideStatus.DRIVER_ACCEPTED) && (
                <TouchableOpacity style={styles.devBtn} onPress={async () => {
                  try { await api.post(`/drivers/rides/${currentRide.id}/arrive`); } catch {}
                  if (rideId) fetchRide(rideId);
                }}><Text style={styles.devBtnText}>Arrive</Text></TouchableOpacity>
              )}
              {currentRide.status === RideStatus.DRIVER_ARRIVED && (
                <TouchableOpacity style={styles.devBtn} onPress={async () => {
                  try { await api.post(`/drivers/rides/${currentRide.id}/start`); } catch {}
                  if (rideId) fetchRide(rideId);
                }}><Text style={styles.devBtnText}>Start</Text></TouchableOpacity>
              )}
              {currentRide.status === RideStatus.IN_PROGRESS && (
                <TouchableOpacity style={styles.devBtn} onPress={async () => {
                  try { await api.post(`/drivers/rides/${currentRide.id}/complete`); } catch {}
                  if (rideId) fetchRide(rideId);
                }}><Text style={styles.devBtnText}>Complete</Text></TouchableOpacity>
              )}
            </View>
          )}
        </BottomSheetScrollView>
      </BottomSheet>

      <ConfirmSheet
        visible={confirmSheet.visible} title={confirmSheet.title} message={confirmSheet.message}
        variant={confirmSheet.variant} buttons={confirmSheet.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))} />
      <CancelReasonSheet
        visible={reasonVisible}
        onConfirm={performCancel}
        onClose={() => setReasonVisible(false)}
      />
    </View>
  );
}


export default function DriverArrivingScreen() {
  return (
    <ErrorBoundary>
      <DriverArrivingScreenContent />
    </ErrorBoundary>
  );
}

function createStyles(colors: ThemeColors, sf: (s: number) => number, insets: { top: number; bottom: number }) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: '#E8E8E8' },

    mapPlaceholder: {
      flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surfaceLight,
    },
    loadingText: {
      marginTop: 12, fontSize: sf(14), fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim,
    },
    // ── Floating header ──
    floatingHeader: {
      position: 'absolute', left: 0, right: 0, zIndex: 20,
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16,
    },
    floatingBtn: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: '#FFF',
      justifyContent: 'center', alignItems: 'center',
      shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.15, shadowRadius: 6, elevation: 6,
    },
    etaPill: {
      flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFF',
      paddingHorizontal: 16, paddingVertical: 10, borderRadius: 24,
      shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 6, elevation: 6,
    },
    greenDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#10B981', marginRight: 8 },
    etaText: { fontSize: sf(15), fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },

    // ── Bottom sheet ──
    sheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24, borderTopRightRadius: 24,
      shadowColor: '#000', shadowOffset: { width: 0, height: -4 }, shadowOpacity: 0.12, shadowRadius: 12, elevation: 16,
    },
    sheetHandle: {
      width: 40, height: 4, borderRadius: 2, backgroundColor: '#D1D5DB',
    },

    // ── Searching state ──
    searchingSection: { alignItems: 'center', paddingHorizontal: 20, paddingTop: 20, paddingBottom: 8 },
    pulseRing: {
      position: 'absolute', top: 10, width: 80, height: 80, borderRadius: 40,
      backgroundColor: colors.primary + '20',
    },
    searchingIcon: {
      width: 64, height: 64, borderRadius: 32,
      backgroundColor: colors.primary + '15',
      justifyContent: 'center', alignItems: 'center', marginBottom: 16,
    },
    searchingTitle: {
      fontSize: sf(20), fontFamily: 'PlusJakartaSans_700Bold', color: colors.text, marginBottom: 6,
    },
    searchingSubtitle: {
      fontSize: sf(14), fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginBottom: 20, textAlign: 'center',
    },
    cancelSearchBtn: {
      paddingVertical: 14, paddingHorizontal: 32, borderRadius: 28,
      borderWidth: 1.5, borderColor: colors.border, marginTop: 8,
    },
    cancelSearchText: {
      fontSize: sf(15), fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
    },

    // ── Driver card ──
    shareBanner: {
      flexDirection: 'row', alignItems: 'center', marginHorizontal: 16, marginTop: 12, marginBottom: 12,
      backgroundColor: '#FFF5F5', padding: 14, borderRadius: 14, borderWidth: 1, borderColor: '#FFE0E0', gap: 12,
    },
    shareBannerIcon: {
      width: 34, height: 34, borderRadius: 17, backgroundColor: colors.primary,
      justifyContent: 'center', alignItems: 'center',
    },
    shareBannerTitle: { fontSize: sf(14), fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    shareBannerSub: { fontSize: sf(12), fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 1 },

    driverCard: {
      marginHorizontal: 16, marginBottom: 12, backgroundColor: colors.surfaceLight,
      borderRadius: 16, padding: 16,
    },
    driverCardHeader: {
      flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12,
    },
    driverCardLabel: {
      fontSize: sf(11), fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary, letterSpacing: 0.5,
    },
    driverRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 14 },
    avatar: {
      width: 52, height: 52, borderRadius: 26, backgroundColor: '#E8E8E8',
      justifyContent: 'center', alignItems: 'center', position: 'relative',
    },
    avatarImg: { width: 52, height: 52, borderRadius: 26 },
    ratingBadge: {
      position: 'absolute', bottom: -3, left: -3, flexDirection: 'row', alignItems: 'center',
      backgroundColor: '#FFF', paddingHorizontal: 5, paddingVertical: 2, borderRadius: 8,
      shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.08, shadowRadius: 2,
    },
    ratingText: { fontSize: sf(10), fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text, marginLeft: 2 },
    driverName: { fontSize: sf(18), fontFamily: 'PlusJakartaSans_700Bold', color: colors.text },
    driverTrips: { fontSize: sf(12), fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2 },

    vehicleSection: {
      flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
      paddingTop: 12, borderTopWidth: 1, borderTopColor: '#E8E8E8',
    },
    vehicleRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 },
    vehicleText: { fontSize: sf(14), fontFamily: 'PlusJakartaSans_500Medium', color: colors.text },
    plateBox: {
      backgroundColor: '#F3F4F6', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8,
      borderWidth: 1, borderColor: '#E5E7EB',
    },
    plateText: { fontSize: sf(15), fontFamily: 'PlusJakartaSans_700Bold', color: colors.text, letterSpacing: 1.5 },

    // ── PIN ──
    pinCard: {
      marginHorizontal: 16, marginBottom: 12, padding: 16, alignItems: 'center',
      backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: '#EEE',
      shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.04, shadowRadius: 6, elevation: 2,
    },
    pinLabel: { fontSize: sf(11), fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary, letterSpacing: 1, marginBottom: 12 },
    pinBoxes: { flexDirection: 'row', gap: 10 },
    pinBox: {
      width: 50, height: 58, borderRadius: 12, backgroundColor: '#F8F9FA',
      borderWidth: 1.5, borderColor: colors.primary, justifyContent: 'center', alignItems: 'center',
    },
    pinDigit: { fontSize: sf(26), fontFamily: 'PlusJakartaSans_700Bold', color: colors.text },

    // ── Action buttons ──
    actionRow: { flexDirection: 'row', gap: 10, marginHorizontal: 16, marginBottom: 14 },
    actionBtnPrimary: {
      flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      backgroundColor: colors.primary, paddingVertical: 14, borderRadius: 28, gap: 8,
    },
    actionBtnPrimaryText: { fontSize: sf(15), fontFamily: 'PlusJakartaSans_600SemiBold', color: '#FFF' },
    actionBtnOutline: {
      width: 50, height: 50, borderRadius: 25, borderWidth: 1.5, borderColor: colors.border,
      justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surface,
    },

    // ── Trip summary ──
    tripSummary: {
      flexDirection: 'row', marginHorizontal: 16, marginBottom: 14, paddingVertical: 12,
      paddingHorizontal: 14, backgroundColor: colors.surfaceLight, borderRadius: 14, gap: 12,
    },
    tripDotLine: { alignItems: 'center', paddingTop: 2 },
    tripDot: { width: 10, height: 10, borderRadius: 5 },
    tripConnector: { width: 2, height: 20, backgroundColor: '#D1D5DB', marginVertical: 3 },
    tripAddress: { fontSize: sf(14), fontFamily: 'PlusJakartaSans_500Medium', color: colors.text },

    // ── Dev ──
    devBar: {
      flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8,
      marginHorizontal: 16, marginTop: 8, padding: 10,
      backgroundColor: '#FEF3C7', borderRadius: 10, borderWidth: 1, borderColor: '#F59E0B',
    },
    devLabel: { fontSize: 11, fontWeight: '700', color: '#92400E' },
    devBtn: { backgroundColor: '#F59E0B', paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6 },
    devBtnText: { fontSize: 11, fontWeight: '700', color: '#FFF' },
  });
}
