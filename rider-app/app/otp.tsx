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
  const { phoneNumber, consentAccepted: consentParam } = useLocalSearchParams<{ phoneNumber: string; consentAccepted?: string }>();

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [countdown, setCountdown] = useState(30);
  const [canResend, setCanResend] = useState(false);
  // Mutable copy of login.tsx's route param -- login.tsx no longer requires
  // the checkbox to be checked before reaching this screen, so this can
  // start false for a proactive new signup who didn't tick it, or a
  // returning user for whom it's simply unused. If the backend comes back
  // with consent_required (see handleVerify's catch), the inline consent
  // card below lets the user accept it here instead of bouncing them back
  // to login.tsx.
  const [consentAccepted, setConsentAccepted] = useState(consentParam === 'true');
  // True only after a verify attempt is rejected specifically for missing
  // consent -- this only ever happens for a genuine new-account creation
  // (routes/auth.py's verify_otp never checks this field for a returning
  // user), so a returning user never sees this state.
  const [needsConsent, setNeedsConsent] = useState(false);
  const [resendingForConsent, setResendingForConsent] = useState(false);
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
        // A brand-new signup already gets a current consent_version stamped
        // at creation (routes/auth.py's verify_otp), so this check only
        // ever fires true for a pre-existing account with no recorded
        // consent — a legacy-imported rider, or one that predates
        // consent-version tracking. Dark-shipped: while
        // app_settings.legacy_consent_notice_enabled is off, /consent/status
        // always reports needs_notice: false, so this is a no-op today.
        api.get('/consent/status').then(
          (res) => {
            if ((res.data as any)?.needs_notice) {
              router.replace('/legacy-consent-notice' as any);
            } else {
              router.replace('/(tabs)' as any);
            }
          },
          () => router.replace('/(tabs)' as any), // fail open — never block login on this check
        );
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
        // Explicit consent gesture from login.tsx's checkbox (or accepted
        // inline below, if the backend rejected an earlier attempt for
        // missing consent). Only enforced by the backend when this call
        // actually creates a new account — harmless to send on a
        // returning-user login too.
        consent_accepted: consentAccepted,
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
      // errors.auth.consent_required (backend/utils/error_keys.py) fires
      // only when this phone number turns out to be a brand-new account and
      // consent wasn't accepted before this call. The code that was just
      // entered is already consumed server-side (OTPs are deleted on
      // successful match, before the consent check runs) -- retrying the
      // same code would just fail with ERR_OTP_INVALID, so this reveals an
      // inline consent step that sends a fresh code once accepted, rather
      // than telling the user their correct code was "invalid".
      if (err?.messageKey === 'errors.auth.consent_required') {
        setNeedsConsent(true);
        setCode('');
        showToast(
          'One More Step',
          'Please agree to our Terms of Service and Privacy Policy to finish creating your account.',
          'warning',
        );
        return;
      }
      triggerShake();
      setCode('');
      showToast('Verification Failed', getApiErrorMessage(err, 'Invalid code. Please try again.'), 'danger');
    } finally {
      setVerifying(false);
    }
  };

  const handleAgreeAndResend = async () => {
    if (!consentAccepted || resendingForConsent) return;
    setResendingForConsent(true);
    try {
      await api.post('/auth/send-otp', { phone: phoneNumber });
      setNeedsConsent(false);
      setCode('');
      setCountdown(30);
      setCanResend(false);
      showToast('Code Sent', 'A new verification code has been sent to your phone.', 'success');
    } catch (e: any) {
      showToast('Failed', getApiErrorMessage(e, 'Could not send a new code. Please try again.'), 'danger');
    } finally {
      setResendingForConsent(false);
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

        {needsConsent ? (
          // Only reached when the backend rejected a correct code because
          // this turned out to be a brand-new account with no recorded
          // consent (errors.auth.consent_required) — a returning user never
          // sees this. Mirrors login.tsx's checkbox exactly (same
          // accessibility pattern: icon-based checked state, checkbox and
          // links as separate touchables) so it reads as the same gesture,
          // just relocated here instead of bouncing the user back a screen.
          <View style={styles.consentCard}>
            <Text style={styles.consentCardTitle}>Almost there</Text>
            <Text style={styles.consentCardBody}>
              To finish creating your account, please confirm you agree to Spinr&apos;s Terms of
              Service and Privacy Policy. We&apos;ll send a new code once you do.
            </Text>
            <View style={styles.consentRow} accessible={false}>
              <TouchableOpacity
                onPress={() => setConsentAccepted((c) => !c)}
                activeOpacity={0.7}
                disabled={resendingForConsent}
                hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: consentAccepted, disabled: resendingForConsent }}
                accessibilityLabel="I agree to Spinr's Terms of Service and Privacy Policy"
              >
                <Ionicons
                  name={consentAccepted ? 'checkbox' : 'square-outline'}
                  size={22}
                  color={consentAccepted ? colors.primary : colors.textDim}
                />
              </TouchableOpacity>
              <Text style={styles.consentRowText}>
                I agree to Spinr&apos;s{' '}
                <Text
                  style={styles.termsLink}
                  onPress={() => router.push({ pathname: '/legal', params: { type: 'tos' } } as any)}
                  accessibilityRole="link"
                  accessibilityLabel="Terms of Service"
                >
                  Terms of Service
                </Text>
                {' '}and{' '}
                <Text
                  style={styles.termsLink}
                  onPress={() => router.push({ pathname: '/legal', params: { type: 'privacy' } } as any)}
                  accessibilityRole="link"
                  accessibilityLabel="Privacy Policy"
                >
                  Privacy Policy
                </Text>
              </Text>
            </View>
            <TouchableOpacity
              style={[
                styles.verifyBtn,
                (!consentAccepted || resendingForConsent) && styles.verifyBtnInactive,
              ]}
              onPress={handleAgreeAndResend}
              disabled={!consentAccepted || resendingForConsent}
              activeOpacity={0.85}
              accessibilityLabel="Agree and send new code"
              accessibilityState={{ disabled: !consentAccepted || resendingForConsent }}
            >
              {resendingForConsent ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={[styles.verifyBtnText, !consentAccepted && styles.verifyBtnTextInactive]}>
                  Agree & Send New Code
                </Text>
              )}
            </TouchableOpacity>
          </View>
        ) : (
        <>
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
        </>
        )}
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
    consentCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 16,
      borderWidth: 1, borderColor: colors.border,
      padding: 18, marginBottom: 24,
    },
    consentCardTitle: { fontSize: 16, fontWeight: '700', color: colors.text, marginBottom: 6 },
    consentCardBody: { fontSize: 13, color: colors.textDim, lineHeight: 19, marginBottom: 14 },
    consentRow: {
      flexDirection: 'row', alignItems: 'flex-start', gap: 10,
      minHeight: 44, paddingVertical: 4, marginBottom: 14,
    },
    consentRowText: { flex: 1, fontSize: 12, color: colors.textDim, lineHeight: 18, paddingTop: 3 },
    termsLink: { color: colors.primary, fontWeight: '600' },
  });
}
