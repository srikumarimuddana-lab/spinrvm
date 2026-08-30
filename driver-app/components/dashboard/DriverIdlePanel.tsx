import React, { useRef, useEffect, useMemo, useState } from 'react';
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

// Auto-collapse the vehicle/online hud ~2s after going online — before
// that it's the reassurance a driver checks before tapping GO; once online
// it's just chrome eating map real estate every second it cruises. A tap
// re-expands it (re-arming the same timer) if they want to double-check
// the plate mid-shift. See DriverIdlePanel's onExpandedChange for how the
// map (index.tsx) tracks this to reclaim mapPadding when collapsed.
const HUD_AUTO_COLLAPSE_MS = 2_000;
const HUD_RE_COLLAPSE_MS = 2_500;
// Content heights the hud animates between — exported so index.tsx's
// mapPadding can reserve the right amount of map space for each state
// (same pattern as ActiveRidePanel's onExpandedChange in round 6).
// Expanded ≈ vehiclePill (~38) + its 10 margin + statusPill (~38).
export const HUD_EXPANDED_HEIGHT_DP = 90;
// Collapsed ≈ one compact pill row.
export const HUD_COLLAPSED_HEIGHT_DP = 34;

interface IdlePanelProps {
  isOnline: boolean;
  onToggleOnline: () => void;
  pulseAnim: any;
  // Reports the hud's expanded/collapsed state so the map can reserve
  // mapPadding accordingly — same contract as ActiveRidePanel's
  // onExpandedChange (round 6).
  onExpandedChange?: (expanded: boolean) => void;
}

export const DriverIdlePanel: React.FC<IdlePanelProps> = ({
  isOnline,
  onToggleOnline,
  onExpandedChange,
}) => {
  const { colors } = useTheme();
  const { t } = useLanguageStore();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const insets = useSafeAreaInsets();
  const driver = useAuthStore(s => s.driver);

  // ── Collapsible hud (vehicle pill + online pill) ──
  const [hudExpanded, setHudExpanded] = useState(true);
  useEffect(() => {
    onExpandedChange?.(hudExpanded);
  }, [hudExpanded, onExpandedChange]);
  // Playful spring, not a linear fade — matches ActiveRidePanel's draggable
  // sheet spring (tension/friction) so transitions feel consistent across
  // the app rather than each screen inventing its own motion.
  const hudAnim = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    Animated.spring(hudAnim, {
      toValue: hudExpanded ? 1 : 0,
      useNativeDriver: false, // drives height/margin, which the native driver can't touch
      tension: 70,
      friction: 8,
    }).start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hudExpanded]);
  const collapseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (collapseTimerRef.current) clearTimeout(collapseTimerRef.current);
    if (isOnline) {
      collapseTimerRef.current = setTimeout(() => setHudExpanded(false), HUD_AUTO_COLLAPSE_MS);
    } else {
      // Always show full info in the pre-online state — this is the
      // reassurance moment (right vehicle/plate on file) before tapping GO.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHudExpanded(true);
    }
    return () => { if (collapseTimerRef.current) clearTimeout(collapseTimerRef.current); };
  }, [isOnline]);
  const handleHudTap = () => {
    if (hudExpanded) return;
    setHudExpanded(true);
    if (collapseTimerRef.current) clearTimeout(collapseTimerRef.current);
    collapseTimerRef.current = setTimeout(() => setHudExpanded(false), HUD_RE_COLLAPSE_MS);
  };
  // hudAnim is the same verified-safe stable ref idiom as goAnim below
  // (mutated only via Animated.spring()/.interpolate(), never reassigned).
  // Precomputed once here (with the required disable comments) so the JSX
  // below reads plain interpolation values instead of touching the ref
  // itself at each usage site.
  // eslint-disable-next-line react-hooks/refs
  const hudHeight = hudAnim.interpolate({ inputRange: [0, 1], outputRange: [HUD_COLLAPSED_HEIGHT_DP, HUD_EXPANDED_HEIGHT_DP] });
  // eslint-disable-next-line react-hooks/refs
  const hudMarginBottom = hudAnim.interpolate({ inputRange: [0, 1], outputRange: [12, 20] });
  const fullContentOpacity = hudAnim;
  // eslint-disable-next-line react-hooks/refs
  const fullContentScale = hudAnim.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1] });
  // eslint-disable-next-line react-hooks/refs
  const collapsedPillOpacity = hudAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 0] });
  // eslint-disable-next-line react-hooks/refs
  const collapsedPillScale = hudAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 0.9] });

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

      {/* HUD Info Area — animated height so the collapse/expand reads as one
          continuous playful motion, not an instant cut. overflow:'hidden'
          clips the fading-out variant during the spring instead of letting
          it poke past the shrinking box. */}
      <Animated.View
        style={[
          styles.hudArea,
          {
            height: hudHeight,
            marginBottom: hudMarginBottom,
            overflow: 'hidden',
          },
        ]}
        pointerEvents="box-none"
      >
        {/* Full content — vehicle pill + status pill. Fades/scales out as
            the hud collapses; absolutely positioned once collapsed so it
            doesn't force the shrinking box back open. */}
        {/* fullContentOpacity/fullContentScale are plain vars aliased from
            the verified-safe hudAnim ref above (see its declaration
            comment) — not a fresh ref read, but the linter's data-flow
            tracking still traces the alias back to it. */}
        <Animated.View
          // eslint-disable-next-line react-hooks/refs
          style={{
            width: '100%',
            alignItems: 'center',
            // eslint-disable-next-line react-hooks/refs
            opacity: fullContentOpacity,
            transform: [{ scale: fullContentScale }],
            position: hudExpanded ? 'relative' : 'absolute',
          }}
          pointerEvents={hudExpanded ? 'box-none' : 'none'}
        >
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
        </Animated.View>

        {/* Collapsed pill — tap to re-expand. Only rendered/relevant once
            online (the offline state never collapses). */}
        <Animated.View
          style={{
            opacity: collapsedPillOpacity,
            transform: [{ scale: collapsedPillScale }],
            position: hudExpanded ? 'absolute' : 'relative',
          }}
          pointerEvents={hudExpanded ? 'none' : 'box-none'}
        >
          <TouchableOpacity
            style={styles.collapsedHudPill}
            onPress={handleHudTap}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityLabel={t('dashboard.youreOnline')}
            accessibilityHint="Tap to show vehicle details"
          >
            <View style={styles.onlineDot} />
            <Text allowFontScaling={false} style={styles.statusPillTextOnline}>{t('dashboard.youreOnline')}</Text>
            <Ionicons name="chevron-down" size={12} color={colors.success} />
          </TouchableOpacity>
        </Animated.View>
      </Animated.View>

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

    collapsedHudPill: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.successBg,
      paddingHorizontal: 14,
      paddingVertical: 6,
      borderRadius: 18,
      borderWidth: 1,
      borderColor: colors.success,
      gap: 6,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
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
