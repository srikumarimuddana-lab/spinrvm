import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Image,
  Dimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '../config/spinr.config';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;
const { width: SCREEN_WIDTH } = Dimensions.get('window');
const MAP_HEIGHT = 200;

export default function RideOptionsScreen() {
  const router = useRouter();
  const { pickup, dropoff, stops, estimates, selectedVehicle, fetchEstimates, selectVehicle, isLoading } = useRideStore();
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [mapUrl, setMapUrl] = useState<string | null>(null);

  useEffect(() => {
    if (pickup && dropoff) {
      fetchEstimates();
      fetchRouteAndBuildMap();
    }
  }, [pickup, dropoff]);

  useEffect(() => {
    if (estimates.length > 0 && !selectedVehicle) {
      selectVehicle(estimates[0].vehicle_type);
    }
  }, [estimates]);

  // Fetch actual road route from Directions API, then build static map URL
  const fetchRouteAndBuildMap = async () => {
    if (!pickup || !dropoff || !GOOGLE_MAPS_API_KEY) return;

    try {
      const waypoints = stops
        .filter(s => s.lat && s.lng)
        .map(s => `${s.lat},${s.lng}`)
        .join('|');
      const waypointParam = waypoints ? `&waypoints=${encodeURIComponent(waypoints)}` : '';
      const directionsUrl = `https://maps.googleapis.com/maps/api/directions/json?origin=${pickup.lat},${pickup.lng}&destination=${dropoff.lat},${dropoff.lng}${waypointParam}&key=${GOOGLE_MAPS_API_KEY}`;

      const response = await fetch(directionsUrl);
      const data = await response.json();

      if (data.routes && data.routes.length > 0) {
        const encodedPolyline = data.routes[0].overview_polyline.points;
        buildMapWithPolyline(encodedPolyline);
      } else {
        buildMapFallback();
      }
    } catch (error) {
      console.log('Directions API error:', error);
      buildMapFallback();
    }
  };

  const buildMapWithPolyline = (encodedPolyline: string) => {
    if (!pickup || !dropoff || !GOOGLE_MAPS_API_KEY) return;
    const markers = [];
    markers.push(`markers=color:green%7Clabel:A%7C${pickup.lat},${pickup.lng}`);
    stops.forEach((stop, i) => {
      if (stop.lat && stop.lng) {
        markers.push(`markers=color:orange%7Clabel:${String.fromCharCode(66 + i)}%7C${stop.lat},${stop.lng}`);
      }
    });
    markers.push(`markers=color:red%7Clabel:B%7C${dropoff.lat},${dropoff.lng}`);
    const color = SpinrConfig.theme.colors.primary.replace('#', '');
    const mapWidth = Math.round(SCREEN_WIDTH - 40);
    setMapUrl(`https://maps.googleapis.com/maps/api/staticmap?size=${mapWidth}x${MAP_HEIGHT}&maptype=roadmap&${markers.join('&')}&path=color:0x${color}ff%7Cweight:5%7Cenc:${encodeURIComponent(encodedPolyline)}&key=${GOOGLE_MAPS_API_KEY}`);
  };

  const buildMapFallback = () => {
    if (!pickup || !dropoff || !GOOGLE_MAPS_API_KEY) return;
    const markers = [];
    markers.push(`markers=color:green%7Clabel:A%7C${pickup.lat},${pickup.lng}`);
    markers.push(`markers=color:red%7Clabel:B%7C${dropoff.lat},${dropoff.lng}`);
    const mapWidth = Math.round(SCREEN_WIDTH - 40);
    setMapUrl(`https://maps.googleapis.com/maps/api/staticmap?size=${mapWidth}x${MAP_HEIGHT}&maptype=roadmap&${markers.join('&')}&key=${GOOGLE_MAPS_API_KEY}`);
  };

  const handleSelect = (index: number) => {
    setSelectedIndex(index);
    selectVehicle(estimates[index].vehicle_type);
  };

  const handleConfirm = () => {
    router.push('/payment-confirm');
  };

  // Random ETA per vehicle for visual display
  const getETA = (index: number) => {
    const etas = [3, 8, 5, 4, 7];
    return etas[index % etas.length];
  };

  const selectedEstimate = estimates[selectedIndex];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header with back button and destination chip */}
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

      {/* Map Preview */}
      {mapUrl ? (
        <View style={styles.mapContainer}>
          <Image
            source={{ uri: mapUrl }}
            style={styles.mapImage}
            resizeMode="cover"
          />
        </View>
      ) : (
        <View style={[styles.mapContainer, styles.mapPlaceholder]}>
          <ActivityIndicator size="small" color={SpinrConfig.theme.colors.primary} />
        </View>
      )}

      {/* Choose a ride header with commission badge */}
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Choose a ride</Text>
        <View style={styles.commissionBadge}>
          <Text style={styles.commissionText}>% 0% Commission</Text>
        </View>
      </View>

      {/* Vehicle Options */}
      {isLoading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={SpinrConfig.theme.colors.primary} />
          <Text style={styles.loadingText}>Finding best rides...</Text>
        </View>
      ) : (
        <ScrollView style={styles.optionsList} showsVerticalScrollIndicator={false}>
          {estimates.map((estimate, index) => (
            <TouchableOpacity
              key={estimate.vehicle_type.id}
              style={[
                styles.optionCard,
                selectedIndex === index && styles.optionCardSelected,
              ]}
              onPress={() => handleSelect(index)}
              activeOpacity={0.7}
            >
              {/* Car Image */}
              <View style={styles.carImageContainer}>
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
              <View style={styles.optionInfo}>
                <View style={styles.optionNameRow}>
                  <Text style={styles.optionName}>{estimate.vehicle_type.name}</Text>
                  <View style={styles.capacityBadge}>
                    <Ionicons name="person" size={12} color="#666" />
                    <Text style={styles.capacityText}>{estimate.vehicle_type.capacity}</Text>
                  </View>
                </View>
                <Text style={styles.optionETA}>{getETA(index)} min away</Text>
              </View>

              {/* Price */}
              <View style={styles.optionPriceContainer}>
                <Text style={styles.optionPrice}>${estimate.total_fare.toFixed(2)}</Text>
                {selectedIndex === index && (
                  <View style={styles.selectedCheck}>
                    <Ionicons name="checkmark-circle" size={22} color={SpinrConfig.theme.colors.primary} />
                  </View>
                )}
              </View>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}

      {/* Payment Method + Confirm Button */}
      {!isLoading && estimates.length > 0 && (
        <View style={styles.footer}>
          {/* Payment method row */}
          <TouchableOpacity style={styles.paymentRow}>
            <Ionicons name="card" size={20} color="#1A1A1A" />
            <Text style={styles.paymentText}>Visa •••• 4242</Text>
            <Ionicons name="chevron-forward" size={16} color="#999" />
          </TouchableOpacity>

          {/* Confirm button */}
          <TouchableOpacity
            style={styles.confirmButton}
            onPress={handleConfirm}
            activeOpacity={0.8}
          >
            <Text style={styles.confirmButtonText}>
              Confirm {selectedEstimate?.vehicle_type.name}
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
  mapPlaceholder: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  mapImage: {
    width: '100%',
    height: MAP_HEIGHT,
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
});
