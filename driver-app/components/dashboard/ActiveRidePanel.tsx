import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, Alert } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { Animated } from 'react-native';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = {
  primary: SpinrConfig.theme.colors.background,
  accent: SpinrConfig.theme.colors.primary,
  accentDim: SpinrConfig.theme.colors.primaryDark,
  surface: SpinrConfig.theme.colors.surface,
  surfaceLight: SpinrConfig.theme.colors.surfaceLight,
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
  orange: '#FF9500',
  gold: '#FFD700',
  danger: SpinrConfig.theme.colors.error,
  border: SpinrConfig.theme.colors.border,
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
      case 'navigating_to_pickup':
        return 'Navigating to Pickup';
      case 'arrived_at_pickup':
        return 'Waiting for Rider';
      case 'trip_in_progress':
        return 'Trip in Progress';
    }
  };

  const getStatusColor = () => {
    switch (rideState) {
      case 'trip_in_progress':
        return COLORS.accent;
      case 'arrived_at_pickup':
        return COLORS.orange;
      default:
        return COLORS.gold;
    }
  };

  return (
    <Animated.View
      style={[styles.activePanel, { transform: [{ translateY: slideUpAnim }], opacity: fadeAnim }]}
    >
      <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.activePanelInner}>
        {/* Status Bar */}
        <View style={styles.rideStatusBar}>
          <View style={[styles.statusIndicator, { backgroundColor: getStatusColor() }]} />
          <Text style={styles.rideStatusText}>{getStatusText()}</Text>
          {ride.total_fare && <Text style={styles.rideFare}>${ride.total_fare.toFixed(2)}</Text>}
        </View>

        {/* Rider Info */}
        {rider && (
          <View style={styles.riderCard}>
            <View style={styles.riderAvatar}>
              <Ionicons name="person" size={24} color={COLORS.textDim} />
            </View>
            <View style={styles.riderInfo}>
              <Text style={styles.riderCardName}>{rider.first_name || rider.name || 'Rider'}</Text>
              {rider.rating && (
                <View style={styles.riderRating}>
                  <Ionicons name="star" size={12} color={COLORS.gold} />
                  <Text style={styles.textDim}>{rider.rating}</Text>
                </View>
              )}
            </View>
            <TouchableOpacity style={styles.contactBtn}>
              <Ionicons name="chatbubble-ellipses" size={20} color={COLORS.accent} />
            </TouchableOpacity>
            <TouchableOpacity style={styles.contactBtn}>
              <Ionicons name="call" size={20} color={COLORS.accent} />
            </TouchableOpacity>
          </View>
        )}

        {/* Addresses */}
        <View style={styles.addressRow}>
          <View style={styles.addressDots}>
            <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
            <View style={styles.dottedLine} />
            <View style={[styles.dot, { backgroundColor: '#FF4757' }]} />
          </View>
          <View style={styles.addresses}>
            <Text style={styles.addressTextSm} numberOfLines={1}>
              {ride.pickup_address}
            </Text>
            <View style={styles.addressDivider} />
            <Text style={styles.addressTextSm} numberOfLines={1}>
              {ride.dropoff_address}
            </Text>
          </View>
        </View>

        {/* OTP Input for arrived state */}
        {rideState === 'arrived_at_pickup' && (
          <View style={styles.otpSection}>
            <Text style={styles.otpLabel}>Enter Rider's PIN</Text>
            <View style={styles.otpRow}>
              {[0, 1, 2, 3].map((i) => (
                <TouchableOpacity
                  key={i}
                  style={[styles.otpBox, otpInput.length > i && styles.otpBoxFilled]}
                >
                  <Text style={styles.otpDigit}>{otpInput[i] || ''}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <View style={styles.otpKeypad}>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, null, 0, 'del'].map((key) => (
                <TouchableOpacity
                  key={String(key)}
                  style={styles.keypadBtn}
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
                >
                  {key === 'del' ? (
                    <Ionicons name="backspace" size={22} color={COLORS.text} />
                  ) : key !== null ? (
                    <Text style={styles.keypadText}>{key}</Text>
                  ) : (
                    <View />
                  )}
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
                style={styles.navBtn}
                onPress={() => onNavigate(ride.pickup_lat, ride.pickup_lng, 'Pickup')}
              >
                <Ionicons name="navigate" size={20} color="#fff" />
                <Text style={styles.navBtnText}>Navigate</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.arriveBtn}
                onPress={onArriveAtPickup}
                disabled={isLoading}
              >
                <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
                  {isLoading ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={styles.actionBtnText}>I've Arrived</Text>
                  )}
                </LinearGradient>
              </TouchableOpacity>
            </>
          )}

          {rideState === 'arrived_at_pickup' && (
            <TouchableOpacity
              style={styles.startBtn}
              onPress={onStartRide}
              disabled={isLoading}
            >
              <LinearGradient colors={[COLORS.orange, '#FF6B35']} style={styles.actionGradient}>
                {isLoading ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.actionBtnText}>Start Without PIN</Text>
                )}
              </LinearGradient>
            </TouchableOpacity>
          )}

          {rideState === 'trip_in_progress' && (
            <>
              <TouchableOpacity
                style={styles.navBtn}
                onPress={() => onNavigate(ride.dropoff_lat, ride.dropoff_lng, 'Dropoff')}
              >
                <Ionicons name="navigate" size={20} color="#fff" />
                <Text style={styles.navBtnText}>Navigate</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.completeBtn}
                onPress={() => {
                  Alert.alert(
                    'Complete Ride',
                    'Are you sure you want to complete this trip?',
                    [
                      { text: 'Cancel', style: 'cancel' },
                      { text: 'Complete', onPress: onCompleteRide },
                    ]
                  );
                }}
                disabled={isLoading}
              >
                <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
                  {isLoading ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={styles.actionBtnText}>Complete Trip</Text>
                  )}
                </LinearGradient>
              </TouchableOpacity>
            </>
          )}

          {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') && (
            <TouchableOpacity
              style={styles.cancelRideBtn}
              onPress={() => {
                Alert.alert(
                  'Cancel Ride',
                  'Are you sure you want to cancel? This may affect your rating.',
                  [
                    { text: 'No', style: 'cancel' },
                    { text: 'Yes, Cancel', style: 'destructive', onPress: onCancelRide },
                  ]
                );
              }}
            >
              <Text style={styles.cancelRideText}>Cancel Ride</Text>
            </TouchableOpacity>
          )}
        </View>
      </LinearGradient>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  activePanel: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: '70%',
  },
  activePanelInner: {
    flex: 1,
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    padding: 20,
    paddingBottom: 40,
  },
  rideStatusBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 16,
  },
  statusIndicator: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  rideStatusText: {
    flex: 1,
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.text,
  },
  rideFare: {
    fontSize: 18,
    fontWeight: '800',
    color: COLORS.accent,
  },
  riderCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  riderAvatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(255,255,255,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  riderInfo: {
    flex: 1,
  },
  riderCardName: {
    fontSize: 16,
    fontWeight: '600',
    color: COLORS.text,
  },
  riderRating: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 4,
  },
  textDim: {
    color: COLORS.textDim,
    fontSize: 14,
  },
  contactBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 8,
  },
  addressRow: {
    flexDirection: 'row',
    marginBottom: 16,
  },
  addressDots: {
    alignItems: 'center',
    width: 24,
    marginRight: 12,
  },
  dot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  dottedLine: {
    width: 2,
    flex: 1,
    backgroundColor: COLORS.surfaceLight,
    marginVertical: 4,
  },
  addresses: {
    flex: 1,
  },
  addressTextSm: {
    fontSize: 14,
    color: COLORS.text,
    paddingVertical: 8,
    fontWeight: '500',
  },
  addressDivider: {
    height: 1,
    backgroundColor: COLORS.surfaceLight,
  },
  otpSection: {
    marginBottom: 16,
  },
  otpLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: 12,
    textAlign: 'center',
  },
  otpRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 12,
    marginBottom: 16,
  },
  otpBox: {
    width: 50,
    height: 60,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
  },
  otpBoxFilled: {
    borderColor: COLORS.accent,
    backgroundColor: 'rgba(255, 71, 87, 0.05)',
  },
  otpDigit: {
    fontSize: 28,
    fontWeight: '700',
    color: COLORS.text,
  },
  otpKeypad: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 12,
  },
  keypadBtn: {
    width: 70,
    height: 55,
    borderRadius: 14,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  keypadText: {
    fontSize: 22,
    fontWeight: '600',
    color: COLORS.text,
  },
  rideActions: {
    gap: 12,
  },
  navBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.text,
    borderRadius: 16,
    paddingVertical: 16,
    gap: 8,
  },
  navBtnText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  arriveBtn: {
    borderRadius: 16,
    overflow: 'hidden',
  },
  startBtn: {
    borderRadius: 16,
    overflow: 'hidden',
  },
  completeBtn: {
    borderRadius: 16,
    overflow: 'hidden',
  },
  actionGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 16,
    gap: 8,
  },
  actionBtnText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  cancelRideBtn: {
    paddingVertical: 12,
    alignItems: 'center',
  },
  cancelRideText: {
    fontSize: 14,
    color: COLORS.danger,
    fontWeight: '600',
  },
});

export default ActiveRidePanel;
