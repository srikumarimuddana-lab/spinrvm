import React, { useEffect, useState, useRef, useMemo, useCallback } from 'react';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  useWindowDimensions,
  Platform,
  Animated,
  Keyboard,
  BackHandler,
} from 'react-native';
import { Image as ExpoImage } from 'expo-image';
import BottomSheet, { BottomSheetScrollView, BottomSheetBackdrop, BottomSheetTextInput } from '@gorhom/bottom-sheet';
import CustomToggle from '../components/CustomToggle';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polygon, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';

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
import api, { getApiErrorMessage } from '@shared/api/client';
import { Analytics } from '@shared/analytics';
import { useScheduledRideReminder } from '../hooks/useScheduledRideReminder';
import { useAnimatedValue } from '../hooks/useAnimatedValue';
import { promoDiscountForEstimate, grandTotalOf } from '../utils/promoDiscount';
import { selectDefaultCardId } from '../utils/selectDefaultCard';

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
  // Payment selector is a real @gorhom/bottom-sheet instance (same library as
  // the vehicle sheet and the promo sheet below), NOT an RN <Modal>. The Modal
  // stacked over the vehicle sheet rendered fine but was touch-dead on iOS New
  // Arch (TestFlight 2.0.0 (16)) — rows and the old footer button drew but
  // presses never arrived, so tapping a payment method couldn't dismiss it.
  // Sibling sheets from this library are the one overlay pattern proven to
  // receive touches on this screen.
  const paymentSheetRef = useRef<BottomSheet>(null);
  const [paymentSheetOpen, setPaymentSheetOpen] = useState(false);
  const paymentMaxSheetHeight = SCREEN_HEIGHT * 0.8;
  const openPaymentSheet = useCallback(() => {
    paymentSheetRef.current?.expand();
  }, []);
  const closePaymentSheet = useCallback(() => {
    paymentSheetRef.current?.close();
  }, []);
  const handlePaymentSheetChange = useCallback((index: number) => {
    setPaymentSheetOpen(index >= 0);
  }, []);
  // The Modal used to intercept Android hardware back via onRequestClose;
  // keep that exit — back closes the sheet instead of popping the screen.
  useEffect(() => {
    if (!paymentSheetOpen) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      paymentSheetRef.current?.close();
      return true;
    });
    return () => sub.remove();
  }, [paymentSheetOpen]);
  const renderPaymentBackdrop = useCallback((props: any) => (
    <BottomSheetBackdrop
      {...props}
      appearsOnIndex={0}
      disappearsOnIndex={-1}
      pressBehavior="close"
      opacity={0.4}
    />
  ), []);
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
  const [promoInput, setPromoInput] = useState('');
  const [promoError, setPromoError] = useState('');
  // Promo sheet is a real @gorhom/bottom-sheet instance (same lib as the main
  // vehicle sheet below), not an RN <Modal>. keyboardBehavior="interactive"
  // (below) floats the sheet above the keyboard by putting it into a
  // "temporary position" — on iOS, calling .close() while it's still in that
  // temporary position (keyboard not yet fully dismissed) corrupts the
  // sheet's internal layout state and crashes on the next touch/drag. Gate
  // our own close calls behind the keyboard actually finishing its hide
  // animation, same idea as the old Modal-era pending-close pattern, just
  // scoped to this one race instead of also driving positioning by hand.
  const promoSheetRef = useRef<BottomSheet>(null);
  const promoKeyboardVisibleRef = useRef(false);
  const promoPendingCloseRef = useRef(false);
  useEffect(() => {
    if (Platform.OS !== 'ios') return;
    const show = Keyboard.addListener('keyboardWillShow', () => {
      promoKeyboardVisibleRef.current = true;
    });
    const hide = Keyboard.addListener('keyboardWillHide', () => {
      promoKeyboardVisibleRef.current = false;
      if (promoPendingCloseRef.current) {
        promoPendingCloseRef.current = false;
        promoSheetRef.current?.close();
      }
    });
    return () => { show.remove(); hide.remove(); };
  }, []);
  // Dynamic sizing (no fixed snapPoints) so the sheet is only as tall as its
  // content — 2 offers vs. 8 shouldn't both claim a fixed 75% and leave a
  // huge empty gap under a short list. Capped at 80% of the screen so a long
  // offer list scrolls instead of pushing off the top.
  const promoMaxSheetHeight = SCREEN_HEIGHT * 0.8;
  const openPromoSheet = useCallback(() => {
    setPromoError('');
    promoSheetRef.current?.expand();
  }, []);
  const closePromoSheet = useCallback(() => {
    if (Platform.OS === 'ios' && promoKeyboardVisibleRef.current) {
      promoPendingCloseRef.current = true;
      Keyboard.dismiss();
    } else {
      promoSheetRef.current?.close();
    }
  }, []);
  const renderPromoBackdrop = useCallback((props: any) => (
    <BottomSheetBackdrop
      {...props}
      appearsOnIndex={0}
      disappearsOnIndex={-1}
      pressBehavior="close"
      opacity={0.4}
      onPress={() => Keyboard.dismiss()}
    />
  ), []);
  const [fareBreakdownOpen, setFareBreakdownOpen] = useState(false);
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean; title: string; message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const mapRef = useRef<MapView>(null);
  const sheetRef = useRef<BottomSheet>(null);
  const snapPoints = useMemo(() => ['40%', '68%'], []);
  const fareChevronAnim = useAnimatedValue(0);
  const { scheduleReminder } = useScheduledRideReminder();

  // Service area boundary polygons — fetched once per mount and shown as a
  // translucent zone overlay on the map so riders can see the coverage area.
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

  // ── Derived values ──
  const selectedEstimate = estimates.length > selectedIndex ? estimates[selectedIndex] : null;

  const allUnavailable = estimates.length > 0 && !estimates.some(e => e.available);
  // Per-card promo math (mirrors the server — utils/promoDiscount): the
  // server-computed discount_amount is only valid for the single fare promos
  // were fetched with, so it goes stale the moment the rider switches vehicle
  // type. The server recomputes and enforces the real discount at booking.
  const promoDiscount = promoDiscountForEstimate(appliedPromo, selectedEstimate as any);
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

  // Work profiles + wallet: load once on mount.
  useEffect(() => {
    fetchWorkProfiles();
    fetchWallet();
  }, []);

  // Load saved cards on mount AND every time this screen regains focus.
  // The rider adds a card on /manage-cards (reached via router.push) and
  // returns via router.back(), which re-focuses this screen without
  // remounting it — so a mount-only fetch would never see the new card and
  // the payment selector would stay empty. Refetching on focus makes the
  // freshly added card appear and auto-selects it so the rider can confirm
  // their vehicle type without restarting the booking.
  const loadSavedCards = useCallback(() => {
    api.get('/payments/cards').then((res) => {
      const cards: SavedCard[] = Array.isArray(res.data) ? res.data : [];
      setSavedCards(cards);
      // Preserve the rider's explicit selection if it's still valid; otherwise
      // fall back to the default card (the backend marks the first card as
      // default), so a new customer's first card is auto-selected.
      setSelectedCardId((prev) => selectDefaultCardId(prev, cards));
    }).catch((e) => {
      console.warn('[RideOptions] Failed to load saved cards:', e?.message ?? e);
    });
  }, []);

  useFocusEffect(loadSavedCards);

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
          { text: 'Add / select card', onPress: () => openPaymentSheet() },
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
        showToast('Booking Failed', getApiErrorMessage(error, 'Failed to book ride. Please try again.'), 'danger');
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
      closePromoSheet();
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
              // NO optimizeWaypoints: the backend prices + dispatches `stops` in
              // the rider-entered order, so Directions must keep that order.
              // Optimising would let onReady persist a reordered polyline as
              // planned_route_polyline, making the map show a different stop
              // sequence than the actual ride contract.
            />
          )}
          {/* Real derived route, drawn via the shared orange→red gradient line. */}
          <RouteLine
            path={routeCoordinates}
            pickup={{ latitude: pickup.lat, longitude: pickup.lng }}
            destination={{ latitude: dropoff.lat, longitude: dropoff.lng }}
          />
          <RoutePins
            pickup={{ latitude: pickup.lat, longitude: pickup.lng }}
            dropoff={{ latitude: dropoff.lat, longitude: dropoff.lng }}
          />
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
                  <SkeletonBox width={88} height={58} borderRadius={8} style={{ marginRight: 12 }} />
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
            onPress={openPromoSheet}
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
                accessibilityRole="button"
                accessibilityLabel="Remove promo code"
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
              <TouchableOpacity style={styles.actionRow} onPress={() => openPaymentSheet()} activeOpacity={0.7}>
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
                  <TouchableOpacity
                    onPress={() => setScheduledTime(null)}
                    hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                    accessibilityRole="button"
                    accessibilityLabel="Clear scheduled pickup time"
                  >
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
                  <Text style={styles.confirmButtonText}>
                    {scheduledTime ? 'Schedule' : 'Confirm'} {selectedEstimate.vehicle_type.name} · ${totalFare.toFixed(2)}
                  </Text>
                )}
              </TouchableOpacity>
            </View>
          )}
        </BottomSheetScrollView>
      </BottomSheet>

      {/* ═══ Payment method sheet ═══ */}
      {/* A @gorhom/bottom-sheet sibling like the promo sheet below — replaces
          the RN <Modal> that stacked over the vehicle sheet and was touch-dead
          on iOS New Arch (TestFlight 2.0.0 (16)): rows rendered but presses
          never arrived, so tapping a payment method couldn't dismiss it. Rows
          commit their selection and dismiss in the same press; backdrop tap,
          swipe-down, and Android hardware back are the other exits. */}
      <BottomSheet
        ref={paymentSheetRef}
        index={-1}
        enableDynamicSizing
        maxDynamicContentSize={paymentMaxSheetHeight}
        enablePanDownToClose
        backdropComponent={renderPaymentBackdrop}
        backgroundStyle={styles.sheetBackground}
        handleIndicatorStyle={styles.sheetIndicator}
        onChange={handlePaymentSheetChange}
      >
        <BottomSheetScrollView
          contentContainerStyle={styles.paymentSheetContent}
          showsVerticalScrollIndicator={false}
        >
          <Text style={styles.paymentModalTitle}>Payment method</Text>

          {/* Saved cards */}
          {savedCards.map((card) => {
            const isSelected = selectedPayment === 'card' && selectedCardId === card.id && !useCorporate;
            return (
              <TouchableOpacity key={card.id}
                style={[styles.paymentOption, isSelected && styles.paymentOptionSelected]}
                onPress={() => { setSelectedPayment('card'); setSelectedCardId(card.id); setUseCorporate(false); closePaymentSheet(); }}>
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
              style={styles.paymentOption}
              onPress={() => { setSelectedPayment('card'); setUseCorporate(false); closePaymentSheet(); router.push('/manage-cards' as any); }}>
              <View style={styles.paymentOptionIcon}>
                <Ionicons name="card" size={22} color={colors.textDim} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.paymentOptionName}>Credit Card</Text>
                <Text style={styles.paymentOptionDetail}>Tap to add a card</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
            </TouchableOpacity>
          )}

          {/* Wallet */}
          <TouchableOpacity
            style={[styles.paymentOption, selectedPayment === 'wallet' && !useCorporate && styles.paymentOptionSelected]}
            onPress={() => { setSelectedPayment('wallet'); setUseCorporate(false); closePaymentSheet(); }}>
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
                    onPress={() => { setUseCorporate(true); setSelectedCorporateId(acct.id); closePaymentSheet(); }}>
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
            onPress={() => { closePaymentSheet(); router.push('/manage-cards' as any); }}>
            <Ionicons name="add-circle-outline" size={20} color={colors.primary} />
            <Text style={styles.addPaymentText}>Add payment method</Text>
          </TouchableOpacity>
        </BottomSheetScrollView>
      </BottomSheet>

      {/* ═══ Promo selection sheet ═══ */}
      <BottomSheet
        ref={promoSheetRef}
        index={-1}
        enableDynamicSizing
        maxDynamicContentSize={promoMaxSheetHeight}
        enablePanDownToClose
        enableBlurKeyboardOnGesture
        backdropComponent={renderPromoBackdrop}
        backgroundStyle={styles.sheetBackground}
        handleIndicatorStyle={styles.sheetIndicator}
        // "interactive" floats the whole sheet up above the keyboard (same
        // height, repositioned) instead of growing it — "extend" was a no-op
        // here since dynamic sizing only ever produces a single detent, so it
        // left the keyboard covering the input. Now that the sheet is
        // content-sized (not a fixed 75%), floating it up no longer risks
        // pushing it past the top of the screen.
        keyboardBehavior="interactive"
        keyboardBlurBehavior="restore"
        android_keyboardInputMode="adjustResize"
        onClose={() => Keyboard.dismiss()}
      >
        <BottomSheetScrollView
          contentContainerStyle={styles.promoSheetContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={availablePromos.length > 3}
        >
          <Text style={styles.paymentModalTitle}>Promo Code</Text>

          {/* Manual code entry */}
          <View style={styles.promoInputRow}>
            <View style={[styles.promoInputWrap, promoError ? { borderColor: '#EF4444' } : {}]}>
              <Ionicons name="pricetag-outline" size={16} color={colors.textDim} style={{ marginLeft: 12 }} />
              <BottomSheetTextInput
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

          {/* Available promos list. Show the count so a rider knows there are
              multiple offers to scroll through, not just the one on screen. */}
          {availablePromos.length > 0 && (
            <Text style={[styles.promoSectionLabel, { color: colors.textDim }]}>
              Available Offers{availablePromos.length > 1 ? ` · ${availablePromos.length}` : ''}
            </Text>
          )}

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
                  closePromoSheet();
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

          {/* No Done button by design: the sheet closes as soon as a coupon
              row is tapped, Apply is pressed, the backdrop is tapped, or it's
              swiped down (all via closePromoSheet / enablePanDownToClose).
              Removing an applied code just clears it and keeps the sheet open
              so another can be picked. */}
          {appliedPromo && (
            <TouchableOpacity
              style={styles.promoRemoveRow}
              onPress={() => applyPromo(null)}
            >
              <Ionicons name="close-circle-outline" size={18} color="#EF4444" />
              <Text style={styles.promoRemoveText}>Remove applied code</Text>
            </TouchableOpacity>
          )}
        </BottomSheetScrollView>
      </BottomSheet>

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
  const scaleAnim = useAnimatedValue(isSelected ? 1 : 0);
  // Car-image emphasis. Driven by a native SCALE transform (below) inside a
  // fixed-size container, so it rides the native driver like scaleAnim — the
  // old version animated width/height (useNativeDriver:false), which ran on the
  // JS thread AND changed the row height, reflowing the whole list on every
  // selection switch (the "shaking").
  const imageSizeAnim = useAnimatedValue(isSelected ? 1 : 0);

  useEffect(() => {
    Animated.spring(scaleAnim, {
      toValue: isSelected ? 1 : 0,
      tension: 120, friction: 14, useNativeDriver: true,
    }).start();
    Animated.spring(imageSizeAnim, {
      toValue: isSelected ? 1 : 0,
      tension: 120, friction: 14, useNativeDriver: true,
    }).start();
  }, [isSelected]);

  const scale = scaleAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 1.03] });
  // Car art keeps the original sizes — selected is the full 150×98 hero,
  // unselected the 88×58 thumbnail — but the growth is a native-driver SCALE
  // (transform only, no layout). The container reserves the MAX (150×98) box so
  // the row height is constant; the transform scales DOWN to 0.59 for unselected.
  // Nothing reflows on selection switch, so it's smooth with no shake.
  const imageScale = imageSizeAnim.interpolate({ inputRange: [0, 1], outputRange: [0.59, 1] });

  // Every card shows its own discounted price (not just the selected one) —
  // computed per fare because percentage promos scale with each card's ride
  // portion and min_ride_fare can gate cheaper types out (utils/promoDiscount).
  const cardGrandTotal = grandTotalOf(estimate);
  const cardDiscount = promoDiscountForEstimate(appliedPromo, estimate);

  return (
    <Animated.View style={[
      { transform: [{ scale }] },
      // Only non-layout props toggle on selection (shadow/elevation/zIndex/
      // radius) — margins are NOT changed here, since toggling margin shifts the
      // card + its siblings and was part of the switch "shake".
      isSelected && isAvailable && {
        shadowColor: '#000', shadowOffset: { width: 0, height: 3 },
        shadowOpacity: 0.12, shadowRadius: 8, elevation: 6,
        zIndex: 10, borderRadius: 14,
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
        <Animated.View style={[
          styles.carImageContainer,
          { transform: [{ scale: imageScale }] },
          !isAvailable && { opacity: 0.4 },
        ]}>
          {estimate.vehicle_type.image_url ? (
            <ExpoImage
              source={{ uri: estimate.vehicle_type.image_url }}
              style={styles.carImage}
              contentFit="contain"
              cachePolicy="disk"
            />
          ) : (
            <View style={styles.carIconFallback}>
              <Ionicons name="car" size={isSelected && isAvailable ? 60 : 42} color="#666" />
            </View>
          )}
        </Animated.View>
        <View style={[styles.optionInfo, !isAvailable && { opacity: 0.4 }]}>
          <View style={styles.optionNameRow}>
            <Text style={styles.optionName}>{estimate.vehicle_type.name}</Text>
            {(estimate.surge_multiplier ?? 1) > 1.0 && (
              <View style={styles.surgeBadge}>
                <Ionicons name="trending-up" size={10} color="#fff" />
                <Text style={styles.surgeBadgeText}>{estimate.surge_multiplier}x</Text>
              </View>
            )}
            <View style={styles.capacityBadge}>
              <Ionicons name="person" size={11} color="#666" />
              <Text style={styles.capacityText}>{estimate.vehicle_type.capacity}</Text>
            </View>
          </View>
          {isAvailable ? (
            <Text style={styles.optionETA}>
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
          {cardDiscount > 0 ? (
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.optionPriceStruck}>
                ${cardGrandTotal.toFixed(2)}
              </Text>
              <Text style={styles.optionPriceDiscounted}>
                ${Math.max(0, cardGrandTotal - cardDiscount).toFixed(2)}
              </Text>
            </View>
          ) : (
            <Text style={styles.optionPrice}>
              ${cardGrandTotal.toFixed(2)}
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
    // Width/height are animated per-card (64×42 resting → 118×76 selected),
    // so the container only carries layout; the image/fallback fill it.
    carImageContainer: {
      // Fixed layout box at the MAX (selected) car size — the pop is a transform
      // scale (no layout), so the row height is constant and the list never
      // reflows on switch. Unselected rows scale the image down to the thumbnail.
      width: 150,
      height: 98,
      marginRight: 12,
      justifyContent: 'center',
      alignItems: 'center',
    },
    carImage: {
      width: '100%',
      height: '100%',
    },
    carIconFallback: {
      width: '100%',
      height: '100%',
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

    // ── Payment sheet ──
    paymentSheetContent: {
      paddingHorizontal: 20,
      paddingBottom: Math.max(insets.bottom, 16) + 8,
      paddingTop: 4,
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
    // ── Promo sheet ──
    promoSheetContent: {
      paddingHorizontal: 20,
      paddingBottom: Math.max(insets.bottom, 16) + 8,
      paddingTop: 4,
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
