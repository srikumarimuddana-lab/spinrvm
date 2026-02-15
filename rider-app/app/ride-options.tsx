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
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, Circle, PROVIDER_GOOGLE } from 'react-native-maps';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

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
    isLoading
  } = useRideStore();

  const [selectedIndex, setSelectedIndex] = useState(0);
  const [routeKey, setRouteKey] = useState(0); // Unique key to force re-render
  const [routeCoordinates, setRouteCoordinates] = useState<any[]>([]);
  const [mapReady, setMapReady] = useState(false);
  const mapRef = useRef<MapView>(null);

  useEffect(() => {
    if (pickup && dropoff) {
      console.log('Platform:', Platform.OS, '| Fetching estimates & nearby drivers for:', pickup.address, 'to', dropoff.address);
      fetchEstimates();
      fetchNearbyDrivers();
      fetchRoute();

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

  const fetchRoute = async () => {
    if (!pickup || !dropoff) return;

    // Build straight-line fallback through all points
    const fallbackRoute = [
      { latitude: pickup.lat, longitude: pickup.lng },
      ...stops.filter(s => s.lat && s.lng).map(s => ({ latitude: s.lat, longitude: s.lng })),
      { latitude: dropoff.lat, longitude: dropoff.lng },
    ];

    if (!GOOGLE_MAPS_API_KEY) {
      console.log('No Google Maps API key — using straight-line route');
      setRouteCoordinates(fallbackRoute);
      return;
    }

    try {
      const waypoints = stops
        .filter(s => s.lat && s.lng)
        .map(s => `via:${s.lat},${s.lng}`)
        .join('|');

      const url = `https://maps.googleapis.com/maps/api/directions/json?origin=${pickup.lat},${pickup.lng}&destination=${dropoff.lat},${dropoff.lng}${waypoints ? `&waypoints=${waypoints}` : ''}&key=${GOOGLE_MAPS_API_KEY}`;
      const response = await fetch(url);
      const data = await response.json();

      console.log('Directions API status:', data.status);

      if (data.status === 'OK' && data.routes.length) {
        const points = decodePolyline(data.routes[0].overview_polyline.points);
        console.log('Route decoded:', points.length, 'points');
        if (points.length > 0) {
          console.log('Sample/First Point:', points[0], 'Type Lat:', typeof points[0].latitude);
          console.log('Sample/Last Point:', points[points.length - 1]);
        }

        // Filter out invalid points just in case
        const validPoints = points.filter(p =>
          typeof p.latitude === 'number' && !isNaN(p.latitude) &&
          typeof p.longitude === 'number' && !isNaN(p.longitude) &&
          Math.abs(p.latitude) > 0.1 && Math.abs(p.longitude) > 0.1
        );

        if (validPoints.length !== points.length) {
          console.warn('Filtered out', points.length - validPoints.length, 'invalid points');
        }

        // Increased delay to ensure map is fully ready before drawing route
        setTimeout(() => {
          setRouteCoordinates(validPoints);
          setRouteKey(Date.now()); // Force re-render with new key
        }, 500);
      } else {
        // Fall back to straight line
        setRouteCoordinates(fallbackRoute);
        setRouteKey(Date.now());
      }
    } catch (error) {
      console.log('Directions API fetch error:', error);
      // Fall back to straight line
      setRouteCoordinates(fallbackRoute);
      setRouteKey(Date.now());
    }
  };

  const decodePolyline = (t: string) => {
    let points = [];
    let index = 0, len = t.length;
    let lat = 0, lng = 0;
    while (index < len) {
      let b, shift = 0, result = 0;
      do {
        b = t.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      let dlat = ((result & 1) ? ~(result >> 1) : (result >> 1));
      lat += dlat;
      shift = 0;
      result = 0;
      do {
        b = t.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      let dlng = ((result & 1) ? ~(result >> 1) : (result >> 1));
      lng += dlng;
      points.push({ latitude: (lat / 1e5), longitude: (lng / 1e5) });
    }
    return points;
  };

  const handleSelect = (index: number) => {
    if (!estimates[index].available) return;
    setSelectedIndex(index);
    selectVehicle(estimates[index].vehicle_type);
  };

  const handleConfirm = () => {
    if (!selectedVehicle) return;
    router.push('/payment-confirm');
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
            {/* Route Polyline - Blue works best */}
            {routeCoordinates.length > 0 && (
              <Polyline
                key={`route-${routeKey}`} // Forces deep re-mount on every update
                coordinates={routeCoordinates}
                strokeColor="#2196F3"
                strokeWidth={5}
                zIndex={100}
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

            {/* Nearby Drivers Markers - Filtered */}
            {nearbyDrivers.filter(d =>
              typeof d.lat === 'number' && !isNaN(d.lat) &&
              typeof d.lng === 'number' && !isNaN(d.lng) &&
              Math.abs(d.lat) > 0.1 && Math.abs(d.lng) > 0.1
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
                    <Text style={styles.optionETA}>{estimate.eta_minutes} min away</Text>
                  ) : (
                    <Text style={styles.unavailableText}>Not available</Text>
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
              Confirm {selectedEstimate.vehicle_type.name}
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
});
