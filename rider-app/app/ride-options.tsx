import React, { useEffect, useState, useRef, useMemo, useCallback } from 'react';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Image,
  useWindowDimensions,
  Platform,
  Modal,
  Animated,
  TextInput,
} from 'react-native';
import BottomSheet, { BottomSheetScrollView } from '@gorhom/bottom-sheet';
import CustomToggle from '../components/CustomToggle';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Circle, Polyline, Polygon, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';

import { useRideStore } from '../store/rideStore';
import { useWalletStore } from '../store/walletStore';
import { useWorkProfileStore } from '../store/workProfileStore';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { CarMarker, resolveMarkerVariant } from '@shared/components/CarMarker';
import { useVehicleTypeStore } from '@shared/store/vehicleTypeStore';
import SchedulePicker from '../components/SchedulePicker';
import SkeletonBox from '../components/SkeletonBox';
import { useResponsive } from '@shared/utils/responsive';
import api from '@shared/api/client';
import Analytics from '@shared/analytics';
import { useScheduledRideReminder } from '../hooks/useScheduledRideReminder';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

interface SavedCard {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default: boolean;
}

function RideOptionsScreenContent() {
  const { height: SCREEN_HEIGHT } = useWindowDimensions();
  // The pricing sheet opens at its first snap point (40% — see `snapPoints`
  // and `index={0}` below), so the map must only reserve that much space at
  // the bottom when fitting the route. Reserving the fully-expanded 68% height
  // instead crammed the whole pickup→dropoff route into the top ~32% strip,
  // which pushed the rider's pickup pin into the top-left corner.
  const mapBottomInset = SCREEN_HEIGHT * 0.4 + 30;
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { sf } = useResponsive();
  const styles = useMemo(() => createStyles(colors, sf, insets), [colors, sf, insets]);
  const router = useRouter();

  // ── Ride store ──
  const {
    pickup, dropoff, stops, estimates, selectedVehicle,
    fetchEstimates, fetchNearbyDrivers, nearbyDrivers,
    selectVehicle, createRide, isLoading,
    requiresWav, setRequiresWav, showWavOption,
    scheduledTime, setScheduledTime, quietMode, setQuietMode,
    availablePromos, appliedPromo, fetchAvailablePromos, applyPromo,
    setRoutePolyline, routePolyline,
  } = useRideStore();

  // Vehicle type config (marker variant + custom marker image), synced once
  // per app open by the root layout.
  const vehicleTypesById = useVehicleTypeStore((s) => s.byId);

  // ── Payment state ──
  const { wallet, fetchWallet } = useWalletStore();
  const [selectedPayment, setSelectedPayment] = useState('card');
  const [savedCards, setSavedCards] = useState<SavedCard[]>([]);
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const [showPaymentSheet, setShowPaymentSheet] = useState(false);
  const [isBooking, setIsBooking] = useState(false);

  // ── Work / corporate state ──
  const workModeEnabled = useWorkProfileStore(s => s.workModeEnabled);
  const activeCompanyId = useWorkProfileStore(s => s.activeCompanyId);
  const workProfiles = useWorkProfileStore(s => s.profiles);
  const fetchPolicy = useWorkProfileStore(s => s.fetchPolicy);
  const checkRide = useWorkProfileStore(s => s.checkRide);
  const fetchWorkProfiles = useWorkProfileStore(s => s.fetchProfiles);
  const activeCompanyName = useMemo(
    () => workProfiles.find(p => p.company.id === activeCompanyId)?.company.name ?? null,
    [workProfiles, activeCompanyId],
  );
  const corporateAccounts = useMemo(
    () => workProfiles.map(p => ({ id: p.company.id ?? '', company_name: p.company.name ?? '' })).filter(a => a.id),
    [workProfiles],
  );
  const [useCorporate, setUseCorporate] = useState(workModeEnabled);
  const [selectedCorporateId, setSelectedCorporateId] = useState<string | null>(
    workModeEnabled ? (activeCompanyId ?? null) : null,
  );

  // ── UI state ──
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [routeCoordinates, setRouteCoordinates] = useState<any[]>([]);
  const [mapReady, setMapReady] = useState(false);
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [tempDate, setTempDate] = useState(new Date(Date.now() + 30 * 60000));
  const [showPromoSheet, setShowPromoSheet] = useState(false);
  const [promoInput, setPromoInput] = useState('');
  const [promoError, setPromoError] = useState('');
  const [fareBreakdownOpen, setFareBreakdownOpen] = useState(false);
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean; title: string; message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const mapRef = useRef<MapView>(null);
  const sheetRef = useRef<BottomSheet>(null);
  const snapPoints = useMemo(() => ['40%', '68%'], []);
  const fareChevronAnim = useRef(new Animated.Value(0)).current;
  const { scheduleReminder } = useScheduledRideReminder();

  // Service area boundary polygons — fetched once per mount and shown as a
  // translucent zone overlay on the map so riders can see the coverage area.
  const [serviceAreaPolygons, setServiceAreaPolygons] = useState<Array<Array<{ latitude: number; longitude: number }>>>([]);
  useEffect(() => {
    api.get('/service-areas').then((res: any) => {
      const areas: any[] = res.data || [];
      const polys = areas
        .filter((a: any) => Array.isArray(a.polygon) && a.polygon.length >= 3)
        .map((a: any) => a.polygon.map((p: any) => ({ latitude: p.lat, longitude: p.lng })));
      setServiceAreaPolygons(polys);
    }).catch(() => {});
  }, []);

  // ── Derived values ──
  const selectedEstimate = estimates.length > selectedIndex ? estimates[selectedIndex] : null;

  const allUnavailable = estimates.length > 0 && !estimates.some(e => e.available);
  // Use server-computed discount_amount — correct for percentage promos and free_ride.
  const promoDiscount = appliedPromo?.discount_amount ?? 0;
  const totalFare = Math.max(0, parseFloat((selectedEstimate as any)?.grand_total || selectedEstimate?.total_fare || '0') - promoDiscount);

  const paymentLabel = useMemo(() => {
    if (useCorporate && selectedCorporateId) {
      const corp = corporateAccounts.find(a => a.id === selectedCorporateId);
      return corp?.company_name ?? 'Company account';
    }
    if (selectedPayment === 'wallet') return `Wallet · $${parseFloat(wallet?.balance ?? '0').toFixed(2)}`;
    if (selectedPayment === 'card' && selectedCardId) {
      const card = savedCards.find(c => c.id === selectedCardId);
      if (card) return `${card.brand.charAt(0).toUpperCase() + card.brand.slice(1)} •••• ${card.last4}`;
    }
    return 'Add payment';
  }, [selectedPayment, selectedCardId, savedCards, useCorporate, selectedCorporateId, wallet, corporateAccounts]);

  const paymentIcon = useMemo(() => {
    if (useCorporate && selectedCorporateId) return 'business' as const;
    if (selectedPayment === 'wallet') return 'wallet' as const;
    return 'card' as const;
  }, [selectedPayment, useCorporate, selectedCorporateId]);

  // ── Effects ──

  const handleFetchEstimates = async () => {
    setFetchError(null);
    try { await fetchEstimates(); } catch { setFetchError('Could not load fares. Tap to retry.'); }
  };

  useEffect(() => {
    if (pickup && dropoff) {
      console.log('Platform:', Platform.OS, '| Fetching estimates & nearby drivers');
      void Promise.all([handleFetchEstimates(), fetchNearbyDrivers()]);
      const driversInterval = setInterval(() => fetchNearbyDrivers(), 10000);
      const estimatesInterval = setInterval(() => void handleFetchEstimates(), 15000);
      return () => { clearInterval(driversInterval); clearInterval(estimatesInterval); };
    }
  }, [pickup, dropoff]);

  useEffect(() => {
    if (workModeEnabled && activeCompanyId) fetchPolicy();
  }, [workModeEnabled, activeCompanyId]);

  useEffect(() => {
    if (estimates.length > 0) {
      const est = estimates[selectedIndex] ?? estimates[0];
      const grandTotal = parseFloat((est as any).grand_total || est.total_fare || '0');
      const ridePortion = parseFloat(est.base_fare || '0') + parseFloat(est.distance_fare || '0') + parseFloat(est.time_fare || '0');
      fetchAvailablePromos(grandTotal, ridePortion);
    }
  }, [estimates]);

  useEffect(() => {
    if (!estimates || estimates.length === 0) return;
    setSelectedIndex(prev => (prev >= estimates.length ? 0 : prev));
    if (!selectedVehicle) {
      const firstAvailableIndex = estimates.findIndex(e => e.available);
      if (firstAvailableIndex !== -1) {
        setSelectedIndex(firstAvailableIndex);
        selectVehicle(estimates[firstAvailableIndex].vehicle_type);
      } else {
        setSelectedIndex(0);
        selectVehicle(estimates[0].vehicle_type);
      }
    }
  }, [estimates, isLoading]);

  useEffect(() => {
    if (mapRef.current && mapReady && pickup && dropoff) {
      const markers = [
        { latitude: pickup.lat, longitude: pickup.lng },
        { latitude: dropoff.lat, longitude: dropoff.lng },
        ...stops.filter(s => s.lat && s.lng).map(s => ({ latitude: s.lat, longitude: s.lng })),
        ...routeCoordinates,
      ];
      if (markers.length >= 2) {
        setTimeout(() => {
          mapRef.current?.fitToCoordinates(markers, {
            edgePadding: { top: 60, right: 50, bottom: mapBottomInset, left: 50 },
            animated: true,
          });
        }, 300);
      }
    }
  }, [pickup, dropoff, nearbyDrivers, routeCoordinates, mapReady]);

  // Payment data fetch
  useEffect(() => {
    fetchWorkProfiles();
    fetchWallet();
    api.get('/payments/cards').then((res) => {
      const cards: SavedCard[] = Array.isArray(res.data) ? res.data : [];
      setSavedCards(cards);
      const defaultCard = cards.find(c => c.is_default) ?? cards[0];
      if (defaultCard) setSelectedCardId(defaultCard.id);
    }).catch((e) => {
      console.warn('[RideOptions] Failed to load saved cards:', e?.message ?? e);
    });
  }, []);

  useEffect(() => {
    if (workModeEnabled && corporateAccounts.length > 0) {
      setUseCorporate(true);
      setSelectedCorporateId(prev => prev ?? activeCompanyId ?? corporateAccounts[0]?.id ?? null);
    }
  }, [workModeEnabled, corporateAccounts.length]);

  // Populate route coordinates from the server-provided polyline (returned
  // with the estimate response). Falls back to MapViewDirections on-device
  // if the backend didn't return one.
  useEffect(() => {
    if (routePolyline && routePolyline.length >= 2) {
      const coords = routePolyline.map(([lat, lng]: [number, number]) => ({ latitude: lat, longitude: lng }));
      setRouteCoordinates(coords);
    } else {
      // The store clears routePolyline whenever a waypoint changes (e.g. the
      // rider went back and picked a new destination/stop). Drop the old drawn
      // route too so a stale trace doesn't linger on the map and the
      // MapViewDirections fallback can redraw for the new route.
      setRouteCoordinates([]);
    }
  }, [routePolyline]);

  // ── Handlers ──

  const onReadyDirections = (result: any) => {
    if (result.coordinates) {
      setRouteCoordinates(result.coordinates);
      setRoutePolyline(result.coordinates);
    }
    if (mapRef.current && mapReady) {
      mapRef.current.fitToCoordinates(result.coordinates, {
        edgePadding: { top: 60, right: 50, bottom: mapBottomInset, left: 50 },
        animated: true,
      });
    }
  };

  const handleSelect = (index: number) => {
    if (!estimates[index]?.available) {
      showToast('Unavailable', 'This vehicle type is not available right now.', 'warning');
      return;
    }
    setSelectedIndex(index);
    selectVehicle(estimates[index].vehicle_type);
    // Expand the sheet to its full snap point so the payment, schedule, and
    // Confirm rows come into view. Riders were tapping a vehicle and getting
    // stuck on the 40% snap, not realizing they had to drag the sheet up to
    // reach the Confirm button.
    sheetRef.current?.expand();
    setTimeout(() => fetchNearbyDrivers(), 100);
    const est = estimates[index];
    const grandTotal = parseFloat((est as any).grand_total || est.total_fare || '0');
    const ridePortion = parseFloat(est.base_fare || '0') + parseFloat(est.distance_fare || '0') + parseFloat(est.time_fare || '0');
    fetchAvailablePromos(grandTotal, ridePortion);
  };

  const handleBookRide = async () => {
    if (isBooking || isLoading) return;
    if (!selectedVehicle || !selectedEstimate) return;
    // Payment guard: a card ride must have an actual card selected. Without
    // this the rider could book on the implicit Stripe default (e.g. a stale
    // test card) — booking now requires an explicit card, wallet, or company
    // account. Corporate/wallet methods don't need a saved card.
    if (selectedPayment === 'card' && !selectedCardId && !(useCorporate && selectedCorporateId)) {
      setConfirmSheet({
        visible: true,
        title: 'Add a payment method',
        message: savedCards.length === 0
          ? 'You don’t have a card on file. Add a payment method to book this ride.'
          : 'Please select a card to pay for this ride.',
        variant: 'warning',
        buttons: [
          { text: 'Add / select card', onPress: () => setShowPaymentSheet(true) },
          { text: 'Cancel', style: 'cancel' },
        ],
      });
      return;
    }
    if (scheduledTime) {
      const minTime = new Date(Date.now() + 15 * 60000);
      if (scheduledTime < minTime) {
        showToast('Invalid Time', 'Scheduled time must be at least 15 minutes from now.', 'warning');
        return;
      }
    }
    if (workModeEnabled && activeCompanyId && selectedEstimate) {
      const when = scheduledTime ?? undefined;
      const check = checkRide(parseFloat(selectedEstimate.total_fare || '0'), when);
      if (!check.ok) {
        setConfirmSheet({
          visible: true, title: 'Blocked by company policy',
          message: check.reasons.join('\n'), variant: 'warning',
          buttons: [
            { text: 'Turn off work mode', style: 'destructive', onPress: () => useWorkProfileStore.getState().setWorkMode(false) },
            { text: 'Change ride', style: 'cancel' },
          ],
        });
        return;
      }
    }
    // Surge transparency: surge must be explicitly acknowledged before
    // booking, never discovered on the receipt. The badge on the estimate
    // card is passive — this sheet forces an active confirm at >1.0×.
    const surge = selectedEstimate.surge_multiplier ?? 1;
    if (surge > 1.0) {
      setConfirmSheet({
        visible: true,
        title: `${surge}× surge pricing is in effect`,
        message: `Demand is higher than usual right now, so a ${surge}× surge multiplier is included in your fare. Your total is $${totalFare.toFixed(2)}.`,
        variant: 'warning',
        buttons: [
          // Deliberately NOT awaited: ConfirmSheet.handlePress awaits onPress
          // and then always calls onClose() in its finally. Awaiting the whole
          // booking here would let that onClose() fire AFTER proceedWithBooking
          // sets a follow-up sheet (e.g. the 402 "Unpaid Ride" prompt) and
          // silently close it. Returning immediately closes the surge sheet
          // first; booking continues and any follow-up sheet survives.
          { text: `Book at $${totalFare.toFixed(2)}`, onPress: () => { void proceedWithBooking(); } },
          { text: 'Cancel', style: 'cancel' },
        ],
      });
      return;
    }
    await proceedWithBooking();
  };

  const proceedWithBooking = async () => {
    if (isBooking) return;
    setIsBooking(true);
    try {
      const corpId = useCorporate && selectedCorporateId ? selectedCorporateId : null;
      const pmId = selectedPayment === 'card' ? (selectedCardId ?? undefined) : undefined;
      const ride = await createRide(selectedPayment, corpId, pmId);
      if ('requires_action' in ride) {
        // A card needing on-device 3DS returns RideRequiresAction, but this
        // screen can't run the Stripe confirm (only payment-confirm does).
        // Surface it instead of navigating on with an undefined ride id.
        showToast('Card authentication needed', 'This card needs extra verification. Please pick another payment method or try again.', 'warning');
        return;
      }
      if ((ride as any).promo_error) {
        showToast('Promo not applied', (ride as any).promo_error, 'warning');
      }
      Analytics.rideRequested({ vehicle_type: selectedVehicle?.name ?? 'unknown', estimated_fare: totalFare });
      Analytics.paymentInitiated({ method: selectedPayment, amount: totalFare });
      if (scheduledTime && ride.id) {
        scheduleReminder(ride.id, scheduledTime).catch(() => {});
      }
      if (scheduledTime) {
        router.replace('/(tabs)');
      } else {
        router.replace({ pathname: '/driver-arriving', params: { rideId: ride.id } } as any);
      }
    } catch (error: any) {
      const is409 = error?.response?.status === 409 || error?.message?.includes('already active');
      if (is409) {
        const active = await useRideStore.getState().fetchActiveRide();
        if (active?.active && active.ride) {
          const s = active.ride.status;
          if (s === 'searching' || s === 'driver_assigned' || s === 'driver_accepted') {
            router.replace({ pathname: '/driver-arriving', params: { rideId: active.ride.id } } as any);
          } else if (s === 'driver_arrived') {
            router.replace({ pathname: '/driver-arrived', params: { rideId: active.ride.id } } as any);
          } else if (s === 'in_progress') {
            router.replace({ pathname: '/ride-in-progress', params: { rideId: active.ride.id } } as any);
          } else if (s === 'completed') {
            router.replace({ pathname: '/ride-completed', params: { rideId: active.ride.id } } as any);
          }
          return;
        }
      }
      const is402 = error?.response?.status === 402;
      const errBody = error?.response?.data;
      const unpaidRideId = errBody?.error?.details?.unpaid_ride_id;
      if (is402 && unpaidRideId) {
        setConfirmSheet({
          visible: true,
          title: 'Unpaid Ride',
          message: 'You have an unpaid ride. Please settle the payment before booking a new ride.',
          variant: 'danger',
          buttons: [
            { text: 'Pay Now', onPress: () => router.push({ pathname: '/ride-completed', params: { rideId: unpaidRideId } } as any) },
            { text: 'Cancel', style: 'cancel' as const },
          ],
        });
      } else {
        showToast('Booking Failed', error.message || 'Failed to book ride. Please try again.', 'danger');
      }
    } finally {
      setIsBooking(false);
    }
  };

  const handleScheduleConfirm = (date: Date) => {
    setShowScheduleModal(false);
    if (date < new Date(Date.now() + 15 * 60000)) {
      setConfirmSheet({ visible: true, title: 'Invalid Time', message: 'Scheduled time must be at least 15 minutes from now.', variant: 'warning' });
      return;
    }
    setTempDate(date);
    setScheduledTime(date);
  };

  const toggleFareBreakdown = useCallback(() => {
    const next = !fareBreakdownOpen;
    setFareBreakdownOpen(next);
    Animated.spring(fareChevronAnim, {
      toValue: next ? 1 : 0,
      tension: 120, friction: 14, useNativeDriver: true,
    }).start();
  }, [fareBreakdownOpen, fareChevronAnim]);

  const fareChevronRotation = fareChevronAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '180deg'],
  });

  const handleManualPromo = () => {
    const code = promoInput.trim().toUpperCase();
    if (!code) return;
    const match = availablePromos.find(
      (p: any) => p.code?.toUpperCase() === code
    ) as any;
    if (match) {
      if (match.eligible === false) {
        showToast('Not eligible', match.ineligible_reason || 'This promo cannot be applied to this ride', 'warning');
        setPromoError(match.ineligible_reason || 'Minimum fare not met');
        return;
      }
      applyPromo(match);
      setPromoInput('');
      setPromoError('');
      setShowPromoSheet(false);
    } else {
      setPromoError('Code not found or has expired');
    }
  };

  // ── Render ──

  return (
    <View style={styles.container}>
      {/* ═══ Full-screen map ═══ */}
      {/* pointerEvents="none" on the wrapper prevents Google Maps' native gesture
          recognizer from stealing vertical scroll events from the sheet's ScrollView
          on Android production builds. The map is decorative here (auto-fit route). */}
      {pickup && dropoff ? (
        <View style={StyleSheet.absoluteFill} pointerEvents="none">
        <MapView
          ref={mapRef}
          provider={Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined}
          style={StyleSheet.absoluteFill}
          onMapReady={() => setMapReady(true)}
          initialRegion={{
            latitude: pickup.lat, longitude: pickup.lng,
            latitudeDelta: 0.05, longitudeDelta: 0.05,
          }}
        >
          {/* Fallback: only call Google Directions client-side if the backend
              didn't return a route_polyline with the estimate response. */}
          {GOOGLE_MAPS_API_KEY && routeCoordinates.length === 0 && (
            <MapViewDirections
              origin={{ latitude: pickup.lat, longitude: pickup.lng }}
              destination={{ latitude: dropoff.lat, longitude: dropoff.lng }}
              waypoints={stops.filter(s => s.lat && s.lng).map(s => ({ latitude: s.lat, longitude: s.lng }))}
              apikey={GOOGLE_MAPS_API_KEY}
              strokeWidth={0}
              strokeColor="transparent"
              onReady={onReadyDirections}
              onError={(err: any) => console.warn('[RideOptions] Directions API error:', err?.message ?? err)}
              optimizeWaypoints
            />
          )}
          {routeCoordinates.length > 1 && (() => {
            const total = routeCoordinates.length;
            const segments: { coords: any[]; color: string }[] = [];
            const SEGMENT_COUNT = 30;
            const chunkSize = Math.max(1, Math.floor(total / SEGMENT_COUNT));
            for (let i = 0; i < total - 1; i += chunkSize) {
              const end = Math.min(i + chunkSize + 1, total);
              const t = i / Math.max(total - 1, 1);
              const r = Math.round(255 + (238 - 255) * t);
              const g = Math.round(149 + (43 - 149) * t);
              const b = Math.round(0 + (43 - 0) * t);
              segments.push({ coords: routeCoordinates.slice(i, end), color: `rgb(${r},${g},${b})` });
            }
            return (
              <>
                <Polyline coordinates={routeCoordinates} strokeWidth={9} strokeColor="rgba(238,43,43,0.12)" />
                {segments.map((seg, idx) => (
                  <Polyline key={`seg-${idx}`} coordinates={seg.coords} strokeWidth={5}
                    strokeColor={seg.color} lineCap="round" lineJoin="round" />
                ))}
              </>
            );
          })()}
          <Marker coordinate={{ latitude: pickup.lat, longitude: pickup.lng }} anchor={{ x: 0.5, y: 0.5 }} zIndex={103}>
            <View style={styles.markerContainer}><View style={[styles.markerDot, { backgroundColor: '#10B981' }]} /></View>
          </Marker>
          <Marker coordinate={{ latitude: dropoff.lat, longitude: dropoff.lng }} anchor={{ x: 0.5, y: 0.5 }} zIndex={103}>
            <View style={styles.markerContainer}><View style={[styles.markerDot, { backgroundColor: '#EF4444' }]} /></View>
          </Marker>
          {stops.map((stop, i) => (
            <Marker key={`stop-${i}`} coordinate={{ latitude: stop.lat, longitude: stop.lng }} anchor={{ x: 0.5, y: 0.5 }}>
              <View style={styles.markerContainer}><View style={[styles.markerDot, { backgroundColor: '#F59E0B' }]} /></View>
            </Marker>
          ))}
          {nearbyDrivers.filter(d =>
            typeof d.lat === 'number' && !isNaN(d.lat) &&
            typeof d.lng === 'number' && !isNaN(d.lng) &&
            Math.abs(d.lat) > 0.1 && Math.abs(d.lng) > 0.1
          ).map((driver) => {
            const vt = vehicleTypesById[driver.vehicle_type_id ?? ''];
            return (
              <CarMarker key={driver.id} identifier={driver.id}
                coordinate={{ latitude: driver.lat, longitude: driver.lng }}
                heading={(driver as any).heading ?? Math.random() * 360}
                imageUri={vt?.marker_image_url}
                variant={resolveMarkerVariant(
                  vt?.marker_variant ?? driver.marker_variant,
                  vt?.name ?? driver.vehicle_type_name,
                )}
                size={36} zIndex={101} />
            );
          })}
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
        </View>
      ) : (
        <View style={styles.mapPlaceholder}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      )}

      {/* ═══ Floating back button ═══ */}
      <TouchableOpacity
        style={[styles.floatingBack, { top: insets.top + 8 }]}
        onPress={() => router.back()}
        accessibilityRole="button"
        accessibilityLabel="Go back"
      >
        <Ionicons name="arrow-back" size={22} color="#1A1A1A" />
      </TouchableOpacity>

      {/* ═══ Destination chip ═══ */}
      {dropoff && (
        <View style={[styles.destinationChip, { top: insets.top + 12 }]}>
          <Text style={styles.destinationChipText} numberOfLines={1}>
            {dropoff.address}
          </Text>
        </View>
      )}

      {/* ═══ Bottom sheet ═══ */}
      <BottomSheet
        ref={sheetRef}
        index={0}
        snapPoints={snapPoints}
        backgroundStyle={styles.sheetBackground}
        handleIndicatorStyle={styles.sheetIndicator}
        enableOverDrag={false}
        enablePanDownToClose={false}
      >
        <BottomSheetScrollView
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          bounces={false}
          overScrollMode="never"
        >

          {/* Section header */}
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Choose a ride</Text>
            <View style={styles.commissionBadge}>
              <Text style={styles.commissionText}>0% Commission</Text>
            </View>
          </View>

          {/* Busy banner */}
          {allUnavailable && !isLoading && (
            <View style={styles.busyBanner}>
              <Ionicons name="warning" size={20} color="#B91C1C" />
              <Text style={styles.busyText}>No cars available right now. Please try again later.</Text>
            </View>
          )}

          {/* Loading skeleton */}
          {fetchError ? (
            <View style={styles.errorContainer}>
              <Ionicons name="alert-circle-outline" size={36} color="#EF4444" />
              <Text style={styles.errorText}>{fetchError}</Text>
              <TouchableOpacity style={styles.retryButton} onPress={handleFetchEstimates}>
                <Text style={styles.retryButtonText}>Retry</Text>
              </TouchableOpacity>
            </View>
          ) : isLoading ? (
            <View style={{ paddingHorizontal: 16, paddingTop: 8 }}>
              {[0, 1, 2].map((i) => (
                <View key={i} style={{ flexDirection: 'row', alignItems: 'center', padding: 14, marginBottom: 6,
                  backgroundColor: colors.surface, borderRadius: 12 }}>
                  <SkeletonBox width={64} height={42} borderRadius={8} style={{ marginRight: 12 }} />
                  <View style={{ flex: 1, gap: 6 }}>
                    <SkeletonBox width="50%" height={14} />
                    <SkeletonBox width="35%" height={11} />
                  </View>
                  <SkeletonBox width={48} height={20} borderRadius={6} />
                </View>
              ))}
            </View>
          ) : (
            /* Vehicle cards — Lyft-style animated selection */
            <View style={styles.cardList}>
              {estimates.map((estimate, index) => (
                <AnimatedVehicleCard
                  key={estimate.vehicle_type.id}
                  estimate={estimate}
                  index={index}
                  isSelected={selectedIndex === index}
                  isAvailable={estimate.available}
                  onPress={handleSelect}
                  styles={styles}
                  colors={colors}
                  appliedPromo={appliedPromo}
                />
              ))}
            </View>
          )}

          {/* Promo row — always visible */}
          <TouchableOpacity
            style={[styles.promoEntryRow, appliedPromo && styles.promoEntryRowActive]}
            onPress={() => { setPromoError(''); setShowPromoSheet(true); }}
            activeOpacity={0.75}
          >
            <View style={[styles.promoEntryIcon, appliedPromo && { backgroundColor: '#D1FAE5' }]}>
              <Ionicons name="pricetag" size={16} color={appliedPromo ? '#059669' : colors.textDim} />
            </View>
            {appliedPromo ? (
              <View style={{ flex: 1 }}>
                <Text style={styles.promoEntryCode}>{appliedPromo.code}</Text>
                <Text style={styles.promoEntryDesc}>
                  {appliedPromo.free_ride
                    ? 'Free ride — 100% covered'
                    : appliedPromo.discount_type === 'percentage'
                      ? `${appliedPromo.discount_value}% off your ride fare`
                      : `$${Number(appliedPromo.discount_amount ?? appliedPromo.discount_value).toFixed(2)} off`}
                </Text>
              </View>
            ) : (
              <View style={{ flex: 1 }}>
                <Text style={styles.promoEntryLabel}>Add promo code</Text>
                {availablePromos.length > 0 && (
                  <Text style={styles.promoEntryHint}>{availablePromos.length} offer{availablePromos.length !== 1 ? 's' : ''} available</Text>
                )}
              </View>
            )}
            {appliedPromo ? (
              <TouchableOpacity
                onPress={(e) => { e.stopPropagation(); applyPromo(null); }}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                style={styles.promoRemoveBtn}
              >
                <Ionicons name="close-circle" size={20} color="#059669" />
              </TouchableOpacity>
            ) : (
              <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
            )}
          </TouchableOpacity>

          {/* Work mode banner */}
          {workModeEnabled && activeCompanyId && (
            <View style={styles.workBanner}>
              <Ionicons name="briefcase" size={16} color="#1D4ED8" />
              <View style={{ flex: 1 }}>
                <Text style={styles.workBannerTitle}>Billed to {activeCompanyName ?? 'your employer'}</Text>
                <Text style={styles.workBannerSubtitle}>This ride will be covered by your work allowance.</Text>
              </View>
            </View>
          )}

          {/* WAV toggle */}
          {showWavOption && (() => {
            const wavCount = selectedEstimate?.wav_available ?? 0;
            const wavDisabled = wavCount === 0;
            return (
              <View style={[styles.optionRow, wavDisabled && { opacity: 0.45 }]}>
                <Ionicons name="accessibility-outline" size={20} color="#1A1A1A" />
                <View style={{ flex: 1 }}>
                  <Text style={styles.optionRowLabel}>Wheelchair accessible</Text>
                  <Text style={styles.optionRowHint}>
                    {wavDisabled ? 'No WAV drivers nearby' : `${wavCount} WAV driver${wavCount !== 1 ? 's' : ''} available`}
                  </Text>
                </View>
                <CustomToggle value={requiresWav}
                  onValueChange={(v) => { if (!wavDisabled) setRequiresWav(v); }}
                  disabled={wavDisabled}
                  trackColor={{ false: '#D1D5DB', true: colors.primary + '60' }}
                  thumbColor={requiresWav ? colors.primary : '#F3F4F6'}
                  accessibilityLabel="Request wheelchair-accessible vehicle" />
              </View>
            );
          })()}

          {/* Quiet mode */}
          <View style={styles.optionRow}>
            <Ionicons name="volume-mute" size={20} color="#1A1A1A" />
            <View style={{ flex: 1 }}>
              <Text style={styles.optionRowLabel}>Quiet ride</Text>
              <Text style={styles.optionRowHint}>Prefer minimal conversation</Text>
            </View>
            <CustomToggle value={quietMode} onValueChange={setQuietMode}
              trackColor={{ false: '#D1D5DB', true: colors.primary + '60' }}
              thumbColor={quietMode ? colors.primary : '#F3F4F6'}
              accessibilityLabel="Request quiet ride" />
          </View>

          {/* Fare breakdown — collapsible, collapsed by default */}
          {selectedEstimate?.fare_breakdown && selectedEstimate.fare_breakdown.length > 0 && (
            <View style={styles.fareBreakdownCard}>
              <TouchableOpacity
                style={styles.fareBreakdownHeader}
                onPress={toggleFareBreakdown}
                activeOpacity={0.7}
                accessibilityRole="button"
                accessibilityLabel={fareBreakdownOpen ? 'Collapse fare breakdown' : 'Expand fare breakdown'}
              >
                <Text style={styles.fareBreakdownTitle}>Fare breakdown</Text>
                <View style={styles.fareBreakdownHeaderRight}>
                  <Text style={styles.fareBreakdownTotalValue}>${totalFare.toFixed(2)}</Text>
                  <Animated.View style={{ transform: [{ rotate: fareChevronRotation }] }}>
                    <Ionicons name="chevron-down" size={16} color={colors.textDim} />
                  </Animated.View>
                </View>
              </TouchableOpacity>
              {fareBreakdownOpen && (
                <View style={{ marginTop: 8 }}>
                  {selectedEstimate.fare_breakdown.map((line, i) => (
                    line.amount != null ? (
                      <View key={i} style={[styles.fareBreakdownRow, line.type === 'ride' && { alignItems: 'flex-start' }]}>
                        {line.type === 'ride' ? (
                          <View style={{ flex: 1 }}>
                            <Text style={styles.fareBreakdownLabel}>{line.label}</Text>
                            <Text style={styles.fareBreakdownDriverBadge}>100% goes to your driver · ride local, support local</Text>
                          </View>
                        ) : (
                          <Text style={[styles.fareBreakdownLabel, line.type === 'tax' && { color: '#6B7280' }]}>{line.label}</Text>
                        )}
                        <Text style={styles.fareBreakdownValue}>${parseFloat(String(line.amount)).toFixed(2)}</Text>
                      </View>
                    ) : null
                  ))}
                  {appliedPromo && promoDiscount > 0 && (
                    <View style={[styles.fareBreakdownRow, { marginTop: 2 }]}>
                      <Text style={[styles.fareBreakdownLabel, { color: '#10B981' }]}>Promo ({appliedPromo.code})</Text>
                      <Text style={[styles.fareBreakdownValue, { color: '#10B981' }]}>-${promoDiscount.toFixed(2)}</Text>
                    </View>
                  )}
                  <View style={[styles.fareBreakdownRow, styles.fareBreakdownTotal]}>
                    <Text style={styles.fareBreakdownTotalLabel}>Total</Text>
                    <Text style={styles.fareBreakdownTotalValue}>${totalFare.toFixed(2)}</Text>
                  </View>
                </View>
              )}
            </View>
          )}

          {/* Cancel policy */}
          <View style={styles.policyRow}>
            <Ionicons name="information-circle-outline" size={14} color="#9CA3AF" />
            <Text style={styles.policyText}>
              Free cancellation within 2 min of driver acceptance.
            </Text>
          </View>

          {/* ═══ Fixed footer — Uber style ═══ */}
          {!isLoading && !allUnavailable && estimates.length > 0 && selectedEstimate && (
            <View style={[styles.fixedFooter, { paddingBottom: Math.max(insets.bottom, 12) + 4 }]}>
              {/* Payment method row */}
              <TouchableOpacity style={styles.actionRow} onPress={() => setShowPaymentSheet(true)} activeOpacity={0.7}>
                <View style={styles.actionRowIcon}>
                  <Ionicons name={paymentIcon} size={18} color={colors.primary} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.actionRowLabel}>Payment</Text>
                  <Text style={styles.actionRowValue}>{paymentLabel}</Text>
                </View>
                <Ionicons name="chevron-forward" size={16} color="#9CA3AF" />
              </TouchableOpacity>

              {/* Schedule row */}
              <TouchableOpacity
                style={styles.actionRow}
                onPress={() => scheduledTime ? setScheduledTime(null) : setShowScheduleModal(true)}
                activeOpacity={0.7}
              >
                <View style={styles.actionRowIcon}>
                  <Ionicons name="calendar-outline" size={18} color={colors.primary} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.actionRowLabel}>Pickup time</Text>
                  {scheduledTime ? (
                    <Text style={styles.actionRowValue}>
                      {`${scheduledTime.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })} at ${scheduledTime.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })}`}
                    </Text>
                  ) : (
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                      <Text style={styles.actionRowValue}>Now</Text>
                      <Text style={styles.actionRowHint}>· tap to schedule</Text>
                    </View>
                  )}
                </View>
                {scheduledTime ? (
                  <TouchableOpacity onPress={() => setScheduledTime(null)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                    <Ionicons name="close-circle" size={18} color="#9CA3AF" />
                  </TouchableOpacity>
                ) : (
                  <Ionicons name="chevron-forward" size={16} color="#9CA3AF" />
                )}
              </TouchableOpacity>

              {/* Confirm button */}
              <TouchableOpacity
                style={[styles.confirmButton, (!selectedEstimate.available || isBooking) && { opacity: 0.5 }]}
                onPress={handleBookRide}
                activeOpacity={0.8}
                disabled={!selectedEstimate.available || isBooking}
                accessibilityRole="button"
                accessibilityLabel={`${scheduledTime ? 'Schedule' : 'Confirm'} ${selectedEstimate.vehicle_type.name}`}
              >
                {isBooking ? (
                  <ActivityIndicator color="#FFFFFF" />
                ) : (
                  <Text style={styles.confirmButtonText} allowFontScaling={false}>
                    {scheduledTime ? 'Schedule' : 'Confirm'} {selectedEstimate.vehicle_type.name} · ${totalFare.toFixed(2)}
                  </Text>
                )}
              </TouchableOpacity>
            </View>
          )}
        </BottomSheetScrollView>
      </BottomSheet>

      {/* ═══ Payment method modal ═══ */}
      <Modal visible={showPaymentSheet} animationType="slide" transparent onRequestClose={() => setShowPaymentSheet(false)}>
        <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={() => setShowPaymentSheet(false)}>
          <TouchableOpacity activeOpacity={1} style={styles.paymentModal}>
            <View style={styles.paymentModalHandle} />
            <Text style={styles.paymentModalTitle}>Payment method</Text>

            <ScrollView showsVerticalScrollIndicator={false} style={{ maxHeight: 400 }}>
              {/* Saved cards */}
              {savedCards.map((card) => {
                const isSelected = selectedPayment === 'card' && selectedCardId === card.id && !useCorporate;
                return (
                  <TouchableOpacity key={card.id}
                    style={[styles.paymentOption, isSelected && styles.paymentOptionSelected]}
                    onPress={() => { setSelectedPayment('card'); setSelectedCardId(card.id); setUseCorporate(false); }}>
                    <View style={styles.paymentOptionIcon}>
                      <Ionicons name="card" size={22} color={isSelected ? colors.primary : colors.textDim} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.paymentOptionName}>{card.brand.charAt(0).toUpperCase() + card.brand.slice(1)}</Text>
                      <Text style={styles.paymentOptionDetail}>•••• {card.last4}  {card.exp_month}/{String(card.exp_year).slice(-2)}</Text>
                    </View>
                    {isSelected && (
                      <View style={styles.paymentCheck}>
                        <Ionicons name="checkmark" size={16} color="#FFF" />
                      </View>
                    )}
                  </TouchableOpacity>
                );
              })}

              {savedCards.length === 0 && (
                <TouchableOpacity
                  style={[styles.paymentOption, selectedPayment === 'card' && !useCorporate && styles.paymentOptionSelected]}
                  onPress={() => { setSelectedPayment('card'); setUseCorporate(false); }}>
                  <View style={styles.paymentOptionIcon}>
                    <Ionicons name="card" size={22} color={colors.textDim} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.paymentOptionName}>Credit Card</Text>
                  </View>
                  {selectedPayment === 'card' && !useCorporate && (
                    <View style={styles.paymentCheck}><Ionicons name="checkmark" size={16} color="#FFF" /></View>
                  )}
                </TouchableOpacity>
              )}

              {/* Wallet */}
              <TouchableOpacity
                style={[styles.paymentOption, selectedPayment === 'wallet' && !useCorporate && styles.paymentOptionSelected]}
                onPress={() => { setSelectedPayment('wallet'); setUseCorporate(false); }}>
                <View style={styles.paymentOptionIcon}>
                  <Ionicons name="wallet" size={22} color={selectedPayment === 'wallet' && !useCorporate ? colors.primary : colors.textDim} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.paymentOptionName}>Spinr Wallet</Text>
                  <Text style={styles.paymentOptionDetail}>Balance: ${parseFloat(wallet?.balance ?? '0').toFixed(2)}</Text>
                </View>
                {selectedPayment === 'wallet' && !useCorporate && (
                  <View style={styles.paymentCheck}><Ionicons name="checkmark" size={16} color="#FFF" /></View>
                )}
              </TouchableOpacity>

              {/* Corporate billing */}
              {corporateAccounts.length > 0 && (
                <>
                  <View style={styles.paymentDivider}>
                    <View style={styles.paymentDividerLine} />
                    <Text style={styles.paymentDividerText}>Corporate</Text>
                    <View style={styles.paymentDividerLine} />
                  </View>
                  {corporateAccounts.map((acct) => {
                    const isSelected = useCorporate && selectedCorporateId === acct.id;
                    return (
                      <TouchableOpacity key={acct.id}
                        style={[styles.paymentOption, isSelected && styles.paymentOptionSelected]}
                        onPress={() => { setUseCorporate(true); setSelectedCorporateId(acct.id); }}>
                        <View style={styles.paymentOptionIcon}>
                          <Ionicons name="business" size={22} color={isSelected ? colors.primary : colors.textDim} />
                        </View>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.paymentOptionName}>{acct.company_name}</Text>
                          <Text style={styles.paymentOptionDetail}>Company account</Text>
                        </View>
                        {isSelected && (
                          <View style={styles.paymentCheck}><Ionicons name="checkmark" size={16} color="#FFF" /></View>
                        )}
                      </TouchableOpacity>
                    );
                  })}
                </>
              )}

              {/* Add payment */}
              <TouchableOpacity style={styles.addPaymentRow}
                onPress={() => { setShowPaymentSheet(false); router.push('/manage-cards' as any); }}>
                <Ionicons name="add-circle-outline" size={20} color={colors.primary} />
                <Text style={styles.addPaymentText}>Add payment method</Text>
              </TouchableOpacity>
            </ScrollView>

            <TouchableOpacity style={styles.paymentDoneBtn} onPress={() => setShowPaymentSheet(false)}>
              <Text style={styles.paymentDoneBtnText}>Done</Text>
            </TouchableOpacity>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      {/* ═══ Promo selection modal ═══ */}
      <Modal visible={showPromoSheet} animationType="slide" transparent onRequestClose={() => setShowPromoSheet(false)}>
        <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={() => setShowPromoSheet(false)}>
          <TouchableOpacity activeOpacity={1} style={styles.promoSheet}>
            <View style={styles.paymentModalHandle} />
            <Text style={styles.paymentModalTitle}>Promo Code</Text>

            {/* Manual code entry */}
            <View style={styles.promoInputRow}>
              <View style={[styles.promoInputWrap, promoError ? { borderColor: '#EF4444' } : {}]}>
                <Ionicons name="pricetag-outline" size={16} color={colors.textDim} style={{ marginLeft: 12 }} />
                <TextInput
                  style={[styles.promoInputField, { color: colors.text }]}
                  placeholder="Enter code"
                  placeholderTextColor={colors.textDim}
                  value={promoInput}
                  onChangeText={t => { setPromoInput(t); setPromoError(''); }}
                  autoCapitalize="characters"
                  autoCorrect={false}
                  returnKeyType="done"
                  onSubmitEditing={handleManualPromo}
                />
              </View>
              <TouchableOpacity
                style={[styles.promoApplyBtn, { backgroundColor: promoInput.trim() ? colors.primary : colors.border }]}
                onPress={handleManualPromo}
                disabled={!promoInput.trim()}
              >
                <Text style={styles.promoApplyText}>Apply</Text>
              </TouchableOpacity>
            </View>
            {promoError ? (
              <View style={styles.promoErrorRow}>
                <Ionicons name="alert-circle-outline" size={14} color="#EF4444" />
                <Text style={styles.promoErrorText}>{promoError}</Text>
              </View>
            ) : null}

            {/* Available promos list */}
            {availablePromos.length > 0 && (
              <Text style={[styles.promoSectionLabel, { color: colors.textDim }]}>Available Offers</Text>
            )}

            <ScrollView showsVerticalScrollIndicator={false} style={{ maxHeight: 340 }}>
              {availablePromos.map((promo: any) => {
                const isSelected = appliedPromo?.promo_id === promo.promo_id || appliedPromo?.code === promo.code;
                const isIneligible = promo.eligible === false;
                const discountLabel = promo.free_ride
                  ? 'Free ride'
                  : promo.discount_type === 'percentage'
                    ? `${promo.discount_value}% off${promo.max_discount ? ` · max $${promo.max_discount}` : ''}`
                    : `$${Number(promo.discount_value).toFixed(2)} off`;
                return (
                  <TouchableOpacity
                    key={promo.promo_id || promo.code}
                    style={[styles.promoRow, isSelected && styles.promoRowSelected, isIneligible && { opacity: 0.45 }]}
                    onPress={() => {
                      if (isIneligible) {
                        showToast('Not eligible', promo.ineligible_reason || 'This promo cannot be applied to this ride', 'warning');
                        return;
                      }
                      applyPromo(promo);
                      setShowPromoSheet(false);
                    }}
                    activeOpacity={0.75}
                  >
                    <View style={[styles.promoRowIcon, { backgroundColor: isSelected ? '#D1FAE5' : isIneligible ? '#F3F4F6' : '#F0FDF4' }]}>
                      <Ionicons name="pricetag" size={18} color={isSelected ? '#059669' : isIneligible ? '#9CA3AF' : '#10B981'} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                        <Text style={[styles.promoRowCode, isIneligible && { color: '#9CA3AF' }]}>{promo.code}</Text>
                        <View style={[styles.promoSavingPill, isIneligible && { backgroundColor: '#F3F4F6' }]}>
                          <Text style={[styles.promoSavingPillText, isIneligible && { color: '#9CA3AF' }]}>{discountLabel}</Text>
                        </View>
                      </View>
                      {promo.description ? (
                        <Text style={styles.promoRowDesc}>{promo.description}</Text>
                      ) : null}
                      {isIneligible && promo.min_ride_fare > 0 && (
                        <Text style={{ fontSize: 11, color: '#EF4444', marginTop: 2 }}>Min. fare ${Number(promo.min_ride_fare).toFixed(2)}</Text>
                      )}
                    </View>
                    {isSelected
                      ? <Ionicons name="checkmark-circle" size={22} color="#059669" />
                      : isIneligible
                        ? <Ionicons name="lock-closed" size={16} color="#9CA3AF" />
                        : <Ionicons name="chevron-forward" size={16} color={colors.border} />}
                  </TouchableOpacity>
                );
              })}

              {availablePromos.length === 0 && (
                <View style={styles.promoEmpty}>
                  <View style={styles.promoEmptyIcon}>
                    <Ionicons name="gift-outline" size={28} color={colors.textDim} />
                  </View>
                  <Text style={styles.promoEmptyTitle}>No offers right now</Text>
                  <Text style={styles.promoEmptyText}>Enter a code above if you have one</Text>
                </View>
              )}
            </ScrollView>

            {appliedPromo && (
              <TouchableOpacity
                style={styles.promoRemoveRow}
                onPress={() => { applyPromo(null); setShowPromoSheet(false); }}
              >
                <Ionicons name="close-circle-outline" size={18} color="#EF4444" />
                <Text style={styles.promoRemoveText}>Remove applied code</Text>
              </TouchableOpacity>
            )}

            <TouchableOpacity style={styles.paymentDoneBtn} onPress={() => setShowPromoSheet(false)}>
              <Text style={styles.paymentDoneBtnText}>Done</Text>
            </TouchableOpacity>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      {/* ═══ Schedule picker ═══ */}
      <SchedulePicker
        visible={showScheduleModal}
        onClose={() => setShowScheduleModal(false)}
        onConfirm={handleScheduleConfirm}
        minDate={new Date(Date.now() + 15 * 60000)}
        maxDate={new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)}
      />

      <ConfirmSheet
        visible={confirmSheet.visible} title={confirmSheet.title} message={confirmSheet.message}
        variant={confirmSheet.variant} buttons={confirmSheet.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))} />
    </View>
  );
}

