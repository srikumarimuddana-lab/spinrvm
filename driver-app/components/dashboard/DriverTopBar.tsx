import React from 'react';
import { View, Text, StyleSheet, Platform, StatusBar } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import SpinrConfig from '@shared/config/spinr.config';
import { useLanguageStore } from '../../store/languageStore';
import type { ConnectionState } from '../../hooks/useDriverDashboard';

// expo-blur crashes on Android under RN 0.85 Bridgeless mode when props change
// during a re-render (native component init race). On Android it doesn't
// actually blur — just a semi-transparent overlay — so use a plain View.
const SafeBlurView: React.FC<{ intensity?: number; tint?: string; style?: any; children?: React.ReactNode }> = ({ style, children }) => (
  <View style={style}>{children}</View>
);

const COLORS = {
  overlay: 'rgba(255, 255, 255, 0.75)',
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
  surfaceLight: SpinrConfig.theme.colors.surfaceLight,
  success: SpinrConfig.theme.colors.success,
  primary: SpinrConfig.theme.colors.primary,
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
  connectionState?: ConnectionState;
  surgeMultiplier?: number;
}

export const DriverTopBar: React.FC<DriverTopBarProps> = ({
  driverData,
  user,
  isOnline,
  connectionState,
  surgeMultiplier,
}) => {
  const isSurgeActive = isOnline && typeof surgeMultiplier === 'number' && surgeMultiplier > 1.0;
  const { t } = useLanguageStore();
  const insets = useSafeAreaInsets();
  const statusBarHeight = StatusBar.currentHeight ?? 0;
  const showBanner = isOnline && connectionState && connectionState !== 'connected';
  const isReconnecting = connectionState === 'reconnecting';

  return (
    <View style={[styles.topBarContainer, { top: Math.max(insets.top, statusBarHeight) }]}>
      <SafeBlurView intensity={Platform.OS === 'ios' ? 60 : 100} tint="light" style={styles.blurContainer}>
        {showBanner && (
          <View style={[styles.connectionBanner, isReconnecting ? styles.bannerReconnecting : styles.bannerDisconnected]}>
            <Ionicons
              name={isReconnecting ? 'wifi-outline' : 'wifi-outline'}
              size={13}
              color={isReconnecting ? '#92400E' : '#991B1B'}
            />
            <Text allowFontScaling={false} style={[styles.bannerText, isReconnecting ? styles.bannerTextReconnecting : styles.bannerTextDisconnected]}>
              {isReconnecting ? t('dashboard.reconnecting') : t('dashboard.connectionLost')}
            </Text>
          </View>
        )}
        <View style={styles.topBarInner}>
          <View style={styles.driverInfo}>
            <View style={styles.avatarWrapper}>
              <View style={styles.avatarSmall}>
                <Ionicons name="person" size={20} color={COLORS.primary} />
              </View>
              {isOnline && (
                <View style={styles.onlineDotIndicatorOuter}>
                  <View style={styles.onlineDotIndicatorInner} />
                </View>
              )}
            </View>
            <View>
              <Text allowFontScaling={false} style={styles.driverName}>
                {driverData?.name || user?.first_name || t('dashboard.driver')}
              </Text>
              <Text allowFontScaling={false} style={styles.vehicleInfo}>
                {driverData?.vehicle_make || t('dashboard.vehicle')} {driverData?.vehicle_model || t('dashboard.info')} • <Text allowFontScaling={false} style={styles.plate}>{driverData?.license_plate || t('dashboard.plate')}</Text>
              </Text>
            </View>
          </View>
          <View style={styles.badgesRow}>
            {isSurgeActive && (
              <View style={styles.surgeBadge}>
                <Ionicons name="flash" size={11} color="#fff" />
                <Text allowFontScaling={false} style={styles.surgeBadgeText}>
                  {surgeMultiplier!.toFixed(1)}×
                </Text>
              </View>
            )}
            <View style={[styles.onlineBadge, isOnline ? styles.onlineBadgeActive : styles.onlineBadgeInactive]}>
              <Text allowFontScaling={false} style={[styles.onlineBadgeText, isOnline ? styles.onlineBadgeTextActive : styles.onlineBadgeTextInactive]}>
                {isOnline ? t('dashboard.statusOnline') : t('dashboard.statusOffline')}
              </Text>
            </View>
          </View>
        </View>
      </SafeBlurView>
    </View>
  );
};

const styles = StyleSheet.create({
  topBarContainer: {
    position: 'absolute',
    left: 16,
    right: 16,
    zIndex: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 16,
    elevation: 8,
  },
  blurContainer: {
    borderRadius: 24,
    overflow: 'hidden',
    backgroundColor: COLORS.overlay,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.8)',
  },
  topBarInner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 10,
    paddingRight: 16,
  },
  driverInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  avatarWrapper: {
    position: 'relative',
  },
  avatarSmall: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255, 59, 48, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  onlineDotIndicatorOuter: {
    position: 'absolute',
    bottom: -2,
    right: -2,
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: '#fff',
    justifyContent: 'center',
    alignItems: 'center',
  },
  onlineDotIndicatorInner: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: COLORS.success,
  },
  driverName: {
    color: COLORS.text,
    fontSize: 16,
    fontWeight: '800',
    letterSpacing: -0.3,
  },
  vehicleInfo: {
    color: COLORS.textDim,
    fontSize: 12,
    fontWeight: '500',
    marginTop: 1,
  },
  plate: {
    fontWeight: '700',
    color: COLORS.text,
  },
  onlineBadge: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    borderWidth: 1.5,
  },
  onlineBadgeActive: {
    backgroundColor: 'rgba(0, 230, 118, 0.12)',
    borderColor: 'rgba(0, 230, 118, 0.3)',
  },
  onlineBadgeInactive: {
    backgroundColor: 'rgba(0, 0, 0, 0.04)',
    borderColor: 'rgba(0,0,0,0.08)',
  },
  onlineBadgeText: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  onlineBadgeTextActive: {
    color: COLORS.success,
  },
  onlineBadgeTextInactive: {
    color: COLORS.textDim,
  },
  connectionBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    paddingVertical: 5,
    paddingHorizontal: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  bannerReconnecting: {
    backgroundColor: 'rgba(254, 243, 199, 0.9)',
    borderBottomColor: 'rgba(217, 119, 6, 0.25)',
  },
  bannerDisconnected: {
    backgroundColor: 'rgba(254, 226, 226, 0.9)',
    borderBottomColor: 'rgba(239, 68, 68, 0.25)',
  },
  bannerText: {
    fontSize: 12,
    fontWeight: '600',
  },
  bannerTextReconnecting: {
    color: '#92400E',
  },
  bannerTextDisconnected: {
    color: '#991B1B',
  },
  badgesRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  surgeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 20,
    backgroundColor: '#F59E0B',
  },
  surgeBadgeText: {
    fontSize: 11,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: 0.3,
  },
});

export default DriverTopBar;
