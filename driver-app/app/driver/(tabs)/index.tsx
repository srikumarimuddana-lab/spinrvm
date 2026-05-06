import React, { useState, useEffect, useRef, useMemo } from 'react';
import { View, Text, StyleSheet, Platform, Linking, Animated, TouchableOpacity, ActivityIndicator, AppState } from 'react-native';
import CustomAlert from '@shared/components/CustomAlert';
import MapView, { Marker, Polyline, Heatmap, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { Ionicons } from '@expo/vector-icons';
import { useDriverStore } from '../../../store/driverStore';
import { useAuthStore } from '@shared/store/authStore';
import {
  DriverTopBar,
  DriverIdlePanel,
  ActiveRidePanel,
  TripCompletedPanel,
  MapControls,
} from '../../../components/dashboard';
import { useDriverDashboard } from '../../../hooks/useDriverDashboard';
import { CarMarker } from '../../../components/CarMarker';
import { SOSButton } from '@shared/components/SOSButton';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { useLanguageStore } from '../../../store/languageStore';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';

// Use Google Maps on Android, Apple Maps (native) on iOS
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

function DriverDashboard() {
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

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

  const { t } = useLanguageStore();

  const {
    isOnline,
    connectionState,
    location,
    otpInput,
    setOtpInput,
    toggleOnline,
    openNavigation,
    mapRef,
    currentRegionRef,
    pulseAnim,
    slideUpAnim,
    fadeAnim,
    dashAlert,
    showDashAlert,
    closeDashAlert,
    wsError,
    refreshLocation,
  } = useDriverDashboard();

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

  // Live ETA from Google Directions — updated every 30s via the
  // directionsKey mechanism below.
  const [routeEtaMinutes, setRouteEtaMinutes] = useState<number | null>(null);
  const [routeDistanceKm, setRouteDistanceKm] = useState<number | null>(null);

  // Force MapViewDirections to re-compute every 30 seconds by changing
  // a key prop. This gives the driver a road-aware ETA that accounts
  // for traffic and route changes without hammering the Directions API.
  const [directionsKey, setDirectionsKey] = useState(0);
  useEffect(() => {
    if (rideState !== 'navigating_to_pickup' && rideState !== 'trip_in_progress') return;
    const interval = setInterval(() => setDirectionsKey((k) => k + 1), 30000);
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
  // Additionally, when transitioning back to idle (ride cancelled / completed),
  // re-center the map on the driver's current location — otherwise the map
  // stays framed on the pickup/dropoff bounding box from the previous ride
  // and the driver marker ends up off-screen.
  useEffect(() => {
    setRouteCoords([]);
    setRouteEtaMinutes(null);
    setRouteDistanceKm(null);
    setDirectionsKey(0);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rideState]);

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
      showDashAlert('Error', error, 'danger', [{ text: 'OK', style: 'default', onPress: clearError }]);
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
  const pickupAddress = ride?.pickup_address;
  const dropoffAddress = ride?.dropoff_address;

  const mapMarkers = useMemo(() => {
    const markers: any[] = [];
    if (pickupLat && pickupLng) {
      markers.push(
        <Marker
          key="pickup"
          coordinate={{ latitude: pickupLat, longitude: pickupLng }}
          title="Pickup"
          description={pickupAddress}
          tracksViewChanges={false}
        >
          <View style={styles.markerContainer}>
            <View style={[styles.markerDot, { backgroundColor: '#10B981' }]}>
              <Ionicons name="location" size={16} color="#fff" />
            </View>
          </View>
        </Marker>
      );
    }
    if (dropoffLat && dropoffLng) {
      markers.push(
        <Marker
          key="dropoff"
          coordinate={{ latitude: dropoffLat, longitude: dropoffLng }}
          title="Dropoff"
          description={dropoffAddress}
          tracksViewChanges={false}
        >
          <View style={styles.markerContainer}>
            <View style={[styles.markerDot, { backgroundColor: '#EF4444' }]}>
              <Ionicons name="flag" size={16} color="#fff" />
            </View>
          </View>
        </Marker>
      );
    }
    return markers;
  }, [pickupLat, pickupLng, dropoffLat, dropoffLng, pickupAddress, dropoffAddress, styles]);

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
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={{ color: colors.text, marginTop: 12, fontSize: 15 }}>{t('home.gettingLocation')}</Text>
      </View>
    );
  }

  return (
    <View style={styles.container} collapsable={false}>
      {/* Offline indicator — slides in from the top when network drops */}
      <OfflineBanner />

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
          />
        )}
        {mapMarkers}

        {/* Route polyline.
            - ride_offered: pickup → dropoff, so the driver previews the
              actual trip they'd be accepting.
            - active phases: driver → pickup, then pickup → dropoff once
              the trip starts.
            Snap the driver's GPS to a 4-decimal grid (~11 m) so small
            jitter doesn't refetch the Directions API and flicker the
            polyline every render. */}
        {GOOGLE_MAPS_API_KEY && ride && (rideState === 'ride_offered' || rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && (() => {
          const driverLat = location?.coords?.latitude != null ? Math.round(location.coords.latitude * 10000) / 10000 : null;
          const driverLng = location?.coords?.longitude != null ? Math.round(location.coords.longitude * 10000) / 10000 : null;

          let origin: { latitude: number; longitude: number };
          let destination: { latitude: number; longitude: number };
          if (rideState === 'ride_offered') {
            origin = { latitude: ride.pickup_lat, longitude: ride.pickup_lng };
            destination = { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng };
          } else if (rideState === 'trip_in_progress') {
            origin = driverLat != null && driverLng != null
              ? { latitude: driverLat, longitude: driverLng }
              : { latitude: ride.pickup_lat, longitude: ride.pickup_lng };
            destination = { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng };
          } else {
            origin = driverLat != null && driverLng != null
              ? { latitude: driverLat, longitude: driverLng }
              : { latitude: ride.pickup_lat, longitude: ride.pickup_lng };
            destination = { latitude: ride.pickup_lat, longitude: ride.pickup_lng };
          }

          return (
            // Key the entire route subtree on rideState so Android's native
            // map layer fully drops the old polyline (trace artifact) when
            // the ride ends or transitions to a new phase.
            <React.Fragment key={`route-${rideState}`}>
              <MapViewDirections
                key={directionsKey}
                origin={origin}
                destination={destination}
                apikey={GOOGLE_MAPS_API_KEY}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={(result) => {
                  setRouteCoords(result.coordinates);
                  if (result.duration != null) setRouteEtaMinutes(Math.round(result.duration));
                  if (result.distance != null) setRouteDistanceKm(Math.round(result.distance * 10) / 10);
                  if (directionsKey === 0 && mapRef.current && result.coordinates?.length > 1) {
                    mapRef.current.fitToCoordinates(result.coordinates, {
                      edgePadding: { top: 100, right: 60, bottom: 300, left: 60 },
                      animated: true,
                    });
                  }
                }}
                onError={(err) => console.log('Directions error:', err)}
              />
              {routeCoords.length > 1 && (() => {
                const total = routeCoords.length;
                const SEGS = 20;
                const chunk = Math.max(1, Math.floor(total / SEGS));
                const segments: { coords: any[]; color: string }[] = [];
                for (let i = 0; i < total - 1; i += chunk) {
                  const end = Math.min(i + chunk + 1, total);
                  const t = i / Math.max(total - 1, 1);
                  // Interpolate: #FF9500 (orange) → #EE2B2B (red)
                  const r = Math.round(255 + (238 - 255) * t);
                  const g = Math.round(149 + (43 - 149) * t);
                  const b = Math.round(0 + (43 - 0) * t);
                  segments.push({ coords: routeCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
                }
                return (
                  <>
                    {/* Outer glow */}
                    <Polyline
                      coordinates={routeCoords}
                      strokeWidth={9}
                      strokeColor="rgba(238, 43, 43, 0.12)"
                    />
                    {segments.map((seg, idx) => (
                      <Polyline
                        key={`route-seg-${idx}`}
                        coordinates={seg.coords}
                        strokeWidth={5}
                        strokeColor={seg.color}
                        lineCap="round"
                        lineJoin="round"
                      />
                    ))}
                  </>
                );
              })()}
            </React.Fragment>
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
      <DriverTopBar driverData={driverData ?? undefined} user={user ?? undefined} isOnline={isOnline} connectionState={connectionState} surgeMultiplier={surgeMultiplier} />

      {/* SOS Button — visible during active ride */}
      {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && activeRide?.ride?.id && (
        <View style={{ position: 'absolute', top: Platform.OS === 'ios' ? 100 : 80, right: 16, zIndex: 50 }}>
          <SOSButton
            rideId={activeRide.ride.id}
            onTrigger={async (rideId, lat, lng) => {
              try { await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng }); } catch (err) { console.error('[index]', err); }
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
          driverData={driverData as any ?? undefined}
          earnings={earnings ?? undefined}
          onToggleOnline={toggleOnline}
          pulseAnim={pulseAnim}
        />
      )}
      {rideState === 'ride_offered' && renderRideOfferPanel()}
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
          onCompleteRide={() => completeRide(activeRide!.ride.id)}
          onCancelRide={() => cancelRide(activeRide!.ride.id)}
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
      <CustomAlert
        visible={dashAlert.visible}
        title={dashAlert.title}
        message={dashAlert.message}
        variant={dashAlert.variant}
        buttons={dashAlert.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={closeDashAlert}
      />
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
    markerContainer: {
      alignItems: 'center',
    },
    markerDot: {
      width: 32,
      height: 32,
      borderRadius: 16,
      justifyContent: 'center',
      alignItems: 'center',
      borderWidth: 2,
      borderColor: '#fff',
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
    <ErrorBoundary>
      <DriverDashboard />
    </ErrorBoundary>
  );
}
