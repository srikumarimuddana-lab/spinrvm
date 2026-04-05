# Rider-App UI Richness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every rider-app screen up to the visual richness of the driver-app by rewriting the two bare auth screens and applying a consistency audit to all remaining rider-app screens.

**Architecture:** Phase 1 rewrites `login.tsx` and `otp.tsx` with full code mirroring `driver-app/app/login.tsx` and `driver-app/app/otp.tsx`, then audits `profile-setup.tsx`. Phases 2 & 3 run a mechanical per-file audit against a fixed checklist — no new components, no shared-library changes, in-file edits only.

**Tech Stack:** React Native + Expo Router, `SpinrConfig` theme tokens, `@shared/components/CustomAlert`, `@expo/vector-icons` (Ionicons), `react-native-safe-area-context`.

**Spec:** `docs/superpowers/specs/2026-04-05-rider-app-ui-richness-design.md`

**Verification environment note:** The project root `C:/Users/swarn/Documents/SpinrApp` is not currently a git repository. Commit steps below assume one of: (a) you init a repo in `spinr/` before starting, or (b) you skip the `git commit` steps and rely on the file-save as the stopping point. If no git is set up, treat every "Commit" step as "verify files saved and proceed."

---

## Shared Reference: The Design Language Checklist

Every Phase 2 and Phase 3 task checks the file it touches against this list. Read once, apply many.

**§C1 — Color discipline**
- No hardcoded `#000` as text/fill (except intentional pure-black UI like map route lines).
- No hardcoded primary-family colors (`#FF3B30`, `#007AFF`, `#D32F2F`) — use `THEME.primary`, `THEME.primaryDark`.
- Neutral greys (`#F0F0F0`, `#F8F9FA`, `#F9F9F9`, `#E5E5E5`, `#999`, `#CCC`, `#fff`) are allowed.
- Import `THEME` via `const THEME = SpinrConfig.theme.colors;` (or `const COLORS = SpinrConfig.theme.colors;` where file convention already uses `COLORS`).

**§C2 — Alerts**
- No `import { Alert }` / `Alert.alert` calls. Replace with `CustomAlert` + local `alertState` pattern (see Phase 1 Task 1 Step 3 for canonical form).

**§C3 — Safe area**
- File uses `useSafeAreaInsets()` or `SafeAreaView` from `react-native-safe-area-context`. Never `SafeAreaView` from `react-native`.

**§C4 — Stack header (non-tab screens with a back button)**
- 44×44 back button with `Ionicons name="arrow-back" size={24}`.
- Centered title: `{fontSize: 18, fontWeight: '700', color: THEME.text}`.
- Symmetric right spacer (`width: 44`).
- Bottom hairline: `borderBottomWidth: 1, borderBottomColor: '#F0F0F0'`.

**§C5 — Primary CTA buttons**
- `backgroundColor: THEME.primary`, `borderRadius: 16`, `height: 58`, colored shadow `{shadowColor: THEME.primary, shadowOffset: {width: 0, height: 6}, shadowOpacity: 0.25, shadowRadius: 12, elevation: 6}`.
- Content: label (16px, 700, `#fff`) + optional `Ionicons name="arrow-forward" size={20}` with `gap: 8`.
- Disabled: `backgroundColor: '#F0F0F0'`, `shadowOpacity: 0`, `elevation: 0`, text color `#999`.
- Loading: `backgroundColor: THEME.primaryDark`, `<ActivityIndicator color="#fff" />`.

**§C6 — Input fields**
- Wrapper: `backgroundColor: '#F8F9FA'`, `borderRadius: 16`, `height: 60`, `borderWidth: 1.5`, `borderColor: '#F0F0F0'`.
- Focus: border → `THEME.primary`, background → `#fff`, shadow `{color: primary, offset: {0,0}, opacity: 0.1, radius: 8, elevation: 3}`.
- Uppercase label above: `{fontSize: 11, fontWeight: '700', color: THEME.textDim, letterSpacing: 1, marginBottom: 8}`.
- Input text: `{fontSize: 18, fontWeight: '600', color: THEME.text}`.

