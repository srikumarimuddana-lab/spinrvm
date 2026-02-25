import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

export default function RideStatusScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, simulateDriverArrival, clearRide } = useRideStore();

  const [pulseAnim] = useState(new Animated.Value(1));
  const [dotAnim] = useState(new Animated.Value(0));

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      // Poll for updates
      const interval = setInterval(() => fetchRide(rideId), 3000);
      return () => clearInterval(interval);
    }
  }, [rideId]);

  useEffect(() => {
    // Pulse animation for searching
    if (currentRide?.status === 'searching') {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 1.2, duration: 800, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 800, useNativeDriver: true }),
        ])
      ).start();
    }

    // Dot animation
    Animated.loop(
      Animated.timing(dotAnim, { toValue: 3, duration: 1500, useNativeDriver: false })
    ).start();
  }, [currentRide?.status]);

  const handleCancel = async () => {
    await cancelRide();
    clearRide();
    router.replace('/(tabs)');
  };

  // handleSimulateArrival and handleRideComplete removed for production

  const renderSearching = () => (
    <View style={styles.statusContainer}>
      <Animated.View style={[styles.searchingCircle, { transform: [{ scale: pulseAnim }] }]}>
        <Ionicons name="car" size={40} color="#FFFFFF" />
      </Animated.View>
      <Text style={styles.statusTitle}>Finding your driver</Text>
      <Text style={styles.statusSubtitle}>This usually takes 1-3 minutes</Text>
    </View>
  );

  const renderDriverAssigned = () => (
    <View style={styles.driverContainer}>
      {/* Driver Info Card */}
      <View style={styles.driverCard}>
        <View style={styles.driverHeader}>
          <View style={styles.driverAvatar}>
            <Ionicons name="person" size={32} color="#666" />
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{currentDriver?.name}</Text>
            <View style={styles.ratingContainer}>
              <Ionicons name="star" size={14} color="#FFB800" />
              <Text style={styles.ratingText}>{currentDriver?.rating}</Text>
              <Text style={styles.tripsText}>• {currentDriver?.total_rides} trips</Text>
            </View>
          </View>
          <TouchableOpacity style={styles.callButton}>
            <Ionicons name="call" size={22} color={SpinrConfig.theme.colors.primary} />
          </TouchableOpacity>
        </View>

        <View style={styles.vehicleCard}>
          <View style={styles.vehicleInfo}>
            <Text style={styles.vehicleMake}>
              {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
            </Text>
            <Text style={styles.licensePlate}>{currentDriver?.license_plate}</Text>
          </View>
          <Ionicons name="car" size={32} color={SpinrConfig.theme.colors.primary} />
        </View>
      </View>

      {/* Status Info */}
      <View style={styles.statusInfo}>
        <View style={styles.statusIcon}>
          <Ionicons name="navigate" size={24} color={SpinrConfig.theme.colors.primary} />
        </View>
        <View style={styles.statusTextContainer}>
          <Text style={styles.statusLabel}>Driver is on the way</Text>
          <Text style={styles.statusEta}>Arriving in ~5 min</Text>
        </View>
      </View>

    </View>
  );

  const renderDriverArrived = () => (
    <View style={styles.arrivedContainer}>
      {/* OTP Display */}
      <View style={styles.otpCard}>
        <Text style={styles.otpLabel}>Share this PIN with your driver</Text>
        <View style={styles.otpBox}>
          {currentRide?.pickup_otp.split('').map((digit, index) => (
            <View key={index} style={styles.otpDigit}>
              <Text style={styles.otpDigitText}>{digit}</Text>
            </View>
          ))}
        </View>
        <Text style={styles.otpHint}>Driver will enter this to start the trip</Text>
      </View>

      {/* Driver Info */}
      <View style={styles.driverCard}>
        <View style={styles.driverHeader}>
          <View style={styles.driverAvatar}>
            <Ionicons name="person" size={32} color="#666" />
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{currentDriver?.name}</Text>
            <Text style={styles.arrivedText}>
              <Ionicons name="checkmark-circle" size={14} color="#10B981" /> Arrived at pickup
            </Text>
          </View>
          <TouchableOpacity style={styles.callButton}>
            <Ionicons name="call" size={22} color={SpinrConfig.theme.colors.primary} />
          </TouchableOpacity>
        </View>

        <View style={styles.vehicleCard}>
          <View style={styles.vehicleInfo}>
            <Text style={styles.vehicleMake}>
              {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
            </Text>
            <Text style={styles.licensePlate}>{currentDriver?.license_plate}</Text>
          </View>
          <Ionicons name="car" size={32} color={SpinrConfig.theme.colors.primary} />
        </View>
      </View>

    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={handleCancel}>
          <Ionicons name="close" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>
          {currentRide?.status === 'searching' && 'Finding driver...'}
          {currentRide?.status === 'driver_assigned' && 'Driver on the way'}
          {currentRide?.status === 'driver_arrived' && 'Driver arrived'}
        </Text>
        <View style={{ width: 44 }} />
      </View>

      {/* Map Placeholder */}
      <View style={styles.mapPlaceholder}>
        <Ionicons name="map" size={48} color="#CCC" />
        <Text style={styles.mapText}>Map View</Text>
      </View>

      {/* Bottom Sheet */}
      <View style={styles.bottomSheet}>
        {currentRide?.status === 'searching' && renderSearching()}
        {currentRide?.status === 'driver_assigned' && renderDriverAssigned()}
        {currentRide?.status === 'driver_arrived' && renderDriverArrived()}

        {/* Cancel Button */}
        {currentRide?.status !== 'driver_arrived' && (
          <TouchableOpacity style={styles.cancelButton} onPress={handleCancel}>
            <Text style={styles.cancelButtonText}>Cancel Ride</Text>
          </TouchableOpacity>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#E8E8E8',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    backgroundColor: '#FFFFFF',
    borderBottomWidth: 1,
    borderBottomColor: '#F0F0F0',
  },
  backButton: {
    width: 44,
    height: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  mapPlaceholder: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#E0E7E0',
  },
  mapText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
    marginTop: 8,
  },
  bottomSheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    padding: 24,
    minHeight: 280,
  },
  statusContainer: {
    alignItems: 'center',
    paddingVertical: 20,
  },
  searchingCircle: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
  },
  statusTitle: {
    fontSize: 22,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 8,
  },
  statusSubtitle: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
  },
  demoButton: {
    marginTop: 20,
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#F0F0F0',
    borderRadius: 20,
  },
  demoButtonText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  driverContainer: {
    paddingTop: 8,
  },
  driverCard: {
    backgroundColor: '#F9F9F9',
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  driverHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  driverAvatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  driverInfo: {
    flex: 1,
  },
  driverName: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 4,
  },
  ratingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  ratingText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginLeft: 4,
  },
  tripsText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginLeft: 8,
  },
  callButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#FFF0F0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  vehicleCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 12,
  },
  vehicleInfo: {},
  vehicleMake: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  licensePlate: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
    marginTop: 2,
  },
  statusInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF5F5',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  statusIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#FFE8E8',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  statusTextContainer: {},
  statusLabel: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  statusEta: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
  },
  simulateButton: {
    backgroundColor: '#E8E8E8',
    borderRadius: 12,
    padding: 14,
    alignItems: 'center',
  },
  simulateButtonText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  arrivedContainer: {},
  otpCard: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 16,
    padding: 24,
    alignItems: 'center',
    marginBottom: 20,
  },
  otpLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: 'rgba(255,255,255,0.8)',
    marginBottom: 16,
  },
  otpBox: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 12,
  },
  otpDigit: {
    width: 52,
    height: 60,
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
  },
  otpDigitText: {
    fontSize: 28,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#FFFFFF',
  },
  otpHint: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: 'rgba(255,255,255,0.7)',
  },
  arrivedText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#10B981',
  },
  completeButton: {
    backgroundColor: '#10B981',
    borderRadius: 28,
    padding: 18,
    alignItems: 'center',
  },
  completeButtonText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFFFFF',
  },
  cancelButton: {
    marginTop: 16,
    padding: 14,
    alignItems: 'center',
  },
  cancelButtonText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
  },
});
