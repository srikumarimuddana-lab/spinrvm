import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  ScrollView,
  Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useAuthStore, type User } from '@shared/store/authStore';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { Analytics } from '@shared/analytics';
import { logCompleteRegistration } from '@shared/analytics/meta';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAnimatedValue, useAnimatedValues } from '../hooks/useAnimatedValue';

const CODE_LENGTH = 4;

export default function OtpScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { phoneNumber } = useLocalSearchParams<{ phoneNumber: string }>();

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [countdown, setCountdown] = useState(30);
  const [canResend, setCanResend] = useState(false);
  const { user, initialize, clearError } = useAuthStore();
  const inputRef = useRef<TextInput>(null);
  const resendInFlight = useRef(false);
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const shakeAnim = useAnimatedValue(0);
  const dotAnims = useAnimatedValues(CODE_LENGTH, 0);

  useEffect(() => {
    if (!phoneNumber) router.back();
    return () => { resendInFlight.current = false; };
    // phoneNumber is a route param fixed at navigation time (doesn't change
    // while this screen stays mounted); router is expo-router's stable
    // singleton — adding both doesn't change when this fires in practice.
  }, [phoneNumber, router]);

  useEffect(() => {
    dotAnims.forEach((anim, i) => {
      Animated.spring(anim, {
        toValue: i < code.length ? 1 : 0,
        friction: 5,
        tension: 100,
        useNativeDriver: true,
      }).start();
    });
    // dotAnims is a stable array (useAnimatedValues, created once).
  }, [code, dotAnims]);

  useEffect(() => {
    if (countdown > 0) {
      const timer = setTimeout(() => setCountdown(c => c - 1), 1000);
      return () => clearTimeout(timer);
    } else {
      // Countdown-reached-zero transition; canResend isn't a dep of this
      // effect (only countdown is), so no loop.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCanResend(true);
    }
  }, [countdown]);

  useEffect(() => {
    if (user) {
      const hasProfileData = !!(user.first_name && user.last_name && user.email);
      const profileComplete = !!user.profile_complete || hasProfileData;
      if (profileComplete) {
        router.replace('/(tabs)' as any);
      } else {
        router.replace('/profile-setup');
      }
    }
    // router is expo-router's stable singleton.
  }, [user, router]);

  const triggerShake = () => {
    Animated.sequence([
      Animated.timing(shakeAnim, { toValue: 12, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: -12, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: 8, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: -8, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: 0, duration: 60, useNativeDriver: true }),
    ]).start();
  };

  const handleCodeChange = (text: string) => {
    const digits = text.replace(/\D/g, '');
    if (digits.length <= CODE_LENGTH) setCode(digits);
  };

  const handleVerify = async () => {
    if (code.length !== CODE_LENGTH) {
      triggerShake();
      showToast('Invalid Code', `Please enter the ${CODE_LENGTH}-digit code sent to your phone.`, 'warning');
      return;
    }

    setVerifying(true);
    clearError();
    try {
      const response = await api.post<{ token?: string; refresh_token?: string; expires_in?: number; user?: User }>('/auth/verify-otp', {
        phone: phoneNumber,
        code,
        // Tells the backend which funnel this signup belongs to, so a rider
        // signup is reported to the rider dataset. Both apps share this
        // endpoint; without the hint the backend defaults to 'rider'.
        client_app: 'rider',
      });
      if (!response.data) throw new Error('Empty response from auth server');
      const data = response.data as any;

      // PIPEDA: the account is pending deletion (reactivable any time before
      // the 7-year retention ceiling). The backend issued no access token —
      // route to the reactivation screen instead of signing in.
      if (data.requires_reactivation) {
        router.replace({
          pathname: '/reactivate-account',
          params: {
            reactivationToken: data.reactivation_token ?? '',
            deletionScheduledAt: data.deletion_scheduled_at ?? '',
          },
        } as any);
        return;
      }

      const { token, refresh_token, expires_in, user: userData } = data;

      if (token) {
        await useAuthStore.getState().setTokens(token, refresh_token ?? '', expires_in ?? 900);
      }
      Analytics.otpVerified();

      // Meta CompleteRegistration — only on a genuine new account, and only
      // here, at the success callback where the account actually exists. The
      // event_id was minted by the backend, which sends its own copy of this
      // event with the same id; Meta collapses the pair into one conversion.
      // Absent when the signup is a returning login or when Meta tracking is
      // not configured, in which case logCompleteRegistration no-ops.
      if (data.is_new_user) {
        logCompleteRegistration({ eventId: data.meta_event_id, surface: 'rider' });
      }

      if (userData) {
        useAuthStore.setState({ user: userData, isInitialized: true, isLoading: false });
        Analytics.login();
      } else {
        await initialize();
      }
    } catch (err: any) {
      triggerShake();
      setCode('');
      showToast('Verification Failed', getApiErrorMessage(err, 'Invalid code. Please try again.'), 'danger');
    } finally {
      setVerifying(false);
    }
  };

  const handleResend = async () => {
    if (!canResend || resendInFlight.current) return;
    resendInFlight.current = true;
    setCanResend(false);
    setCountdown(30);
    try {
      await api.post('/auth/send-otp', { phone: phoneNumber });
      showToast('Code Sent', 'A new verification code has been sent to your phone.', 'success');
    } catch (e: any) {
      if (e?.response?.status === 429) {
        const retryAfterHeader = parseInt(e.response.headers?.['retry-after'] ?? '0', 10);
        const detailMatch = String(e.response?.data?.detail ?? '').match(/(\d+)\s*s/i);
        const retrySeconds = retryAfterHeader || (detailMatch ? parseInt(detailMatch[1], 10) : 60);
        setCountdown(retrySeconds > 0 ? retrySeconds : 60);
        showToast('Too Many Attempts', `Please wait ${retrySeconds} seconds before requesting another code.`, 'warning');
      } else {
        showToast('Failed', getApiErrorMessage(e, 'Could not resend code. Please try again.'), 'danger');
      }
    } finally {
      resendInFlight.current = false;
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior="padding"
    >
      <ScrollView
        style={styles.container}
        contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + 16, paddingBottom: insets.bottom + 24 }]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>

        {/* Top-anchored so that when the keyboard opens (autoFocus fires it
            immediately) the code boxes and Verify button flow from the top and
            stay scrollable above the keyboard. Vertically centering here made
            the overflow un-scrollable on Android, leaving Verify behind the
            keyboard. */}
        <View style={styles.centerArea}>
        <View style={styles.illustrationContainer}>
          <View style={styles.illustrationCircle}>
            <View style={styles.illustrationInner}>
              <Ionicons name="shield-checkmark" size={40} color={colors.primary} />
            </View>
          </View>
        </View>

        <View style={styles.titleSection}>
          <Text style={styles.title}>Verify Your Number</Text>
          <Text style={styles.subtitle}>We sent a {CODE_LENGTH}-digit code to</Text>
          <Text style={styles.phoneDisplay}>{phoneNumber}</Text>
        </View>

        <Animated.View style={[styles.codeContainer, { transform: [{ translateX: shakeAnim }] }]}>
          <TextInput
            ref={inputRef}
            style={styles.hiddenInput}
            value={code}
            onChangeText={handleCodeChange}
            keyboardType="phone-pad"
            maxLength={CODE_LENGTH}
            autoFocus
          />
          <TouchableOpacity
            style={styles.codeBoxes}
            activeOpacity={1}
            onPress={() => inputRef.current?.focus()}
          >
            {Array.from({ length: CODE_LENGTH }).map((_, i) => {
              const isFilled = i < code.length;
              const isActive = i === code.length;
              const scale = dotAnims[i].interpolate({
                inputRange: [0, 1],
                outputRange: [1, 1.1],
              });
              return (
                <Animated.View
                  key={i}
                  style={[
                    styles.codeBox,
                    isActive && styles.codeBoxActive,
                    isFilled && styles.codeBoxFilled,
                    { transform: [{ scale }] },
                  ]}
                  accessibilityLabel={`Digit ${i + 1}${isFilled ? ', entered' : ', empty'}`}
                >
                  <Text style={[styles.codeDigit, isFilled && styles.codeDigitFilled]}>
                    {code[i] || ''}
                  </Text>
                  {isActive && <View style={styles.cursor} />}
                </Animated.View>
              );
            })}
          </TouchableOpacity>
        </Animated.View>

        <TouchableOpacity
          style={[
            styles.verifyBtn,
            code.length !== CODE_LENGTH && styles.verifyBtnInactive,
            verifying && styles.verifyBtnLoading,
          ]}
          onPress={handleVerify}
          disabled={verifying || code.length !== CODE_LENGTH}
          activeOpacity={0.85}
          accessibilityLabel="Verify and continue"
          accessibilityState={{ disabled: verifying || code.length !== CODE_LENGTH }}
        >
          {verifying ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <View style={styles.verifyBtnContent}>
              <Text style={[styles.verifyBtnText, code.length !== CODE_LENGTH && styles.verifyBtnTextInactive]}>
                Verify & Continue
              </Text>
              <Ionicons
                name="checkmark-circle"
                size={20}
                color={code.length === CODE_LENGTH ? '#fff' : colors.textDim}
              />
            </View>
          )}
        </TouchableOpacity>

        <View style={styles.resendSection}>
          {canResend ? (
            <TouchableOpacity onPress={handleResend} style={styles.resendBtn}>
              <Ionicons name="refresh" size={16} color={colors.primary} />
              <Text style={styles.resendText}>Resend Code</Text>
            </TouchableOpacity>
          ) : (
            <View style={styles.resendCountdown}>
              <Ionicons name="time-outline" size={16} color={colors.textDim} />
              <Text style={styles.countdownText}>
                Resend code in <Text style={styles.countdownNumber}>{countdown}s</Text>
              </Text>
            </View>
          )}
          <TouchableOpacity onPress={() => router.back()} style={styles.changeNumberBtn}>
            <Ionicons name="call-outline" size={14} color={colors.textDim} />
            <Text style={styles.changeNumberText}>Change phone number</Text>
          </TouchableOpacity>
        </View>
        </View>
      </ScrollView>

    </KeyboardAvoidingView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    scrollContent: { flexGrow: 1, paddingHorizontal: 24 },
    // flexGrow (not flex) so the ScrollView fills the screen but never shrinks
    // below its content. Content is top-anchored (no justifyContent: center)
    // so that when the keyboard is up the overflow stays scrollable and the
    // Verify button can be reached instead of being clipped behind it.
    centerArea: { flexGrow: 1 },
    backBtn: {
      width: 44, height: 44, borderRadius: 14,
      backgroundColor: colors.surfaceLight,
      justifyContent: 'center', alignItems: 'center', marginBottom: 24,
    },
    illustrationContainer: { alignItems: 'center', marginBottom: 28 },
    illustrationCircle: {
      width: 96, height: 96, borderRadius: 48,
      backgroundColor: `${colors.primary}0A`,
      justifyContent: 'center', alignItems: 'center',
    },
    illustrationInner: {
      width: 72, height: 72, borderRadius: 36,
      backgroundColor: `${colors.primary}14`,
      justifyContent: 'center', alignItems: 'center',
    },
    titleSection: { alignItems: 'center', marginBottom: 36 },
    title: {
      fontSize: 26, fontWeight: '800', color: colors.text,
      letterSpacing: -0.5, marginBottom: 10,
    },
    subtitle: { fontSize: 15, color: colors.textDim, lineHeight: 22 },
    phoneDisplay: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 4 },
    codeContainer: { marginBottom: 28 },
    hiddenInput: { position: 'absolute', opacity: 0, width: 1, height: 1 },
    codeBoxes: { flexDirection: 'row', justifyContent: 'center', gap: 12 },
    codeBox: {
      width: 56, height: 64, borderRadius: 16,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1.5, borderColor: colors.border,
      justifyContent: 'center', alignItems: 'center',
    },
    codeBoxActive: {
      borderColor: colors.primary, backgroundColor: colors.surface,
      shadowColor: colors.primary, shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.12, shadowRadius: 8, elevation: 3,
    },
    codeBoxFilled: {
      borderColor: colors.primary,
      backgroundColor: `${colors.primary}08`,
    },
    codeDigit: { fontSize: 28, fontWeight: '800', color: colors.textDim },
    codeDigitFilled: { color: colors.text },
    cursor: {
      position: 'absolute', bottom: 14,
      width: 20, height: 2,
      backgroundColor: colors.primary, borderRadius: 1,
    },
    verifyBtn: {
      backgroundColor: colors.primary, borderRadius: 16, height: 58,
      justifyContent: 'center', alignItems: 'center',
      shadowColor: colors.primary, shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.25, shadowRadius: 12, elevation: 6, marginBottom: 24,
    },
    verifyBtnInactive: { backgroundColor: colors.border, shadowOpacity: 0, elevation: 0 },
    verifyBtnLoading: { backgroundColor: colors.primaryDark },
    verifyBtnContent: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    verifyBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
    verifyBtnTextInactive: { color: colors.textDim },
    resendSection: { alignItems: 'center', gap: 16 },
    resendBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8 },
    resendText: { fontSize: 15, fontWeight: '600', color: colors.primary },
    resendCountdown: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    countdownText: { fontSize: 14, color: colors.textDim },
    countdownNumber: { fontWeight: '700', color: colors.text },
    changeNumberBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8 },
    changeNumberText: { fontSize: 14, color: colors.textDim, fontWeight: '500' },
  });
}
