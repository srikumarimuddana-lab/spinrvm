import React, { useRef, useEffect, useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Animated, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAuthStore } from '@shared/store/authStore';
import { useLanguageStore } from '../../store/languageStore';

const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
// Guarded native-module require — must stay runtime require(), not a
// static import, so it only executes when notifications are usable (not
// Expo Go/web); a static import would throw at module load in those
// environments where the native module isn't linked.
const Notifications: typeof import('expo-notifications') | null =
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  (!isExpoGo && Platform.OS !== 'web') ? require('expo-notifications') : null;

interface IdlePanelProps {
  isOnline: boolean;
  onToggleOnline: () => void;
  pulseAnim: any;
}

export const DriverIdlePanel: React.FC<IdlePanelProps> = ({
  isOnline,
  onToggleOnline,
}) => {
  const { colors } = useTheme();
  const { t } = useLanguageStore();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const insets = useSafeAreaInsets();
  const driver = useAuthStore(s => s.driver);

  // Only drivers with status='active' can go online. The button stays
  // pressable in every state so toggleOnline can surface a specific toast
  // for each ineligible case (missing vehicle, documents pending, etc.).
  const driverStatus = (driver as any)?.status || 'pending';
  const canGoOnline = driverStatus === 'active';

  // Vehicle shown as persistent text — reads from the auth store, which is
  // hydrated from cache on cold start / offline, so it stays visible instead
  // of blanking. Falls back to the make/model line when color/plate missing.
  const vehicleLine = driver
    ? [driver.vehicle_color, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(' ')
    : '';

  // react-hooks/refs ("Cannot access refs during render"): standard
  // Animated.Value driver idiom — mutated only via Animated.timing()/
  // .stopAnimation()/.setValue() inside the effect below, read only in the
  // JSX transform binding further down. Verified safe — grepped this file
  // for `.current =`: zero matches, goAnim is never reassigned after
  // creation.
  // eslint-disable-next-line react-hooks/refs
  const goAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (canGoOnline && !isOnline) {
      try {
        Animated.loop(
          Animated.sequence([
            Animated.timing(goAnim, { toValue: 1.05, duration: 1000, useNativeDriver: true }),
            Animated.timing(goAnim, { toValue: 1, duration: 1000, useNativeDriver: true })
          ])
        ).start();
      } catch (e) {
        console.log('[Animated] pulse loop failed:', e);
      }
    } else {
      try { goAnim.stopAnimation(); } catch {}
      goAnim.setValue(1);
    }
    return () => {
      try { goAnim.stopAnimation(); } catch {}
    };
    // goAnim is intentionally excluded: it's the same verified-safe stable
    // useRef(...).current animation-driver idiom noted above (never
    // reassigned after creation). Adding a ref-derived value to a
    // dependency array is itself a render-time ref read under
    // react-hooks/refs (confirmed in otp.tsx's dotAnims — same fix applied
    // here). goAnim's identity never changes across this component's
    // lifetime, so excluding it changes nothing about when this effect
    // fires.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canGoOnline, isOnline]);

  // Welcome / Onboarding Notification Check — fires once when documents are
  // first required so a new driver knows to upload them.
  useEffect(() => {
    const onboardingStatus = (driver as any)?.onboarding_status;
    if (onboardingStatus === 'documents_required') {
      const triggerWelcomeNotif = async () => {
        try {
          const hasSent = await AsyncStorage.getItem('@notif_welcome_docs');
          if (!hasSent && Notifications) {
            await Notifications.scheduleNotificationAsync({
              content: {
                title: 'Welcome to Spinr!',
                body: 'Please upload your required documents to get approved and start driving.',
                sound: true,
              },
              trigger: null, // Send immediately
            });
            await AsyncStorage.setItem('@notif_welcome_docs', 'true');
          }
        } catch (e) {
          console.warn('Failed to schedule welcome notification:', e);
        }
      };
      triggerWelcomeNotif();
    }
  }, [driver]);

  return (
    <View style={[styles.idlePanelContainer, { paddingBottom: insets.bottom + 16 }]} pointerEvents="box-none">

      {/* HUD Info Area */}
      <View style={styles.hudArea} pointerEvents="box-none">

        {/* Vehicle details as persistent text — stays visible offline */}
        {vehicleLine ? (
          <View style={styles.vehiclePill}>
            <Ionicons name="car-outline" size={14} color={colors.textSecondary} />
            <Text style={styles.vehiclePillText} numberOfLines={1}>{vehicleLine}</Text>
            {driver?.license_plate ? (
              <View style={styles.plateBadge}>
                <Text style={styles.plateBadgeText}>{driver.license_plate.toUpperCase()}</Text>
              </View>
            ) : null}
          </View>
        ) : null}


        {/* Status Indicator Pill */}
        <View style={styles.statusPillWrapper}>
            {isOnline ? (
                <View style={styles.statusPillOnline} accessibilityRole="text" accessibilityLabel={t('dashboard.youreOnline')}>
                    <View style={styles.onlineDot} />
                    <Text allowFontScaling={false} style={styles.statusPillTextOnline}>{t('dashboard.youreOnline')}</Text>
                </View>
            ) : (
                <View style={styles.statusPillOffline} accessibilityRole="text" accessibilityLabel={t('home.offline')}>
                    <View style={styles.offlineDot} />
                    <Text allowFontScaling={false} style={styles.statusPillTextOffline}>{t('home.offline')}</Text>
                </View>
            )}
        </View>

      </View>

      {/* Floating GO Button */}
      <View style={styles.goButtonArea} pointerEvents="box-none">
        
        <TouchableOpacity
          activeOpacity={0.9}
          onPress={onToggleOnline}
          style={styles.goButtonOuterContainer}
          accessibilityRole="button"
          accessibilityLabel={isOnline ? t('home.stop') : t('home.go')}
          accessibilityState={{ disabled: !canGoOnline }}
        >
          <Animated.View style={[
            styles.goButtonShadow, 
            !canGoOnline && styles.goButtonShadowDisabled,
            isOnline && styles.goButtonShadowOnline,
            { transform: [{ scale: goAnim }] }
          ]}>
            <LinearGradient
              colors={
                !canGoOnline
                  ? ['#E5E7EB', '#D1D5DB']
                  : (isOnline ? ['#059669', '#10B981'] : [colors.primary, colors.primaryDark])
              }
              style={styles.goButtonInner}
            >
              <Text allowFontScaling={false} style={[styles.goButtonText, !canGoOnline && styles.goButtonTextDisabled]}>
                {isOnline ? t('home.stop') : t('home.go')}
              </Text>
            </LinearGradient>
          </Animated.View>
        </TouchableOpacity>
      </View>

    </View>
  );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    idlePanelContainer: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      alignItems: 'center',
      justifyContent: 'flex-end',
    },
    hudArea: {
      width: '100%',
      alignItems: 'center',
      paddingHorizontal: 20,
      marginBottom: 20,
    },
    vehiclePill: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      borderRadius: 20,
      paddingHorizontal: 14,
      paddingVertical: 8,
      marginBottom: 10,
      gap: 6,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.08,
      shadowRadius: 6,
      elevation: 3,
      borderWidth: 1,
      borderColor: colors.border,
      maxWidth: '90%',
    },
    vehiclePillText: {
      fontSize: 12,
      fontWeight: '600',
      color: colors.textSecondary,
      flexShrink: 1,
    },
    plateBadge: {
      backgroundColor: colors.primary + '20',
      borderRadius: 6,
      paddingHorizontal: 7,
      paddingVertical: 2,
    },
    plateBadgeText: {
      fontSize: 11,
      fontWeight: '700',
      color: colors.primary,
      letterSpacing: 0.5,
    },

    statusPillWrapper: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    statusPillOnline: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.successBg,
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: colors.success,
      gap: 6,
    },
    statusPillOffline: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: colors.border,
      gap: 6,
    },
    onlineDot: {
      width: 8,
      height: 8,
      borderRadius: 4,
      backgroundColor: colors.success,
    },
    offlineDot: {
      width: 8,
      height: 8,
      borderRadius: 4,
      backgroundColor: colors.textDim,
    },
    statusPillTextOnline: {
      fontSize: 13,
      fontWeight: '700',
      color: colors.success,
    },
    statusPillTextOffline: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.textSecondary,
    },

    goButtonArea: {
      alignItems: 'center',
    },
    goButtonOuterContainer: {
      width: 100,
      height: 100,
      justifyContent: 'center',
      alignItems: 'center',
    },
    goButtonShadow: {
      width: 84,
      height: 84,
      borderRadius: 42,
      backgroundColor: colors.surface,
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 8 },
      shadowOpacity: 0.4,
      shadowRadius: 16,
      elevation: 10,
      justifyContent: 'center',
      alignItems: 'center',
      padding: 6,
    },
    goButtonShadowOnline: {
      shadowColor: colors.success,
    },
    goButtonShadowDisabled: {
      shadowColor: '#000',
      shadowOpacity: 0.15,
    },
    goButtonInner: {
      width: '100%',
      height: '100%',
      borderRadius: 36,
      justifyContent: 'center',
      alignItems: 'center',
    },
    goButtonText: {
      fontSize: 24,
      fontWeight: '900',
      color: '#fff',
      letterSpacing: 1,
    },
    goButtonTextDisabled: {
      color: colors.textDim,
    },
  });
}

export default DriverIdlePanel;
