import React, { useRef, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Animated, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import { BlurView } from 'expo-blur';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import SpinrConfig from '@shared/config/spinr.config';
import { useAuthStore, DriverOnboardingStatus } from '@shared/store/authStore';
import { useLanguageStore } from '../../store/languageStore';

// expo-notifications push APIs were removed from Expo Go in SDK 53.
// Lazy-require so the module doesn't crash on import in Expo Go.
const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
const Notifications: typeof import('expo-notifications') | null =
  (!isExpoGo && Platform.OS !== 'web') ? require('expo-notifications') : null;

const COLORS = {
  primary: SpinrConfig.theme.colors.background,
  accent: SpinrConfig.theme.colors.primary,
  accentDark: SpinrConfig.theme.colors.primaryDark,
  success: SpinrConfig.theme.colors.success,
  surface: SpinrConfig.theme.colors.surface,
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
  orange: '#FF9500',
};

interface DriverData {
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
  const { t } = useLanguageStore();
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
      Animated.loop(
        Animated.sequence([
          Animated.timing(goAnim, { toValue: 1.05, duration: 1000, useNativeDriver: true }),
          Animated.timing(goAnim, { toValue: 1, duration: 1000, useNativeDriver: true })
        ])
      ).start();
    } else {
      goAnim.setValue(1);
    }
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
    <View style={[styles.idlePanelContainer, { paddingBottom: Math.max(insets.bottom, 20) }]} pointerEvents="box-none">
      
      {/* HUD Info Area */}
      <View style={styles.hudArea} pointerEvents="box-none">
        
        {/* Banner (if any) */}
        {banner && (
          <View style={styles.actionCardContainer}>
            <View style={styles.actionCard}>
              <View style={[styles.actionCardIcon, banner.tone === 'danger' && styles.actionCardIconDanger, banner.tone === 'warning' && styles.actionCardIconWarning]}>
                <Ionicons 
                  name={banner.icon as keyof typeof Ionicons.glyphMap} 
                  size={24} 
                  color={banner.tone === 'danger' ? '#DC2626' : banner.tone === 'warning' ? '#D97706' : COLORS.accent} 
                />
              </View>
              <View style={styles.actionCardContent}>
                <Text style={styles.actionCardTitle}>{banner.title}</Text>
                <Text style={styles.actionCardSubtitle}>{banner.subtitle}</Text>
              </View>
            </View>
            <TouchableOpacity
              style={[styles.actionCardButton, banner.tone === 'danger' && styles.actionCardButtonDanger, banner.tone === 'warning' && styles.actionCardButtonWarning]}
              onPress={() => {
                if (banner.target.startsWith('mailto:') || banner.target.startsWith('http')) {
                  require('react-native').Linking.openURL(banner.target);
                } else {
                  router.push(banner.target as any);
                }
              }}
              activeOpacity={0.85}
            >
              <Text style={styles.actionCardButtonText}>{banner.button}</Text>
              <Ionicons name="arrow-forward" size={16} color="#FFF" />
            </TouchableOpacity>
          </View>
        )}


        {/* Status Indicator Pill */}
        <View style={styles.statusPillWrapper}>
            {isOnline ? (
                <View style={styles.statusPillOnline}>
                    <Ionicons name="pulse" size={14} color="#059669" />
                    <Text style={styles.statusPillTextOnline}>{t('dashboard.findingRides')}</Text>
                </View>
            ) : (
                <View style={styles.statusPillOffline}>
                    <View style={styles.offlineDot} />
                    <Text style={styles.statusPillTextOffline}>{t('home.offline')}</Text>
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
                  : (isOnline ? ['#059669', '#10B981'] : [COLORS.accent, COLORS.accentDark])
              }
              style={styles.goButtonInner}
            >
              <Text style={[styles.goButtonText, !canGoOnline && styles.goButtonTextDisabled]}>
                {isOnline ? t('home.stop') : t('home.go')}
              </Text>
            </LinearGradient>
          </Animated.View>
        </TouchableOpacity>
      </View>

    </View>
  );
};

const styles = StyleSheet.create({
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
  // Action Card
  actionCardContainer: {
    width: '100%',
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 20,
    elevation: 8,
  },
  actionCard: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  actionCardIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(0, 212, 170, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 16,
  },
  actionCardIconWarning: {
    backgroundColor: 'rgba(217, 119, 6, 0.1)',
  },
  actionCardIconDanger: {
    backgroundColor: 'rgba(220, 38, 38, 0.1)',
  },
  actionCardContent: {
    flex: 1,
  },
  actionCardTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: COLORS.text,
    marginBottom: 4,
  },
  actionCardSubtitle: {
    fontSize: 13,
    color: COLORS.textDim,
    lineHeight: 18,
  },
  actionCardButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.accent,
    borderRadius: 14,
    paddingVertical: 14,
    gap: 8,
  },
  actionCardButtonWarning: {
    backgroundColor: '#D97706',
  },
  actionCardButtonDanger: {
    backgroundColor: '#DC2626',
  },
  actionCardButtonText: {
    color: '#FFF',
    fontSize: 15,
    fontWeight: '700',
  },

  // Status Pill
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
      backgroundColor: '#ECFDF5',
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: '#A7F3D0',
      gap: 6,
  },
  statusPillOffline: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: '#F3F4F6',
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: '#E5E7EB',
      gap: 6,
  },
  offlineDot: {
      width: 8,
      height: 8,
      borderRadius: 4,
      backgroundColor: '#9CA3AF',
  },
  statusPillTextOnline: {
      fontSize: 13,
      fontWeight: '700',
      color: '#065F46',
  },
  statusPillTextOffline: {
      fontSize: 13,
      fontWeight: '600',
      color: '#6B7280',
  },
  // GO Button Area
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
    backgroundColor: '#fff',
    shadowColor: COLORS.accent,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.4,
    shadowRadius: 16,
    elevation: 10,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 6,
  },
  goButtonShadowOnline: {
    shadowColor: '#10B981',
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
    color: '#9CA3AF',
  },
});

export default DriverIdlePanel;
