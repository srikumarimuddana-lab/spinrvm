import React, { useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Dimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

const { width } = Dimensions.get('window');

export default function RideCompletedScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, clearRide } = useRideStore();

  const handleHelp = () => {
    // Open help
  };

  const handleProceedToRating = () => {
    // Navigate to rating screen
    router.replace({ pathname: '/rate-ride', params: { rideId } });
  };

  const fare = currentRide?.total_fare || 14.50;
  const duration = currentRide?.duration_minutes || 12;
  const distance = currentRide?.distance_km || 5.2;
  const destination = currentRide?.dropoff_address || '123 Saskatchewan Crescent E';

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      {/* Help Link */}
      <View style={styles.header}>
        <View style={{ width: 50 }} />
        <View style={{ flex: 1 }} />
        <TouchableOpacity onPress={handleHelp}>
          <Text style={styles.helpText}>Help</Text>
        </TouchableOpacity>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {/* Success Icon */}
        <View style={styles.successIcon}>
          <View style={styles.successCircle}>
            <Ionicons name="checkmark" size={48} color={SpinrConfig.theme.colors.primary} />
          </View>
        </View>

        {/* Arrival Message */}
        <Text style={styles.title}>You have arrived!</Text>
        <Text style={styles.destination}>at {destination}</Text>

        {/* Fare */}
        <Text style={styles.fare}>${fare.toFixed(2)}</Text>
        
        {/* Payment Method */}
        <View style={styles.paymentMethod}>
          <Ionicons name="card" size={18} color="#666" />
          <Text style={styles.paymentText}>APPLE PAY</Text>
        </View>

        {/* Stats */}
        <View style={styles.statsContainer}>
          <View style={styles.statCard}>
            <View style={styles.statIconContainer}>
              <Ionicons name="time" size={24} color={SpinrConfig.theme.colors.primary} />
            </View>
            <Text style={styles.statValue}>{duration} min</Text>
            <Text style={styles.statLabel}>DURATION</Text>
          </View>
          
          <View style={styles.statCard}>
            <View style={styles.statIconContainer}>
              <Ionicons name="location" size={24} color={SpinrConfig.theme.colors.primary} />
            </View>
            <Text style={styles.statValue}>{distance} km</Text>
            <Text style={styles.statLabel}>DISTANCE</Text>
          </View>
        </View>

        {/* Route Recap */}
        <View style={styles.routeRecap}>
          <View style={styles.routeMapPlaceholder}>
            <Text style={styles.routeLabel}>ROUTE RECAP</Text>
            {/* Simulated route line */}
            <View style={styles.routeLine} />
            {/* Start marker */}
            <View style={[styles.routeMarker, styles.startMarker]} />
            {/* End marker */}
            <View style={[styles.routeMarker, styles.endMarker]} />
          </View>
        </View>
      </ScrollView>

      {/* Bottom Button */}
      <View style={styles.bottomContainer}>
        <TouchableOpacity style={styles.ratingButton} onPress={handleProceedToRating}>
          <Text style={styles.ratingButtonText}>Proceed to Rating</Text>
          <Ionicons name="arrow-forward" size={20} color="#FFF" />
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFF',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 12,
  },
  helpText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
  },
  content: {
    flexGrow: 1,
    alignItems: 'center',
    paddingHorizontal: 24,
    paddingTop: 20,
  },
  successIcon: {
    marginBottom: 24,
  },
  successCircle: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: '#FFF0F0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  title: {
    fontSize: 28,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 8,
  },
  destination: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginBottom: 24,
  },
  fare: {
    fontSize: 48,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
    marginBottom: 8,
  },
  paymentMethod: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F5F5F5',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    marginBottom: 32,
  },
  paymentText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#666',
    marginLeft: 8,
    letterSpacing: 0.5,
  },
  statsContainer: {
    flexDirection: 'row',
    gap: 16,
    marginBottom: 24,
    width: '100%',
  },
  statCard: {
    flex: 1,
    backgroundColor: '#F9F9F9',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
  },
  statIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#FFF0F0',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  statValue: {
    fontSize: 24,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 4,
  },
  statLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#999',
    letterSpacing: 0.5,
  },
  routeRecap: {
    width: '100%',
    marginBottom: 20,
  },
  routeMapPlaceholder: {
    height: 160,
    backgroundColor: '#F0F0F0',
    borderRadius: 16,
    position: 'relative',
    overflow: 'hidden',
  },
  routeLabel: {
    position: 'absolute',
    bottom: 12,
    left: 12,
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    backgroundColor: '#FFF',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 4,
    letterSpacing: 0.5,
  },
  routeLine: {
    position: 'absolute',
    top: '30%',
    left: '15%',
    width: 200,
    height: 3,
    backgroundColor: SpinrConfig.theme.colors.primary,
    transform: [{ rotate: '20deg' }],
  },
  routeMarker: {
    width: 12,
    height: 12,
    borderRadius: 6,
    position: 'absolute',
  },
  startMarker: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    top: '25%',
    left: '12%',
  },
  endMarker: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    top: '40%',
    right: '15%',
  },
  bottomContainer: {
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderTopWidth: 1,
    borderTopColor: '#F0F0F0',
  },
  ratingButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingVertical: 18,
    borderRadius: 32,
    gap: 8,
  },
  ratingButtonText: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
  },
});
