import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Animated } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = {
  primary: SpinrConfig.theme.colors.background,
  accent: SpinrConfig.theme.colors.primary,
  success: SpinrConfig.theme.colors.success,
  surface: SpinrConfig.theme.colors.surface,
  surfaceLight: SpinrConfig.theme.colors.surfaceLight,
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
  border: SpinrConfig.theme.colors.border,
  orange: '#FF9500',
};

interface DriverData {
  name?: string;
  vehicle_make?: string;
  vehicle_model?: string;
  license_plate?: string;
  is_online?: boolean;
  acceptance_rate?: string;
  total_rides?: string;
  is_verified?: boolean;
}

interface Earnings {
  total_earnings?: number;
}

interface IdlePanelProps {
  isOnline: boolean;
  driverData?: DriverData;
  earnings?: Earnings;
  onToggleOnline: () => void;
  pulseAnim: any;
}

export const DriverIdlePanel: React.FC<IdlePanelProps> = ({
  isOnline,
  driverData,
  earnings,
  onToggleOnline,
  pulseAnim,
}) => {
  const renderStatsRow = () => (
    <View style={styles.statsGrid}>
      <View style={styles.statBox}>
        <Text style={styles.statValue}>{driverData?.acceptance_rate || '100'}%</Text>
        <Text style={styles.statLabel}>Acceptance</Text>
      </View>
      <View style={styles.statBox}>
        <Text style={styles.statValue}>${(earnings?.total_earnings || 0).toFixed(2)}</Text>
        <Text style={styles.statLabel}>Earnings</Text>
      </View>
      <View style={styles.statBox}>
        <Text style={styles.statValue}>{driverData?.total_rides || '0'}</Text>
        <Text style={styles.statLabel}>Rides</Text>
      </View>
    </View>
  );

  return (
    <View style={styles.idlePanel}>
      <TouchableOpacity
        style={[
          styles.onlineToggle,
          !driverData?.is_verified ? styles.onlineDisabled : (isOnline ? styles.onlineActive : styles.onlineInactive)
        ]}
        onPress={onToggleOnline}
        activeOpacity={driverData?.is_verified ? 0.8 : 1}
        disabled={!driverData?.is_verified}
      >
        <Animated.View style={[styles.pulseIndicator, { transform: [{ scale: pulseAnim }] }]}>
          <View style={[
            styles.statusDot,
            !driverData?.is_verified ? { backgroundColor: COLORS.orange } : (isOnline ? { backgroundColor: COLORS.success } : { backgroundColor: '#FF4757' })
          ]} />
        </Animated.View>
        <View style={styles.toggleText}>
          <Text style={styles.toggleLabel}>
            {!driverData?.is_verified ? 'Account Not Verified' : (isOnline ? "You're Online" : "You're Offline")}
          </Text>
          <Text style={styles.toggleSub}>
            {!driverData?.is_verified
              ? 'Complete your profile and wait for admin approval'
              : (isOnline ? 'Waiting for ride requests...' : 'Go online to start earning')}
          </Text>
        </View>
        <View style={[
          styles.toggleSwitch,
          !driverData?.is_verified ? styles.toggleSwitchDisabled : (isOnline && styles.toggleSwitchOn)
        ]}>
          <View style={[
            styles.toggleKnob,
            !driverData?.is_verified ? styles.toggleKnobDisabled : (isOnline && styles.toggleKnobOn)
          ]} />
        </View>
      </TouchableOpacity>

      {renderStatsRow()}
    </View>
  );
};

const styles = StyleSheet.create({
  idlePanel: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingBottom: 30,
    paddingHorizontal: 16,
    paddingTop: 16,
  },
  onlineToggle: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 20,
    borderRadius: 16,
    gap: 14,
    backgroundColor: COLORS.surface,
    marginBottom: 16,
    borderWidth: 1,
  },
  onlineActive: {
    borderColor: COLORS.success,
  },
  onlineInactive: {
    borderColor: 'transparent',
  },
  onlineDisabled: {
    opacity: 0.7,
    borderColor: COLORS.border,
  },
  pulseIndicator: {
    marginRight: 0,
  },
  statusDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  toggleText: {
    flex: 1,
  },
  toggleLabel: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 4,
  },
  toggleSub: {
    fontSize: 13,
    color: COLORS.textDim,
  },
  toggleSwitch: {
    width: 50,
    height: 30,
    borderRadius: 15,
    backgroundColor: COLORS.surfaceLight,
    padding: 2,
    justifyContent: 'center',
  },
  toggleSwitchOn: {
    backgroundColor: COLORS.success,
    alignItems: 'flex-end',
  },
  toggleSwitchDisabled: {
    backgroundColor: COLORS.border,
  },
  toggleKnob: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: '#fff',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.2,
    shadowRadius: 2,
    elevation: 2,
  },
  toggleKnobOn: {
    marginRight: 0,
  },
  toggleKnobDisabled: {
    backgroundColor: '#f5f5f5',
  },
  statsGrid: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  statBox: {
    flex: 1,
    backgroundColor: COLORS.surface,
    padding: 12,
    borderRadius: 12,
    alignItems: 'center',
  },
  statValue: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 4,
  },
  statLabel: {
    fontSize: 11,
    color: COLORS.textDim,
    fontWeight: '600',
    textTransform: 'uppercase',
  },
});

export default DriverIdlePanel;
