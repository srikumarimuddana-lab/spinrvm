import React, { useRef, useEffect, useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Animated, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAuthStore, DriverOnboardingStatus } from '@shared/store/authStore';
import { useLanguageStore } from '../../store/languageStore';

const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
const Notifications: typeof import('expo-notifications') | null =
  (!isExpoGo && Platform.OS !== 'web') ? require('expo-notifications') : null;

interface DriverData {
  acceptance_rate?: string;
  total_rides?: string;
  is_verified?: boolean;
}

interface Earnings {
  total_earnings?: string; // MoneyString
}

interface IdlePanelProps {
  isOnline: boolean;
  driverData?: DriverData;
  earnings?: Earnings;
  onToggleOnline: () => void;
  pulseAnim: any;
}

function getStateBanners(t: (key: string) => string, driverData?: any) {
  return {
    profile_incomplete: {
      title: t('dashboard.completeProfile'),
      subtitle: t('dashboard.profileIncompleteMsg'),
      button: t('dashboard.finishProfile'),
      icon: 'person', tone: 'info', target: '/profile-setup',
    },
    vehicle_required: {
      title: t('dashboard.addVehicle'),
      subtitle: t('dashboard.profileIncompleteMsg'),
      button: t('dashboard.addVehicleDetails'),
      icon: 'car', tone: 'info', target: '/vehicle-info',
    },
    documents_required: {
      title: t('dashboard.actionRequired'),
      subtitle: t('dashboard.uploadDocs'),
      button: t('dashboard.uploadDocs'),
      icon: 'document-text', tone: 'warning', target: '/documents',
    },
    documents_rejected: {
      title: t('dashboard.docsRejected'),
      subtitle: t('dashboard.fixDocuments'),
      button: t('dashboard.fixDocuments'),
      icon: 'alert-circle', tone: 'danger', target: '/documents',
    },
    documents_expired: {
      title: t('dashboard.docsExpired'),
      subtitle: t('dashboard.updateDocs'),
      button: t('dashboard.updateDocs'),
      icon: 'time', tone: 'warning', target: '/documents',
    },
    pending_review: {
      title: t('dashboard.underReview'),
      subtitle: t('dashboard.viewStatus'),
      button: t('dashboard.viewStatus'),
      icon: 'hourglass', tone: 'info', target: '/documents',
    },
    suspended: {
      title: t('dashboard.accountSuspended'),
      subtitle: driverData?.suspension_reason || driverData?.ban_reason || t('dashboard.contactSupport'),
      button: t('dashboard.contactSupport'),
      icon: 'ban', tone: 'danger', target: 'mailto:support@spinr.ca?subject=Account%20Suspended',
    },
  };
}

export const DriverIdlePanel: React.FC<IdlePanelProps> = ({
  isOnline,
  driverData,
  earnings,
  onToggleOnline,
}) => {
  const { colors } = useTheme();
  const { t } = useLanguageStore();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const STATE_BANNERS = getStateBanners(t, driverData);
  const insets = useSafeAreaInsets();
  const router = useRouter();
  let onboardingStatus = useAuthStore(s => s.user?.driver_onboarding_status ?? null);
  const driver = useAuthStore(s => s.driver);

  // Fallback: If backend returns null (e.g. role still 'rider'), infer status locally
  if (!onboardingStatus) {
    if (!driver) {
      // No driver row at all — they need to register a vehicle
      onboardingStatus = 'vehicle_required';
    } else if (!driver.vehicle_make) {
      onboardingStatus = 'vehicle_required';
    } else if (!driver.is_verified) {
      onboardingStatus = 'documents_required';
    }
  }

  const banner = onboardingStatus && onboardingStatus !== 'verified'
    ? STATE_BANNERS[onboardingStatus as keyof typeof STATE_BANNERS]
    : null;

  // Only drivers with status='active' can go online. The backend enforces
  // this too, but we disable the GO button client-side for better UX.
  const driverStatus = (driverData as any)?.status || 'pending';
  const canGoOnline = driverStatus === 'active';

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
  }, [canGoOnline, isOnline]);

  // Welcome / Onboarding Notification Check
  useEffect(() => {
    if (onboardingStatus === 'documents_required') {
      const triggerWelcomeNotif = async () => {
        try {
          const hasSent = await AsyncStorage.getItem('@notif_welcome_docs');
          if (!hasSent && Notifications) {
            await Notifications.scheduleNotificationAsync({
              content: {
                title: 'Welcome to Spinr! 🎉',
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
  }, [onboardingStatus]);

  return (
    <View style={[styles.idlePanelContainer, { paddingBottom: insets.bottom + 16 }]} pointerEvents="box-none">
      
      {/* HUD Info Area */}
      <View style={styles.hudArea} pointerEvents="box-none">
        
        {/* Onboarding banner */}
        {banner && (
          <TouchableOpacity
            style={[
              styles.actionBar,
              banner.tone === 'danger' && styles.actionBarDanger,
              banner.tone === 'warning' && styles.actionBarWarning,
            ]}
            onPress={() => {
              if (banner.target.startsWith('mailto:') || banner.target.startsWith('http')) {
                require('react-native').Linking.openURL(banner.target);
              } else {
                router.push(banner.target as any);
              }
            }}
            activeOpacity={0.85}
            accessibilityRole="button"
            accessibilityLabel={banner.button}
          >
            <Ionicons
              name={banner.icon as keyof typeof Ionicons.glyphMap}
              size={16}
              color={banner.tone === 'danger' ? colors.error : banner.tone === 'warning' ? '#92400E' : colors.primary}
            />
            <Text
              style={[
                styles.actionBarText,
                banner.tone === 'danger' && styles.actionBarTextDanger,
                banner.tone === 'warning' && styles.actionBarTextWarning,
              ]}
              numberOfLines={1}
            >
              {banner.title}
            </Text>
            <Text style={styles.actionBarCta}>{banner.button}</Text>
            <Ionicons name="chevron-forward" size={14} color={colors.primary} />
          </TouchableOpacity>
        )}


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
          disabled={!canGoOnline}
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
    actionBar: {
      flexDirection: 'row',
      alignItems: 'center',
      width: '100%',
      backgroundColor: colors.surface,
      borderRadius: 16,
      paddingHorizontal: 14,
      paddingVertical: 12,
      marginBottom: 12,
      gap: 8,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.1,
      shadowRadius: 10,
      elevation: 5,
      borderWidth: 1,
      borderColor: colors.border,
    },
    actionBarWarning: {
      backgroundColor: colors.warningBg,
      borderColor: colors.warning,
    },
    actionBarDanger: {
      backgroundColor: colors.dangerBg,
      borderColor: colors.error,
    },
    actionBarText: {
      flex: 1,
      fontSize: 13,
      fontWeight: '700',
      color: colors.text,
    },
    actionBarTextWarning: {
      color: '#92400E',
    },
    actionBarTextDanger: {
      color: '#991B1B',
    },
    actionBarCta: {
      fontSize: 12,
      fontWeight: '700',
      color: colors.primary,
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