**§C7 — Card / row**
- Card: `backgroundColor: '#F9F9F9'` or `'#F8F9FA'`, `borderRadius: 16`, 14px horizontal padding.
- Row: flex row, `paddingVertical: 14`, 1px `#F0F0F0` bottom border, 40×40 icon tile (radius 12, tinted bg), title 15/600 + subtitle 12/`#999`, trailing `chevron-forward`.

**§C8 — Typography scale**
- Screen title: `{fontSize: 28, fontWeight: '800', color: THEME.text, letterSpacing: -0.5}` (auth/welcome only) or `{fontSize: 26, fontWeight: '800'}` (dense screens).
- Section label (all caps): `{fontSize: 11-13, fontWeight: '700', color: THEME.textDim, letterSpacing: 0.5-1}`.
- Body: 15px, line-height 22.
- Micro: 12px.

A file "passes" when every applicable item above is satisfied. If an item doesn't apply (e.g. no inputs on this screen), skip it.

---

## Phase 1 — Auth Flow Rewrites

### Task 1: Rewrite `rider-app/app/login.tsx`

**Files:**
- Modify: `spinr/rider-app/app/login.tsx` (full rewrite, 121 → ~270 lines)

**Behavior to preserve from existing file:**
- The `useFocusEffect` logout-on-reentry (lines 13–22 of current file) — if a user swipes back from profile-setup, partial auth state must be cleared so the next phone number works.
- The backend-mode-only OTP flow (rider-app does not have Firebase phone auth wired; it always posts to `/auth/send-otp` and routes with `mode: 'backend'`).
- Error handling via `error.response?.data?.detail || error.message`.

- [ ] **Step 1: Read the reference driver file for context**

Run: `cat spinr/driver-app/app/login.tsx | head -260`
Expected: file exists, you can see the `THEME`, `CustomAlert`, `inputRef`, `formatPhoneDisplay`, `handleSendCode`, and the full `styles` block. This is your visual target.

- [ ] **Step 2: Replace the full contents of `rider-app/app/login.tsx`**

Exact new file contents:

```tsx
import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StatusBar,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter, useFocusEffect } from 'expo-router';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';
import { useAuthStore } from '@shared/store/authStore';

const THEME = SpinrConfig.theme.colors;

export default function LoginScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [phoneNumber, setPhoneNumber] = useState('');
  const [loading, setLoading] = useState(false);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<TextInput>(null);
  const { user, logout } = useAuthStore();

  // Clear partial auth state if the user swiped back from profile-setup.
  // Without this, a stale `user` blocks a fresh phone-number entry.
  useFocusEffect(
    React.useCallback(() => {
      if (user) {
        logout();
      }
    }, [user, logout])
  );

  // Alert state
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const formatPhoneDisplay = (raw: string) => {
    const digits = raw.replace(/\D/g, '');
    if (digits.length <= 3) return digits;
    if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6, 10)}`;
  };

  const handlePhoneChange = (text: string) => {
    const digits = text.replace(/\D/g, '');
    if (digits.length <= 10) {
      setPhoneNumber(digits);
    }
  };

  const handleSendCode = async () => {
    if (!phoneNumber || phoneNumber.length < 10) {
      setAlertState({
        visible: true,
        title: 'Invalid Number',
        message: 'Please enter a valid 10-digit phone number.',
        variant: 'warning',
      });
      return;
    }

    setLoading(true);
    const formattedNumber = `+1${phoneNumber.replace(/\D/g, '')}`;

    try {
      const response = await api.post('/auth/send-otp', { phone: formattedNumber });
      if (response.data.success) {
        router.push({
          pathname: '/otp',
          params: { phoneNumber: formattedNumber, mode: 'backend' },
        });
      } else {
        setAlertState({
          visible: true,
          title: 'Failed',
          message: 'Could not send verification code. Please try again.',
          variant: 'danger',
        });
      }
    } catch (error: any) {
      setAlertState({
        visible: true,
        title: 'Connection Error',
        message: error.response?.data?.detail || error.message || 'Unable to reach server. Please check your connection.',
        variant: 'danger',
      });
    } finally {
      setLoading(false);
    }
  };

  const isValid = phoneNumber.length === 10;

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      style={styles.container}
    >
      <StatusBar barStyle="dark-content" />

      {/* Top accent strip */}
      <View style={[styles.topStrip, { paddingTop: insets.top }]}>
        <View style={styles.brandRow}>
          <View style={styles.logoCircle}>
            <Ionicons name="car-sport" size={24} color="#fff" />
          </View>
          <Text style={styles.brandName}>Spinr</Text>
          <View style={styles.riderBadge}>
            <Text style={styles.riderBadgeText}>Rider</Text>
          </View>
        </View>
      </View>

      {/* Main content */}
      <View style={styles.content}>
        <View style={styles.welcomeSection}>
          <Text style={styles.greeting}>Welcome back 👋</Text>
          <Text style={styles.title}>Enter your phone number</Text>
          <Text style={styles.subtitle}>
            We'll send you a verification code to confirm your identity
          </Text>
        </View>

        {/* Phone Input */}
        <View style={styles.inputSection}>
          <Text style={styles.inputLabel}>PHONE NUMBER</Text>
          <TouchableOpacity
            activeOpacity={1}
            onPress={() => inputRef.current?.focus()}
            style={[
              styles.inputContainer,
              focused && styles.inputContainerFocused,
            ]}
          >
            <View style={styles.flagContainer}>
              <Text style={styles.flagEmoji}>🇨🇦</Text>
              <Text style={styles.countryCode}>+1</Text>
              <Ionicons name="chevron-down" size={14} color={THEME.textDim} />
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
            />
            {isValid && (
              <View style={styles.checkIcon}>
                <Ionicons name="checkmark-circle" size={22} color={THEME.success} />
              </View>
            )}
          </TouchableOpacity>

          <View style={styles.devHintContainer}>
            <Ionicons name="information-circle" size={14} color={THEME.primary} />
            <Text style={styles.devHint}>Dev mode — OTP is 1234</Text>
          </View>
        </View>

        {/* Continue Button */}
        <TouchableOpacity
          style={[
            styles.button,
            !isValid && styles.buttonInactive,
            loading && styles.buttonLoading,
          ]}
          onPress={handleSendCode}
          disabled={loading || !isValid}
          activeOpacity={0.85}
        >
          {loading ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <View style={styles.buttonContent}>
              <Text style={[styles.buttonText, !isValid && styles.buttonTextInactive]}>
                Send Verification Code
              </Text>
              <Ionicons
                name="arrow-forward"
                size={20}
                color={isValid ? '#fff' : '#999'}
              />
            </View>
          )}
        </TouchableOpacity>

        <View style={styles.footer}>
          <Ionicons name="lock-closed" size={14} color={THEME.textDim} />
          <Text style={styles.footerText}>
            Your number is secured and only used for verification
          </Text>
        </View>
      </View>

      {/* Terms */}
      <View style={[styles.terms, { paddingBottom: insets.bottom + 16 }]}>
        <Text style={styles.termsText}>
          By continuing, you agree to our{' '}
          <Text style={styles.termsLink}>Terms of Service</Text>
          {' '}and{' '}
          <Text style={styles.termsLink}>Privacy Policy</Text>
        </Text>
      </View>

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
  topStrip: { backgroundColor: '#fff', paddingHorizontal: 24, paddingBottom: 8 },
  brandRow: { flexDirection: 'row', alignItems: 'center', paddingTop: 12, gap: 10 },
  logoCircle: {
    width: 42, height: 42, borderRadius: 14,
    backgroundColor: THEME.primary,
    justifyContent: 'center', alignItems: 'center',
  },
  brandName: { fontSize: 24, fontWeight: '800', color: THEME.text, letterSpacing: -0.5 },
  riderBadge: {
    backgroundColor: `${THEME.primary}14`,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8,
  },
  riderBadgeText: { fontSize: 12, fontWeight: '700', color: THEME.primary },
  content: { flex: 1, paddingHorizontal: 24, justifyContent: 'center' },
  welcomeSection: { marginBottom: 36 },
  greeting: { fontSize: 16, color: THEME.textDim, marginBottom: 8, fontWeight: '500' },
  title: { fontSize: 28, fontWeight: '800', color: THEME.text, letterSpacing: -0.5, marginBottom: 8 },
  subtitle: { fontSize: 15, color: THEME.textDim, lineHeight: 22 },
  inputSection: { marginBottom: 24 },
  inputLabel: {
    fontSize: 11, fontWeight: '700', color: THEME.textDim,
    letterSpacing: 1, marginBottom: 8,
  },
  inputContainer: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#F8F9FA', borderRadius: 16,
    height: 60, borderWidth: 1.5, borderColor: '#F0F0F0',
  },
  inputContainerFocused: {
    borderColor: THEME.primary, backgroundColor: '#fff',
    shadowColor: THEME.primary, shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.1, shadowRadius: 8, elevation: 3,
  },
  flagContainer: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, gap: 6 },
  flagEmoji: { fontSize: 20 },
  countryCode: { fontSize: 16, fontWeight: '600', color: THEME.text },
  inputDivider: { width: 1, height: 28, backgroundColor: '#E0E0E0' },
  input: {
    flex: 1, paddingHorizontal: 14, fontSize: 18,
    fontWeight: '600', color: THEME.text, height: '100%', letterSpacing: 0.5,
  },
  checkIcon: { paddingRight: 14 },
  devHintContainer: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    marginTop: 10, paddingHorizontal: 4,
  },
  devHint: { fontSize: 13, color: THEME.primary, fontWeight: '500' },
  button: {
    backgroundColor: THEME.primary, borderRadius: 16, height: 58,
    justifyContent: 'center', alignItems: 'center',
    shadowColor: THEME.primary, shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.25, shadowRadius: 12, elevation: 6, marginBottom: 20,
  },
  buttonInactive: { backgroundColor: '#F0F0F0', shadowOpacity: 0, elevation: 0 },
  buttonLoading: { backgroundColor: THEME.primaryDark },
  buttonContent: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  buttonTextInactive: { color: '#999' },
  footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
  footerText: { fontSize: 12, color: THEME.textDim },
  terms: { paddingHorizontal: 24, alignItems: 'center' },
  termsText: { fontSize: 12, color: '#B0B0B0', textAlign: 'center', lineHeight: 18 },
  termsLink: { color: THEME.primary, fontWeight: '600' },
});
```

- [ ] **Step 3: Type-check**

Run: `cd spinr/rider-app && npx tsc --noEmit 2>&1 | grep "login.tsx"`
Expected: no output (or no errors related to `login.tsx`).

If `THEME.success` errors out: check `spinr/shared/config/spinr.config.ts` for the `success` color key. If missing, replace `THEME.success` with `'#34C759'` in the file.

- [ ] **Step 4: Visual smoke-test**

Run: `cd spinr/rider-app && npx expo start`
- Open app on simulator or device.
- You should see the branded top strip (red logo circle, "Spinr", "Rider" badge), the welcome greeting, the labeled phone input, and the shadowed red CTA.
- Tap the input: border should turn red and the background should become white with a subtle glow.
- Type a 10-digit number: the checkmark should appear inline and the CTA should activate.
- Tap Send: should navigate to `/otp` with a valid `phoneNumber` param.

- [ ] **Step 5: Commit**

```bash
cd spinr && git add rider-app/app/login.tsx && git commit -m "feat(rider-app): rich login screen matching driver-app"
```

(If no git repo, skip the git command and continue.)

---

### Task 2: Rewrite `rider-app/app/otp.tsx`

**Files:**
- Modify: `spinr/rider-app/app/otp.tsx` (full rewrite, 151 → ~480 lines)

**Behavior to preserve from existing file:**
- Dual-mode: `isBackendMode = mode === 'backend' || !verificationId` with `codeLength = isBackendMode ? 4 : 6` (lines 29–30).
- Post-auth routing effect (lines 36–46): if `user` exists, check `hasProfileData = !!(user.first_name && user.last_name && user.email)`, then route to `/(tabs)/activity` if profile complete else `/profile-setup`.
- Backend verify: POST `/auth/verify-otp` with `{phone, code}`, store returned `token` via the `storage` helper, then `initialize()`.
- Firebase fallback: `verifyOTP(verificationId!, code)` from `useAuthStore`.

**Note on `setInMemoryToken`:** Confirmed exported from `spinr/shared/api/client.ts` line 49. Safe to import.

- [ ] **Step 1: Replace the full contents of `rider-app/app/otp.tsx`**

Exact new file contents:

```tsx
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
  const codeLength = isBackendMode ? 4 : 6;

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [hasAttemptedVerification, setHasAttemptedVerification] = useState(false);
  const [countdown, setCountdown] = useState(30);
  const [canResend, setCanResend] = useState(false);
  const { verifyOTP, user, initialize, clearError } = useAuthStore();
  const inputRef = useRef<TextInput>(null);

  const shakeAnim = useRef(new Animated.Value(0)).current;
  const dotAnims = useRef(
    Array.from({ length: codeLength }, () => new Animated.Value(0))
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

  // Post-auth routing (preserves rider-app behavior)
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
    setHasAttemptedVerification(true);
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
    if (!canResend) return;
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
    } catch {
      setAlertState({
        visible: true,
        title: 'Failed',
        message: 'Could not resend code. Please try again.',
        variant: 'danger',
      });
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
```

- [ ] **Step 2: Type-check**

Run: `cd spinr/rider-app && npx tsc --noEmit 2>&1 | grep "otp.tsx"`
Expected: no output.

- [ ] **Step 3: End-to-end auth walkthrough**

Run: `cd spinr/rider-app && npx expo start`
Then on a simulator/device:
1. Start from a signed-out state (if you're signed in, clear storage or sign out first).
2. From login, enter a 10-digit phone, tap Send.
3. You should arrive at the new OTP screen with the red shield illustration, centered title, and 4 boxed code inputs (backend mode = 4 digits).
4. Type `1234` — the boxes should fill with an animated scale, the Verify CTA should activate.
5. Tap Verify — you should route to `/(tabs)/activity` if profile is complete, or `/profile-setup` if not.
6. Type a wrong code: expect shake animation + red `CustomAlert`.
7. Wait 30s: "Resend Code" should appear in place of countdown.
8. Tap "Change phone number": returns to login.

- [ ] **Step 4: Commit**

```bash
cd spinr && git add rider-app/app/otp.tsx && git commit -m "feat(rider-app): rich OTP screen matching driver-app"
```

---

### Task 3: Audit `rider-app/app/profile-setup.tsx`

**Files:**
- Modify: `spinr/rider-app/app/profile-setup.tsx` (targeted edits only)

- [ ] **Step 1: Read the current file**

Run: `cat spinr/rider-app/app/profile-setup.tsx | head -200; echo ...; tail -80 spinr/rider-app/app/profile-setup.tsx`
Hold it in context.

- [ ] **Step 2: Grep for violations**

Run these three greps and note every hit:

```bash
cd spinr/rider-app/app && grep -n "Alert\.alert\|from 'react-native'.*Alert\|import.*Alert.*react-native" profile-setup.tsx
grep -nE "#000[^0-9a-fA-F]|#007AFF|#FF3B30" profile-setup.tsx
grep -n "SafeAreaView.*'react-native'\b" profile-setup.tsx
```

- [ ] **Step 3: Walk the checklist**

For `profile-setup.tsx`, run through §C1–§C8 from the Shared Reference at the top of this plan. For every failure found:
- Replace `Alert.alert(title, message)` with a `CustomAlert` + `alertState` pattern (canonical form in Task 1's file, lines 47–54 and the `setAlertState` calls).
- Replace primary-family hex literals with `THEME.primary` / `THEME.primaryDark`. Add `import SpinrConfig from '@shared/config/spinr.config'; const THEME = SpinrConfig.theme.colors;` if not already present.
- Swap `SafeAreaView` from `react-native` for `useSafeAreaInsets` or `SafeAreaView` from `react-native-safe-area-context`.
- If there's a stack header, verify it matches §C4.
- If there's a primary CTA, verify it matches §C5 (radius 16, colored shadow, icon pairing, disabled/loading).
- If there are inputs, verify they match §C6 (F8F9FA bg, 16 radius, 60 height, focus state).
- Typography: verify title is 26–28/800 letter-spacing −0.5; section labels 11–13/700 tracked.

Make only the edits needed to pass the checklist. **Do not rewrite.**

- [ ] **Step 4: Type-check and smoke-test**

Run: `cd spinr/rider-app && npx tsc --noEmit 2>&1 | grep "profile-setup.tsx"`
Expected: no output.

Then on a device, complete the auth flow through login → otp → profile-setup and confirm the screen renders, inputs work, save button works.

- [ ] **Step 5: Commit**

```bash
cd spinr && git add rider-app/app/profile-setup.tsx && git commit -m "chore(rider-app): consistency pass on profile-setup"
```

---

## Phase 2 — Consistency Audit (General Screens)

**Template for every task in this phase:**

1. Read the file fully.
2. Run the three greps from Task 3 Step 2, replacing `profile-setup.tsx` with the target filename.
3. Walk the §C1–§C8 checklist; edit only what fails.
4. `npx tsc --noEmit 2>&1 | grep "<filename>"` — expect no output.
5. Simulator smoke-test: navigate to the screen, verify it renders and all interactive elements still work.
6. Commit with message `chore(rider-app): consistency pass on <filename>`.

**Do not rewrite screens.** If the file already passes the checklist, note it in the commit as `chore(rider-app): no-op audit of <filename>` (empty commit via `--allow-empty`) or skip the commit.

### Task 4: Audit `app/index.tsx` (splash)

**File:** `spinr/rider-app/app/index.tsx`
**Notes:** Splash screen with animated logo. Already uses `SpinrConfig.theme.colors.primary`. Expected findings: likely passes; confirm tagline and logo colors route through theme tokens and no hardcoded red.

- [ ] Apply template steps 1–6 above.

### Task 5: Audit `app/(tabs)/_layout.tsx`

**File:** `spinr/rider-app/app/(tabs)/_layout.tsx`
**Notes:** Tab bar config. Specifically check `tabBarActiveTintColor` and `tabBarInactiveTintColor` — they must reference `THEME.primary` and `THEME.textDim`, not hex literals.

- [ ] Apply template steps 1–6.

### Task 6: Audit `app/(tabs)/index.tsx` (home)

**File:** `spinr/rider-app/app/(tabs)/index.tsx` (622 lines)
**Notes:** Largest tab screen. Likely the map-based home with search. Focus on CTAs, any floating action buttons, and header treatment.

- [ ] Apply template steps 1–6.

### Task 7: Audit `app/(tabs)/activity.tsx`

**File:** `spinr/rider-app/app/(tabs)/activity.tsx` (402 lines)
**Notes:** Ride history list. Focus on list row styling, empty-state, header.

- [ ] Apply template steps 1–6.

### Task 8: Audit `app/(tabs)/account.tsx`

**File:** `spinr/rider-app/app/(tabs)/account.tsx` (308 lines)
**Notes:** Profile hub. Cards, rows, avatar treatment.

- [ ] Apply template steps 1–6.

### Task 9: Audit `app/legal.tsx`

**File:** `spinr/rider-app/app/legal.tsx` (120 lines)
**Notes:** Already well-structured per pre-plan inspection. Expected finding: passes §C1–§C8 with possibly one §C3 fix (verify `SafeAreaView` origin).

- [ ] Apply template steps 1–6.

### Task 10: Audit `app/settings.tsx`

**File:** `spinr/rider-app/app/settings.tsx` (185 lines)
**Notes:** Already rich (cards, tinted icon tiles). Expected finding: one `Alert.alert` on line ~59 (language picker) needs replacement with `CustomAlert`.

- [ ] Apply template steps 1–6.

### Task 11: Audit `app/privacy-settings.tsx`

**File:** `spinr/rider-app/app/privacy-settings.tsx` (150 lines)

- [ ] Apply template steps 1–6.

### Task 12: Audit `app/support.tsx`

**File:** `spinr/rider-app/app/support.tsx` (215 lines)

- [ ] Apply template steps 1–6.

### Task 13: Audit `app/promotions.tsx`

**File:** `spinr/rider-app/app/promotions.tsx` (186 lines)

- [ ] Apply template steps 1–6.

### Task 14: Audit `app/report-safety.tsx`

**File:** `spinr/rider-app/app/report-safety.tsx` (187 lines)

- [ ] Apply template steps 1–6.

### Task 15: Audit `app/saved-places.tsx`

**File:** `spinr/rider-app/app/saved-places.tsx` (327 lines)

- [ ] Apply template steps 1–6.

### Task 16: Audit `app/manage-cards.tsx`

**File:** `spinr/rider-app/app/manage-cards.tsx` (366 lines)

- [ ] Apply template steps 1–6.

### Task 17: Audit `app/emergency-contacts.tsx`

**File:** `spinr/rider-app/app/emergency-contacts.tsx` (524 lines)

- [ ] Apply template steps 1–6.

### Task 18: Audit `app/become-driver.tsx`

**File:** `spinr/rider-app/app/become-driver.tsx` (494 lines)

- [ ] Apply template steps 1–6.

### Task 19: Audit `app/chat-driver.tsx`

**File:** `spinr/rider-app/app/chat-driver.tsx` (456 lines)
**Notes:** Chat bubbles — verify bubble background and accent colors route through theme.

- [ ] Apply template steps 1–6.

### Task 20: Audit `app/ride-details.tsx`

**File:** `spinr/rider-app/app/ride-details.tsx` (274 lines)

- [ ] Apply template steps 1–6.

### Task 21: Phase 2 end-to-end verification

- [ ] Launch the app, navigate to every screen touched in Tasks 4–20. Confirm:
  - All screens render with no red-screen errors.
  - All primary CTAs have the red shadow.
  - No native `Alert.alert` dialogs appear anywhere in the flow (all alerts go through `CustomAlert`).
  - Tab bar tints respect the theme.

- [ ] Commit any final adjustments: `chore(rider-app): phase 2 end-to-end verification`.

---

## Phase 3 — Ride Flow Audit

Same template as Phase 2: read → grep → walk §C1–§C8 → tsc → smoke-test → commit. No rewrites.

### Task 22: Audit `app/search-destination.tsx`

**File:** `spinr/rider-app/app/search-destination.tsx` (804 lines)
**Notes:** Search input + results list. Focus on search input §C6 compliance and result row styling.

- [ ] Apply template steps 1–6.

### Task 23: Audit `app/pick-on-map.tsx`

**File:** `spinr/rider-app/app/pick-on-map.tsx` (329 lines)
**Notes:** Map with floating pin. Verify floating action buttons match §C5.

- [ ] Apply template steps 1–6.

### Task 24: Audit `app/ride-options.tsx`

**File:** `spinr/rider-app/app/ride-options.tsx` (1005 lines)
**Notes:** Largest rider-app screen. Vehicle category cards. Verify card pattern §C7 and sticky bottom CTA §C5.

- [ ] Apply template steps 1–6.

### Task 25: Audit `app/payment-confirm.tsx`

**File:** `spinr/rider-app/app/payment-confirm.tsx` (644 lines)
**Notes:** Payment method rows, fare breakdown, confirm CTA.

- [ ] Apply template steps 1–6.

### Task 26: Audit `app/ride-status.tsx`

**File:** `spinr/rider-app/app/ride-status.tsx` (572 lines)

- [ ] Apply template steps 1–6.

### Task 27: Audit `app/driver-arriving.tsx`

**File:** `spinr/rider-app/app/driver-arriving.tsx` (1034 lines)
**Notes:** Second-largest screen. Driver card, map, action sheet. Multi-part screen — verify each sub-component passes checklist.

- [ ] Apply template steps 1–6.

### Task 28: Audit `app/driver-arrived.tsx`

**File:** `spinr/rider-app/app/driver-arrived.tsx` (486 lines)

- [ ] Apply template steps 1–6.

### Task 29: Audit `app/ride-in-progress.tsx`

**File:** `spinr/rider-app/app/ride-in-progress.tsx` (585 lines)

- [ ] Apply template steps 1–6.

### Task 30: Audit `app/ride-completed.tsx`

**File:** `spinr/rider-app/app/ride-completed.tsx` (507 lines)

- [ ] Apply template steps 1–6.

### Task 31: Audit `app/rate-ride.tsx`

**File:** `spinr/rider-app/app/rate-ride.tsx` (558 lines)
**Notes:** Star rating + feedback form. Verify star color uses `THEME.primary`.

- [ ] Apply template steps 1–6.

### Task 32: Phase 3 end-to-end verification

- [ ] Run a full simulated ride flow from the home screen through search → options → confirm → arriving → arrived → in-progress → completed → rate. Confirm:
  - Every screen transitions cleanly.
  - All CTAs and inputs respect the shared design language.
  - No `Alert.alert` escapes.
  - `npx tsc --noEmit` in rider-app has no new errors vs baseline.

- [ ] Final commit: `chore(rider-app): phase 3 ride-flow verification complete`.

---

## Self-Review (done by plan author, not executor)

**Spec coverage:**
- Spec §4 design language → encoded as shared §C1–§C8 at top of plan ✓
- Spec §5 Phase 1 (login, otp, profile-setup) → Tasks 1–3 ✓
- Spec §5 Phase 2 (17 screens) → Tasks 4–21 ✓
- Spec §5 Phase 3 (10 ride-flow screens) → Tasks 22–32 ✓
- Spec §6 done-checklist → embedded in §C1–§C8 and task step "walk the checklist" ✓
- Spec §8 testing → each task has tsc + simulator smoke-test steps ✓
- Spec §10 out-of-scope → honored (no shared-lib edits, no new components, no feature changes) ✓

**Placeholder scan:** No "TBD", no "implement later", no "similar to above" without full code. Phase 1 tasks have complete literal code. Phases 2–3 intentionally reference the shared checklist rather than inline code, because the audit content depends on what's currently in each file — this is by design per the user's plan-shape choice, not a placeholder.

**Type consistency:** `THEME = SpinrConfig.theme.colors` used consistently; `CustomAlert` import path identical across Tasks 1 and 2; `setInMemoryToken` import verified present in shared client.

**One known risk noted inline:** `THEME.success` in Task 1 — fallback instruction provided if the token is missing from `spinr.config.ts`.
