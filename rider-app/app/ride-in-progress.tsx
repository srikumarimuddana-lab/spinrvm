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

export default function RideInProgressScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, clearRide } = useRideStore();
  const [eta, setEta] = useState(15);
  const [estimatedTime, setEstimatedTime] = useState('12:45 PM');
  const [currentLocation, setCurrentLocation] = useState('4th Avenue North');
  const [isSharingLocation, setIsSharingLocation] = useState(false);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
    }

    // Calculate estimated arrival time
    const now = new Date();
    now.setMinutes(now.getMinutes() + 15);
    setEstimatedTime(now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
  }, [rideId]);

  useEffect(() => {
    if (currentRide?.status === 'completed') {
      router.replace({ pathname: '/ride-completed', params: { rideId } });
    }
  }, [currentRide?.status]);

  const handleSafety = () => {
    Alert.alert('Safety', 'Emergency services will be contacted.');
  };

  const handleShareTrip = async () => {
    const liveTrackingUrl = `spinr://track/${rideId || 'demo'}`;
    const tripDetails = `
🚗 TRACK MY SPINR RIDE - LIVE LOCATION

👤 DRIVER: ${currentDriver?.name || 'Unknown'}
⭐ RATING: ${currentDriver?.rating || 'New'}

🚙 VEHICLE: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'}
📋 LICENSE PLATE: ${currentDriver?.license_plate || 'Pending'}

📍 CURRENT LOCATION: ${currentLocation}
📍 HEADING TO: ${currentRide?.dropoff_address || '1055 Canada Place'}

⏱️ ESTIMATED ARRIVAL: ${estimatedTime} (${eta} min left)

🔴 LIVE TRACKING LINK:
${liveTrackingUrl}

I've shared my live location with you for safety.
`.trim();

    try {
      await Share.share({
        message: tripDetails,
        title: 'Track My Spinr Ride',
      });
      setIsSharingLocation(true);
      Alert.alert(
        'Trip Shared!',
        'Your live location is now being shared. They can track your journey in real-time.'
      );
    } catch (error) {
      console.log(error);
    }
  };

  const handleCopyTrackingLink = async () => {
    const trackingLink = `spinr://track/${rideId || 'demo'}`;
    await Clipboard.setStringAsync(trackingLink);
    Alert.alert('Copied!', 'Live tracking link copied to clipboard');
  };

  const handleCancelRide = () => {
    Alert.alert(
      'Cancel Ride',
      'Are you sure you want to cancel this ride? You may be charged a cancellation fee.',
      [
        { text: 'No', style: 'cancel' },
        {
          text: 'Yes, Cancel',
          style: 'destructive',
          onPress: async () => {
            await cancelRide();
            clearRide();
            router.replace('/(tabs)');
          },
        },
      ]
    );
  };

  const handleLocation = () => {
    // Center on current location
  };

  const progressPercent = ((15 - eta) / 15) * 100;

  return (
    <View style={styles.container}>
      {/* Header Status */}
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.statusPill}>
          <View style={styles.greenDot} />
          <Text style={styles.statusText}>Ride Started - Enjoy your trip</Text>
        </View>
      </SafeAreaView>

      {/* Map Area */}
      <View style={styles.mapContainer}>
        <View style={styles.mapPlaceholder}>
          {/* Grid pattern */}
          <View style={styles.gridPattern}>
            {Array.from({ length: 10 }).map((_, i) => (
              <View key={`h-${i}`} style={[styles.gridLine, styles.gridHorizontal, { top: `${i * 10}%` }]} />
            ))}
            {Array.from({ length: 10 }).map((_, i) => (
              <View key={`v-${i}`} style={[styles.gridLine, styles.gridVertical, { left: `${i * 10}%` }]} />
            ))}
          </View>

          {/* Route line */}
          <View style={styles.routeLine} />

          {/* Current position marker */}
          <View style={styles.carMarker}>
            <Ionicons name="car" size={18} color="#FFF" />
          </View>

          {/* Destination marker */}
          <View style={styles.destinationMarker}>
            <View style={styles.destinationDot} />
          </View>
        </View>

        {/* Location button */}
        <TouchableOpacity style={styles.locationButton} onPress={handleLocation}>
          <Ionicons name="navigate" size={22} color="#1A1A1A" />
        </TouchableOpacity>
      </View>

      {/* Bottom Sheet */}
      <View style={styles.bottomSheet}>
        <View style={styles.sheetHandle} />

        {/* ETA Section */}
        <View style={styles.etaSection}>
          <View style={styles.etaLeft}>
            <Text style={styles.etaLabel}>ESTIMATED ARRIVAL</Text>
            <Text style={styles.etaTime}>{estimatedTime}</Text>
            <View style={styles.etaRemaining}>
              <Ionicons name="time" size={16} color={SpinrConfig.theme.colors.primary} />
              <Text style={styles.etaRemainingText}>{eta} min left</Text>
            </View>
          </View>

          <TouchableOpacity style={styles.safetyButton} onPress={handleSafety}>
            <Ionicons name="shield-checkmark" size={24} color="#FFF" />
            <Text style={styles.safetyText}>Safety</Text>
          </TouchableOpacity>
        </View>

        {/* Progress Bar */}
        <View style={styles.progressContainer}>
          <View style={[styles.progressBar, { width: `${progressPercent}%` }]} />
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
                <Text style={styles.currentLabel}>Current: {currentLocation}</Text>
              </View>

              <View style={[styles.addressRow, { marginTop: 24 }]}>
                <Text style={styles.destinationName} numberOfLines={1}>
                  {currentRide?.dropoff_address || '1055 Canada Place'}
                </Text>
                <Text style={styles.destinationCity}>Saskatoon, SK</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Live Sharing Banner - if active */}
        {isSharingLocation && (
          <View style={styles.liveSharingBanner}>
            <View style={styles.liveIndicator}>
              <View style={styles.liveDot} />
              <Text style={styles.liveText}>LIVE</Text>
            </View>
            <Text style={styles.sharingText}>Location sharing active</Text>
            <TouchableOpacity onPress={handleCopyTrackingLink}>
              <Ionicons name="copy-outline" size={18} color="#666" />
            </TouchableOpacity>
          </View>
        )}

        {/* Action Buttons */}
        <View style={styles.actionButtons}>
          <TouchableOpacity
            style={[styles.shareButton, isSharingLocation && styles.shareButtonActive]}
            onPress={handleShareTrip}
          >
            <View style={styles.shareButtonIcon}>
              <Ionicons name="location" size={18} color={isSharingLocation ? "#FFF" : "#1A1A1A"} />
              {isSharingLocation && <View style={styles.sharePulse} />}
            </View>
            <Text style={[styles.shareButtonText, isSharingLocation && styles.shareButtonTextActive]}>
              {isSharingLocation ? 'Sharing Live' : 'Share Trip'}
            </Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.cancelButton} onPress={handleCancelRide}>
            <Ionicons name="close" size={20} color="#1A1A1A" />
            <Text style={styles.cancelButtonText}>Cancel Ride</Text>
          </TouchableOpacity>
        </View>
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
    alignItems: 'center',
    paddingTop: 8,
  },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  greenDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#10B981',
    marginRight: 10,
  },
  statusText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  mapContainer: {
    flex: 1,
    position: 'relative',
  },
  mapPlaceholder: {
    flex: 1,
    backgroundColor: '#F0F0F0',
    position: 'relative',
    overflow: 'hidden',
  },
  gridPattern: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  gridLine: {
    position: 'absolute',
    backgroundColor: '#E0E0E0',
  },
  gridHorizontal: {
    height: 1,
    left: 0,
    right: 0,
  },
  gridVertical: {
    width: 1,
    top: 0,
    bottom: 0,
  },
  routeLine: {
    position: 'absolute',
    top: '20%',
    left: '15%',
    width: 250,
    height: 3,
    backgroundColor: SpinrConfig.theme.colors.primary,
    transform: [{ rotate: '45deg' }],
  },
  carMarker: {
    position: 'absolute',
    top: '40%',
    left: '40%',
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 3,
    borderColor: '#FFF',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
  },
  destinationMarker: {
    position: 'absolute',
    top: '25%',
    right: '25%',
  },
  destinationDot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderWidth: 6,
    borderColor: 'rgba(238, 43, 43, 0.3)',
  },
  locationButton: {
    position: 'absolute',
    right: 20,
    bottom: 20,
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 6,
    elevation: 4,
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
  etaSection: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 16,
  },
  etaLeft: {},
  etaLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
    letterSpacing: 0.5,
  },
  etaTime: {
    fontSize: 36,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginVertical: 2,
  },
  etaRemaining: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  etaRemainingText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: SpinrConfig.theme.colors.primary,
    marginLeft: 4,
  },
  safetyButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 16,
    alignItems: 'center',
  },
  safetyText: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
    marginTop: 4,
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
    marginBottom: 20,
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
    height: 40,
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
  currentLabel: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
  },
  destinationName: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  destinationCity: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  liveSharingBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F0FFF4',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#D1FAE5',
  },
  liveIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#EF4444',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    marginRight: 10,
  },
  liveDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#FFF',
    marginRight: 4,
  },
  liveText: {
    fontSize: 10,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#FFF',
    letterSpacing: 0.5,
  },
  sharingText: {
    flex: 1,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#059669',
  },
  actionButtons: {
    flexDirection: 'row',
    gap: 12,
  },
  shareButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#FFF',
    paddingVertical: 14,
    borderRadius: 28,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    gap: 8,
  },
  shareButtonActive: {
    backgroundColor: '#10B981',
    borderColor: '#10B981',
  },
  shareButtonIcon: {
    position: 'relative',
  },
  sharePulse: {
    position: 'absolute',
    top: -2,
    right: -2,
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#EF4444',
  },
  shareButtonText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  shareButtonTextActive: {
    color: '#FFF',
  },
  cancelButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#FFF',
    paddingVertical: 14,
    borderRadius: 28,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    gap: 8,
  },
  cancelButtonText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
});
