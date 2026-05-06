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
  Switch,
  Modal,
  TextInput,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Circle, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { useRideStore } from '../store/rideStore';
import { useWorkProfileStore } from '../store/workProfileStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { CarMarker } from '@shared/components/CarMarker';
import DateTimePicker from '@react-native-community/datetimepicker';
import SkeletonBox from '../components/SkeletonBox';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

function RideOptionsScreenContent() {
  const { width: SCREEN_WIDTH, height } = useWindowDimensions();
  // Reactive map height: 35% of current screen height, clamped for usability
  const MAP_HEIGHT = Math.min(Math.max(Math.round(height * 0.35), 180), 380);
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors, MAP_HEIGHT), [colors, MAP_HEIGHT]);
  const router = useRouter();
  const {
    pickup,
    dropoff,
    stops,
    estimates,
    selectedVehicle,
    fetchEstimates,
    fetchNearbyDrivers,
    nearbyDrivers,
    selectVehicle,
    isLoading,
    error: storeError,
    scheduledTime,
    setScheduledTime,
    requiresWav,
    setRequiresWav,
    quietMode,
    setQuietMode,
    riderNotes,
    setRiderNotes,
    availablePromos,
    appliedPromo,
    fetchAvailablePromos,
    applyPromo,
  } = useRideStore();

  const workModeEnabled = useWorkProfileStore(s => s.workModeEnabled);
  const activeCompanyId = useWorkProfileStore(s => s.activeCompanyId);
  const workProfiles = useWorkProfileStore(s => s.profiles);
  const fetchPolicy = useWorkProfileStore(s => s.fetchPolicy);
  const checkRide = useWorkProfileStore(s => s.checkRide);
  const activeCompanyName = useMemo(
    () => workProfiles.find(p => p.company.id === activeCompanyId)?.company.name ?? null,
    [workProfiles, activeCompanyId],
  );

  const [fetchError, setFetchError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [routeKey, setRouteKey] = useState(0);
  const [routeCoordinates, setRouteCoordinates] = useState<any[]>([]);
  const [mapReady, setMapReady] = useState(false);
  const [isScheduling, setIsScheduling] = useState(false);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [showTimePicker, setShowTimePicker] = useState(false);
  const [showPromoSheet, setShowPromoSheet] = useState(false);
  const [tempDate, setTempDate] = useState<Date>(new Date(Date.now() + 30 * 60000)); // default 30 min from now
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });
  const mapRef = useRef<MapView>(null);

  const handleFetchEstimates = async () => {
    setFetchError(null);
    try {
      await fetchEstimates();
    } catch {
      setFetchError('Could not load fares. Tap to retry.');
    }
  };

  useEffect(() => {
    if (pickup && dropoff) {
      console.log('Platform:', Platform.OS, '| Fetching estimates & nearby drivers for:', pickup.address, 'to', dropoff.address);
      // R-P2-51: run both fetches in parallel — they are independent requests.
      void Promise.all([handleFetchEstimates(), fetchNearbyDrivers()]);

      // Auto-refresh drivers every 10 seconds
      const interval = setInterval(() => {
        fetchNearbyDrivers();
      }, 10000);

      return () => clearInterval(interval);
    }
  }, [pickup, dropoff]);

  // Refresh work policy when work mode is active
  useEffect(() => {
    if (workModeEnabled && activeCompanyId) {
      fetchPolicy();
    }
  }, [workModeEnabled, activeCompanyId]);

  // Fetch promos when estimates are ready
  useEffect(() => {
    if (estimates.length > 0) {
      const selectedFare = parseFloat(estimates[selectedIndex]?.total_fare || estimates[0]?.total_fare || '0');
      fetchAvailablePromos(selectedFare);
    }
  }, [estimates]);

  useEffect(() => {
    // Auto-select first AVAILABLE vehicle
    if (estimates.length > 0 && !selectedVehicle) {
      const firstAvailableIndex = estimates.findIndex(e => e.available);
      if (firstAvailableIndex !== -1) {
        setSelectedIndex(firstAvailableIndex);
        selectVehicle(estimates[firstAvailableIndex].vehicle_type);
      } else {
        // Fallback to first if none available
        setSelectedIndex(0);
        selectVehicle(estimates[0].vehicle_type);
      }
    }
  }, [estimates, isLoading]);

  // Fit map to markers
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
            edgePadding: { top: 50, right: 50, bottom: 50, left: 50 },
            animated: true,
          });
        }, 300);
      }
    }
  }, [pickup, dropoff, nearbyDrivers, routeCoordinates, mapReady]);

  // Helper to trigger map fit when directions are ready
  const onReadyDirections = (result: any) => {
    if (result.coordinates) {
      setRouteCoordinates(result.coordinates);
    }
    if (mapRef.current && mapReady) {
      mapRef.current.fitToCoordinates(result.coordinates, {
        edgePadding: { top: 50, right: 50, bottom: 50, left: 50 },
        animated: true,
      });
    }
  };

  const handleSelect = (index: number) => {
    if (!estimates[index]?.available) {
      setAlertState({ visible: true, title: 'Unavailable', message: 'This vehicle type is not available right now. Please choose another.', variant: 'warning' });
      return;
    }
    setSelectedIndex(index);
    selectVehicle(estimates[index].vehicle_type);
    // Re-fetch nearby drivers filtered by this vehicle type
    setTimeout(() => fetchNearbyDrivers(), 100);
    // Re-calculate promo discount for new fare
    fetchAvailablePromos(parseFloat(estimates[index].total_fare || '0'));
  };

  const handleConfirm = () => {
    if (!selectedVehicle) return;
    if (isScheduling && !scheduledTime) {
      setAlertState({ visible: true, title: 'Select Time', message: 'Please pick a date and time for your scheduled ride.', variant: 'warning' });
      return;
    }
    if (workModeEnabled && activeCompanyId && selectedEstimate) {
      const when = isScheduling && scheduledTime ? scheduledTime : undefined;
      const check = checkRide(parseFloat(selectedEstimate.total_fare || '0'), when);
      if (!check.ok) {
        setAlertState({
          visible: true,
          title: 'Blocked by company policy',
          message: check.reasons.join('\n'),
          variant: 'warning',
          buttons: [
            { text: 'Turn off work mode', style: 'destructive', onPress: () => {
              useWorkProfileStore.getState().setWorkMode(false);
            }},
            { text: 'Change ride', style: 'cancel' },
          ],
        });
        return;
      }
    }
    router.push('/payment-confirm');
  };

  const handleToggleSchedule = (value: boolean) => {
    setIsScheduling(value);
    if (!value) {
      setScheduledTime(null);
    } else {
      setShowDatePicker(true);
    }
  };

  const handleCancelSchedule = () => {
    setShowDatePicker(false);
    setShowTimePicker(false);
    if (!scheduledTime) {
      setIsScheduling(false);
    }
  };

  const handleDateChange = (event: any, selectedDate?: Date) => {
    if (Platform.OS === 'android') {
      setShowDatePicker(false);
    }
    if (event.type === 'dismissed') {
      handleCancelSchedule();
      return;
    }
    if (selectedDate) {
      setTempDate(selectedDate);
      if (Platform.OS === 'android') {
        setShowTimePicker(true);
      }
    }
  };

  const confirmDateSelection = () => {
    setShowDatePicker(false);
    setTimeout(() => setShowTimePicker(true), 100);
  };

  const handleTimeChange = (event: any, selectedTime?: Date) => {
    if (Platform.OS === 'android') {
      setShowTimePicker(false);
    }
    if (event.type === 'dismissed') {
      handleCancelSchedule();
      return;
    }
    if (selectedTime) {
      const combined = new Date(tempDate);
      combined.setHours(selectedTime.getHours(), selectedTime.getMinutes());
      setTempDate(combined);
      
      if (Platform.OS === 'android') {
        confirmTimeSelection(combined);
      }
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

  const selectedEstimate = estimates.length > selectedIndex ? estimates[selectedIndex] : null;
  const allUnavailable = estimates.length > 0 && !estimates.some(e => e.available);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.backButton}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        {dropoff && (
          <View style={styles.destinationChip}>
            <Text style={styles.destinationChipText} numberOfLines={1}>
              GOING TO {dropoff.address.toUpperCase()}
            </Text>
          </View>
        )}
        <View style={{ width: 44 }} />
      </View>

      {/* Dynamic Map */}
      <View style={styles.mapContainer}>
        {pickup && dropoff ? (
          <MapView
            ref={mapRef}
            key={`map-${routeKey}`} // NUCLEAR OPTION: Force full map re-mount on route change
            provider={Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined}
            style={styles.map}
            onMapReady={() => setMapReady(true)}
            initialRegion={{
              latitude: pickup.lat,
              longitude: pickup.lng,
              latitudeDelta: 0.05,
              longitudeDelta: 0.05,
            }}
          >
            {/* Fetch route via MapViewDirections (hidden stroke) */}
            {GOOGLE_MAPS_API_KEY && (
              <MapViewDirections
                origin={{ latitude: pickup.lat, longitude: pickup.lng }}
                destination={{ latitude: dropoff.lat, longitude: dropoff.lng }}
                waypoints={stops.filter(s => s.lat && s.lng).map(s => ({ latitude: s.lat, longitude: s.lng }))}
                apikey={GOOGLE_MAPS_API_KEY}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={onReadyDirections}
                optimizeWaypoints={true}
              />
            )}

            {/* Gradient route polyline — red to orange */}
            {routeCoordinates.length > 1 && (() => {
              // Split route into segments with interpolated colors for iOS + Android
              const total = routeCoordinates.length;
              const segments: { coords: any[]; color: string }[] = [];
              const SEGMENT_COUNT = 30;
              const chunkSize = Math.max(1, Math.floor(total / SEGMENT_COUNT));

              for (let i = 0; i < total - 1; i += chunkSize) {
                const end = Math.min(i + chunkSize + 1, total);
                const t = i / Math.max(total - 1, 1);
                // Interpolate: #FF9500 (orange) → #FF6B35 (orange-red) → #ee2b2b (red)
                const r = Math.round(255 + (238 - 255) * t);
                const g = Math.round(149 + (43 - 149) * t);
                const b = Math.round(0 + (43 - 0) * t);
                const color = `rgb(${r},${g},${b})`;
                segments.push({ coords: routeCoordinates.slice(i, end), color });
              }

              return (
                <>
                  {/* Outer glow */}
                  <Polyline
                    coordinates={routeCoordinates}
                    strokeWidth={9}
                    strokeColor="rgba(238, 43, 43, 0.12)"
                  />
                  {/* Gradient segments — works on both iOS and Android */}
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

            {/* Pickup Marker */}
            <Marker coordinate={{ latitude: pickup.lat, longitude: pickup.lng }} anchor={{ x: 0.5, y: 0.5 }} zIndex={103}>
              <View style={styles.markerContainer}>
                <View style={[styles.markerDot, { backgroundColor: '#10B981' }]} />
              </View>
            </Marker>

            {/* Dropoff Marker */}
            <Marker coordinate={{ latitude: dropoff.lat, longitude: dropoff.lng }} anchor={{ x: 0.5, y: 0.5 }} zIndex={103}>
              <View style={styles.markerContainer}>
                <View style={[styles.markerDot, { backgroundColor: '#EF4444' }]} />
              </View>
            </Marker>

            {/* Stops Markers */}
            {stops.map((stop, i) => (
              <Marker key={`stop-${i}`} coordinate={{ latitude: stop.lat, longitude: stop.lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={styles.markerContainer}>
                  <View style={[styles.markerDot, { backgroundColor: '#F59E0B' }]} />
                </View>
              </Marker>
            ))}

            {/* Nearby Drivers - 3D car markers filtered by selected vehicle type */}
            {nearbyDrivers.filter(d =>
              typeof d.lat === 'number' && !isNaN(d.lat) &&
              typeof d.lng === 'number' && !isNaN(d.lng) &&
              Math.abs(d.lat) > 0.1 && Math.abs(d.lng) > 0.1
            ).map((driver) => (
              <CarMarker
                key={driver.id}
                identifier={driver.id}
                coordinate={{ latitude: driver.lat, longitude: driver.lng }}
                heading={(driver as any).heading ?? Math.random() * 360}
                size={36}
                zIndex={101}
              />
            ))}
          </MapView>
        ) : (
          <View style={styles.mapPlaceholder}>
            <ActivityIndicator size="small" color={colors.primary} />
          </View>
        )}
      </View>

      {/* Promo Banner */}
      {(appliedPromo || availablePromos.length > 0) && (
        <TouchableOpacity
          style={styles.promoBanner}
          onPress={() => setShowPromoSheet(true)}
          activeOpacity={0.8}
        >
          <Ionicons name="pricetag" size={16} color="#10B981" />
          {appliedPromo ? (
            <Text style={styles.promoBannerText}>
              {appliedPromo.discount_type === 'percentage'
                ? `Save ${appliedPromo.discount_value}% off${appliedPromo.max_discount ? ` ($${appliedPromo.max_discount} max)` : ''}`
                : `Save $${appliedPromo.discount_value.toFixed(2)} off`}
              {' · '}
              <Text style={{ fontWeight: '800' }}>{appliedPromo.code}</Text>
            </Text>
          ) : (
            <Text style={styles.promoBannerText}>
              {availablePromos.length} promo{availablePromos.length !== 1 ? 's' : ''} available — tap to select
            </Text>
          )}
          {availablePromos.length > 1 && appliedPromo && (
            <Text style={styles.promoBannerMore}>{availablePromos.length - 1} more</Text>
          )}
          <Ionicons name="chevron-forward" size={14} color="#999" />
        </TouchableOpacity>
      )}

      {/* Work Mode Banner */}
      {workModeEnabled && activeCompanyId && (
        <View style={styles.workBanner}>
          <Ionicons name="briefcase" size={16} color="#1D4ED8" />
          <View style={{ flex: 1 }}>
            <Text style={styles.workBannerTitle}>
              Billed to {activeCompanyName ?? 'your employer'}
            </Text>
            <Text style={styles.workBannerSubtitle}>
              This ride will be covered by your work allowance.
            </Text>
          </View>
        </View>
      )}

      {/* Options Header */}
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Choose a ride</Text>
        <View style={styles.commissionBadge}>
          <Text style={styles.commissionText}>0% Commission</Text>
        </View>
      </View>

      {/* Busy Banner */}
      {allUnavailable && !isLoading && (
        <View style={styles.busyBanner}>
          <Ionicons name="warning" size={20} color="#B91C1C" />
          <Text style={styles.busyText}>No cars available right now. Please try again later.</Text>
        </View>
      )}

      {/* Vehicle Options */}
      {fetchError ? (
        <View style={styles.loadingContainer}>
          <Ionicons name="alert-circle-outline" size={40} color="#EF4444" />
          <Text style={[styles.loadingText, { color: '#EF4444', marginTop: 10 }]}>{fetchError}</Text>
          <TouchableOpacity style={styles.retryButton} onPress={handleFetchEstimates}>
            <Text style={styles.retryButtonText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : isLoading ? (
        <View style={styles.optionsList}>
          {[0, 1, 2].map((i) => (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', padding: 16, marginBottom: 8, backgroundColor: colors.surface, borderRadius: 12 }}>
              <SkeletonBox width={72} height={48} borderRadius={8} style={{ marginRight: 14 }} />
              <View style={{ flex: 1, gap: 8 }}>
                <SkeletonBox width="50%" height={16} />
                <SkeletonBox width="35%" height={12} />
              </View>
              <SkeletonBox width={52} height={22} borderRadius={6} />
            </View>
          ))}
        </View>
      ) : (
        <ScrollView style={styles.optionsList} showsVerticalScrollIndicator={false}>
          {estimates.map((estimate, index) => {
            const isSelected = selectedIndex === index;
            const isAvailable = estimate.available;

            return (
              <TouchableOpacity
                key={estimate.vehicle_type.id}
                style={[
                  styles.optionCard,
                  isSelected && isAvailable && styles.optionCardSelected,
                  !isAvailable && styles.optionCardDisabled,
                ]}
                onPress={() => handleSelect(index)}
                activeOpacity={isAvailable ? 0.7 : 1}
                disabled={!isAvailable}
              >
                {/* Car Image */}
                <View style={[styles.carImageContainer, !isAvailable && { opacity: 0.4 }]}>
                  {estimate.vehicle_type.image_url ? (
                    <Image
                      source={{ uri: estimate.vehicle_type.image_url }}
                      style={styles.carImage}
                      resizeMode="contain"
                    />
                  ) : (
                    <View style={styles.carIconFallback}>
                      <Ionicons name="car" size={36} color="#666" />
                    </View>
                  )}
                </View>

                {/* Info */}
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
                      <Ionicons name="person" size={12} color="#666" />
                      <Text style={styles.capacityText}>{estimate.vehicle_type.capacity}</Text>
                    </View>
                  </View>

                  {isAvailable ? (
                    <Text style={styles.optionETA} allowFontScaling={false}>
                      {estimate.eta_minutes ? `${estimate.eta_minutes} min away` : 'Nearby'}
                      {estimate.driver_count > 0 && ` · ${estimate.driver_count} driver${estimate.driver_count > 1 ? 's' : ''}`}
                    </Text>
                  ) : (
                    <Text style={styles.unavailableText}>No drivers nearby</Text>
                  )}
                  {(estimate.surge_multiplier ?? 1) > 1.0 && (
                    <Text style={styles.surgeNotice}>Fares are higher due to increased demand</Text>
                  )}
                </View>

                {/* Price — with promo struck-through */}
                <View style={[styles.optionPriceContainer, !isAvailable && { opacity: 0.4 }]}>
                  {appliedPromo && appliedPromo.discount_value > 0 && isSelected ? (
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={styles.optionPriceStruck} allowFontScaling={false}>${parseFloat(estimate.total_fare || '0').toFixed(2)}</Text>
                      <Text style={styles.optionPriceDiscounted} allowFontScaling={false}>
                        ${Math.max(0, parseFloat(estimate.total_fare || '0') - appliedPromo.discount_value).toFixed(2)}
                      </Text>
                    </View>
                  ) : (
                    <Text style={styles.optionPrice} allowFontScaling={false}>${parseFloat(estimate.total_fare || '0').toFixed(2)}</Text>
                  )}
                  {isSelected && isAvailable && (
                    <View style={styles.selectedCheck}>
                      <Ionicons name="checkmark-circle" size={22} color={colors.primary} />
                    </View>
                  )}
                </View>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      )}

      {/* Confirm Button */}
      {!isLoading && !allUnavailable && estimates.length > 0 && selectedEstimate && (
        <View style={styles.footer}>
          {/* Schedule Toggle */}
          <View style={styles.scheduleRow}>
            <View style={styles.scheduleInfo}>
              <Ionicons name="time-outline" size={20} color="#1A1A1A" />
              <Text style={styles.scheduleLabel}>Schedule later</Text>
            </View>
            <Switch
              value={isScheduling}
              onValueChange={handleToggleSchedule}
              trackColor={{ false: '#D1D5DB', true: colors.primary + '60' }}
              thumbColor={isScheduling ? colors.primary : '#F3F4F6'}
            />
          </View>

          {/* Scheduled Time Display */}
          {isScheduling && scheduledTime && (
            <TouchableOpacity
              style={styles.scheduledTimeRow}
              onPress={() => setShowDatePicker(true)}
            >
              <Ionicons name="calendar-outline" size={18} color={colors.primary} />
              <Text style={styles.scheduledTimeText} allowFontScaling={false}>
                {scheduledTime.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })}{' '}
                at {scheduledTime.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })}
              </Text>
              <Text style={styles.changeText}>Change</Text>
            </TouchableOpacity>
          )}

          {/* DateTimePicker Modals */}
          {showDatePicker && (
            Platform.OS === 'ios' ? (
              <Modal transparent animationType="slide" visible={showDatePicker}>
                <View style={styles.modalOverlay}>
                  <View style={styles.pickerContainer}>
                    <View style={styles.pickerHeader}>
                      <TouchableOpacity onPress={handleCancelSchedule}>
                        <Text style={styles.pickerCancelText}>Cancel</Text>
                      </TouchableOpacity>
                      <TouchableOpacity onPress={confirmDateSelection}>
                        <Text style={styles.pickerDoneText}>Next</Text>
                      </TouchableOpacity>
                    </View>
                    <DateTimePicker
                      value={tempDate}
                      mode="date"
                      display="spinner"
                      minimumDate={new Date(Date.now() + 15 * 60 * 1000)}
                      maximumDate={new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)}
                      onChange={handleDateChange}
                      textColor="#000000"
                    />
                  </View>
                </View>
              </Modal>
            ) : (
              <DateTimePicker
                value={tempDate}
                mode="date"
                display="default"
                minimumDate={new Date(Date.now() + 15 * 60 * 1000)}
                maximumDate={new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)}
                onChange={handleDateChange}
              />
            )
          )}
          {showTimePicker && (
            Platform.OS === 'ios' ? (
              <Modal transparent animationType="slide" visible={showTimePicker}>
                <View style={styles.modalOverlay}>
                  <View style={styles.pickerContainer}>
                    <View style={styles.pickerHeader}>
                      <TouchableOpacity onPress={handleCancelSchedule}>
                        <Text style={styles.pickerCancelText}>Cancel</Text>
                      </TouchableOpacity>
                      <TouchableOpacity onPress={() => confirmTimeSelection()}>
                        <Text style={styles.pickerDoneText}>Done</Text>
                      </TouchableOpacity>
                    </View>
                    <DateTimePicker
                      value={tempDate}
                      mode="time"
                      display="spinner"
                      onChange={handleTimeChange}
                      textColor="#000000"
                    />
                  </View>
                </View>
              </Modal>
            ) : (
              <DateTimePicker
                value={tempDate}
                mode="time"
                display="default"
                onChange={handleTimeChange}
              />
            )
          )}

          {/* WAV toggle */}
          <View style={styles.scheduleRow} accessibilityRole="none">
            <View style={styles.scheduleInfo}>
              <Ionicons name="accessibility" size={20} color="#1A1A1A" />
              <View>
                <Text style={styles.scheduleLabel}>Wheelchair-accessible vehicle</Text>
                <Text style={styles.wavSubLabel}>Only match me with WAV drivers</Text>
              </View>
            </View>
            <Switch
              value={requiresWav}
              onValueChange={setRequiresWav}
              trackColor={{ false: '#D1D5DB', true: colors.primary + '60' }}
              thumbColor={requiresWav ? colors.primary : '#F3F4F6'}
              accessibilityLabel="Request wheelchair-accessible vehicle"
              accessibilityRole="switch"
            />
          </View>
          {requiresWav && (
            <View style={styles.wavBanner}>
              <Ionicons name="information-circle-outline" size={14} color="#1D4ED8" />
              <Text style={styles.wavBannerText}>
                WAV rides may have longer wait times depending on driver availability.
              </Text>
            </View>
          )}

          {/* Quiet mode toggle */}
          <View style={styles.scheduleRow} accessibilityRole="none">
            <View style={styles.scheduleInfo}>
              <Ionicons name="volume-mute" size={20} color="#1A1A1A" />
              <View>
                <Text style={styles.scheduleLabel}>Quiet ride</Text>
                <Text style={styles.wavSubLabel}>Prefer minimal conversation</Text>
              </View>
            </View>
            <Switch
              value={quietMode}
              onValueChange={setQuietMode}
              trackColor={{ false: '#D1D5DB', true: colors.primary + '60' }}
              thumbColor={quietMode ? colors.primary : '#F3F4F6'}
              accessibilityLabel="Request quiet ride"
              accessibilityRole="switch"
            />
          </View>

          {/* Notes to driver */}
          <View style={styles.notesRow}>
            <Ionicons name="chatbubble-outline" size={18} color="#6B7280" style={{ marginTop: 2 }} />
            <TextInput
              style={styles.notesInput}
              placeholder="Note for driver (optional)"
              placeholderTextColor="#9CA3AF"
              value={riderNotes}
              onChangeText={setRiderNotes}
              maxLength={200}
              multiline={false}
              returnKeyType="done"
              accessibilityLabel="Note for your driver"
            />
          </View>

          {/* Payment method row */}
          <TouchableOpacity style={styles.paymentRow}>
            <Ionicons name="card" size={20} color="#1A1A1A" />
            <Text style={styles.paymentText}>Visa •••• 4242</Text>
            <Ionicons name="chevron-forward" size={16} color="#999" />
          </TouchableOpacity>

          {/* Cancellation policy disclosure (UX-001) */}
          <View style={styles.cancelPolicyRow}>
            <Ionicons name="information-circle-outline" size={15} color="#6B7280" />
            <Text style={styles.cancelPolicyText}>
              Free cancellation within 2 min of driver acceptance. A cancellation fee applies after.
            </Text>
          </View>

          <TouchableOpacity
            style={styles.confirmButton}
            onPress={handleConfirm}
            activeOpacity={0.8}
            disabled={!selectedEstimate.available}
          >
            <Text style={styles.confirmButtonText}>
              {isScheduling ? 'Schedule' : 'Confirm'} {selectedEstimate.vehicle_type.name}
            </Text>
          </TouchableOpacity>
        </View>
      )}
      {/* Promo Selection Bottom Sheet */}
      <Modal
        visible={showPromoSheet}
        animationType="slide"
        transparent
        onRequestClose={() => setShowPromoSheet(false)}
      >
        <TouchableOpacity
          style={styles.modalOverlay}
          activeOpacity={1}
          onPress={() => setShowPromoSheet(false)}
        >
          <TouchableOpacity activeOpacity={1} style={styles.promoSheet}>
            <View style={styles.promoSheetHandle} />
            <Text style={styles.promoSheetTitle}>Available Promos</Text>

            {/* No promo option */}
            <TouchableOpacity
              style={[styles.promoRow, !appliedPromo && styles.promoRowSelected]}
              onPress={() => { applyPromo(null); setShowPromoSheet(false); }}
            >
              <View style={styles.promoRowIcon}>
                <Ionicons name="close-circle-outline" size={22} color={colors.textDim} />
              </View>
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
                <TouchableOpacity
                  key={promo.promo_id || promo.code}
                  style={[styles.promoRow, isSelected && styles.promoRowSelected]}
                  onPress={() => { applyPromo(promo); setShowPromoSheet(false); }}
                >
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
                <Ionicons name="gift-outline" size={40} color={colors.border} />
                <Text style={styles.promoEmptyText}>No promos available right now</Text>
              </View>
            )}

            <TouchableOpacity style={styles.promoCloseBtn} onPress={() => setShowPromoSheet(false)}>
              <Text style={styles.promoCloseBtnText}>Close</Text>
            </TouchableOpacity>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

export default function RideOptionsScreen() {
  return (
    <ErrorBoundary>
      <RideOptionsScreenContent />
    </ErrorBoundary>
  );
}

function createStyles(colors: ThemeColors, mapHeight: number = 280) {
  return StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  backButton: {
    width: 44,
    height: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  destinationChip: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 8,
    marginHorizontal: 8,
    alignItems: 'center',
  },
  destinationChipText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: colors.textSecondary,
    letterSpacing: 0.5,
  },
  mapContainer: {
    marginHorizontal: 0,
    backgroundColor: colors.border,
    height: mapHeight,
  },
  map: {
    width: '100%',
    height: '100%',
  },
  mapPlaceholder: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
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
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  sectionTitle: {
    fontSize: 18,
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
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#2E7D32',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    marginTop: 16,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: colors.textDim,
  },
  retryButton: {
    marginTop: 16,
    paddingHorizontal: 28,
    paddingVertical: 12,
    backgroundColor: colors.primary,
    borderRadius: 24,
  },
  retryButtonText: {
    color: '#FFF',
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  optionsList: {
    flex: 1,
  },
  optionCard: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  optionCardSelected: {
    backgroundColor: `${colors.primary}14`,
    borderLeftWidth: 3,
    borderLeftColor: colors.primary,
  },
  optionCardDisabled: {
    backgroundColor: colors.surfaceLight,
  },
  carImageContainer: {
    width: 72,
    height: 48,
    marginRight: 14,
    justifyContent: 'center',
    alignItems: 'center',
  },
  carImage: {
    width: 72,
    height: 48,
  },
  carIconFallback: {
    width: 72,
    height: 48,
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
    gap: 6,
  },
  optionName: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: colors.text,
  },
  surgeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: '#EF4444',
    borderRadius: 8,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  surgeBadgeText: {
    fontSize: 10,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#fff',
  },
  surgeNotice: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#EF4444',
    marginTop: 2,
  },
  capacityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
    backgroundColor: colors.surfaceLight,
    borderRadius: 8,
    paddingHorizontal: 5,
    paddingVertical: 2,
  },
  capacityText: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.textDim,
  },
  optionETA: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#10B981',
    marginTop: 2,
  },
  unavailableText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: colors.textDim,
    marginTop: 2,
    fontStyle: 'italic',
  },
  optionPriceContainer: {
    alignItems: 'flex-end',
  },
  optionPrice: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: colors.text,
  },
  optionPriceStruck: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.textDim,
    textDecorationLine: 'line-through',
  },
  optionPriceDiscounted: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#10B981',
  },
  workBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 20,
    marginTop: 8,
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
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1E3A8A',
  },
  workBannerSubtitle: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#1D4ED8',
    marginTop: 1,
  },
  promoBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 20,
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
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#065F46',
  },
  promoBannerMore: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#10B981',
  },
  selectedCheck: {
    marginTop: 4,
  },
  footer: {
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 20,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
  },
  paymentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    marginBottom: 12,
  },
  paymentText: {
    flex: 1,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.text,
  },
  cancelPolicyRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    marginBottom: 10,
    paddingHorizontal: 4,
  },
  cancelPolicyText: {
    flex: 1,
    fontSize: 12,
    color: colors.textSecondary,
    lineHeight: 17,
  },
  confirmButton: {
    backgroundColor: colors.primary,
    borderRadius: 28,
    paddingVertical: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  confirmButtonText: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFFFFF',
  },
  busyBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FEF2F2',
    padding: 12,
    margin: 16,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#FCA5A5',
    gap: 10,
  },
  busyText: {
    color: '#B91C1C',
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    flex: 1,
  },
  scheduleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    marginBottom: 4,
  },
  scheduleInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  scheduleLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.text,
  },
  wavSubLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: colors.textDim,
    marginTop: 1,
  },
  wavBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    backgroundColor: '#EFF6FF',
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 10,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#BFDBFE',
  },
  wavBannerText: {
    flex: 1,
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#1D4ED8',
    lineHeight: 17,
  },
  scheduledTimeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    marginBottom: 8,
  },
  scheduledTimeText: {
    flex: 1,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.text,
  },
  changeText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: colors.primary,
  },
  modalOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.4)',
  },
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
    fontSize: 16,
    color: colors.textSecondary,
    fontFamily: 'PlusJakartaSans_500Medium',
  },
  pickerDoneText: {
    fontSize: 16,
    color: colors.primary,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  // Promo Sheet
  promoSheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingBottom: 32,
    paddingTop: 12,
    maxHeight: '80%',
  },
  promoSheetHandle: {
    width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border,
    alignSelf: 'center', marginBottom: 16,
  },
  promoSheetTitle: {
    fontSize: 18, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    marginBottom: 16,
  },
  promoRow: {
    flexDirection: 'row', alignItems: 'center', gap: 14,
    paddingVertical: 14, paddingHorizontal: 12, borderRadius: 14,
    marginBottom: 8, backgroundColor: colors.surfaceLight,
    borderWidth: 1.5, borderColor: 'transparent',
  },
  promoRowSelected: {
    borderColor: colors.primary, backgroundColor: `${colors.primary}10`,
  },
  promoRowIcon: {
    width: 44, height: 44, borderRadius: 12, justifyContent: 'center', alignItems: 'center',
    backgroundColor: colors.surfaceLight,
  },
  promoRowCode: {
    fontSize: 16, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text, letterSpacing: 0.5,
  },
  promoRowDesc: {
    fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 1,
  },
  promoRowSaving: {
    fontSize: 13, fontFamily: 'PlusJakartaSans_600SemiBold', color: '#10B981', marginTop: 2,
  },
  promoEmpty: {
    alignItems: 'center', paddingVertical: 32, gap: 10,
  },
  promoEmptyText: {
    fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim,
  },
  promoCloseBtn: {
    marginTop: 8, paddingVertical: 14, borderRadius: 24,
    backgroundColor: colors.surfaceLight, alignItems: 'center',
  },
  promoCloseBtnText: {
    fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.textDim,
  },
  fareBreakdown: {
    backgroundColor: colors.surfaceLight,
    marginHorizontal: 16,
    marginVertical: 8,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: colors.border,
  },
  fareBreakdownHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  fareBreakdownTitle: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: colors.text,
  },
  fareBreakdownVehicle: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.textSecondary,
    backgroundColor: colors.border,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  fareRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 5,
  },
  fareLabel: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: colors.textSecondary,
  },
  fareValue: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: colors.text,
  },
  fareDivider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: 8,
  },
  fareTotalLabel: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: colors.text,
  },
  fareTotalValue: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: colors.primary,
  },
  notesRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    marginTop: 8,
  },
  notesInput: {
    flex: 1,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: colors.text,
  },
  });
}
