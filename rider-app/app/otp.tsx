import React, { useState, useEffect, useRef } from 'react';
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
import { useAuthStore } from '@shared/store/authStore';
import api, { setInMemoryToken } from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';

const THEME = SpinrConfig.theme.colors;

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
  const params = useLocalSearchParams<{ verificationId?: string; phoneNumber: string; mode?: string }>();
  const { phoneNumber, verificationId, mode } = params;
  const isBackendMode = mode === 'backend' || !verificationId;
  // Unified 6-digit OTP across both backend-issued and Firebase Phone Auth
  // flows. Previously the backend-issued code was 4 digits, which was
  // insufficient entropy for phone auth (1/10,000 guess odds per try).
  const codeLength = 6;

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [countdown, setCountdown] = useState(30);
  const [canResend, setCanResend] = useState(false);
  const { verifyOTP, user, initialize, clearError } = useAuthStore();
  const inputRef = useRef<TextInput>(null);
  const resendInFlight = useRef(false);

  const shakeAnim = useRef(new Animated.Value(0)).current;
  // Size to max possible length (6); only `codeLength` entries are rendered.
  const dotAnims = useRef(
    Array.from({ length: 6 }, () => new Animated.Value(0))
  ).current;

  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
  }>({ visible: false, title: '', message: '', variant: 'info' });

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

  useEffect(() => {
    if (countdown > 0) {
      const timer = setTimeout(() => setCountdown(countdown - 1), 1000);
      return () => clearTimeout(timer);
    } else {
      setCanResend(true);
    }
  }, [countdown]);

  // Post-auth routing — preserves rider-app behavior (route to /(tabs)/activity, not /driver)
  useEffect(() => {
    if (user) {
      const hasProfileData = !!(user.first_name && user.last_name && user.email);
      const profileComplete = !!user.profile_complete || hasProfileData;
      if (profileComplete) {
        router.replace('/(tabs)/activity');
      } else {
        router.replace('/profile-setup');
      }
    }
  }, [user]);

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
      setAlertState({
        visible: true,
        title: 'Invalid Code',
        message: `Please enter the ${codeLength}-digit code sent to your phone.`,
        variant: 'warning',
      });
      return;
    }

    setVerifying(true);
    clearError();

    try {
      if (isBackendMode) {
        const response = await api.post('/auth/verify-otp', {
          phone: phoneNumber,
          code: code,
        });
        const { token, user: userData } = response.data;
        if (token) {
          setInMemoryToken(token);
          await storage.setItem('auth_token', token);
          if (userData) {
            useAuthStore.setState({
              user: userData,
              token: token,
              isInitialized: true,
              isLoading: false,
            });
          } else {
            await initialize();
          }
        }
      } else {
        await verifyOTP(verificationId!, code);
      }
    } catch (err: any) {
      triggerShake();
      setCode('');
      const message = err.response?.data?.detail || err.message || 'Invalid code. Please try again.';
      setAlertState({
        visible: true,
        title: 'Verification Failed',
        message,
        variant: 'danger',
      });
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
      setAlertState({
        visible: true,
        title: 'Code Sent',
        message: 'A new verification code has been sent to your phone.',
        variant: 'success',
      });
    } catch (e) {
      console.log('[Auth] resend failed', e);
      setAlertState({
        visible: true,
        title: 'Failed',
        message: 'Could not resend code. Please try again.',
        variant: 'danger',
      });
    } finally {
      resendInFlight.current = false;
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        contentContainerStyle={[
          styles.scrollContent,
          { paddingTop: insets.top + 16, paddingBottom: insets.bottom + 24 },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={THEME.text} />
        </TouchableOpacity>

        <View style={styles.illustrationContainer}>
          <View style={styles.illustrationCircle}>
            <View style={styles.illustrationInner}>
              <Ionicons name="shield-checkmark" size={40} color={THEME.primary} />
            </View>
          </View>
        </View>

        <View style={styles.titleSection}>
          <Text style={styles.title}>Verify Your Number</Text>
          <Text style={styles.subtitle}>
            We sent a {codeLength}-digit code to
          </Text>
          <Text style={styles.phoneDisplay}>{phoneNumber}</Text>
        </View>

        <Animated.View
          style={[styles.codeContainer, { transform: [{ translateX: shakeAnim }] }]}
        >
          <TextInput
            ref={inputRef}
            style={styles.hiddenInput}
            value={code}
            onChangeText={handleCodeChange}
            keyboardType="phone-pad"
            maxLength={codeLength}
            autoFocus
          />

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
                Verify & Continue
              </Text>
              <Ionicons
                name="checkmark-circle"
                size={20}
                color={code.length === codeLength ? '#fff' : '#999'}
              />
            </View>
          )}
        </TouchableOpacity>

        <View style={styles.resendSection}>
          {canResend ? (
            <TouchableOpacity onPress={handleResend} style={styles.resendBtn}>
              <Ionicons name="refresh" size={16} color={THEME.primary} />
              <Text style={styles.resendText}>Resend Code</Text>
            </TouchableOpacity>
          ) : (
            <View style={styles.resendCountdown}>
              <Ionicons name="time-outline" size={16} color={THEME.textDim} />
              <Text style={styles.countdownText}>
                Resend code in <Text style={styles.countdownNumber}>{countdown}s</Text>
              </Text>
            </View>
          )}

          <TouchableOpacity onPress={() => router.back()} style={styles.changeNumberBtn}>
            <Ionicons name="call-outline" size={14} color={THEME.textDim} />
            <Text style={styles.changeNumberText}>Change phone number</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={[{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  scrollContent: { flexGrow: 1, paddingHorizontal: 24 },
  backBtn: {
    width: 44, height: 44, borderRadius: 14,
    backgroundColor: '#F5F5F5',
    justifyContent: 'center', alignItems: 'center', marginBottom: 24,
  },
  illustrationContainer: { alignItems: 'center', marginBottom: 28 },
  illustrationCircle: {
    width: 96, height: 96, borderRadius: 48,
    backgroundColor: `${THEME.primary}0A`,
    justifyContent: 'center', alignItems: 'center',
  },
  illustrationInner: {
    width: 72, height: 72, borderRadius: 36,
    backgroundColor: `${THEME.primary}14`,
    justifyContent: 'center', alignItems: 'center',
  },
  titleSection: { alignItems: 'center', marginBottom: 36 },
  title: {
    fontSize: 26, fontWeight: '800', color: THEME.text,
    letterSpacing: -0.5, marginBottom: 10,
  },
  subtitle: { fontSize: 15, color: THEME.textDim, lineHeight: 22 },
  phoneDisplay: { fontSize: 17, fontWeight: '700', color: THEME.text, marginTop: 4 },
  codeContainer: { marginBottom: 28 },
  hiddenInput: { position: 'absolute', opacity: 0, width: 1, height: 1 },
  codeBoxes: { flexDirection: 'row', justifyContent: 'center', gap: 12 },
  codeBox: {
    width: 56, height: 64, borderRadius: 16,
    backgroundColor: '#F8F9FA',
    borderWidth: 1.5, borderColor: '#F0F0F0',
    justifyContent: 'center', alignItems: 'center',
  },
  codeBoxActive: {
    borderColor: THEME.primary, backgroundColor: '#fff',
    shadowColor: THEME.primary, shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.12, shadowRadius: 8, elevation: 3,
  },
  codeBoxFilled: {
    borderColor: THEME.primary,
    backgroundColor: `${THEME.primary}08`,
  },
  codeDigit: { fontSize: 28, fontWeight: '800', color: THEME.textDim },
  codeDigitFilled: { color: THEME.text },
  cursor: {
    position: 'absolute', bottom: 14,
    width: 20, height: 2,
    backgroundColor: THEME.primary, borderRadius: 1,
  },
  verifyBtn: {
    backgroundColor: THEME.primary, borderRadius: 16, height: 58,
    justifyContent: 'center', alignItems: 'center',
    shadowColor: THEME.primary, shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.25, shadowRadius: 12, elevation: 6, marginBottom: 24,
  },
  verifyBtnInactive: { backgroundColor: '#F0F0F0', shadowOpacity: 0, elevation: 0 },
  verifyBtnLoading: { backgroundColor: THEME.primaryDark },
  verifyBtnContent: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  verifyBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  verifyBtnTextInactive: { color: '#999' },
  resendSection: { alignItems: 'center', gap: 16 },
  resendBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8 },
  resendText: { fontSize: 15, fontWeight: '600', color: THEME.primary },
  resendCountdown: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  countdownText: { fontSize: 14, color: THEME.textDim },
  countdownNumber: { fontWeight: '700', color: THEME.text },
  changeNumberBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8 },
  changeNumberText: { fontSize: 14, color: THEME.textDim, fontWeight: '500' },
});
