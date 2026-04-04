import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, Alert, Animated } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = {
  primary: '#0B0F19', // Deep dark blue/black for contrast
  surface: '#1A2138', // Lighter dark for cards
  accent: SpinrConfig.theme.colors.primary,
  accentDim: SpinrConfig.theme.colors.primaryDark,
  success: SpinrConfig.theme.colors.success,
  text: '#FFFFFF',
  textDim: '#A0AEC0',
  orange: '#FF9500',
  gold: '#F6E05E',
  danger: SpinrConfig.theme.colors.error,
  border: 'rgba(255,255,255,0.1)',
};

interface Rider {
  first_name?: string;
  name?: string;
  rating?: number;
}

interface Ride {
  id: string;
  pickup_address: string;
  dropoff_address: string;
  pickup_lat: number;
  pickup_lng: number;
  dropoff_lat: number;
  dropoff_lng: number;
  total_fare?: number;
}

interface ActiveRidePanelProps {
  rideState: 'navigating_to_pickup' | 'arrived_at_pickup' | 'trip_in_progress';
  ride: Ride | null;
  rider: Rider | null;
  isLoading: boolean;
  otpInput: string;
  setOtpInput: (value: string) => void;
  onVerifyOTP: (otp: string) => void;
  onNavigate: (lat: number, lng: number, label: string) => void;
  onArriveAtPickup: () => void;
  onStartRide: () => void;
  onCompleteRide: () => void;
  onCancelRide: () => void;
  slideUpAnim: Animated.Value;
  fadeAnim: Animated.Value;
}

