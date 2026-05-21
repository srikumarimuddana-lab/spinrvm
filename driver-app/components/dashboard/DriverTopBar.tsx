import React, { useMemo, useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Platform, StatusBar, Animated } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../store/languageStore';
import { useNetworkStatus } from '@shared/components/OfflineBanner';
import type { ConnectionState } from '../../hooks/useDriverDashboard';

const SafeBlurView: React.FC<{ intensity?: number; tint?: string; style?: any; children?: React.ReactNode }> = ({ style, children }) => (
  <View style={style}>{children}</View>
);

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
  wsLatency?: number | null;
}

export const DriverTopBar: React.FC<DriverTopBarProps> = ({
  driverData,
  user,
  isOnline,
  connectionState,
  surgeMultiplier,
  wsLatency,
}) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const isSurgeActive = isOnline && typeof surgeMultiplier === 'number' && surgeMultiplier > 1.0;
  const { t } = useLanguageStore();
  const insets = useSafeAreaInsets();
  const statusBarHeight = StatusBar.currentHeight ?? 0;
  const { isOffline: noInternet } = useNetworkStatus();

  // Priority-based unified banner
  type BannerLevel = 'none' | 'no_internet' | 'reconnecting' | 'disconnected';
  let bannerLevel: BannerLevel = 'none';
  if (isOnline) {
    if (noInternet) bannerLevel = 'no_internet';
    else if (connectionState === 'reconnecting') bannerLevel = 'reconnecting';
    else if (connectionState === 'disconnected') bannerLevel = 'disconnected';
  }
  const showBanner = bannerLevel !== 'none';
  const bannerIsRed = bannerLevel === 'no_internet' || bannerLevel === 'disconnected';

  // Connection quality: 3 bars = good (<200ms), 2 = fair (200-500ms), 1 = poor (>500ms)
  const signalBars = useMemo(() => {
    if (!isOnline || connectionState !== 'connected' || wsLatency == null) return 0;
    if (wsLatency < 200) return 3;
    if (wsLatency < 500) return 2;
    return 1;
  }, [isOnline, connectionState, wsLatency]);

  // Banner height animation
  const bannerHeight = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(bannerHeight, {
      toValue: showBanner ? 28 : 0,
      duration: 200,
      useNativeDriver: false,
    }).start();
  }, [showBanner]);

  const bannerText = bannerLevel === 'no_internet'
    ? t('dashboard.noInternet')
    : bannerLevel === 'reconnecting'
      ? t('dashboard.reconnecting')
      : t('dashboard.connectionLost');

  return (
    <View style={[styles.topBarContainer, { top: Math.max(insets.top, statusBarHeight) }]}>
      <SafeBlurView intensity={Platform.OS === 'ios' ? 60 : 100} tint="light" style={styles.blurContainer}>
        <Animated.View
          style={[
            styles.connectionBanner,
            bannerIsRed ? styles.bannerDisconnected : styles.bannerReconnecting,
            { height: bannerHeight, overflow: 'hidden' as const },
          ]}
          accessibilityRole="text"
          accessibilityLabel={bannerText}
          accessibilityLiveRegion="polite"
        >
          <Ionicons
            name={bannerLevel === 'no_internet' ? 'cloud-offline-outline' : 'wifi-outline'}
            size={13}
            color={bannerIsRed ? '#991B1B' : '#92400E'}
          />
          <Text allowFontScaling={false} style={[styles.bannerText, bannerIsRed ? styles.bannerTextDisconnected : styles.bannerTextReconnecting]}>
            {bannerText}
          </Text>
        </Animated.View>
        <View style={styles.topBarInner}>
          <View style={styles.driverInfo}>
            <View style={styles.avatarWrapper}>
              <View style={styles.avatarSmall}>
                <Ionicons name="person" size={20} color={colors.primary} />
              </View>
              {isOnline && (
                <View style={styles.onlineDotIndicatorOuter}>
                  <View style={styles.onlineDotIndicatorInner} />
                </View>
              )}
            </View>
            <View>
              <Text allowFontScaling={false} style={styles.driverName} accessibilityRole="header">
                {driverData?.name || user?.first_name || t('dashboard.driver')}
              </Text>
              <Text allowFontScaling={false} style={styles.vehicleInfo}>
                {driverData?.vehicle_make || t('dashboard.vehicle')} {driverData?.vehicle_model || t('dashboard.info')} • <Text allowFontScaling={false} style={styles.plate}>{driverData?.license_plate || t('dashboard.plate')}</Text>
              </Text>
            </View>
          </View>
          <View style={styles.badgesRow}>
            {signalBars > 0 && (
              <View style={styles.signalContainer}>
                {[1, 2, 3].map(bar => (
                  <View
                    key={bar}
                    style={[
                      styles.signalBar,
                      { height: 6 + bar * 4 },
                      bar <= signalBars
                        ? { backgroundColor: signalBars >= 3 ? colors.success : signalBars >= 2 ? colors.warning : colors.error }
                        : { backgroundColor: colors.border },
                    ]}
                  />
                ))}
              </View>
            )}
            {isSurgeActive && (
              <View style={styles.surgeBadge} accessibilityRole="text" accessibilityLabel={`Surge ${surgeMultiplier!.toFixed(1)} times`}>
                <Ionicons name="flash" size={11} color="#fff" />
                <Text allowFontScaling={false} style={styles.surgeBadgeText}>
                  {surgeMultiplier!.toFixed(1)}×
                </Text>
              </View>
            )}
            <View style={[styles.onlineBadge, isOnline ? styles.onlineBadgeActive : styles.onlineBadgeInactive]} accessibilityRole="text" accessibilityLabel={isOnline ? t('dashboard.statusOnline') : t('dashboard.statusOffline')}>
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

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
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
      backgroundColor: colors.overlay,
      borderWidth: 1,
      borderColor: colors.border,
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
      backgroundColor: `${colors.primary}1A`,
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
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
    },
    onlineDotIndicatorInner: {
      width: 10,
      height: 10,
      borderRadius: 5,
      backgroundColor: colors.success,
    },
    driverName: {
      color: colors.text,
      fontSize: 16,
      fontWeight: '800',
      letterSpacing: -0.3,
    },
    vehicleInfo: {
      color: colors.textDim,
      fontSize: 12,
      fontWeight: '500',
      marginTop: 1,
    },
    plate: {
      fontWeight: '700',
      color: colors.text,
    },
    onlineBadge: {
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 20,
      borderWidth: 1.5,
    },
    onlineBadgeActive: {
      backgroundColor: colors.successBg,
      borderColor: colors.success,
    },
    onlineBadgeInactive: {
      backgroundColor: colors.surfaceLight,
      borderColor: colors.border,
    },
    onlineBadgeText: {
      fontSize: 11,
      fontWeight: '800',
      letterSpacing: 0.5,
    },
    onlineBadgeTextActive: {
      color: colors.success,
    },
    onlineBadgeTextInactive: {
      color: colors.textDim,
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
      backgroundColor: colors.warningBg,
      borderBottomColor: colors.warning,
    },
    bannerDisconnected: {
      backgroundColor: colors.dangerBg,
      borderBottomColor: colors.error,
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
      backgroundColor: colors.warning,
    },
    surgeBadgeText: {
      fontSize: 11,
      fontWeight: '800',
      color: '#fff',
      letterSpacing: 0.3,
    },
    signalContainer: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: 2,
      paddingHorizontal: 4,
    },
    signalBar: {
      width: 4,
      borderRadius: 1,
    },
  });
}

export default DriverTopBar;
