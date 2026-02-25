import React, { useEffect } from 'react';
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

export default function DriverArrivedScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide } = useRideStore();

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      const interval = setInterval(() => fetchRide(rideId), 3000);
      return () => clearInterval(interval);
    }
  }, [rideId]);

  useEffect(() => {
    if (currentRide?.status === 'in_progress') {
      router.replace({ pathname: '/ride-in-progress', params: { rideId } });
    }
  }, [currentRide?.status]);

  const handleBack = () => {
    router.back();
  };

  const handleMessage = () => {
    router.push({ pathname: '/chat-driver', params: { rideId } });
  };

  const handleCall = () => {
    // Initiate call
  };

  const handleShareTrip = async () => {
    const driverInfo = `
🚗 SPINR RIDE - DRIVER ARRIVED!

👤 DRIVER: ${currentDriver?.name || 'Unknown'}
⭐ RATING: ${currentDriver?.rating || 'New'}
🚙 TOTAL TRIPS: ${currentDriver?.total_rides || 0}

🚙 VEHICLE: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'}
📋 LICENSE PLATE: ${currentDriver?.license_plate || 'Pending'}

📍 PICKUP: ${currentRide?.pickup_address || 'University of Saskatchewan'}
🔑 PICKUP OTP: ${pickupOtp}

I'm sharing this ride for safety. Screenshot this info!
    `.trim();

    try {
      await Share.share({
        message: driverInfo,
        title: 'My Spinr Ride - Driver Arrived',
      });
    } catch (error) {
      console.log('Share error:', error);
    }
  };

  const handleCopyDetails = async () => {
    const details = `Driver: ${currentDriver?.name || 'Unknown'} | Vehicle: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'} | Plate: ${currentDriver?.license_plate || 'Pending'} | OTP: ${pickupOtp}`;
    await Clipboard.setStringAsync(details);
    Alert.alert('Copied!', 'Driver details copied to clipboard');
  };

  // handleStartRide removed for production; handled automatically via status updates

  const pickupOtp = currentRide?.pickup_otp || '1234';

  return (
    <View style={styles.container}>
      {/* Header */}
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backButton} onPress={handleBack}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>

          <View style={styles.arrivedPill}>
            <View style={styles.greenDot} />
            <Text style={styles.arrivedText}>Driver has arrived</Text>
          </View>

          <TouchableOpacity style={styles.emergencyButton}>
            <Ionicons name="shield" size={20} color={SpinrConfig.theme.colors.primary} />
          </TouchableOpacity>
        </View>
      </SafeAreaView>

      {/* Map Area */}
      <View style={styles.mapContainer}>
        <View style={styles.mapPlaceholder}>
          {/* Pickup marker */}
          <View style={styles.pickupMarker}>
            <View style={styles.pickupMarkerInner}>
              <Ionicons name="location" size={20} color="#FFF" />
            </View>
          </View>

          {/* Car at pickup */}
          <View style={styles.carAtPickup}>
            <Ionicons name="car" size={16} color="#FFF" />
          </View>
        </View>
      </View>

      {/* Bottom Sheet */}
      <View style={styles.bottomSheet}>
        <View style={styles.sheetHandle} />

        {/* OTP Section */}
        <View style={styles.otpSection}>
          <Text style={styles.otpLabel}>Share this PIN with your driver</Text>
          <View style={styles.otpContainer}>
            {pickupOtp.split('').map((digit, index) => (
              <View key={index} style={styles.otpDigit}>
                <Text style={styles.otpDigitText}>{digit}</Text>
              </View>
            ))}
          </View>
          <Text style={styles.otpHint}>Driver will enter this to start the trip</Text>
        </View>

        {/* Driver Details Card - Comprehensive for Screenshot */}
        <View style={styles.driverDetailsCard}>
          <View style={styles.driverCardHeader}>
            <Text style={styles.driverCardTitle}>DRIVER DETAILS</Text>
            <TouchableOpacity style={styles.copyButton} onPress={handleCopyDetails}>
              <Ionicons name="copy-outline" size={14} color="#666" />
              <Text style={styles.copyText}>Copy</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.driverSection}>
            <View style={styles.driverAvatar}>
              <Ionicons name="person" size={28} color="#666" />
              <View style={styles.ratingBadge}>
                <Ionicons name="star" size={10} color="#FFB800" />
                <Text style={styles.ratingText}>{currentDriver?.rating || 'New'}</Text>
              </View>
            </View>

            <View style={styles.driverInfo}>
              <Text style={styles.driverName}>{currentDriver?.name || 'Unknown'}</Text>
              <Text style={styles.totalTrips}>{currentDriver?.total_rides || 0} trips completed</Text>
              <View style={styles.arrivedIndicator}>
                <Ionicons name="checkmark-circle" size={14} color="#10B981" />
                <Text style={styles.arrivedIndicatorText}>Arrived at pickup</Text>
              </View>
            </View>
          </View>

          {/* Vehicle Details */}
          <View style={styles.vehicleSection}>
            <View style={styles.vehicleRow}>
              <Ionicons name="car" size={18} color={SpinrConfig.theme.colors.primary} />
              <View style={styles.vehicleTextContainer}>
                <Text style={styles.vehicleLabel}>VEHICLE</Text>
                <Text style={styles.vehicleValue}>
                  {currentDriver?.vehicle_color || ''} {currentDriver?.vehicle_make || 'Unknown'} {currentDriver?.vehicle_model || 'Vehicle'}
                </Text>
              </View>
            </View>

            <View style={styles.plateRow}>
              <Text style={styles.plateEmoji}>🪪</Text>
              <View style={styles.vehicleTextContainer}>
                <Text style={styles.vehicleLabel}>LICENSE PLATE</Text>
                <Text style={styles.plateValue}>{currentDriver?.license_plate || 'Pending'}</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Action Buttons */}
        <View style={styles.actionButtons}>
          <TouchableOpacity style={styles.messageButton} onPress={handleMessage}>
            <Ionicons name="chatbubble" size={18} color="#FFF" />
            <Text style={styles.messageButtonText}>Message</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.callButton} onPress={handleCall}>
            <Ionicons name="call" size={20} color={SpinrConfig.theme.colors.primary} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.shareIconButton} onPress={handleShareTrip}>
            <Ionicons name="share-outline" size={20} color="#1A1A1A" />
          </TouchableOpacity>
        </View>

        {/* Demo Button removed for production */}
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
  arrivedPill: {
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
  arrivedText: {
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
    justifyContent: 'center',
    alignItems: 'center',
  },
  pickupMarker: {
    alignItems: 'center',
  },
  pickupMarkerInner: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 3,
    borderColor: '#FFF',
  },
  carAtPickup: {
    position: 'absolute',
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#1A1A1A',
    justifyContent: 'center',
    alignItems: 'center',
    top: '45%',
    left: '55%',
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
  otpSection: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
    marginBottom: 20,
  },
  otpLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: 'rgba(255,255,255,0.8)',
    marginBottom: 12,
  },
  otpContainer: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 10,
  },
  otpDigit: {
    width: 50,
    height: 56,
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
  },
  otpDigitText: {
    fontSize: 26,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#FFF',
  },
  otpHint: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: 'rgba(255,255,255,0.7)',
  },
  driverDetailsCard: {
    backgroundColor: '#F9F9F9',
    borderRadius: 16,
    padding: 14,
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
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    gap: 4,
  },
  copyText: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  driverSection: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  driverAvatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: '#E8E8E8',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
    position: 'relative',
  },
  ratingBadge: {
    position: 'absolute',
    bottom: -4,
    left: -4,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  ratingText: {
    fontSize: 10,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginLeft: 2,
  },
  driverInfo: {
    flex: 1,
  },
  driverName: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  totalTrips: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 1,
  },
  arrivedIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 4,
  },
  arrivedIndicatorText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#10B981',
    marginLeft: 4,
  },
  vehicleSection: {
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#E8E8E8',
  },
  vehicleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
  },
  vehicleTextContainer: {
    marginLeft: 10,
    flex: 1,
  },
  vehicleLabel: {
    fontSize: 9,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#999',
    letterSpacing: 0.5,
    marginBottom: 1,
  },
  vehicleValue: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  plateRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  plateEmoji: {
    fontSize: 16,
    width: 18,
  },
  plateValue: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    letterSpacing: 2,
  },
  callButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF0F0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  shareIconButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  actionButtons: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 12,
  },
  messageButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingVertical: 14,
    borderRadius: 28,
    gap: 6,
  },
  messageButtonText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
  },
  demoButton: {
    backgroundColor: '#10B981',
    borderRadius: 12,
    padding: 14,
    alignItems: 'center',
  },
  demoButtonText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
  },
});
