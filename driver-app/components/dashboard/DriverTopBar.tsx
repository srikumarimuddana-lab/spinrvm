import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = {
  overlay: 'rgba(255, 255, 255, 0.95)',
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
  surfaceLight: SpinrConfig.theme.colors.surfaceLight,
  success: SpinrConfig.theme.colors.success,
};

interface DriverData {
  name?: string;
  vehicle_make?: string;
  vehicle_model?: string;
  license_plate?: string;
}

interface DriverTopBarProps {
  driverData?: DriverData;
  user?: { first_name?: string };
  isOnline: boolean;
}

export const DriverTopBar: React.FC<DriverTopBarProps> = ({
  driverData,
  user,
  isOnline,
}) => {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.topBar, { top: insets.top + 8 }]}>
      <View style={styles.topBarInner}>
        <View style={styles.driverInfo}>
          <View style={styles.avatarSmall}>
            <Ionicons name="person" size={18} color={COLORS.textDim} />
          </View>
          <View>
            <Text style={styles.driverName}>
              {driverData?.name || user?.first_name || 'Driver'}
            </Text>
            <Text style={styles.vehicleInfo}>
              {driverData?.vehicle_make} {driverData?.vehicle_model} · {driverData?.license_plate}
            </Text>
          </View>
        </View>
        <View style={[styles.onlineBadge, isOnline && styles.onlineBadgeActive]}>
          <View style={[styles.onlineDot, { backgroundColor: isOnline ? COLORS.success : COLORS.textDim }]} />
          <Text style={[styles.onlineBadgeText, isOnline && { color: COLORS.success }]}>
            {isOnline ? 'ONLINE' : 'OFFLINE'}
          </Text>
        </View>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  topBar: {
    position: 'absolute',
    left: 16,
    right: 16,
    zIndex: 10,
  },
  topBarInner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: COLORS.overlay,
    borderRadius: 16,
    padding: 12,
    paddingHorizontal: 16,
  },
  driverInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  avatarSmall: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  driverName: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
  vehicleInfo: {
    color: COLORS.textDim,
    fontSize: 11,
  },
  onlineBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: COLORS.surfaceLight,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 20,
  },
  onlineBadgeActive: {
    backgroundColor: 'rgba(0, 230, 118, 0.12)',
  },
  onlineDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  onlineBadgeText: {
    color: COLORS.textDim,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.8,
  },
});

export default DriverTopBar;
