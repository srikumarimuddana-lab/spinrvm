import React, { useState, useRef, useMemo, useCallback } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  ScrollView,
  Image,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter, useFocusEffect } from 'expo-router';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useAuthStore } from '@shared/store/authStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function LoginScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [phoneNumber, setPhoneNumber] = useState('');
  const [loading, setLoading] = useState(false);
  const [focused, setFocused] = useState(false);
  // Explicit, unchecked-by-default consent gesture — the account-creation
  // endpoint (POST /auth/verify-otp, called from otp.tsx) now rejects a
  // brand-new signup unless this was actively checked. Previously this
  // screen only showed passive "by continuing you agree" text with no
  // tappable action behind it. See
  // docs/change-log/2026-08-20-explicit-signup-consent-checkbox.md.
  const [consentAccepted, setConsentAccepted] = useState(false);
  const inputRef = useRef<TextInput>(null);
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Login stays in the stack below /(tabs) after sign-in (we push /otp so the
  // user can swipe back to change their number). Without this guard, an iOS
  // edge-swipe from the tabs pops back to this screen. When login regains focus
  // while already authenticated, bounce forward to the app.
  useFocusEffect(
    useCallback(() => {
      if (useAuthStore.getState().token) {
        router.replace('/(tabs)' as any);
      }
    }, [router]),
  );

  const formatPhoneDisplay = (raw: string) => {
    const digits = raw.replace(/\D/g, '');
    if (digits.length <= 3) return digits;
    if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6, 10)}`;
  };

  const handlePhoneChange = (text: string) => {
    const digits = text.replace(/\D/g, '');
    if (digits.length <= 10) setPhoneNumber(digits);
  };

  const handleSendCode = async () => {
    if (!isValid || !consentAccepted || loading) return;
    setLoading(true);
    const formattedNumber = `+1${phoneNumber}`;
    try {
      const response = await api.post<{ success?: boolean }>('/auth/send-otp', { phone: formattedNumber });
      if (response.data.success) {
        // Carried to otp.tsx so its POST /auth/verify-otp call can send
        // consent_accepted — the account isn't created here, it's created
        // (or not, if this checkbox was unchecked) on that later call.
        router.push({
          pathname: '/otp',
          params: { phoneNumber: formattedNumber, consentAccepted: String(consentAccepted) },
        } as any);
      } else {
        showToast('Code Not Sent', 'Could not send verification code. Please try again.', 'danger');
      }
    } catch (error: any) {
      // If the server answered (e.g. 503 "Verification is temporarily
      // unavailable", or a 429 lockout — thrown as RateLimitError, which has
      // no .response), show its reason. Only a genuinely silent failure gets
      // labelled a connectivity problem, or users report "can't reach server"
      // while the backend is up and saying exactly what's wrong.
      const serverMessage = getApiErrorMessage(error, '');
      if (serverMessage) {
        showToast('Sign-in Unavailable', serverMessage, 'danger');
      } else {
        showToast('Connection Error', 'Unable to reach server. Please check your connection.', 'danger');
      }
    } finally {
      setLoading(false);
    }
  };

  const isValid = phoneNumber.length === 10;
  const canContinue = isValid && consentAccepted;

  return (
    <KeyboardAvoidingView
      // "padding" works off JS keyboard events on both platforms, so it lifts
      // the input + button above the keyboard regardless of the native
      // windowSoftInputMode (works on OTA-only builds, no rebuild needed).
      // Avoid Android "height" (phantom gap on dismiss) and "undefined" (a
      // no-op unless the native build is adjustResize).
      behavior="padding"
      style={styles.container}
    >
      {/* Body is wrapped in a ScrollView with a flexGrow:1 content container so
          the brand row / welcome / input / button / terms center nicely when
          there's spare height, but scroll instead of overflowing when height is
          tight (small devices, or the keyboard raised). Without this, the
          centered `content` block can't shrink and spills over the fixed brand
          row and terms — the overlap seen on smaller iPhones. Mirrors otp.tsx. */}
      <ScrollView
        style={styles.container}
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
        bounces={false}
      >
      <View style={[styles.topStrip, { paddingTop: insets.top }]}>
        <View style={styles.brandRow}>
          {/* Spinr wordmark. The PNG is a dark logo on a transparent
              background, so tint it white in dark mode to stay visible on the
              dark surface. */}
          <Image
            source={require('../assets/images/spinr-logo.png')}
            style={[styles.brandLogo, isDark && styles.brandLogoDark]}
            resizeMode="contain"
            accessibilityRole="image"
            accessibilityLabel="Spinr"
          />
          <View style={styles.riderBadge}>
            <Text style={styles.riderBadgeText}>Rider</Text>
          </View>
        </View>
      </View>

      <View style={styles.content}>
        <View style={styles.welcomeSection}>
          <Text style={styles.greeting}>Welcome back</Text>
          <Text style={styles.title}>Enter your phone number</Text>
          <Text style={styles.subtitle}>
            We&apos;ll send you a verification code to confirm your identity
          </Text>
        </View>

        <View style={styles.inputSection}>
          <Text style={styles.inputLabel}>PHONE NUMBER</Text>
          <TouchableOpacity
            activeOpacity={1}
            onPress={() => inputRef.current?.focus()}
            style={[styles.inputContainer, focused && styles.inputContainerFocused]}
            accessible={false}
          >
            <View style={styles.flagContainer} accessibilityLabel="Canada +1">
              <Text style={styles.flagEmoji}>🇨🇦</Text>
              <Text style={styles.countryCode}>+1</Text>
              <Ionicons name="chevron-down" size={14} color={colors.textDim} />
            </View>
            <View style={styles.inputDivider} />
            <TextInput
              ref={inputRef}
              style={styles.input}
              placeholder="(306) 555-0199"
              placeholderTextColor="#C4C4C4"
              keyboardType="phone-pad"
              value={formatPhoneDisplay(phoneNumber)}
              onChangeText={handlePhoneChange}
              maxLength={14}
              editable={!loading}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              accessibilityLabel="Phone number"
              accessibilityHint="Enter your 10-digit Canadian phone number"
            />
            {isValid && (
              <View style={styles.checkIcon}>
                <Ionicons name="checkmark-circle" size={22} color={colors.success} />
              </View>
            )}
          </TouchableOpacity>

          {__DEV__ && (
            <View style={styles.devHintContainer}>
              <Ionicons name="information-circle" size={14} color={colors.primary} />
              <Text style={styles.devHint}>Dev mode — OTP is 1234</Text>
            </View>
          )}
        </View>

        <TouchableOpacity
          style={[styles.button, !canContinue && styles.buttonInactive, loading && styles.buttonLoading]}
          onPress={handleSendCode}
          disabled={loading || !canContinue}
          activeOpacity={0.85}
          accessibilityLabel="Send verification code"
          accessibilityState={{ disabled: loading || !canContinue }}
        >
          {loading ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <View style={styles.buttonContent}>
              <Text style={[styles.buttonText, !canContinue && styles.buttonTextInactive]}>
                Send Verification Code
              </Text>
              <Ionicons name="arrow-forward" size={20} color={canContinue ? '#fff' : colors.textDim} />
            </View>
          )}
        </TouchableOpacity>

        <View style={styles.footer}>
          <Ionicons name="lock-closed" size={14} color={colors.textDim} />
          <Text style={styles.footerText}>
            Your number is secured and only used for verification
          </Text>
        </View>
      </View>

      <View style={[styles.terms, { paddingBottom: insets.bottom + 16 }]}>
        {/* Explicit, unchecked-by-default consent checkbox — replaces the
            old passive "by continuing you agree" text, which had no
            tappable action or opt-in gesture behind it. Icon (not color
            alone) signals checked state for WCAG 2.1 AA. */}
        <TouchableOpacity
          style={styles.consentRow}
          onPress={() => setConsentAccepted((c) => !c)}
          activeOpacity={0.7}
          disabled={loading}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: consentAccepted, disabled: loading }}
          accessibilityLabel="I agree to Spinr's Terms of Service and Privacy Policy"
        >
          <Ionicons
            name={consentAccepted ? 'checkbox' : 'square-outline'}
            size={22}
            color={consentAccepted ? colors.primary : colors.textDim}
          />
          <Text style={styles.termsText}>
            I agree to Spinr&apos;s{' '}
            <Text
              style={styles.termsLink}
              onPress={() => router.push({ pathname: '/legal', params: { type: 'tos' } } as any)}
              accessibilityRole="link"
            >
              Terms of Service
            </Text>
            {' '}and{' '}
            <Text
              style={styles.termsLink}
              onPress={() => router.push({ pathname: '/legal', params: { type: 'privacy' } } as any)}
              accessibilityRole="link"
            >
              Privacy Policy
            </Text>
          </Text>
        </TouchableOpacity>
      </View>
      </ScrollView>

    </KeyboardAvoidingView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    // flexGrow (not flex) so the ScrollView fills the screen but grows past it
    // to stay scrollable when content exceeds the viewport on small screens.
    scrollContent: { flexGrow: 1 },
    topStrip: { backgroundColor: colors.surface, paddingHorizontal: 24, paddingBottom: 8 },
    brandRow: { flexDirection: 'row', alignItems: 'center', paddingTop: 12, gap: 10 },
    // 384:156 native ratio → 96:39 keeps the wordmark crisp.
    brandLogo: { width: 96, height: 39 },
    brandLogoDark: { tintColor: '#fff' },
    riderBadge: {
      backgroundColor: `${colors.primary}14`,
      paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8,
    },
    riderBadgeText: { fontSize: 12, fontWeight: '700', color: colors.primary },
    content: { flexGrow: 1, paddingHorizontal: 24, justifyContent: 'center' },
    welcomeSection: { marginBottom: 36 },
    greeting: { fontSize: 16, color: colors.textDim, marginBottom: 8, fontWeight: '500' },
    title: {
      fontSize: 28, fontWeight: '800', color: colors.text,
      letterSpacing: -0.5, marginBottom: 8,
    },
    subtitle: { fontSize: 15, color: colors.textDim, lineHeight: 22 },
    inputSection: { marginBottom: 24 },
    inputLabel: {
      fontSize: 11, fontWeight: '700', color: colors.textDim,
      letterSpacing: 1, marginBottom: 8,
    },
    inputContainer: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight, borderRadius: 16,
      height: 60, borderWidth: 1.5, borderColor: colors.border,
    },
    inputContainerFocused: {
      borderColor: colors.primary, backgroundColor: colors.surface,
      shadowColor: colors.primary, shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.1, shadowRadius: 8, elevation: 3,
    },
    flagContainer: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, gap: 6 },
    flagEmoji: { fontSize: 20 },
    countryCode: { fontSize: 16, fontWeight: '600', color: colors.text },
    inputDivider: { width: 1, height: 28, backgroundColor: colors.border },
    input: {
      flex: 1, paddingHorizontal: 14, fontSize: 18,
      fontWeight: '600', color: colors.text, height: '100%' as any, letterSpacing: 0.5,
    },
    checkIcon: { paddingRight: 14 },
    devHintContainer: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      marginTop: 10, paddingHorizontal: 4,
    },
    devHint: { fontSize: 13, color: colors.primary, fontWeight: '500' },
    button: {
      backgroundColor: colors.primary, borderRadius: 16, height: 58,
      justifyContent: 'center', alignItems: 'center',
      shadowColor: colors.primary, shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.25, shadowRadius: 12, elevation: 6, marginBottom: 20,
    },
    buttonInactive: { backgroundColor: colors.border, shadowOpacity: 0, elevation: 0 },
    buttonLoading: { backgroundColor: colors.primaryDark },
    buttonContent: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    buttonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
    buttonTextInactive: { color: colors.textDim },
    footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
    footerText: { fontSize: 12, color: colors.textDim, flex: 1 },
    terms: { paddingHorizontal: 24, alignItems: 'center' },
    consentRow: {
      flexDirection: 'row', alignItems: 'flex-start', gap: 10,
      minHeight: 44, paddingVertical: 4,
    },
    termsText: { flex: 1, fontSize: 12, color: '#B0B0B0', lineHeight: 18, paddingTop: 3 },
    termsLink: { color: colors.primary, fontWeight: '600' },
  });
}