export const ActiveRidePanel: React.FC<ActiveRidePanelProps> = ({
  rideState,
  ride,
  rider,
  isLoading,
  otpInput,
  setOtpInput,
  onVerifyOTP,
  onNavigate,
  onArriveAtPickup,
  onStartRide,
  onCompleteRide,
  onCancelRide,
  slideUpAnim,
  fadeAnim,
}) => {
  if (!ride) return null;

  const getStatusText = () => {
    switch (rideState) {
      case 'navigating_to_pickup': return 'Navigating to Pickup';
      case 'arrived_at_pickup': return 'Waiting for Rider';
      case 'trip_in_progress': return 'Trip in Progress';
    }
  };

  const getStatusIndicator = () => {
    switch (rideState) {
      case 'trip_in_progress': 
        return <View style={[styles.pulseDot, { backgroundColor: COLORS.success }]} />;
      case 'arrived_at_pickup': 
        return <Ionicons name="time" size={16} color={COLORS.orange} />;
      default: 
        return <Ionicons name="navigate-circle" size={18} color={COLORS.accent} />;
    }
  };

  return (
    <Animated.View
      style={[styles.activePanelContainer, { transform: [{ translateY: slideUpAnim }], opacity: fadeAnim }]}
    >
      {/* Floating Header Card */}
      <View style={styles.floatingHeader}>
        <View style={styles.statusRow}>
          {getStatusIndicator()}
          <Text style={styles.statusText}>{getStatusText()}</Text>
        </View>
        <Text style={styles.fareHighlight}>
          ${ride.total_fare ? ride.total_fare.toFixed(2) : '0.00'}
        </Text>
      </View>

      <View style={styles.mainBottomSheet}>
        
        {/* Rider Info Card */}
        {rider && (
          <View style={styles.riderCard}>
            <View style={styles.riderAvatar}>
              <Ionicons name="person" size={24} color={COLORS.textDim} />
            </View>
            <View style={styles.riderInfo}>
              <Text style={styles.riderName}>{rider.first_name || rider.name || 'Rider'}</Text>
              {rider.rating && (
                <View style={styles.riderRating}>
                  <Ionicons name="star" size={12} color={COLORS.gold} />
                  <Text style={styles.ratingNumber}>{rider.rating.toFixed(1)}</Text>
                </View>
              )}
            </View>
            <TouchableOpacity style={styles.contactBtnActive}>
              <Ionicons name="chatbubble" size={20} color="#fff" />
            </TouchableOpacity>
            <TouchableOpacity style={styles.contactBtn}>
              <Ionicons name="call" size={20} color={COLORS.textDim} />
            </TouchableOpacity>
          </View>
        )}

        {/* Dynamic OTP Section */}
        {rideState === 'arrived_at_pickup' && (
          <View style={styles.otpSection}>
            <Text style={styles.otpLabel}>Ask rider for their 4-digit PIN</Text>
            <View style={styles.otpRow}>
              {[0, 1, 2, 3].map((i) => (
                <View key={i} style={[styles.otpBox, otpInput.length > i && styles.otpBoxFilled]}>
                  <Text style={styles.otpDigit}>{otpInput[i] || ''}</Text>
                </View>
              ))}
            </View>
            <View style={styles.otpKeypad}>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, null, 0, 'del'].map((key, idx) => (
                <TouchableOpacity
                  key={idx}
                  style={[styles.keypadBtn, key === null && { backgroundColor: 'transparent', elevation: 0 }]}
                  onPress={() => {
                    if (key === 'del') {
                      setOtpInput(otpInput.slice(0, -1));
                    } else if (key !== null && otpInput.length < 4) {
                      const newOtp = otpInput + String(key);
                      setOtpInput(newOtp);
                      if (newOtp.length === 4) {
                        onVerifyOTP(newOtp);
                      }
                    }
                  }}
                  activeOpacity={0.7}
                  disabled={key === null}
                >
                  {key === 'del' ? (
                    <Ionicons name="backspace" size={24} color={COLORS.text} />
                  ) : key !== null ? (
                    <Text style={styles.keypadText}>{key}</Text>
                  ) : null}
                </TouchableOpacity>
              ))}
            </View>
          </View>
        )}

        {/* Action Buttons */}
        <View style={styles.rideActions}>
          {rideState === 'navigating_to_pickup' && (
            <>
              <TouchableOpacity
                style={[styles.actionBtn, { backgroundColor: COLORS.surface }]}
                onPress={() => onNavigate(ride.pickup_lat, ride.pickup_lng, 'Pickup')}
              >
                <Ionicons name="navigate" size={20} color="#63B3ED" />
                <Text style={styles.actionBtnTextOutline}>Navigate</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.actionBtn, styles.primaryBtn]}
                onPress={onArriveAtPickup}
                disabled={isLoading}
              >
                {isLoading ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>I've Arrived</Text>}
              </TouchableOpacity>
            </>
          )}

          {rideState === 'arrived_at_pickup' && (
            <TouchableOpacity
              style={[styles.actionBtn, { backgroundColor: 'rgba(255,255,255,0.1)' }]}
              onPress={onStartRide}
              disabled={isLoading}
            >
              {isLoading ? <ActivityIndicator color="#fff" /> : <Text style={styles.actionBtnTextOutline}>Start Without PIN</Text>}
            </TouchableOpacity>
          )}

          {rideState === 'trip_in_progress' && (
            <>
              <TouchableOpacity
                style={[styles.actionBtn, { backgroundColor: COLORS.surface }]}
                onPress={() => onNavigate(ride.dropoff_lat, ride.dropoff_lng, 'Dropoff')}
              >
                <Ionicons name="navigate" size={20} color="#63B3ED" />
                <Text style={styles.actionBtnTextOutline}>Navigate</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.actionBtn, styles.primaryBtn, { backgroundColor: COLORS.success }]}
                onPress={() => {
                  Alert.alert('Complete Ride', 'Are you sure you want to complete this trip?', [
                    { text: 'Cancel', style: 'cancel' },
                    { text: 'Complete', onPress: onCompleteRide },
                  ]);
                }}
                disabled={isLoading}
              >
                {isLoading ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Complete Trip</Text>}
              </TouchableOpacity>
            </>
          )}

          {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') && (
            <TouchableOpacity
              style={styles.cancelRideBtn}
              onPress={() => {
                Alert.alert('Cancel Ride', 'Are you sure you want to cancel? This may affect your rating.', [
                  { text: 'No', style: 'cancel' },
                  { text: 'Yes, Cancel', style: 'destructive', onPress: onCancelRide },
                ]);
              }}
            >
              <Text style={styles.cancelRideText}>Cancel Ride</Text>
            </TouchableOpacity>
          )}
        </View>

      </View>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  activePanelContainer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    zIndex: 100,
  },
  // Floating top status pill
  floatingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: COLORS.primary,
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderRadius: 24,
    marginHorizontal: 16,
    marginBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 8,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  pulseDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  statusText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  fareHighlight: {
    fontSize: 18,
    fontWeight: '900',
    color: COLORS.success,
  },
  // Dark bottom sheet
  mainBottomSheet: {
    backgroundColor: COLORS.primary,
    borderTopLeftRadius: 32,
    borderTopRightRadius: 32,
    padding: 24,
    paddingBottom: 40,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -5 },
    shadowOpacity: 0.3,
    shadowRadius: 15,
    elevation: 10,
  },
  // Rider profile card
  riderCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: 20,
    padding: 16,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  riderAvatar: {
    width: 50,
    height: 50,
    borderRadius: 25,
    backgroundColor: 'rgba(255,255,255,0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  riderInfo: {
    flex: 1,
  },
  riderName: {
    fontSize: 18,
    fontWeight: '700',
    color: '#fff',
    marginBottom: 4,
  },
  riderRating: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  ratingNumber: {
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '600',
  },
  contactBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.05)',
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 10,
  },
  contactBtnActive: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: COLORS.accent,
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 10,
  },
  // OTP Section
  otpSection: {
    backgroundColor: COLORS.surface,
    borderRadius: 24,
    padding: 20,
    alignItems: 'center',
    marginBottom: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  otpLabel: {
    fontSize: 15,
    color: COLORS.textDim,
    fontWeight: '600',
    marginBottom: 16,
  },
  otpRow: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 24,
  },
  otpBox: {
    width: 55,
    height: 65,
    borderRadius: 16,
    backgroundColor: 'rgba(0,0,0,0.2)',
    borderWidth: 2,
    borderColor: 'transparent',
    justifyContent: 'center',
    alignItems: 'center',
  },
  otpBoxFilled: {
    borderColor: COLORS.accent,
    backgroundColor: 'rgba(255, 71, 87, 0.1)',
  },
  otpDigit: {
    fontSize: 32,
    fontWeight: '800',
    color: '#fff',
  },
  otpKeypad: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 12,
  },
  keypadBtn: {
    width: '28%',
    aspectRatio: 1.5,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
  },
  keypadText: {
    fontSize: 24,
    fontWeight: '600',
    color: '#fff',
  },
  // Actions
  rideActions: {
    gap: 12,
  },
  actionBtn: {
    flexDirection: 'row',
    height: 56,
    borderRadius: 16,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 8,
  },
  actionBtnTextOutline: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  primaryBtn: {
    backgroundColor: COLORS.accent,
  },
  primaryBtnText: {
    fontSize: 18,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: 0.5,
  },
  cancelRideBtn: {
    paddingVertical: 14,
    alignItems: 'center',
  },
  cancelRideText: {
    color: COLORS.danger,
    fontSize: 15,
    fontWeight: '600',
  },
});

export default ActiveRidePanel;
