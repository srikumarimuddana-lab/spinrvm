import React, { useEffect, useMemo, useRef, useState } from 'react';
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
import { useRouter } from 'expo-router';
import { useAuthStore, type User } from '@shared/store/authStore';
import api, { RateLimitError } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { tKey } from '../i18n';
import { useAnimatedValue } from '../hooks/useAnimatedValue';

const CODE_LENGTH_MIN = 4;
const CODE_LENGTH_MAX = 6;
// Mirrors backend/dependencies/__init__.py::OTP_EXPIRY_MINUTES (5), read
// directly from backend/routes/users.py's N14 block per the task brief —
// there is no runtime endpoint that surfaces this, so it's a literal here
// exactly like otp.tsx hardcodes CODE_LENGTH for the phone flow.
const OTP_EXPIRY_MINUTES = 5;

// A user object augmented with the email-verification fields the shared
// `User` type doesn't declare yet (GET /auth/me's `UserProfile` response
// schema — backend/schemas.py — doesn't return them; see the Change Impact
// Log for this PR). Confirm's own response DOES include `email_verified`,
// so we can merge it into the store locally without touching the shared
// type or any backend file (both out of scope for this change).
type VerifiableUser = User & { email_verified?: boolean; email_verified_at?: string | null };

// Local shape for the two structured-error fields this screen branches on.
// SpinrApiError (thrown by @shared/api/client) carries both; duck-typed here
// so the screen doesn't need to import the class just to read two fields.
interface StructuredApiError {
  messageKey?: string;
  message?: string;
  code?: number;
}

function resolveErrorCopy(err: unknown, fallback: string): string {
  const e = err as StructuredApiError | null | undefined;
  if (e && typeof e === 'object' && e.messageKey) {
    return tKey(e.messageKey, e.message || fallback);
  }
  if (e && typeof e === 'object' && typeof e.message === 'string' && e.message) {
    return e.message;
  }
  return fallback;
}

