import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Animated } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import SpinrConfig from '@shared/config/spinr.config';
import { useAuthStore, DriverOnboardingStatus } from '@shared/store/authStore';

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

// Maps the onboarding state to the banner shown above the Go Online toggle.
// Only rendered when the driver is NOT in the verified state — a verified
// driver sees no banner, just the normal online/offline toggle.
const STATE_BANNERS: Record<Exclude<DriverOnboardingStatus, 'verified'>, {
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  tone: 'warning' | 'danger' | 'info';
  cta: string;
  target: string;
}> = {
  profile_incomplete: {
    title: 'Finish your profile',
    subtitle: 'Add your personal details to continue.',
    icon: 'person-circle-outline',
    tone: 'info',
    cta: 'Complete Profile',
    target: '/profile-setup',
  },
  vehicle_required: {
    title: 'Add your vehicle',
    subtitle: 'Tell us what you drive to finish onboarding.',
    icon: 'car-outline',
    tone: 'info',
    cta: 'Add Vehicle',
    target: '/become-driver',
  },
  documents_required: {
    title: 'Documents required',
    subtitle: 'Upload your mandatory documents to get verified.',
    icon: 'document-text-outline',
    tone: 'warning',
    cta: 'Upload Now',
    target: '/documents',
  },
  documents_rejected: {
    title: 'Document rejected',
    subtitle: 'One or more documents were rejected. Please re-upload.',
    icon: 'alert-circle-outline',
    tone: 'danger',
    cta: 'Re-upload',
    target: '/documents',
  },
  documents_expired: {
    title: 'Documents expired',
    subtitle: 'One or more documents have expired. Please re-upload.',
    icon: 'time-outline',
    tone: 'warning',
    cta: 'Re-upload',
    target: '/documents',
  },
  pending_review: {
    title: 'Under review',
    subtitle: 'Your profile is being reviewed. We\u2019ll notify you once approved.',
    icon: 'hourglass-outline',
    tone: 'info',
    cta: 'View Documents',
    target: '/documents',
  },
  suspended: {
    title: 'Account suspended',
    subtitle: 'Your account is suspended. Contact support for help.',
    icon: 'ban-outline',
    tone: 'danger',
    cta: 'Contact Support',
    target: '/driver/settings',
  },
};

export const DriverIdlePanel: React.FC<IdlePanelProps> = ({
  isOnline,
  driverData,
  earnings,
  onToggleOnline,
  pulseAnim,
}) => {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const onboardingStatus = useAuthStore(
    (s) => s.user?.driver_onboarding_status ?? null
  );
  const banner =
    onboardingStatus && onboardingStatus !== 'verified'
      ? STATE_BANNERS[onboardingStatus]
      : null;
  // When banner is shown we hard-gate the Go Online toggle, regardless of
  // the legacy is_verified flag. That way any non-verified state correctly
  // prevents the driver from going online.
  const canGoOnline = !banner && !!driverData?.is_verified;

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
    <View style={[styles.idlePanel, { paddingBottom: Math.max(insets.bottom + 12, 24) }]}>
      {banner && (
        <TouchableOpacity
          style={[
            styles.stateBanner,
            banner.tone === 'danger' && styles.stateBannerDanger,
            banner.tone === 'warning' && styles.stateBannerWarning,
            banner.tone === 'info' && styles.stateBannerInfo,
          ]}
          onPress={() => router.push(banner.target as any)}
          activeOpacity={0.85}
        >
          <View style={styles.stateBannerIcon}>
            <Ionicons
              name={banner.icon}
              size={22}
              color={
                banner.tone === 'danger'
                  ? '#DC2626'
                  : banner.tone === 'warning'
                  ? '#D97706'
                  : COLORS.accent
              }
            />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.stateBannerTitle}>{banner.title}</Text>
            <Text style={styles.stateBannerSub}>{banner.subtitle}</Text>
          </View>
          <View style={styles.stateBannerCta}>
            <Text style={styles.stateBannerCtaText}>{banner.cta}</Text>
            <Ionicons name="chevron-forward" size={16} color={COLORS.text} />
          </View>
        </TouchableOpacity>
      )}

      <TouchableOpacity
        style={[
          styles.onlineToggle,
          !canGoOnline ? styles.onlineDisabled : (isOnline ? styles.onlineActive : styles.onlineInactive)
        ]}
        onPress={onToggleOnline}
        activeOpacity={canGoOnline ? 0.8 : 1}
        disabled={!canGoOnline}
      >
        <Animated.View style={[styles.pulseIndicator, { transform: [{ scale: pulseAnim }] }]}>
          <View style={[
            styles.statusDot,
            !canGoOnline ? { backgroundColor: COLORS.orange } : (isOnline ? { backgroundColor: COLORS.success } : { backgroundColor: '#FF4757' })
          ]} />
        </Animated.View>
        <View style={styles.toggleText}>
          <Text style={styles.toggleLabel}>
            {!canGoOnline ? 'Not Ready to Drive' : (isOnline ? "You're Online" : "You're Offline")}
          </Text>
          <Text style={styles.toggleSub}>
            {!canGoOnline
              ? (banner?.subtitle || 'Complete verification to go online')
              : (isOnline ? 'Waiting for ride requests...' : 'Go online to start earning')}
          </Text>
        </View>
        <View style={[
          styles.toggleSwitch,
          !canGoOnline ? styles.toggleSwitchDisabled : (isOnline && styles.toggleSwitchOn)
        ]}>
          <View style={[
            styles.toggleKnob,
            !canGoOnline ? styles.toggleKnobDisabled : (isOnline && styles.toggleKnobOn)
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
    paddingHorizontal: 16,
    paddingTop: 16,
  },
  // Onboarding state banner shown above the Go Online toggle.
  stateBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    borderRadius: 14,
    marginBottom: 12,
    borderWidth: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 6,
    elevation: 2,
  },
  stateBannerInfo: {
    backgroundColor: 'rgba(255,59,48,0.06)',
    borderColor: 'rgba(255,59,48,0.15)',
  },
  stateBannerWarning: {
    backgroundColor: 'rgba(217,119,6,0.08)',
    borderColor: 'rgba(217,119,6,0.2)',
  },
  stateBannerDanger: {
    backgroundColor: 'rgba(220,38,38,0.08)',
    borderColor: 'rgba(220,38,38,0.2)',
  },
  stateBannerIcon: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.8)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  stateBannerTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: COLORS.text,
  },
  stateBannerSub: {
    fontSize: 12,
    color: COLORS.textDim,
    marginTop: 2,
  },
  stateBannerCta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
  },
  stateBannerCtaText: {
    fontSize: 12,
    fontWeight: '700',
    color: COLORS.text,
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