export default function RideOptionsScreen() {
  return (
    <ErrorBoundary>
      <RideOptionsScreenContent />
    </ErrorBoundary>
  );
}

/** Lyft-style animated vehicle card — spring scale + shadow on selection. */
function AnimatedVehicleCard({
  estimate, index, isSelected, isAvailable, onPress, styles, colors, appliedPromo,
}: {
  estimate: any; index: number; isSelected: boolean; isAvailable: boolean;
  onPress: (i: number) => void; styles: any; colors: any; appliedPromo: any;
}) {
  const scaleAnim = useRef(new Animated.Value(isSelected ? 1 : 0)).current;

  useEffect(() => {
    Animated.spring(scaleAnim, {
      toValue: isSelected ? 1 : 0,
      tension: 120, friction: 14, useNativeDriver: true,
    }).start();
  }, [isSelected]);

  const scale = scaleAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 1.03] });

  return (
    <Animated.View style={[
      { transform: [{ scale }] },
      isSelected && isAvailable && {
        shadowColor: '#000', shadowOffset: { width: 0, height: 3 },
        shadowOpacity: 0.12, shadowRadius: 8, elevation: 6,
        zIndex: 10, marginHorizontal: 2, marginVertical: 3, borderRadius: 14,
      },
    ]}>
      <TouchableOpacity
        style={[
          styles.optionCard,
          isSelected && isAvailable && styles.optionCardSelected,
          !isAvailable && styles.optionCardDisabled,
        ]}
        onPress={() => onPress(index)}
        activeOpacity={isAvailable ? 0.7 : 1}
        disabled={!isAvailable}>
        <View style={[styles.carImageContainer, !isAvailable && { opacity: 0.4 }]}>
          {estimate.vehicle_type.image_url ? (
            <Image source={{ uri: estimate.vehicle_type.image_url }} style={styles.carImage} resizeMode="contain" />
          ) : (
            <View style={styles.carIconFallback}>
              <Ionicons name="car" size={32} color="#666" />
            </View>
          )}
        </View>
        <View style={[styles.optionInfo, !isAvailable && { opacity: 0.4 }]}>
          <View style={styles.optionNameRow}>
            <Text style={styles.optionName}>{estimate.vehicle_type.name}</Text>
            {(estimate.surge_multiplier ?? 1) > 1.0 && (
              <View style={styles.surgeBadge}>
                <Ionicons name="trending-up" size={10} color="#fff" />
                <Text style={styles.surgeBadgeText} allowFontScaling={false}>{estimate.surge_multiplier}x</Text>
              </View>
            )}
            <View style={styles.capacityBadge}>
              <Ionicons name="person" size={11} color="#666" />
              <Text style={styles.capacityText}>{estimate.vehicle_type.capacity}</Text>
            </View>
          </View>
          {isAvailable ? (
            <Text style={styles.optionETA} allowFontScaling={false}>
              {estimate.eta_minutes ? `${estimate.eta_minutes} min` : 'Nearby'}
              {estimate.driver_count > 0 && ` · ${estimate.driver_count} driver${estimate.driver_count > 1 ? 's' : ''}`}
            </Text>
          ) : (
            <Text style={styles.unavailableText}>No drivers nearby</Text>
          )}
          {(estimate.surge_multiplier ?? 1) > 1.0 && (
            <Text style={styles.surgeNotice}>Higher demand</Text>
          )}
        </View>
        <View style={[styles.optionPriceContainer, !isAvailable && { opacity: 0.4 }]}>
          {appliedPromo && (appliedPromo.discount_amount ?? 0) > 0 && isSelected ? (
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.optionPriceStruck} allowFontScaling={false}>
                ${parseFloat((estimate as any).grand_total || estimate.total_fare || '0').toFixed(2)}
              </Text>
              <Text style={styles.optionPriceDiscounted} allowFontScaling={false}>
                ${Math.max(0, parseFloat((estimate as any).grand_total || estimate.total_fare || '0') - (appliedPromo.discount_amount ?? 0)).toFixed(2)}
              </Text>
            </View>
          ) : (
            <Text style={styles.optionPrice} allowFontScaling={false}>
              ${parseFloat((estimate as any).grand_total || estimate.total_fare || '0').toFixed(2)}
            </Text>
          )}
          {isSelected && isAvailable && (
            <View style={styles.selectedCheck}>
              <Ionicons name="checkmark-circle" size={20} color={colors.text} />
            </View>
          )}
        </View>
      </TouchableOpacity>
    </Animated.View>
  );
}

