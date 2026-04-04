import React, { useRef, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Animated, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import { BlurView } from 'expo-blur';
import SpinrConfig from '@shared/config/spinr.config';
import { useAuthStore, DriverOnboardingStatus } from '@shared/store/authStore';

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

const STATE_BANNERS = {
  profile_incomplete: { title: 'Finish profile', icon: 'person', tone: 'info', target: '/profile-setup' },
  vehicle_required: { title: 'Add vehicle', icon: 'car', tone: 'info', target: '/become-driver' },
  documents_required: { title: 'Upload docs', icon: 'document-text', tone: 'warning', target: '/documents' },
  documents_rejected: { title: 'Docs rejected', icon: 'alert-circle', tone: 'danger', target: '/documents' },
  documents_expired: { title: 'Docs expired', icon: 'time', tone: 'warning', target: '/documents' },
  pending_review: { title: 'Under review', icon: 'hourglass', tone: 'info', target: '/documents' },
  suspended: { title: 'Suspended', icon: 'ban', tone: 'danger', target: '/driver/settings' },
};

export const DriverIdlePanel: React.FC<IdlePanelProps> = ({
  isOnline,
  driverData,
  earnings,
  onToggleOnline,
}) => {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const onboardingStatus = useAuthStore(s => s.user?.driver_onboarding_status ?? null);
  
  const banner = onboardingStatus && onboardingStatus !== 'verified' 
    ? STATE_BANNERS[onboardingStatus as keyof typeof STATE_BANNERS] 
    : null;
    
  const canGoOnline = !banner && !!driverData?.is_verified;

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

  return (
    <View style={[styles.idlePanelContainer, { paddingBottom: Math.max(insets.bottom, 20) }]} pointerEvents="box-none">
      
      {/* HUD Info Area */}
      <View style={styles.hudArea} pointerEvents="box-none">
        
        {/* Banner (if any) */}
        {banner && (
          <TouchableOpacity
            style={[styles.hudBannerWrapper, banner.tone === 'danger' && styles.hudBannerDanger, banner.tone === 'warning' && styles.hudBannerWarning]}
            onPress={() => router.push(banner.target as any)}
            activeOpacity={0.8}
          >
            <BlurView intensity={80} tint="light" style={styles.hudBannerBlur}>
              <Ionicons 
                name={banner.icon as keyof typeof Ionicons.glyphMap} 
                size={20} 
                color={banner.tone === 'danger' ? '#DC2626' : banner.tone === 'warning' ? '#D97706' : COLORS.accent} 
              />
              <Text style={styles.hudBannerText}>{banner.title}</Text>
              <Ionicons name="chevron-forward" size={16} color={COLORS.textDim} />
            </BlurView>
          </TouchableOpacity>
        )}

        {/* Stats Glass Bar */}
        {!banner && (
          <View style={styles.statsGlassWrapper}>
            <BlurView intensity={Platform.OS === 'ios' ? 40 : 100} tint="light" style={styles.statsGlassBar}>
              <View style={styles.statItem}>
                <Ionicons name="checkmark-circle" size={16} color={COLORS.success} />
                <Text style={styles.statValue}>{driverData?.acceptance_rate || '100'}%</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.statItemCenter}>
                <Ionicons name="cash" size={20} color={COLORS.accent} />
                <Text style={styles.statValueLarge}>${(earnings?.total_earnings || 0).toFixed(2)}</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.statItem}>
                <Ionicons name="car-sport" size={16} color="#007AFF" />
                <Text style={styles.statValue}>{driverData?.total_rides || '0'}</Text>
              </View>
            </BlurView>
          </View>
        )}

        {/* Status Indicator Pill */}
        <View style={styles.statusPillWrapper}>
            {isOnline ? (
                <View style={styles.statusPillOnline}>
                    <Ionicons name="pulse" size={14} color="#059669" />
                    <Text style={styles.statusPillTextOnline}>Finding rides...</Text>
                </View>
            ) : (
                <View style={styles.statusPillOffline}>
                    <View style={styles.offlineDot} />
                    <Text style={styles.statusPillTextOffline}>Offline</Text>
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
                {isOnline ? 'STOP' : 'GO'}
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
  // Hud Banner
  hudBannerWrapper: {
    borderRadius: 24,
    overflow: 'hidden',
    marginBottom: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.1,
    shadowRadius: 10,
    elevation: 4,
  },
  hudBannerDanger: {
    shadowColor: '#DC2626',
  },
  hudBannerWarning: {
    shadowColor: '#D97706',
  },
  hudBannerBlur: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 12,
    backgroundColor: 'rgba(255,255,255,0.85)',
    gap: 10,
  },
  hudBannerText: {
    fontSize: 15,
    fontWeight: '700',
    color: COLORS.text,
  },
  // Stats Glass Bar
  statsGlassWrapper: {
    width: '100%',
    borderRadius: 24,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.12,
    shadowRadius: 16,
    elevation: 6,
    marginBottom: 16,
  },
  statsGlassBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.75)',
    paddingVertical: 12,
    paddingHorizontal: 24,
  },
  statItem: {
    alignItems: 'center',
    flex: 1,
  },
  statItemCenter: {
    alignItems: 'center',
    flex: 1.5,
  },
  statDivider: {
    width: 1,
    height: 30,
    backgroundColor: 'rgba(0,0,0,0.08)',
  },
  statValue: {
    fontSize: 16,
    fontWeight: '800',
    color: COLORS.text,
    marginTop: 4,
  },
  statValueLarge: {
    fontSize: 20,
    fontWeight: '900',
    color: COLORS.accent,
    marginTop: 2,
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
