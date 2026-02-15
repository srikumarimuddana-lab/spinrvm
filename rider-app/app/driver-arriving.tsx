import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Dimensions,
  Share,
  Alert,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

const { width } = Dimensions.get('window');

export default function DriverArrivingScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, simulateDriverArrival } = useRideStore();
  const [eta, setEta] = useState(4);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      const interval = setInterval(() => fetchRide(rideId), 3000);
      return () => clearInterval(interval);
    }
  }, [rideId]);

  useEffect(() => {
    // Check status changes
    if (currentRide?.status === 'driver_arrived') {
      router.replace({ pathname: '/driver-arrived', params: { rideId } });
    } else if (currentRide?.status === 'in_progress') {
      router.replace({ pathname: '/ride-in-progress', params: { rideId } });
    }
  }, [currentRide?.status]);

  const handleBack = () => {
    router.back();
  };

  const handleEmergency = () => {
    // Emergency action
  };

  const handleMessage = () => {
    router.push({ pathname: '/chat-driver', params: { rideId } });
  };

  const handleCall = () => {
    // Initiate call
  };

  const handleShareTrip = async () => {
    const driverInfo = `
🚗 SPINR RIDE - TRIP DETAILS

👤 DRIVER: ${currentDriver?.name || 'John D.'}
⭐ RATING: ${currentDriver?.rating || 4.9}/5

🚙 VEHICLE: ${currentDriver?.vehicle_color || 'Grey'} ${currentDriver?.vehicle_make || 'Honda'} ${currentDriver?.vehicle_model || 'Civic'}
📋 LICENSE PLATE: ${currentDriver?.license_plate || 'SK-123-ABC'}

📍 PICKUP: ${currentRide?.pickup_address || 'University of Saskatchewan'}
📍 DESTINATION: ${currentRide?.dropoff_address || '123 Main St, Saskatoon'}

⏱️ ETA: ${eta} minutes

Track my live location: spinr://track/${rideId || 'demo'}

I'm sharing this ride for safety. If you don't hear from me, please check on me.
    `.trim();

    try {
      await Share.share({
        message: driverInfo,
        title: 'My Spinr Ride Details',
      });
    } catch (error) {
      console.log('Share error:', error);
    }
  };

  const handleCopyDetails = async () => {
    const details = `Driver: ${currentDriver?.name || 'John D.'} | Vehicle: ${currentDriver?.vehicle_color || 'Grey'} ${currentDriver?.vehicle_make || 'Honda'} ${currentDriver?.vehicle_model || 'Civic'} | Plate: ${currentDriver?.license_plate || 'SK-123-ABC'} | Rating: ${currentDriver?.rating || 4.9}⭐`;
    await Clipboard.setStringAsync(details);
    Alert.alert('Copied!', 'Driver details copied to clipboard');
  };

  const handleSimulateArrival = async () => {
    await simulateDriverArrival();
  };

  return (
    <View style={styles.container}>
      {/* Header */}
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backButton} onPress={handleBack}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>
          
          <View style={styles.etaPill}>
            <View style={styles.greenDot} />
            <Text style={styles.etaText}>Arriving in {eta} min</Text>
          </View>
          
          <TouchableOpacity style={styles.emergencyButton} onPress={handleEmergency}>
            <Ionicons name="shield" size={20} color={SpinrConfig.theme.colors.primary} />
          </TouchableOpacity>
        </View>
      </SafeAreaView>

      {/* Map Area */}
      <View style={styles.mapContainer}>
        <View style={styles.mapPlaceholder}>
          {/* Simulated route line */}
          <View style={styles.routeLine} />
          
          {/* Driver marker */}
          <View style={styles.driverMarker}>
            <View style={styles.driverMarkerInner}>
              <Ionicons name="car" size={18} color="#FFF" />
            </View>
            <View style={styles.etaBadge}>
              <Text style={styles.etaBadgeText}>{eta} min away</Text>
            </View>
          </View>
          
          {/* Pickup marker */}
          <View style={styles.pickupMarker}>
            <View style={styles.pickupDot} />
          </View>
        </View>
      </View>

      {/* Bottom Sheet */}
      <View style={styles.bottomSheet}>
        <View style={styles.sheetHandle} />
        
        {/* Safety Banner - Share Trip */}
        <TouchableOpacity style={styles.shareTripBanner} onPress={handleShareTrip}>
          <View style={styles.shareTripIcon}>
            <Ionicons name="share-social" size={18} color="#FFF" />
          </View>
          <View style={styles.shareTripContent}>
            <Text style={styles.shareTripTitle}>Share your trip</Text>
            <Text style={styles.shareTripSubtitle}>Let friends & family track your live location</Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={SpinrConfig.theme.colors.primary} />
        </TouchableOpacity>

        {/* Driver Details Card - Comprehensive for Screenshot */}
        <View style={styles.driverDetailsCard}>
          <View style={styles.driverCardHeader}>
            <Text style={styles.driverCardTitle}>YOUR DRIVER</Text>
            <TouchableOpacity style={styles.copyButton} onPress={handleCopyDetails}>
              <Ionicons name="copy-outline" size={16} color="#666" />
              <Text style={styles.copyText}>Copy Details</Text>
            </TouchableOpacity>
          </View>
          
          <View style={styles.driverSection}>
            <View style={styles.driverAvatar}>
              <Ionicons name="person" size={28} color="#666" />
              <View style={styles.ratingBadge}>
                <Ionicons name="star" size={10} color="#FFB800" />
                <Text style={styles.ratingText}>{currentDriver?.rating || 4.9}</Text>
              </View>
            </View>
            
            <View style={styles.driverInfo}>
              <Text style={styles.driverName}>{currentDriver?.name || 'John D.'}</Text>
              <Text style={styles.totalTrips}>{currentDriver?.total_rides || 1247} trips completed</Text>
            </View>
          </View>

          {/* Vehicle Details - Clear for Screenshot */}
          <View style={styles.vehicleSection}>
            <View style={styles.vehicleRow}>
              <Ionicons name="car" size={20} color={SpinrConfig.theme.colors.primary} />
              <View style={styles.vehicleTextContainer}>
                <Text style={styles.vehicleLabel}>VEHICLE</Text>
                <Text style={styles.vehicleValue}>
                  {currentDriver?.vehicle_color || 'Grey'} {currentDriver?.vehicle_make || 'Honda'} {currentDriver?.vehicle_model || 'Civic'}
                </Text>
              </View>
            </View>
            
            <View style={styles.plateRow}>
              <View style={styles.plateIconContainer}>
                <Text style={styles.plateIcon}>🪪</Text>
              </View>
              <View style={styles.vehicleTextContainer}>
                <Text style={styles.vehicleLabel}>LICENSE PLATE</Text>
                <Text style={styles.plateValue}>{currentDriver?.license_plate || 'SK-123-ABC'}</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Action Buttons */}
        <View style={styles.actionButtons}>
          <TouchableOpacity style={styles.messageButton} onPress={handleMessage}>
            <Ionicons name="chatbubble" size={20} color="#FFF" />
            <Text style={styles.messageButtonText}>Message</Text>
          </TouchableOpacity>
          
          <TouchableOpacity style={styles.callButton} onPress={handleCall}>
            <Ionicons name="call" size={22} color="#1A1A1A" />
          </TouchableOpacity>
          
          <TouchableOpacity style={styles.shareButton} onPress={handleShareTrip}>
            <Ionicons name="share-outline" size={22} color="#1A1A1A" />
          </TouchableOpacity>
        </View>

        {/* Progress Bar */}
        <View style={styles.progressContainer}>
          <View style={[styles.progressBar, { width: '70%' }]} />
        </View>

        {/* Trip Details */}
        <View style={styles.tripDetails}>
          <View style={styles.tripRow}>
            <View style={styles.tripIndicator}>
              <View style={styles.grayDot} />
              <View style={styles.tripLine} />
              <View style={styles.redDot} />
            </View>
            
            <View style={styles.tripAddresses}>
              <View style={styles.addressRow}>
                <Text style={styles.addressLabel}>PICKED UP</Text>
                <Text style={styles.addressText} numberOfLines={1}>
                  {currentRide?.pickup_address || 'University of Saskatchewan'}
                </Text>
              </View>
              
              <View style={[styles.addressRow, { marginTop: 16 }]}>
                <Text style={styles.dropoffLabel}>DROPPING OFF</Text>
                <Text style={styles.dropoffText} numberOfLines={1}>
                  {currentRide?.dropoff_address || '123 Main St, Saskatoon'}
                </Text>
                <Text style={styles.etaArrival}>Est. arrival 4:15 PM</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Demo Button */}
        <TouchableOpacity style={styles.demoButton} onPress={handleSimulateArrival}>
          <Text style={styles.demoButtonText}>Simulate Driver Arrival (Demo)</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#E8E8E8',
  },
  headerSafeArea: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 10,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  backButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  etaPill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  greenDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#10B981',
    marginRight: 8,
  },
  etaText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  emergencyButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  mapContainer: {
    flex: 1,
  },
  mapPlaceholder: {
    flex: 1,
    backgroundColor: '#D4E4D4',
    position: 'relative',
  },
  routeLine: {
    position: 'absolute',
    top: '30%',
    left: '20%',
    width: 200,
    height: 3,
    backgroundColor: SpinrConfig.theme.colors.primary,
    transform: [{ rotate: '-30deg' }],
  },
  driverMarker: {
    position: 'absolute',
    top: '35%',
    left: '50%',
    alignItems: 'center',
  },
  driverMarkerInner: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 3,
    borderColor: '#FFF',
  },
  etaBadge: {
    backgroundColor: '#FFF',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    marginTop: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  etaBadgeText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  pickupMarker: {
    position: 'absolute',
    top: '25%',
    right: '25%',
  },
  pickupDot: {
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderWidth: 4,
    borderColor: '#FFF',
  },
  bottomSheet: {
    backgroundColor: '#FFF',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 30,
  },
  sheetHandle: {
    width: 40,
    height: 4,
    backgroundColor: '#E0E0E0',
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 20,
  },
  driverSection: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  driverAvatar: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#E8E8E8',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
    position: 'relative',
  },
  ratingBadge: {
    position: 'absolute',
    bottom: -4,
    left: -4,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  ratingText: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginLeft: 2,
  },
  driverInfo: {
    flex: 1,
  },
  driverName: {
    fontSize: 20,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  vehicleInfo: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  plateContainer: {
    backgroundColor: '#F5F5F5',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
    alignSelf: 'flex-start',
    marginTop: 6,
  },
  plateText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    letterSpacing: 1,
  },
  actionButtons: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  messageButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingVertical: 14,
    borderRadius: 28,
    gap: 8,
  },
  messageButtonText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
  },
  callButton: {
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  progressContainer: {
    height: 4,
    backgroundColor: '#F0F0F0',
    borderRadius: 2,
    marginBottom: 20,
  },
  progressBar: {
    height: 4,
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 2,
  },
  tripDetails: {
    marginBottom: 16,
  },
  tripRow: {
    flexDirection: 'row',
  },
  tripIndicator: {
    alignItems: 'center',
    marginRight: 12,
    paddingTop: 4,
  },
  grayDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#CCC',
  },
  tripLine: {
    width: 2,
    height: 50,
    backgroundColor: '#E0E0E0',
    marginVertical: 4,
  },
  redDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: SpinrConfig.theme.colors.primary,
  },
  tripAddresses: {
    flex: 1,
  },
  addressRow: {},
  addressLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#999',
    letterSpacing: 0.5,
  },
  addressText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
    textDecorationLine: 'line-through',
  },
  dropoffLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
    letterSpacing: 0.5,
  },
  dropoffText: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  etaArrival: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  demoButton: {
    backgroundColor: '#F0F0F0',
    borderRadius: 12,
    padding: 12,
    alignItems: 'center',
  },
  demoButtonText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  shareTripBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF5F5',
    padding: 14,
    borderRadius: 14,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#FFE0E0',
  },
  shareTripIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  shareTripContent: {
    flex: 1,
  },
  shareTripTitle: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  shareTripSubtitle: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 1,
  },
  driverDetailsCard: {
    backgroundColor: '#F9F9F9',
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  driverCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  driverCardTitle: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
    letterSpacing: 0.5,
  },
  copyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    gap: 4,
  },
  copyText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  totalTrips: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  vehicleSection: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#E8E8E8',
  },
  vehicleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  vehicleTextContainer: {
    marginLeft: 12,
    flex: 1,
  },
  vehicleLabel: {
    fontSize: 10,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#999',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  vehicleValue: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  plateRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  plateIconContainer: {
    width: 20,
    height: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  plateIcon: {
    fontSize: 16,
  },
  plateValue: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    letterSpacing: 2,
  },
  shareButton: {
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
  },
});
