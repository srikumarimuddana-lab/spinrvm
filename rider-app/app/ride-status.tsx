import React, { useEffect, useState, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
  BackHandler,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import api from '@shared/api/client';
import CustomAlert from '@shared/components/CustomAlert';
import { FreeCancelTimer } from '../components/FreeCancelTimer';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function RideStatusScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, simulateDriverArrival, clearRide } = useRideStore();

  const [pulseAnim] = useState(new Animated.Value(1));
  const [dotAnim] = useState(new Animated.Value(0));
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      // Poll as a fallback. The WebSocket client in
      // hooks/useRiderSocket.ts delivers ride-state updates in
      // real-time, so this only fires if the WS drops.
      const interval = setInterval(() => fetchRide(rideId), 15000);
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

  const handleBackPress = () => {
    if (!currentRide) {
      router.back();
      return;
    }

    // UX-001: Use server-provided cancellation fee (from app_settings) instead
    // of the old hardcoded Math.min(5, fare * 0.2) formula.
    const cancellationFee = (currentRide as any).cancellation_fee ?? 3.0;
    const freeCancelSecondsLeft = (currentRide as any).free_cancel_seconds_remaining ?? null;
    const isFreeCancel = freeCancelSecondsLeft === null || freeCancelSecondsLeft > 0;
    const status = currentRide.status;

    if (status === 'driver_arrived') {
      setAlertState({
        visible: true,
        title: 'Driver is waiting',
        message: isFreeCancel
          ? 'Your driver has arrived. Cancel for free before the window closes.'
          : `Your driver has arrived. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`,
        variant: 'warning',
        buttons: [
          { text: 'Keep Ride', style: 'cancel' },
          {
            text: isFreeCancel ? 'Cancel (Free)' : `Cancel & Pay $${cancellationFee.toFixed(2)}`,
            style: 'destructive',
            onPress: async () => { await cancelRide(); clearRide(); router.replace('/(tabs)' as any); }
          },
        ],
      });
    } else if (status === 'driver_assigned' || status === 'driver_accepted') {
      setAlertState({
        visible: true,
        title: 'Cancel ride?',
        message: isFreeCancel
          ? 'Your driver is on the way. Cancel for free right now.'
          : `Cancellation fee of $${cancellationFee.toFixed(2)} applies.`,
        variant: 'warning',
        buttons: [
          { text: 'Keep Ride', style: 'cancel' },
          {
            text: isFreeCancel ? 'Cancel (Free)' : `Cancel & Pay $${cancellationFee.toFixed(2)}`,
            style: 'destructive',
            onPress: async () => { await cancelRide(); clearRide(); router.replace('/(tabs)' as any); }
          },
        ],
      });
    } else {
      setAlertState({
        visible: true,
        title: 'Cancel search?',
        message: 'Stop looking for a driver? No charge.',
        variant: 'info',
        buttons: [
          { text: 'Keep searching', style: 'cancel' },
          { text: 'Cancel', onPress: async () => { await cancelRide(); clearRide(); router.replace('/(tabs)' as any); } },
        ],
      });
    }
  };

  // Handle hardware back button (Android)
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      handleBackPress();
      return true; // prevent default back
    });
    return () => sub.remove();
  }, [currentRide?.status]);

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
            <Ionicons name="person" size={32} color={colors.textDim} />
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
            <Ionicons name="call" size={22} color={colors.primary} />
          </TouchableOpacity>
        </View>

        <View style={styles.vehicleCard}>
          <View style={styles.vehicleInfo}>
            <Text style={styles.vehicleMake}>
              {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
            </Text>
            <Text style={styles.licensePlate}>{currentDriver?.license_plate}</Text>
          </View>
          <Ionicons name="car" size={32} color={colors.primary} />
        </View>
      </View>

      {/* Status Info */}
      <View style={styles.statusInfo}>
        <View style={styles.statusIcon}>
          <Ionicons name="navigate" size={24} color={colors.primary} />
        </View>
        <View style={styles.statusTextContainer}>
          <Text style={styles.statusLabel}>Driver is on the way</Text>
          <Text style={styles.statusEta}>Arriving in ~5 min</Text>
        </View>
      </View>

      {/* UX-001: Free cancellation countdown */}
      <View style={{ marginTop: 12 }}>
        <FreeCancelTimer
          driverAcceptedAt={(currentRide as any)?.driver_accepted_at}
          freeCancelWindowSeconds={(currentRide as any)?.free_cancel_window_seconds ?? 120}
          cancellationFee={(currentRide as any)?.cancellation_fee ?? 3.0}
        />
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
            <Ionicons name="person" size={32} color={colors.textDim} />
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{currentDriver?.name}</Text>
            <Text style={styles.arrivedText}>
              <Ionicons name="checkmark-circle" size={14} color="#10B981" /> Arrived at pickup
            </Text>
          </View>
          <TouchableOpacity style={styles.callButton}>
            <Ionicons name="call" size={22} color={colors.primary} />
          </TouchableOpacity>
        </View>

        <View style={styles.vehicleCard}>
          <View style={styles.vehicleInfo}>
            <Text style={styles.vehicleMake}>
              {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
            </Text>
            <Text style={styles.licensePlate}>{currentDriver?.license_plate}</Text>
          </View>
          <Ionicons name="car" size={32} color={colors.primary} />
        </View>
      </View>

    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={handleBackPress}>
          <Ionicons name="close" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>
          {!currentRide && 'Loading...'}
          {currentRide?.status === 'searching' && 'Finding driver...'}
          {(currentRide?.status === 'driver_assigned' || currentRide?.status === 'driver_accepted') && 'Driver on the way'}
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
        {!currentRide ? (
          <View style={styles.statusContainer}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.statusTitle}>Loading ride details...</Text>
          </View>
        ) : (
          <>
            {currentRide.status === 'searching' && renderSearching()}
            {(currentRide.status === 'driver_assigned' || currentRide.status === 'driver_accepted') && renderDriverAssigned()}
            {currentRide.status === 'driver_arrived' && renderDriverArrived()}

            {/* Cancel Button */}
            <TouchableOpacity style={styles.cancelButton} onPress={handleBackPress}>
              <Text style={styles.cancelButtonText}>
                {currentRide.status === 'searching' ? 'Cancel Search' : 'Cancel Ride'}
              </Text>
            </TouchableOpacity>

            {/* DEV CONTROLS — remove in production */}
            {__DEV__ && (
              <View style={styles.devBar}>
                <Text style={styles.devLabel}>DEV: {currentRide.status}</Text>
                {currentRide.status === 'searching' && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try { await simulateDriverArrival(); } catch {}
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Assign Driver</Text>
                  </TouchableOpacity>
                )}
                {(currentRide.status === 'driver_assigned' || currentRide.status === 'driver_accepted') && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try { await api.post(`/drivers/rides/${currentRide.id}/arrive`); } catch {}
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Arrive at Pickup</Text>
                  </TouchableOpacity>
                )}
                {currentRide.status === 'driver_arrived' && (
                  <TouchableOpacity style={styles.devBtn} onPress={() => {
                    router.replace({ pathname: '/driver-arriving', params: { rideId: currentRide.id } } as any);
                  }}>
                    <Text style={styles.devBtnText}>Go to Arriving Screen</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
          </>
        )}
      </View>
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

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.border,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
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
      color: colors.text,
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
      color: colors.textDim,
      marginTop: 8,
    },
    bottomSheet: {
      backgroundColor: colors.surface,
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
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 20,
    },
    statusTitle: {
      fontSize: 22,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 8,
    },
    statusSubtitle: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    demoButton: {
      marginTop: 20,
      paddingHorizontal: 20,
      paddingVertical: 10,
      backgroundColor: colors.border,
      borderRadius: 20,
    },
    demoButtonText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    driverContainer: {
      paddingTop: 8,
    },
    driverCard: {
      backgroundColor: colors.surfaceLight,
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
      backgroundColor: colors.border,
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
      color: colors.text,
      marginBottom: 4,
    },
    ratingContainer: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    ratingText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      marginLeft: 4,
    },
    tripsText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
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
      backgroundColor: colors.surface,
      borderRadius: 12,
      padding: 12,
    },
    vehicleInfo: {},
    vehicleMake: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    licensePlate: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
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
      color: colors.text,
    },
    statusEta: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    simulateButton: {
      backgroundColor: colors.border,
      borderRadius: 12,
      padding: 14,
      alignItems: 'center',
    },
    simulateButtonText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    arrivedContainer: {},
    otpCard: {
      backgroundColor: colors.primary,
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
      color: colors.primary,
    },
    devBar: {
      flexDirection: 'row',
      alignItems: 'center',
      flexWrap: 'wrap',
      gap: 8,
      marginTop: 16,
      padding: 12,
      backgroundColor: '#FEF3C7',
      borderRadius: 12,
      borderWidth: 1,
      borderColor: '#F59E0B',
    },
    devLabel: {
      fontSize: 11,
      fontWeight: '700',
      color: '#92400E',
      marginRight: 4,
    },
    devBtn: {
      backgroundColor: '#F59E0B',
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 8,
    },
    devBtnText: {
      fontSize: 12,
      fontWeight: '700',
      color: '#FFF',
    },
  });
}