export default function VerifyEmailScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuthStore() as { user: VerifiableUser | null };
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [code, setCode] = useState('');
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [alreadyVerified, setAlreadyVerified] = useState(!!user?.email_verified);
  const [countdown, setCountdown] = useState(0);
  const inputRef = useRef<TextInput>(null);
  const requestInFlight = useRef(false);

  const shakeAnim = useAnimatedValue(0);

  const email = user?.email || '';

  // No email on file at all — nothing to verify. Route back rather than
  // showing a broken screen (mirrors otp.tsx's "no phoneNumber → back" guard).
  useEffect(() => {
    if (!email) {
      showToast('No Email on File', 'Add an email to your profile before verifying it.', 'warning');
      router.back();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

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
    if (digits.length <= CODE_LENGTH_MAX) setCode(digits);
  };

  // POST /users/verify-email/request — no body, sends a code to whatever
  // email is already on the caller's account. Fired once on screen entry
  // (mirrors otp.tsx's own screen, which arrives already having sent a code
  // via the previous screen's /auth/send-otp call) and again on "Resend".
  const requestCode = async (isResend: boolean) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setSending(true);
    try {
      const response = await api.post<{ success: boolean; already_verified?: boolean; message?: string }>(
        '/users/verify-email/request',
      );
      const data = response.data;
      if (data?.already_verified) {
        setAlreadyVerified(true);
        useAuthStore.setState((state) => ({
          user: state.user ? ({ ...state.user, email_verified: true } as VerifiableUser) : state.user,
        }));
        showToast('Already Verified', 'Your email is already verified.', 'success');
        return;
      }
      setCodeSent(true);
      setCountdown(30);
      showToast(
        isResend ? 'Code Sent' : 'Check Your Email',
        `We sent a verification code to ${email}. It expires in ${OTP_EXPIRY_MINUTES} minutes.`,
        'success',
      );
    } catch (err: any) {
      if (err?.name === 'RateLimitError') {
        const rateLimitErr = err as RateLimitError;
        const retrySeconds = rateLimitErr.retryAfterSeconds > 0 ? rateLimitErr.retryAfterSeconds : 60;
        setCountdown(retrySeconds);
        showToast(
          'Too Many Attempts',
          rateLimitErr.message && rateLimitErr.message !== 'Request failed'
            ? rateLimitErr.message
            : `Please wait before requesting another code.`,
          'warning',
        );
        return;
      }
      showToast(
        'Could Not Send Code',
        resolveErrorCopy(err, 'We could not send a verification code. Please try again.'),
        'danger',
      );
    } finally {
      setSending(false);
      requestInFlight.current = false;
    }
  };

  useEffect(() => {
    if (email && !alreadyVerified) {
      requestCode(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email]);

  // POST /users/verify-email/confirm — { code }. On success the response
  // itself carries `email_verified: true`, so we merge that straight into
  // the store instead of re-fetching /auth/me (whose response schema
  // doesn't return the field today — see Change Impact Log). That keeps
  // this screen and the account-screen badge in sync immediately, with no
  // stale "not verified" state and no extra round trip.
  const handleVerify = async () => {
    if (code.length < CODE_LENGTH_MIN || code.length > CODE_LENGTH_MAX) {
      triggerShake();
      showToast('Invalid Code', 'Please enter the code sent to your email.', 'warning');
      return;
    }
    setVerifying(true);
    try {
      const response = await api.post<{ success: boolean; email_verified?: boolean }>(
        '/users/verify-email/confirm',
        { code },
      );
      if (response.data?.email_verified) {
        useAuthStore.setState((state) => ({
          user: state.user ? ({ ...state.user, email_verified: true, email_verified_at: new Date().toISOString() } as VerifiableUser) : state.user,
        }));
      }
      showToast('Email Verified', 'Your email has been verified.', 'success');
      router.back();
    } catch (err: any) {
      triggerShake();
      setCode('');
      if (err?.name === 'RateLimitError') {
        const rateLimitErr = err as RateLimitError;
        showToast(
          'Too Many Attempts',
          rateLimitErr.message && rateLimitErr.message !== 'Request failed'
            ? rateLimitErr.message
            : 'Too many failed attempts. Please wait before trying again.',
          'warning',
        );
        return;
      }
      showToast('Verification Failed', resolveErrorCopy(err, 'That code did not work. Please try again.'), 'danger');
    } finally {
      setVerifying(false);
    }
  };

  const handleResend = () => {
    if (countdown > 0 || sending) return;
    requestCode(true);
  };

  if (alreadyVerified) {
    return (
      <KeyboardAvoidingView style={styles.container} behavior="padding">
        <View style={[styles.centerArea, { paddingTop: insets.top + 16, paddingHorizontal: 24, justifyContent: 'center' }]}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={22} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.illustrationContainer}>
            <View style={styles.illustrationCircle}>
              <View style={styles.illustrationInner}>
                <Ionicons name="checkmark-circle" size={40} color={colors.primary} />
              </View>
            </View>
          </View>
          <View style={styles.titleSection}>
            <Text style={styles.title}>Email Verified</Text>
            <Text style={styles.subtitle}>{email} is already verified.</Text>
          </View>
          <TouchableOpacity style={styles.verifyBtn} onPress={() => router.back()} accessibilityLabel="Done, already verified">
            <Text style={styles.verifyBtnText}>Done</Text>
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    );
  }

  return (
    <KeyboardAvoidingView style={styles.container} behavior="padding">
      <ScrollView
        style={styles.container}
        contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + 16, paddingBottom: insets.bottom + 24 }]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>

        <View style={styles.centerArea}>
          <View style={styles.illustrationContainer}>
            <View style={styles.illustrationCircle}>
              <View style={styles.illustrationInner}>
                <Ionicons name="mail-open" size={40} color={colors.primary} />
              </View>
            </View>
          </View>

          <View style={styles.titleSection}>
            <Text style={styles.title}>Verify Your Email</Text>
            <Text style={styles.subtitle}>
              {codeSent ? `Enter the code we sent to` : sending ? 'Sending a code to' : 'We’ll send a code to'}
            </Text>
            <Text style={styles.emailDisplay}>{email}</Text>
          </View>

          {sending && !codeSent ? (
            <ActivityIndicator color={colors.primary} size="small" style={{ marginBottom: 24 }} />
          ) : (
            <>
              <Animated.View style={[styles.codeContainer, { transform: [{ translateX: shakeAnim }] }]}>
                <TextInput
                  ref={inputRef}
                  style={styles.hiddenInput}
                  value={code}
                  onChangeText={handleCodeChange}
                  keyboardType="number-pad"
                  maxLength={CODE_LENGTH_MAX}
                  autoFocus
                  accessibilityLabel="Verification code"
                />
                <TouchableOpacity
                  style={styles.codeBoxes}
                  activeOpacity={1}
                  onPress={() => inputRef.current?.focus()}
                >
                  {Array.from({ length: CODE_LENGTH_MAX }).map((_, i) => {
                    const isFilled = i < code.length;
                    const isActive = i === code.length;
                    return (
                      <View
                        key={i}
                        style={[styles.codeBox, isActive && styles.codeBoxActive, isFilled && styles.codeBoxFilled]}
                        accessibilityLabel={`Digit ${i + 1}${isFilled ? ', entered' : ', empty'}`}
                      >
                        <Text style={[styles.codeDigit, isFilled && styles.codeDigitFilled]}>{code[i] || ''}</Text>
                      </View>
                    );
                  })}
                </TouchableOpacity>
              </Animated.View>

              <Text style={styles.expiryNote}>Codes expire after {OTP_EXPIRY_MINUTES} minutes.</Text>

              <TouchableOpacity
                style={[
                  styles.verifyBtn,
                  code.length < CODE_LENGTH_MIN && styles.verifyBtnInactive,
                  verifying && styles.verifyBtnLoading,
                ]}
                onPress={handleVerify}
                disabled={verifying || code.length < CODE_LENGTH_MIN}
                activeOpacity={0.85}
                accessibilityLabel="Verify email"
                accessibilityState={{ disabled: verifying || code.length < CODE_LENGTH_MIN }}
              >
                {verifying ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <Text style={[styles.verifyBtnText, code.length < CODE_LENGTH_MIN && styles.verifyBtnTextInactive]}>
                    Verify Email
                  </Text>
                )}
              </TouchableOpacity>

              <View style={styles.resendSection}>
                {countdown > 0 ? (
                  <View style={styles.resendCountdown}>
                    <Ionicons name="time-outline" size={16} color={colors.textDim} />
                    <Text style={styles.countdownText}>
                      Resend code in <Text style={styles.countdownNumber}>{countdown}s</Text>
                    </Text>
                  </View>
                ) : (
                  <TouchableOpacity
                    onPress={handleResend}
                    style={styles.resendBtn}
                    disabled={sending}
                    accessibilityLabel="Resend code"
                  >
                    <Ionicons name="refresh" size={16} color={colors.primary} />
                    <Text style={styles.resendText}>Resend Code</Text>
                  </TouchableOpacity>
                )}
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
    subtitle: { fontSize: 15, color: colors.textDim, lineHeight: 22, textAlign: 'center' },
    emailDisplay: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 4, textAlign: 'center' },
    codeContainer: { marginBottom: 16 },
    hiddenInput: { position: 'absolute', opacity: 0, width: 1, height: 1 },
    codeBoxes: { flexDirection: 'row', justifyContent: 'center', gap: 8 },
    codeBox: {
      width: 44, height: 56, borderRadius: 14,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1.5, borderColor: colors.border,
      justifyContent: 'center', alignItems: 'center',
    },
    codeBoxActive: { borderColor: colors.primary, backgroundColor: colors.surface },
    codeBoxFilled: { borderColor: colors.primary, backgroundColor: `${colors.primary}08` },
    codeDigit: { fontSize: 24, fontWeight: '800', color: colors.textDim },
    codeDigitFilled: { color: colors.text },
    expiryNote: { fontSize: 12, color: colors.textDim, textAlign: 'center', marginBottom: 24 },
    verifyBtn: {
      backgroundColor: colors.primary, borderRadius: 16, height: 58,
      justifyContent: 'center', alignItems: 'center',
      marginBottom: 24,
    },
    verifyBtnInactive: { backgroundColor: colors.border },
    verifyBtnLoading: { backgroundColor: colors.primaryDark },
    verifyBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
    verifyBtnTextInactive: { color: colors.textDim },
    resendSection: { alignItems: 'center', gap: 16 },
    resendBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8 },
    resendText: { fontSize: 15, fontWeight: '600', color: colors.primary },
    resendCountdown: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    countdownText: { fontSize: 14, color: colors.textDim },
    countdownNumber: { fontWeight: '700', color: colors.text },
  });
}