function createStyles(colors: ThemeColors, sf: (size: number) => number, insets: { top: number; bottom: number }) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: '#E8E8E8',
    },
    mapPlaceholder: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
    },
    markerContainer: {
      backgroundColor: colors.surface,
      padding: 3,
      borderRadius: 12,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 2,
      elevation: 2,
    },
    markerDot: {
      width: 12,
      height: 12,
      borderRadius: 6,
    },

    // ── Floating UI ──
    floatingBack: {
      position: 'absolute',
      left: 16,
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: '#FFFFFF',
      justifyContent: 'center',
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      shadowRadius: 6,
      elevation: 6,
      zIndex: 20,
    },
    destinationChip: {
      position: 'absolute',
      left: 68,
      right: 16,
      backgroundColor: '#FFFFFF',
      borderRadius: 22,
      paddingHorizontal: 16,
      paddingVertical: 10,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.12,
      shadowRadius: 6,
      elevation: 6,
      zIndex: 20,
    },
    destinationChipText: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },

    // ── Bottom sheet ──
    sheetBackground: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.12,
      shadowRadius: 12,
      elevation: 16,
    },
    sheetIndicator: {
      width: 40,
      height: 4,
      borderRadius: 2,
      backgroundColor: '#D1D5DB',
    },
    sectionHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 20,
      paddingVertical: 12,
    },
    sectionTitle: {
      fontSize: sf(18),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    commissionBadge: {
      backgroundColor: '#E8F5E9',
      borderRadius: 12,
      paddingHorizontal: 10,
      paddingVertical: 4,
    },
    commissionText: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#2E7D32',
    },
    busyBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: '#FEF2F2',
      padding: 12,
      marginHorizontal: 16,
      marginBottom: 8,
      borderRadius: 8,
      borderWidth: 1,
      borderColor: '#FCA5A5',
      gap: 10,
    },
    busyText: {
      color: '#B91C1C',
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_500Medium',
      flex: 1,
    },
    errorContainer: {
      paddingVertical: 24,
      alignItems: 'center',
      gap: 8,
    },
    errorText: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#EF4444',
    },
    retryButton: {
      paddingHorizontal: 24,
      paddingVertical: 10,
      backgroundColor: colors.primary,
      borderRadius: 20,
      marginTop: 4,
    },
    retryButtonText: {
      color: '#FFF',
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
    },

    // ── Vehicle cards ──
    cardList: {
      paddingHorizontal: 12,
    },
    optionCard: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    optionCardSelected: {
      backgroundColor: colors.surface,
      borderRadius: 14,
      borderWidth: 1.5,
      borderColor: colors.text,
      borderBottomWidth: 1.5,
      borderBottomColor: colors.text,
    },
    optionCardDisabled: {
      backgroundColor: colors.surfaceLight,
    },
    carImageContainer: {
      width: 64,
      height: 42,
      marginRight: 12,
      justifyContent: 'center',
      alignItems: 'center',
    },
    carImage: {
      width: 64,
      height: 42,
    },
    carIconFallback: {
      width: 64,
      height: 42,
      borderRadius: 8,
      backgroundColor: colors.surfaceLight,
      justifyContent: 'center',
      alignItems: 'center',
    },
    optionInfo: {
      flex: 1,
    },
    optionNameRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
    },
    optionName: {
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    surgeBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 3,
      backgroundColor: '#EF4444',
      borderRadius: 8,
      paddingHorizontal: 5,
      paddingVertical: 2,
    },
    surgeBadgeText: {
      fontSize: sf(10),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#fff',
    },
    surgeNotice: {
      fontSize: sf(10),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#EF4444',
      marginTop: 1,
    },
    capacityBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 2,
      backgroundColor: colors.surfaceLight,
      borderRadius: 8,
      paddingHorizontal: 4,
      paddingVertical: 2,
    },
    capacityText: {
      fontSize: sf(10),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    optionETA: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#10B981',
      marginTop: 2,
    },
    unavailableText: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      fontStyle: 'italic',
      marginTop: 2,
    },
    optionPriceContainer: {
      alignItems: 'flex-end',
    },
    optionPrice: {
      fontSize: sf(17),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    optionPriceStruck: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      textDecorationLine: 'line-through',
    },
    optionPriceDiscounted: {
      fontSize: sf(17),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#10B981',
    },
    selectedCheck: {
      marginTop: 3,
    },

    // ── Promo entry row (always visible) ──
    promoEntryRow: {
      flexDirection: 'row',
      alignItems: 'center',
      marginHorizontal: 16,
      marginTop: 8,
      marginBottom: 4,
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      paddingVertical: 11,
      paddingHorizontal: 12,
      gap: 10,
      borderWidth: 1.5,
      borderColor: colors.border,
    },
    promoEntryRowActive: {
      backgroundColor: '#F0FDF4',
      borderColor: '#6EE7B7',
    },
    promoEntryIcon: {
      width: 34,
      height: 34,
      borderRadius: 10,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
    },
    promoEntryLabel: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    promoEntryHint: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#059669',
      marginTop: 1,
    },
    promoEntryCode: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#065F46',
      letterSpacing: 0.4,
    },
    promoEntryDesc: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#059669',
      marginTop: 1,
    },
    promoRemoveBtn: {
      padding: 2,
    },
    workBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      marginHorizontal: 16,
      marginTop: 6,
      marginBottom: 4,
      backgroundColor: '#EFF6FF',
      borderRadius: 12,
      paddingVertical: 10,
      paddingHorizontal: 14,
      gap: 10,
      borderWidth: 1,
      borderColor: '#BFDBFE',
    },
    workBannerTitle: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#1E3A8A',
    },
    workBannerSubtitle: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#1D4ED8',
      marginTop: 1,
    },

    // ── Option rows ──
    optionRow: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 20,
      paddingVertical: 10,
      gap: 10,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    optionRowLabel: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    optionRowHint: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 1,
    },
    policyRow: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: 6,
      paddingHorizontal: 20,
      paddingVertical: 8,
    },
    policyText: {
      flex: 1,
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#9CA3AF',
      lineHeight: 15,
    },

    // ── Fare breakdown card ──
    fareBreakdownCard: {
      marginHorizontal: 16,
      marginVertical: 8,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 10,
      borderWidth: 1,
      borderColor: colors.border,
    },
    fareBreakdownHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
    },
    fareBreakdownHeaderRight: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
    },
    fareBreakdownTitle: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
    },
    fareBreakdownRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      paddingVertical: 3,
    },
    fareBreakdownLabel: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.text,
    },
    fareBreakdownValue: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    fareBreakdownDriverBadge: {
      fontSize: sf(10),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#10B981',
      marginTop: 2,
    },
    fareBreakdownTotal: {
      borderTopWidth: 1,
      borderTopColor: colors.border,
      marginTop: 4,
      paddingTop: 6,
    },
    fareBreakdownTotalLabel: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    fareBreakdownTotalValue: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.primary,
    },
    fixedFooter: {
      borderTopWidth: 1,
      borderTopColor: colors.border,
      paddingHorizontal: 16,
      paddingTop: 8,
      backgroundColor: colors.surface,
    },
    actionRow: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
      gap: 12,
    },
    actionRowIcon: {
      width: 36,
      height: 36,
      borderRadius: 18,
      backgroundColor: colors.primary + '15',
      justifyContent: 'center',
      alignItems: 'center',
    },
    actionRowValue: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    actionRowLabel: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginBottom: 1,
    },
    actionRowHint: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.primary,
    },
    confirmButton: {
      backgroundColor: colors.primary,
      borderRadius: 28,
      paddingVertical: 16,
      alignItems: 'center',
      justifyContent: 'center',
      marginTop: 10,
    },
    confirmButtonText: {
      fontSize: sf(16),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },

    // ── Payment modal ──
    modalOverlay: {
      flex: 1,
      justifyContent: 'flex-end',
      backgroundColor: 'rgba(0,0,0,0.4)',
    },
    paymentModal: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 20,
      paddingBottom: Math.max(insets.bottom, 16) + 8,
      paddingTop: 12,
    },
    paymentModalHandle: {
      width: 40,
      height: 4,
      borderRadius: 2,
      backgroundColor: colors.border,
      alignSelf: 'center',
      marginBottom: 16,
    },
    paymentModalTitle: {
      fontSize: sf(18),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 16,
    },
    paymentOption: {
      flexDirection: 'row',
      alignItems: 'center',
      padding: 14,
      marginBottom: 8,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      borderWidth: 2,
      borderColor: 'transparent',
    },
    paymentOptionSelected: {
      backgroundColor: '#FFF5F5',
      borderColor: colors.primary,
    },
    paymentOptionIcon: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 12,
    },
    paymentOptionName: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    paymentOptionDetail: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 1,
    },
    paymentCheck: {
      width: 26,
      height: 26,
      borderRadius: 13,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
    },
    paymentDivider: {
      flexDirection: 'row',
      alignItems: 'center',
      marginVertical: 12,
      gap: 10,
    },
    paymentDividerLine: {
      flex: 1,
      height: 1,
      backgroundColor: colors.border,
    },
    paymentDividerText: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    addPaymentRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      paddingVertical: 14,
      gap: 8,
    },
    addPaymentText: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.primary,
    },
    paymentDoneBtn: {
      marginTop: 8,
      paddingVertical: 14,
      borderRadius: 24,
      backgroundColor: colors.primary,
      alignItems: 'center',
    },
    paymentDoneBtnText: {
      fontSize: sf(16),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },

    // ── Promo sheet ──
    promoSheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 20,
      paddingBottom: Math.max(insets.bottom, 16) + 8,
      paddingTop: 12,
      maxHeight: '85%',
    },
    promoInputRow: {
      flexDirection: 'row',
      gap: 8,
      marginBottom: 6,
    },
    promoInputWrap: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      borderRadius: 12,
      borderWidth: 1.5,
      borderColor: colors.border,
      backgroundColor: colors.surfaceLight,
      overflow: 'hidden',
    },
    promoInputField: {
      flex: 1,
      paddingVertical: 13,
      paddingHorizontal: 10,
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      letterSpacing: 1,
    },
    promoApplyBtn: {
      paddingHorizontal: 18,
      borderRadius: 12,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: 48,
    },
    promoApplyText: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#FFF',
    },
    promoErrorRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      marginBottom: 10,
    },
    promoErrorText: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#EF4444',
    },
    promoSectionLabel: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_700Bold',
      textTransform: 'uppercase',
      letterSpacing: 0.6,
      marginTop: 8,
      marginBottom: 8,
    },
    promoRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 13,
      paddingHorizontal: 12,
      borderRadius: 14,
      marginBottom: 8,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1.5,
      borderColor: 'transparent',
    },
    promoRowSelected: {
      borderColor: '#6EE7B7',
      backgroundColor: '#F0FDF4',
    },
    promoRowIcon: {
      width: 38,
      height: 38,
      borderRadius: 10,
      justifyContent: 'center',
      alignItems: 'center',
    },
    promoRowCode: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      letterSpacing: 0.5,
    },
    promoRowDesc: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 2,
    },
    promoSavingPill: {
      backgroundColor: '#D1FAE5',
      borderRadius: 6,
      paddingHorizontal: 6,
      paddingVertical: 2,
    },
    promoSavingPillText: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#065F46',
    },
    promoEmpty: {
      alignItems: 'center',
      paddingVertical: 32,
      gap: 8,
    },
    promoEmptyIcon: {
      width: 56, height: 56, borderRadius: 28,
      backgroundColor: colors.surfaceLight,
      justifyContent: 'center', alignItems: 'center',
      marginBottom: 4,
    },
    promoEmptyTitle: {
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    promoEmptyText: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    promoRemoveRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingVertical: 12,
      marginBottom: 4,
    },
    promoRemoveText: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#EF4444',
    },


  });
}
