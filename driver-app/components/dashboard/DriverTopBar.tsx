import React, { useMemo, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, StatusBar, Animated, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../store/languageStore';
import { useNetworkStatus } from '@shared/components/OfflineBanner';
import type { ConnectionState } from '../../hooks/useDriverDashboard';
import type { EarningsSummary } from '../../store/driverStore';

interface DriverTopBarProps {
  driverData?: unknown;
  user?: unknown;
  isOnline: boolean;
  connectionState?: ConnectionState;
  surgeMultiplier?: number;
  wsLatency?: number | null;
  earnings?: EarningsSummary | null;
  unreadNotifCount?: number;
}

export const DriverTopBar: React.FC<DriverTopBarProps> = ({
  isOnline,
  connectionState,
  surgeMultiplier,
  earnings,
  unreadNotifCount = 0,
}) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const isSurgeActive = isOnline && typeof surgeMultiplier === 'number' && surgeMultiplier > 1.0;
  const { t } = useLanguageStore();
  const insets = useSafeAreaInsets();
  const statusBarHeight = StatusBar.currentHeight ?? 0;
  const { isOffline: noInternet } = useNetworkStatus();
  const router = useRouter();

  type BannerLevel = 'none' | 'no_internet' | 'reconnecting' | 'disconnected';
  let bannerLevel: BannerLevel = 'none';
  if (isOnline) {
    if (noInternet) bannerLevel = 'no_internet';
    else if (connectionState === 'reconnecting') bannerLevel = 'reconnecting';
    else if (connectionState === 'disconnected') bannerLevel = 'disconnected';
  }
  const showBanner = bannerLevel !== 'none';
  const bannerIsRed = bannerLevel === 'no_internet' || bannerLevel === 'disconnected';

  // react-hooks/refs ("Cannot access refs during render"): standard
  // Animated.Value driver idiom — mutated only via Animated.timing() inside
  // the effect below, read only in the JSX height binding further down.
  // Verified safe — grepped this file for `.current =`: zero matches,
  // bannerHeight is never reassigned after creation.
  // eslint-disable-next-line react-hooks/refs
  const bannerHeight = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(bannerHeight, {
      toValue: showBanner ? 28 : 0,
      duration: 200,
      useNativeDriver: false,
    }).start();
    // bannerHeight intentionally excluded — same verified-safe stable
    // useRef(...).current animation-driver idiom noted above; adding a
    // ref-derived value to a dependency array is itself a render-time ref
    // read under react-hooks/refs (confirmed in otp.tsx's dotAnims). Its
    // identity never changes, so excluding it changes nothing about firing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showBanner]);

  const bannerText = bannerLevel === 'no_internet'
    ? t('dashboard.noInternet')
    : bannerLevel === 'reconnecting'
      ? t('dashboard.reconnecting')
      : t('dashboard.connectionLost');

  const todayEarnings = earnings?.total_earnings ?? '0.00';
  const todayTrips = earnings?.total_rides ?? 0;

  return (
    <View style={[styles.topBarContainer, { top: Math.max(insets.top, statusBarHeight) }]}>
      <View style={styles.topRow}>
        <View style={styles.pillRow}>
          <TouchableOpacity
            style={styles.earningsPill}
            onPress={() => router.push('/driver/activity' as never)}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityLabel={`Today's earnings $${todayEarnings}, ${todayTrips} trips. Tap for details.`}
          >
            <Text allowFontScaling={false} style={styles.earningsAmount}>${todayEarnings}</Text>
            <View style={styles.earningsDivider} />
            <Text allowFontScaling={false} style={styles.earningsTrips}>
              {todayTrips} {todayTrips === 1 ? 'trip' : 'trips'}
            </Text>
          </TouchableOpacity>
          {isSurgeActive && (
            <View style={styles.surgeBadge} accessibilityRole="text" accessibilityLabel={`Surge ${surgeMultiplier!.toFixed(1)} times`}>
              <Ionicons name="flash" size={11} color="#fff" />
              <Text allowFontScaling={false} style={styles.surgeBadgeText}>
                {surgeMultiplier!.toFixed(1)}×
              </Text>
            </View>
          )}
        </View>
        <TouchableOpacity
          style={styles.notificationButton}
          onPress={() => router.push('/driver/notifications' as never)}
          activeOpacity={0.7}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          accessibilityRole="button"
          accessibilityLabel={unreadNotifCount > 0 ? `Notifications, ${unreadNotifCount} unread` : 'Notifications'}
        >
          <Ionicons name="notifications-outline" size={22} color={colors.text} />
          {unreadNotifCount > 0 && (
            <View style={styles.notifBadge}>
              <Text allowFontScaling={false} style={styles.notifBadgeText}>
                {unreadNotifCount > 9 ? '9+' : unreadNotifCount}
              </Text>
            </View>
          )}
        </TouchableOpacity>
      </View>
      {showBanner && (
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
      )}
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
    },
    connectionBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      alignSelf: 'flex-start',
      gap: 5,
      paddingVertical: 5,
      paddingHorizontal: 12,
      borderRadius: 20,
      marginTop: 8,
    },
    bannerReconnecting: {
      backgroundColor: colors.warningBg,
      borderWidth: 1,
      borderColor: colors.warning,
    },
    bannerDisconnected: {
      backgroundColor: colors.dangerBg,
      borderWidth: 1,
      borderColor: colors.error,
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
    topRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
    },
    pillRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    notificationButton: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      position: 'relative',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    notifBadge: {
      position: 'absolute',
      top: 6,
      right: 6,
      minWidth: 16,
      height: 16,
      borderRadius: 8,
      backgroundColor: '#EF4444',
      justifyContent: 'center',
      alignItems: 'center',
      paddingHorizontal: 3,
    },
    notifBadgeText: {
      color: '#FFF',
      fontSize: 10,
      fontWeight: '700',
      lineHeight: 14,
    },
    earningsPill: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      borderRadius: 24,
      paddingHorizontal: 16,
      paddingVertical: 10,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.1,
      shadowRadius: 8,
      elevation: 4,
      borderWidth: 1,
      borderColor: colors.border,
    },
    earningsAmount: {
      fontSize: 16,
      fontWeight: '800',
      color: colors.text,
      letterSpacing: -0.3,
    },
    earningsDivider: {
      width: 1,
      height: 16,
      backgroundColor: colors.border,
      marginHorizontal: 10,
    },
    earningsTrips: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.textDim,
    },
    surgeBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 3,
      paddingHorizontal: 10,
      paddingVertical: 8,
      borderRadius: 20,
      backgroundColor: colors.warning,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.1,
      shadowRadius: 8,
      elevation: 4,
    },
    surgeBadgeText: {
      fontSize: 12,
      fontWeight: '800',
      color: '#fff',
      letterSpacing: 0.3,
    },
  });
}

export default DriverTopBar;
