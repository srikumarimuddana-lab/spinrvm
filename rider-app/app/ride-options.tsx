import React, { useEffect, useState, useRef, useMemo } from 'react';
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
} from 'react-native';
import CustomToggle from '../components/CustomToggle';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Circle, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { useRideStore } from '../store/rideStore';
import { useWalletStore } from '../store/walletStore';
import { useWorkProfileStore } from '../store/workProfileStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { CarMarker } from '@shared/components/CarMarker';
import DateTimePicker from '@react-native-community/datetimepicker';
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
  } = useRideStore();

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
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [showTimePicker, setShowTimePicker] = useState(false);
  const [showPromoSheet, setShowPromoSheet] = useState(false);
  const [tempDate, setTempDate] = useState<Date>(new Date(Date.now() + 30 * 60000));
  const [alertState, setAlertState] = useState<{
    visible: boolean; title: string; message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const mapRef = useRef<MapView>(null);
  const { scheduleReminder } = useScheduledRideReminder();

  // ── Derived values ──
  const selectedEstimate = estimates.length > selectedIndex ? estimates[selectedIndex] : null;
  const allUnavailable = estimates.length > 0 && !estimates.some(e => e.available);
  const promoDiscount = appliedPromo?.discount_value ?? 0;
  const totalFare = Math.max(0, parseFloat(selectedEstimate?.total_fare || '0') - promoDiscount);
  const sheetScrollMax = SCREEN_HEIGHT * 0.68 - 20 - 180;

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
      const selectedFare = parseFloat(estimates[selectedIndex]?.total_fare || estimates[0]?.total_fare || '0');
      fetchAvailablePromos(selectedFare);
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
            edgePadding: { top: 60, right: 50, bottom: SCREEN_HEIGHT * 0.68 + 30, left: 50 },
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

  // ── Handlers ──

  const onReadyDirections = (result: any) => {
    if (result.coordinates) setRouteCoordinates(result.coordinates);
    if (mapRef.current && mapReady) {
      mapRef.current.fitToCoordinates(result.coordinates, {
        edgePadding: { top: 60, right: 50, bottom: SCREEN_HEIGHT * 0.68 + 30, left: 50 },
        animated: true,
      });
    }
  };

  const handleSelect = (index: number) => {
    if (!estimates[index]?.available) {
      setAlertState({ visible: true, title: 'Unavailable', message: 'This vehicle type is not available right now.', variant: 'warning' });
      return;
    }
    setSelectedIndex(index);
    selectVehicle(estimates[index].vehicle_type);
    setTimeout(() => fetchNearbyDrivers(), 100);
    fetchAvailablePromos(parseFloat(estimates[index].total_fare || '0'));
  };

  const handleBookRide = async () => {
    if (isBooking || isLoading) return;
    if (!selectedVehicle || !selectedEstimate) return;
    if (scheduledTime) {
      const minTime = new Date(Date.now() + 15 * 60000);
      if (scheduledTime < minTime) {
        setAlertState({ visible: true, title: 'Invalid Time', message: 'Scheduled time must be at least 15 minutes from now.', variant: 'warning' });
        return;
      }
    }
    if (workModeEnabled && activeCompanyId && selectedEstimate) {
      const when = scheduledTime ?? undefined;
      const check = checkRide(parseFloat(selectedEstimate.total_fare || '0'), when);
      if (!check.ok) {
        setAlertState({
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
    setIsBooking(true);
    try {
      const corpId = useCorporate && selectedCorporateId ? selectedCorporateId : null;
      const pmId = selectedPayment === 'card' ? (selectedCardId ?? undefined) : undefined;
      const ride = await createRide(selectedPayment, corpId, pmId);
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
      setAlertState({ visible: true, title: 'Error', message: error.message || 'Failed to book ride', variant: 'danger' });
    } finally {
      setIsBooking(false);
    }
  };

  // Date/time picker handlers
  const handleDateChange = (event: any, selectedDate?: Date) => {
    if (Platform.OS === 'android') setShowDatePicker(false);
    if (event.type === 'dismissed') { setShowDatePicker(false); return; }
    if (selectedDate) {
      setTempDate(selectedDate);
      if (Platform.OS === 'android') setShowTimePicker(true);
    }
  };

  const confirmDateSelection = () => {
    setShowDatePicker(false);
    setTimeout(() => setShowTimePicker(true), 100);
  };

  const handleTimeChange = (event: any, selectedTime?: Date) => {
    if (Platform.OS === 'android') setShowTimePicker(false);
    if (event.type === 'dismissed') { setShowTimePicker(false); return; }
    if (selectedTime) {
      const combined = new Date(tempDate);
      combined.setHours(selectedTime.getHours(), selectedTime.getMinutes());
      setTempDate(combined);
      if (Platform.OS === 'android') confirmTimeSelection(combined);
    }
  };

  const confirmTimeSelection = (timeToConfirm = tempDate) => {
    const minTime = new Date(Date.now() + 15 * 60000);
    if (timeToConfirm < minTime) {
      setAlertState({ visible: true, title: 'Invalid Time', message: 'Scheduled time must be at least 15 minutes from now.', variant: 'warning' });
      return;
    }
    setScheduledTime(timeToConfirm);
    setShowTimePicker(false);
  };

  // ── Render ──

  return (
    <View style={styles.container}>
      {/* ═══ Full-screen map ═══ */}
      {pickup && dropoff ? (
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
          {GOOGLE_MAPS_API_KEY && (
            <MapViewDirections
              origin={{ latitude: pickup.lat, longitude: pickup.lng }}
              destination={{ latitude: dropoff.lat, longitude: dropoff.lng }}
              waypoints={stops.filter(s => s.lat && s.lng).map(s => ({ latitude: s.lat, longitude: s.lng }))}
              apikey={GOOGLE_MAPS_API_KEY}
              strokeWidth={0}
              strokeColor="transparent"
              onReady={onReadyDirections}
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
          ).map((driver) => (
            <CarMarker key={driver.id} identifier={driver.id}
              coordinate={{ latitude: driver.lat, longitude: driver.lng }}
              heading={(driver as any).heading ?? Math.random() * 360} size={36} zIndex={101} />
          ))}
        </MapView>
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
      <View style={[styles.sheet, { maxHeight: SCREEN_HEIGHT * 0.68 }]}>
        <View style={styles.sheetHandle} />

        {/* Scrollable content */}
        <ScrollView style={{ flex: 1, maxHeight: sheetScrollMax }} showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled">

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

          {/* Promo banner — Lyft style */}
          {(appliedPromo || availablePromos.length > 0) && (
            <TouchableOpacity style={styles.promoBanner} onPress={() => setShowPromoSheet(true)} activeOpacity={0.8}>
              <Ionicons name="pricetag" size={16} color="#10B981" />
              {appliedPromo ? (
                <Text style={styles.promoBannerText}>
                  {appliedPromo.discount_type === 'percentage'
                    ? `Save ${appliedPromo.discount_value}% off${appliedPromo.max_discount ? ` ($${appliedPromo.max_discount} max)` : ''}`
                    : `Save $${appliedPromo.discount_value.toFixed(2)} off`}
                  {' · '}<Text style={{ fontWeight: '800' }}>{appliedPromo.code}</Text>
                </Text>
              ) : (
                <Text style={styles.promoBannerText}>
                  {availablePromos.length} promo{availablePromos.length !== 1 ? 's' : ''} available
                </Text>
              )}
              <Ionicons name="chevron-forward" size={14} color="#999" />
            </TouchableOpacity>
          )}

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

          {/* Cancel policy */}
          <View style={styles.policyRow}>
            <Ionicons name="information-circle-outline" size={14} color="#9CA3AF" />
            <Text style={styles.policyText}>
              Free cancellation within 2 min of driver acceptance.
            </Text>
          </View>
        </ScrollView>

        {/* ═══ Fixed footer — Uber style ═══ */}
        {!isLoading && !allUnavailable && estimates.length > 0 && selectedEstimate && (
          <View style={[styles.fixedFooter, { paddingBottom: Math.max(insets.bottom, 12) + 4 }]}>
            {/* Payment method row */}
            <TouchableOpacity style={styles.actionRow} onPress={() => setShowPaymentSheet(true)} activeOpacity={0.7}>
              <View style={styles.actionRowIcon}>
                <Ionicons name={paymentIcon} size={18} color={colors.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.actionRowValue}>{paymentLabel}</Text>
              </View>
              <Ionicons name="chevron-forward" size={16} color="#9CA3AF" />
            </TouchableOpacity>

            {/* Schedule row */}
            <TouchableOpacity
              style={styles.actionRow}
              onPress={() => scheduledTime ? setScheduledTime(null) : setShowDatePicker(true)}
              activeOpacity={0.7}
            >
              <View style={styles.actionRowIcon}>
                <Ionicons name="time" size={18} color={colors.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.actionRowValue}>
                  {scheduledTime
                    ? `${scheduledTime.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })} at ${scheduledTime.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })}`
                    : 'Now'}
                </Text>
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
      </View>

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
            <Text style={styles.paymentModalTitle}>Available Promos</Text>

            <TouchableOpacity
              style={[styles.promoRow, !appliedPromo && styles.promoRowSelected]}
              onPress={() => { applyPromo(null); setShowPromoSheet(false); }}>
              <View style={styles.promoRowIcon}><Ionicons name="close-circle-outline" size={22} color={colors.textDim} /></View>
              <View style={{ flex: 1 }}>
                <Text style={styles.promoRowCode}>No promo</Text>
                <Text style={styles.promoRowDesc}>Pay full fare</Text>
              </View>
              {!appliedPromo && <Ionicons name="checkmark-circle" size={22} color={colors.primary} />}
            </TouchableOpacity>

            {availablePromos.map((promo: any) => {
              const isSelected = appliedPromo?.promo_id === promo.promo_id || appliedPromo?.code === promo.code;
              const discountLabel = promo.discount_type === 'percentage'
                ? `${promo.discount_value}% off${promo.max_discount ? ` (max $${promo.max_discount})` : ''}`
                : `$${promo.discount_value.toFixed(2)} off`;
              return (
                <TouchableOpacity key={promo.promo_id || promo.code}
                  style={[styles.promoRow, isSelected && styles.promoRowSelected]}
                  onPress={() => { applyPromo(promo); setShowPromoSheet(false); }}>
                  <View style={[styles.promoRowIcon, { backgroundColor: '#ECFDF5' }]}>
                    <Ionicons name="pricetag" size={20} color="#10B981" />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.promoRowCode}>{promo.code}</Text>
                    <Text style={styles.promoRowDesc}>{promo.description || discountLabel}</Text>
                    <Text style={styles.promoRowSaving} allowFontScaling={false}>{discountLabel}</Text>
                  </View>
                  {isSelected && <Ionicons name="checkmark-circle" size={22} color={colors.primary} />}
                </TouchableOpacity>
              );
            })}

            {availablePromos.length === 0 && (
              <View style={styles.promoEmpty}>
                <Ionicons name="gift-outline" size={36} color={colors.border} />
                <Text style={styles.promoEmptyText}>No promos available right now</Text>
              </View>
            )}

            <TouchableOpacity style={styles.paymentDoneBtn} onPress={() => setShowPromoSheet(false)}>
              <Text style={styles.paymentDoneBtnText}>Close</Text>
            </TouchableOpacity>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      {/* ═══ Date / Time pickers ═══ */}
      {showDatePicker && (
        Platform.OS === 'ios' ? (
          <Modal transparent animationType="slide" visible>
            <View style={styles.modalOverlay}>
              <View style={styles.pickerContainer}>
                <View style={styles.pickerHeader}>
                  <TouchableOpacity onPress={() => setShowDatePicker(false)}>
                    <Text style={styles.pickerCancelText}>Cancel</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={confirmDateSelection}>
                    <Text style={styles.pickerDoneText}>Next</Text>
                  </TouchableOpacity>
                </View>
                <DateTimePicker value={tempDate} mode="date" display="spinner"
                  minimumDate={new Date(Date.now() + 15 * 60 * 1000)}
                  maximumDate={new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)}
                  onChange={handleDateChange} textColor="#000000" />
              </View>
            </View>
          </Modal>
        ) : (
          <DateTimePicker value={tempDate} mode="date" display="default"
            minimumDate={new Date(Date.now() + 15 * 60 * 1000)}
            maximumDate={new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)}
            onChange={handleDateChange} />
        )
      )}
      {showTimePicker && (
        Platform.OS === 'ios' ? (
          <Modal transparent animationType="slide" visible>
            <View style={styles.modalOverlay}>
              <View style={styles.pickerContainer}>
                <View style={styles.pickerHeader}>
                  <TouchableOpacity onPress={() => setShowTimePicker(false)}>
                    <Text style={styles.pickerCancelText}>Cancel</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => confirmTimeSelection()}>
                    <Text style={styles.pickerDoneText}>Done</Text>
                  </TouchableOpacity>
                </View>
                <DateTimePicker value={tempDate} mode="time" display="spinner"
                  onChange={handleTimeChange} textColor="#000000" />
              </View>
            </View>
          </Modal>
        ) : (
          <DateTimePicker value={tempDate} mode="time" display="default" onChange={handleTimeChange} />
        )
      )}

      <CustomAlert
        visible={alertState.visible} title={alertState.title} message={alertState.message}
        variant={alertState.variant} buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))} />
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
          {appliedPromo && appliedPromo.discount_value > 0 && isSelected ? (
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.optionPriceStruck} allowFontScaling={false}>
                ${parseFloat(estimate.total_fare || '0').toFixed(2)}
              </Text>
              <Text style={styles.optionPriceDiscounted} allowFontScaling={false}>
                ${Math.max(0, parseFloat(estimate.total_fare || '0') - appliedPromo.discount_value).toFixed(2)}
              </Text>
            </View>
          ) : (
            <Text style={styles.optionPrice} allowFontScaling={false}>
              ${parseFloat(estimate.total_fare || '0').toFixed(2)}
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
    sheet: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.12,
      shadowRadius: 12,
      elevation: 16,
    },
    sheetHandle: {
      width: 40,
      height: 4,
      borderRadius: 2,
      backgroundColor: '#D1D5DB',
      alignSelf: 'center',
      marginTop: 10,
      marginBottom: 4,
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

    // ── Promo / work banners ──
    promoBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      marginHorizontal: 16,
      marginTop: 8,
      marginBottom: 4,
      backgroundColor: '#ECFDF5',
      borderRadius: 12,
      paddingVertical: 10,
      paddingHorizontal: 14,
      gap: 8,
    },
    promoBannerText: {
      flex: 1,
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#065F46',
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

    // ── Fixed footer (Uber style) ──
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
      maxHeight: '80%',
    },
    promoRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 14,
      paddingVertical: 14,
      paddingHorizontal: 12,
      borderRadius: 14,
      marginBottom: 8,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1.5,
      borderColor: 'transparent',
    },
    promoRowSelected: {
      borderColor: colors.primary,
      backgroundColor: `${colors.primary}10`,
    },
    promoRowIcon: {
      width: 40,
      height: 40,
      borderRadius: 10,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
    },
    promoRowCode: {
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      letterSpacing: 0.5,
    },
    promoRowDesc: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 1,
    },
    promoRowSaving: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#10B981',
      marginTop: 2,
    },
    promoEmpty: {
      alignItems: 'center',
      paddingVertical: 28,
      gap: 8,
    },
    promoEmptyText: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },

    // ── Pickers ──
    pickerContainer: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 16,
      borderTopRightRadius: 16,
      paddingBottom: 20,
    },
    pickerHeader: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      padding: 16,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    pickerCancelText: {
      fontSize: sf(16),
      color: colors.textSecondary,
      fontFamily: 'PlusJakartaSans_500Medium',
    },
    pickerDoneText: {
      fontSize: sf(16),
      color: colors.primary,
      fontFamily: 'PlusJakartaSans_600SemiBold',
    },
  });
}
