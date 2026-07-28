import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Platform,
  KeyboardAvoidingView,
  ScrollView,
  Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useAuthStore, type User } from '@shared/store/authStore';
import api, { setInMemoryToken, getApiErrorMessage } from '@shared/api/client';
import { logCompleteRegistration } from '@shared/analytics/meta';
import { showToast } from '../hooks/useToast';
import { useLanguageStore } from '../store/languageStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

// Platform-safe token storage
const storage = {
  async setItem(key: string, value: string) {
    try {
      if (Platform.OS === 'web') {
        localStorage.setItem(key, value);
      } else {
        const SecureStore = require('expo-secure-store');
        await SecureStore.setItemAsync(key, value);
      }
    } catch (e) {
      console.log('[Auth] Storage setItem FAILED:', e);
    }
  },
};

export default function OtpScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ phoneNumber: string }>();
  const { phoneNumber } = params;
  const { t } = useLanguageStore();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const codeLength = 4;

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [hasAttemptedVerification, setHasAttemptedVerification] = useState(false);
  const [countdown, setCountdown] = useState(30);
  const [canResend, setCanResend] = useState(false);
  const { user, initialize, clearError } = useAuthStore();
  const inputRef = useRef<TextInput>(null);

  // Animations
  const shakeAnim = useRef(new Animated.Value(0)).current;
  const dotAnims = useRef(
    Array.from({ length: codeLength }, () => new Animated.Value(0))
  ).current;

  useEffect(() => {
    if (!phoneNumber) {
      router.back();
    }
  }, []);

  // Animate dots as user types
  useEffect(() => {
    dotAnims.forEach((anim, i) => {
      Animated.spring(anim, {
        toValue: i < code.length ? 1 : 0,
        friction: 5,
        tension: 100,
        useNativeDriver: true,
      }).start();
    });
  }, [code]);

  // Resend countdown
  useEffect(() => {
    if (countdown > 0) {
      const timer = setTimeout(() => setCountdown(countdown - 1), 1000);
      return () => clearTimeout(timer);
    } else {
      setCanResend(true);
    }
  }, [countdown]);

  // Navigate on successful auth
  useEffect(() => {
    if (hasAttemptedVerification && user) {
      const hasProfileData = !!(user.first_name && user.last_name && user.email);
      const profileComplete = !!user.profile_complete || hasProfileData;
      if (profileComplete) {
        router.replace('/driver');
      } else {
        router.replace('/profile-setup');
      }
    }
  }, [user, hasAttemptedVerification]);

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
    if (digits.length <= codeLength) {
      setCode(digits);
    }
  };

  const handleVerify = async () => {
    if (!code || code.length !== codeLength) {
      triggerShake();
      showToast('error', 'Invalid Code', `Please enter the ${codeLength}-digit code sent to your phone.`);
      return;
    }

    setVerifying(true);
    setHasAttemptedVerification(true);
    clearError();

    try {
      const response = await api.post<{ token?: string; refresh_token?: string; expires_in?: number; user?: User }>('/auth/verify-otp', {
        phone: phoneNumber,
        code: code,
        // Routes this signup to the DRIVER dataset. Both apps share this
        // endpoint and the backend defaults to 'rider', so without this every
        // driver signup would be reported as a rider acquisition.
        client_app: 'driver',
      });
      if (!response.data) throw new Error('Empty response from auth server');
      const otpData = response.data as any;

      // PIPEDA: the account is in its 30-day deletion grace window. The backend
      // issued no access token — route to the reactivation screen instead of
      // signing in.
      if (otpData.requires_reactivation) {
        router.replace({
          pathname: '/reactivate-account',
          params: {
            reactivationToken: otpData.reactivation_token ?? '',
            deletionScheduledAt: otpData.deletion_scheduled_at ?? '',
          },
        } as any);
        return;
      }

      // Meta CompleteRegistration (content_category: driver_application) —
      // only on a genuine new account. The event_id was minted by the backend,
      // which sends its own copy with the same id so Meta collapses the pair
      // into one conversion instead of counting two.
      if (otpData.is_new_user) {
        logCompleteRegistration({ eventId: otpData.meta_event_id, surface: 'driver' });
      }

      const { token, refresh_token, expires_in, user: userData } = otpData;
      if (token) {
        await useAuthStore.getState().setTokens(token, refresh_token ?? '', expires_in ?? 900);
        if (userData) {
          useAuthStore.setState({
            user: userData,
            isInitialized: true,
            isLoading: false,
          });
        } else {
          await initialize();
        }
      }
    } catch (err: any) {
      triggerShake();
      setCode('');
      showToast('error', 'Verification Failed', getApiErrorMessage(err, 'Invalid code. Please try again.'));
    } finally {
      setVerifying(false);
    }
  };

  const handleResend = async () => {
    if (!canResend) return;
    setCanResend(false);
    setCountdown(30);
    try {
      await api.post('/auth/send-otp', { phone: phoneNumber });
      showToast('success', 'Code Sent', 'A new verification code has been sent to your phone.');
    } catch (err: any) {
      showToast('error', 'Failed', getApiErrorMessage(err, 'Could not resend code. Please try again.'));
    }
  };

  const maskedPhone = phoneNumber
    ? `${phoneNumber.slice(0, -4)}${'•'.repeat(4)}`
    : '';

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        style={styles.scrollFlex}
        contentContainerStyle={[
          styles.scrollContent,
          { flexGrow: 1, paddingTop: insets.top + 16, paddingBottom: insets.bottom + 24 },
        ]}
        keyboardShouldPersistTaps="handled"
        automaticallyAdjustKeyboardInsets={true}
        showsVerticalScrollIndicator={false}
      >
        {/* Back button */}
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>

        {/* Centered content area — keeps the form vertically centered so the
            screen doesn't leave a large empty gap below the resend row. */}
        <View style={styles.centerArea}>
        {/* Header illustration */}
        <View style={styles.illustrationContainer}>
          <View style={styles.illustrationCircle}>
            <View style={styles.illustrationInner}>
              <Ionicons name="shield-checkmark" size={40} color={colors.primary} />
            </View>
          </View>
        </View>

        {/* Title section */}
        <View style={styles.titleSection}>
          <Text style={styles.title}>{t('otp.title')}</Text>
          <Text style={styles.subtitle}>
            {t('otp.subtitle').replace('{{length}}', String(codeLength))}
          </Text>
          <Text style={styles.phoneDisplay}>{phoneNumber}</Text>
        </View>

        {/* Code input area */}
        <Animated.View
          style={[
            styles.codeContainer,
            { transform: [{ translateX: shakeAnim }] },
          ]}
        >
          {/* Hidden actual input */}
          <TextInput
            ref={inputRef}
            style={styles.hiddenInput}
            value={code}
            onChangeText={handleCodeChange}
            keyboardType="phone-pad"
            maxLength={codeLength}
            autoFocus
          />

          {/* Visual code boxes */}
          <TouchableOpacity
            style={styles.codeBoxes}
            activeOpacity={1}
            onPress={() => inputRef.current?.focus()}
          >
            {Array.from({ length: codeLength }).map((_, i) => {
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
                >
                  <Text
                    style={[
                      styles.codeDigit,
                      isFilled && styles.codeDigitFilled,
                    ]}
                  >
                    {code[i] || ''}
                  </Text>
                  {isActive && <View style={styles.cursor} />}
                </Animated.View>
              );
            })}
          </TouchableOpacity>
        </Animated.View>

        {/* Verify Button */}
        <TouchableOpacity
          style={[
            styles.verifyBtn,
            code.length !== codeLength && styles.verifyBtnInactive,
            verifying && styles.verifyBtnLoading,
          ]}
          onPress={handleVerify}
          disabled={verifying || code.length !== codeLength}
          activeOpacity={0.85}
        >
          {verifying ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <View style={styles.verifyBtnContent}>
              <Text
                style={[
                  styles.verifyBtnText,
                  code.length !== codeLength && styles.verifyBtnTextInactive,
                ]}
              >
                {t('otp.verifyAndContinue')}
              </Text>
              <Ionicons
                name="checkmark-circle"
                size={20}
                color={code.length === codeLength ? '#fff' : '#999'}
              />
            </View>
          )}
        </TouchableOpacity>

        {/* Resend section */}
        <View style={styles.resendSection}>
          {canResend ? (
            <TouchableOpacity onPress={handleResend} style={styles.resendBtn}>
              <Ionicons name="refresh" size={16} color={colors.primary} />
              <Text style={styles.resendText}>{t('otp.resendCode')}</Text>
            </TouchableOpacity>
          ) : (
            <View style={styles.resendCountdown}>
              <Ionicons name="time-outline" size={16} color={colors.textDim} />
              <Text style={styles.countdownText}>
                {t('otp.resendIn')} <Text style={styles.countdownNumber}>{countdown}s</Text>
              </Text>
            </View>
          )}

          <TouchableOpacity onPress={() => router.back()} style={styles.changeNumberBtn}>
            <Ionicons name="call-outline" size={14} color={colors.textDim} />
            <Text style={styles.changeNumberText}>{t('otp.changeNumber')}</Text>
          </TouchableOpacity>
        </View>
        </View>
      </ScrollView>

    </KeyboardAvoidingView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
    },
    scrollFlex: {
      flex: 1,
    },
    scrollContent: {
      flexGrow: 1,
      paddingHorizontal: 24,
    },
    backBtn: {
      width: 44,
      height: 44,
      borderRadius: 14,
      backgroundColor: colors.surfaceLight,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 24,
    },
    // Vertically centers the OTP form between the back button and the
    // bottom safe-area so there is no large trailing gap at the page end.
    // flexGrow (not flex) so it grows to center when there is room but never
    // shrinks below its content — otherwise the keyboard would clip the
    // Verify button instead of letting the ScrollView scroll to it.
    centerArea: {
      flexGrow: 1,
      justifyContent: 'center',
    },
    // Illustration
    illustrationContainer: {
      alignItems: 'center',
      marginBottom: 28,
    },
    illustrationCircle: {
      width: 96,
      height: 96,
      borderRadius: 48,
      backgroundColor: `${colors.primary}0A`,
      justifyContent: 'center',
      alignItems: 'center',
    },
    illustrationInner: {
      width: 72,
      height: 72,
      borderRadius: 36,
      backgroundColor: `${colors.primary}14`,
      justifyContent: 'center',
      alignItems: 'center',
    },
    // Title
    titleSection: {
      alignItems: 'center',
      marginBottom: 36,
    },
    title: {
      fontSize: 26,
      fontWeight: '800',
      color: colors.text,
      letterSpacing: -0.5,
      marginBottom: 10,
    },
    subtitle: {
      fontSize: 15,
      color: colors.textDim,
      lineHeight: 22,
    },
    phoneDisplay: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
      marginTop: 4,
    },
    // Code input
    codeContainer: {
      marginBottom: 28,
    },
    hiddenInput: {
      position: 'absolute',
      opacity: 0,
      width: 1,
      height: 1,
    },
    codeBoxes: {
      flexDirection: 'row',
      justifyContent: 'center',
      gap: 12,
    },
    codeBox: {
      width: 56,
      height: 64,
      borderRadius: 16,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1.5,
      borderColor: colors.border,
      justifyContent: 'center',
      alignItems: 'center',
    },
    codeBoxActive: {
      borderColor: colors.primary,
      backgroundColor: colors.surface,
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.12,
      shadowRadius: 8,
      elevation: 3,
    },
    codeBoxFilled: {
      borderColor: colors.primary,
      backgroundColor: `${colors.primary}08`,
    },
    codeDigit: {
      fontSize: 28,
      fontWeight: '800',
      color: colors.textDim,
    },
    codeDigitFilled: {
      color: colors.text,
    },
    cursor: {
      position: 'absolute',
      bottom: 14,
      width: 20,
      height: 2,
      backgroundColor: colors.primary,
      borderRadius: 1,
    },
    // Verify button
    verifyBtn: {
      backgroundColor: colors.primary,
      borderRadius: 16,
      height: 58,
      justifyContent: 'center',
      alignItems: 'center',
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.25,
      shadowRadius: 12,
      elevation: 6,
      marginBottom: 24,
    },
    verifyBtnInactive: {
      backgroundColor: colors.border,
      shadowOpacity: 0,
      elevation: 0,
    },
    verifyBtnLoading: {
      backgroundColor: colors.primaryDark,
    },
    verifyBtnContent: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    verifyBtnText: {
      color: '#fff',
      fontSize: 16,
      fontWeight: '700',
    },
    verifyBtnTextInactive: {
      color: colors.textDim,
    },
    // Resend
    resendSection: {
      alignItems: 'center',
      gap: 16,
    },
    resendBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      paddingVertical: 8,
    },
    resendText: {
      fontSize: 15,
      fontWeight: '600',
      color: colors.primary,
    },
    resendCountdown: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
    },
    countdownText: {
      fontSize: 14,
      color: colors.textDim,
    },
    countdownNumber: {
      fontWeight: '700',
      color: colors.text,
    },
    changeNumberBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      paddingVertical: 8,
    },
    changeNumberText: {
      fontSize: 14,
      color: colors.textDim,
      fontWeight: '500',
    },
  });
}
