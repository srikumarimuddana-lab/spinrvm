import React, { useEffect, useState, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Image,
  Dimensions,
  Alert,
  Platform,
  Switch,
  Modal,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Circle, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';
import DateTimePicker from '@react-native-community/datetimepicker';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;
const { width: SCREEN_WIDTH } = Dimensions.get('window');
const MAP_HEIGHT = 280;

export default function RideOptionsScreen() {
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
    scheduledTime,
    setScheduledTime,
  } = useRideStore();

  const [selectedIndex, setSelectedIndex] = useState(0);
  const [routeKey, setRouteKey] = useState(0);
  const [routeCoordinates, setRouteCoordinates] = useState<any[]>([]);
  const [mapReady, setMapReady] = useState(false);
  const [isScheduling, setIsScheduling] = useState(false);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [showTimePicker, setShowTimePicker] = useState(false);
  const [tempDate, setTempDate] = useState<Date>(new Date(Date.now() + 30 * 60000)); // default 30 min from now
  const mapRef = useRef<MapView>(null);

  useEffect(() => {
    if (pickup && dropoff) {
      console.log('Platform:', Platform.OS, '| Fetching estimates & nearby drivers for:', pickup.address, 'to', dropoff.address);
      fetchEstimates();
      fetchNearbyDrivers();

      // Auto-refresh drivers every 10 seconds
      const interval = setInterval(() => {
        fetchNearbyDrivers();
      }, 10000);

      return () => clearInterval(interval);
    }
  }, [pickup, dropoff]);

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
    if (mapRef.current && mapReady) {
      mapRef.current.fitToCoordinates(result.coordinates, {
        edgePadding: { top: 50, right: 50, bottom: 50, left: 50 },
        animated: true,
      });
    }
  };

  const handleSelect = (index: number) => {
    if (!estimates[index].available) return;
    setSelectedIndex(index);
    selectVehicle(estimates[index].vehicle_type);
  };

  const handleConfirm = () => {
    if (!selectedVehicle) return;
    if (isScheduling && !scheduledTime) {
      Alert.alert('Select Time', 'Please pick a date and time for your scheduled ride.');
      return;
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
      Alert.alert('Invalid Time', 'Scheduled time must be at least 15 minutes from now.');
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
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
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
            {/* MapViewDirections to display real route natively */}
            {GOOGLE_MAPS_API_KEY && (
              <MapViewDirections
                origin={{ latitude: pickup.lat, longitude: pickup.lng }}
                destination={{ latitude: dropoff.lat, longitude: dropoff.lng }}
                waypoints={stops.filter(s => s.lat && s.lng).map(s => ({ latitude: s.lat, longitude: s.lng }))}
                apikey={GOOGLE_MAPS_API_KEY}
                strokeWidth={5}
                strokeColor="#2196F3"
                onReady={onReadyDirections}
                optimizeWaypoints={true}
              />
            )}

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

            {/* Nearby Drivers Markers - Filtered by selected vehicle type */}
            {nearbyDrivers.filter(d =>
              typeof d.lat === 'number' && !isNaN(d.lat) &&
              typeof d.lng === 'number' && !isNaN(d.lng) &&
              Math.abs(d.lat) > 0.1 && Math.abs(d.lng) > 0.1 &&
              (!selectedVehicle || d.vehicle_type_id === selectedVehicle.id)
            ).map((driver) => (
              <Marker
                key={driver.id}
                coordinate={{ latitude: driver.lat, longitude: driver.lng }}
                rotation={0}
                flat={true}
                anchor={{ x: 0.5, y: 0.5 }}
                zIndex={101}
              >
                <View style={styles.markerContainer}>
                  <Ionicons name="car" size={20} color={SpinrConfig.theme.colors.primary} />
                </View>
              </Marker>
            ))}
          </MapView>
        ) : (
          <View style={styles.mapPlaceholder}>
            <ActivityIndicator size="small" color={SpinrConfig.theme.colors.primary} />
          </View>
        )}
      </View>

      {/* Options Header */}
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Choose a ride</Text>
        <View style={styles.commissionBadge}>
          <Text style={styles.commissionText}>% 0% Commission</Text>
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
      {isLoading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={SpinrConfig.theme.colors.primary} />
          <Text style={styles.loadingText}>Finding best rides...</Text>
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
                    <View style={styles.capacityBadge}>
                      <Ionicons name="person" size={12} color="#666" />
                      <Text style={styles.capacityText}>{estimate.vehicle_type.capacity}</Text>
                    </View>
                  </View>

                  {isAvailable ? (
                    <Text style={styles.optionETA}>
                      {estimate.eta_minutes ? `${estimate.eta_minutes} min away` : 'Nearby'}
                      {estimate.driver_count > 0 && ` · ${estimate.driver_count} driver${estimate.driver_count > 1 ? 's' : ''}`}
                    </Text>
                  ) : (
                    <Text style={styles.unavailableText}>No drivers nearby</Text>
                  )}
                </View>

                {/* Price */}
                <View style={[styles.optionPriceContainer, !isAvailable && { opacity: 0.4 }]}>
                  <Text style={styles.optionPrice}>${estimate.total_fare.toFixed(2)}</Text>
                  {isSelected && isAvailable && (
                    <View style={styles.selectedCheck}>
                      <Ionicons name="checkmark-circle" size={22} color={SpinrConfig.theme.colors.primary} />
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
              trackColor={{ false: '#D1D5DB', true: SpinrConfig.theme.colors.primary + '60' }}
              thumbColor={isScheduling ? SpinrConfig.theme.colors.primary : '#F3F4F6'}
            />
          </View>

          {/* Scheduled Time Display */}
          {isScheduling && scheduledTime && (
            <TouchableOpacity
              style={styles.scheduledTimeRow}
              onPress={() => setShowDatePicker(true)}
            >
              <Ionicons name="calendar-outline" size={18} color={SpinrConfig.theme.colors.primary} />
              <Text style={styles.scheduledTimeText}>
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
                      minimumDate={new Date()}
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
                minimumDate={new Date()}
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

          {/* Payment method row */}
          <TouchableOpacity style={styles.paymentRow}>
            <Ionicons name="card" size={20} color="#1A1A1A" />
            <Text style={styles.paymentText}>Visa •••• 4242</Text>
            <Ionicons name="chevron-forward" size={16} color="#999" />
          </TouchableOpacity>

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
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
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
    backgroundColor: '#F2F2F2',
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 8,
    marginHorizontal: 8,
    alignItems: 'center',
  },
  destinationChipText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#333',
    letterSpacing: 0.5,
  },
  mapContainer: {
    marginHorizontal: 0,
    backgroundColor: '#F0F0F0',
    height: MAP_HEIGHT,
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
    backgroundColor: 'white',
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
    borderBottomColor: '#F0F0F0',
  },
  sectionTitle: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
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
    color: '#666',
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
    borderBottomColor: '#F5F5F5',
  },
  optionCardSelected: {
    backgroundColor: '#FFF5F5',
    borderLeftWidth: 3,
    borderLeftColor: SpinrConfig.theme.colors.primary,
  },
  optionCardDisabled: {
    backgroundColor: '#FAFAFA',
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
    backgroundColor: '#F5F5F5',
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
    color: '#1A1A1A',
  },
  capacityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
    backgroundColor: '#F0F0F0',
    borderRadius: 8,
    paddingHorizontal: 5,
    paddingVertical: 2,
  },
  capacityText: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
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
    color: '#999',
    marginTop: 2,
    fontStyle: 'italic',
  },
  optionPriceContainer: {
    alignItems: 'flex-end',
  },
  optionPrice: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  selectedCheck: {
    marginTop: 4,
  },
  footer: {
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 20,
    borderTopWidth: 1,
    borderTopColor: '#F0F0F0',
    backgroundColor: '#FFFFFF',
  },
  paymentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#F5F5F5',
    marginBottom: 12,
  },
  paymentText: {
    flex: 1,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  confirmButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
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
    borderBottomColor: '#F5F5F5',
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
    color: '#1A1A1A',
  },
  scheduledTimeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: '#F0F7FF',
    borderRadius: 10,
    marginBottom: 8,
  },
  scheduledTimeText: {
    flex: 1,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  changeText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
  },
  modalOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.4)',
  },
  pickerContainer: {
    backgroundColor: '#FFFFFF',
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
    borderBottomColor: '#E5E7EB',
  },
  pickerCancelText: {
    fontSize: 16,
    color: '#6B7280',
    fontFamily: 'PlusJakartaSans_500Medium',
  },
  pickerDoneText: {
    fontSize: 16,
    color: SpinrConfig.theme.colors.primary,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  fareBreakdown: {
    backgroundColor: '#F9FAFB',
    marginHorizontal: 16,
    marginVertical: 8,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
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
    color: '#1A1A1A',
  },
  fareBreakdownVehicle: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#6B7280',
    backgroundColor: '#E5E7EB',
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
    color: '#6B7280',
  },
  fareValue: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  fareDivider: {
    height: 1,
    backgroundColor: '#E5E7EB',
    marginVertical: 8,
  },
  fareTotalLabel: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  fareTotalValue: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
  },
});
