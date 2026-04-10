import React, { useState, useRef, useEffect } from 'react';
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
  Animated,
  Dimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import * as Location from 'expo-location';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { auth } from '@shared/config/firebaseConfig';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';

const THEME = SpinrConfig.theme.colors;
const { width: SCREEN_WIDTH } = Dimensions.get('window');

// Only import Firebase phone auth when Firebase is actually configured
const isFirebaseConfigured = typeof auth.onAuthStateChanged === 'function';
let PhoneAuthProvider: any = null;
if (isFirebaseConfigured) {
  try {
    PhoneAuthProvider = require('firebase/auth').PhoneAuthProvider;
  } catch (e) {
    console.warn('Firebase auth not available');
  }
}

export default function LoginScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [phoneNumber, setPhoneNumber] = useState('');
  const [loading, setLoading] = useState(false);
  const [focused, setFocused] = useState(false);
  const recaptchaVerifier = useRef(null);
  const inputRef = useRef<TextInput>(null);

  // Alert state
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
  }>({ visible: false, title: '', message: '', variant: 'info' });

  // Request location permission and fetch location early so the map is
  // ready by the time the user reaches the dashboard after login.
  useEffect(() => {
    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted') return;

        // Try fast cached location first
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) {
          await AsyncStorage.setItem('spinr_driver_last_location', JSON.stringify({
            lat: lastKnown.coords.latitude,
            lng: lastKnown.coords.longitude,
          }));
        }

        // Then get accurate position in background
        Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
          .then(async (loc) => {
            await AsyncStorage.setItem('spinr_driver_last_location', JSON.stringify({
              lat: loc.coords.latitude,
              lng: loc.coords.longitude,
            }));
          })
          .catch(() => {});
      } catch (e) {
        console.log('[Login] Early location fetch failed:', e);
      }
    })();
  }, []);

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
      if (isFirebaseConfigured && PhoneAuthProvider) {
        const phoneProvider = new PhoneAuthProvider(auth);
        const verificationId = await phoneProvider.verifyPhoneNumber(
          formattedNumber,
          recaptchaVerifier.current!
        );
        router.push({
          pathname: '/otp',
          params: { verificationId, phoneNumber: formattedNumber, mode: 'firebase' }
        });
      } else {
        const response = await api.post('/auth/send-otp', { phone: formattedNumber });
        if (response.data.success) {
          router.push({
            pathname: '/otp',
            params: { phoneNumber: formattedNumber, mode: 'backend' }
          });
        } else {
          setAlertState({
            visible: true,
            title: 'Failed',
            message: 'Could not send verification code. Please try again.',
            variant: 'danger',
          });
        }
      }
    } catch (error: any) {
      setAlertState({
        visible: true,
        title: 'Connection Error',
        message: error.message || 'Unable to reach server. Please check your connection.',
        variant: 'danger',
      });
    } finally {
      setLoading(false);
    }
  };

  const isValid = phoneNumber.length === 10;

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
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
          <View style={styles.driverBadge}>
            <Text style={styles.driverBadgeText}>Driver</Text>
          </View>
        </View>
      </View>

      {/* Main content */}
      <View style={styles.content}>
        {/* Welcome text */}
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

          {!isFirebaseConfigured && (
            <View style={styles.devHintContainer}>
              <Ionicons name="information-circle" size={14} color={THEME.primary} />
              <Text style={styles.devHint}>Dev mode — OTP is 1234</Text>
            </View>
          )}
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

        {/* Footer */}
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
  container: {
    flex: 1,
    backgroundColor: '#fff',
  },
  topStrip: {
    backgroundColor: '#fff',
    paddingHorizontal: 24,
    paddingBottom: 8,
  },
  brandRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingTop: 12,
    gap: 10,
  },
  logoCircle: {
    width: 42,
    height: 42,
    borderRadius: 14,
    backgroundColor: THEME.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  brandName: {
    fontSize: 24,
    fontWeight: '800',
    color: THEME.text,
    letterSpacing: -0.5,
  },
  driverBadge: {
    backgroundColor: `${THEME.primary}14`,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
  },
  driverBadgeText: {
    fontSize: 12,
    fontWeight: '700',
    color: THEME.primary,
  },
  content: {
    flex: 1,
    paddingHorizontal: 24,
    justifyContent: 'center',
  },
  welcomeSection: {
    marginBottom: 36,
  },
  greeting: {
    fontSize: 16,
    color: THEME.textDim,
    marginBottom: 8,
    fontWeight: '500',
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: THEME.text,
    letterSpacing: -0.5,
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 15,
    color: THEME.textDim,
    lineHeight: 22,
  },
  inputSection: {
    marginBottom: 24,
  },
  inputLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: THEME.textDim,
    letterSpacing: 1,
    marginBottom: 8,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F8F9FA',
    borderRadius: 16,
    height: 60,
    borderWidth: 1.5,
    borderColor: '#F0F0F0',
  },
  inputContainerFocused: {
    borderColor: THEME.primary,
    backgroundColor: '#fff',
    shadowColor: THEME.primary,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.1,
    shadowRadius: 8,
    elevation: 3,
  },
  flagContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    gap: 6,
  },
  flagEmoji: {
    fontSize: 20,
  },
  countryCode: {
    fontSize: 16,
    fontWeight: '600',
    color: THEME.text,
  },
  inputDivider: {
    width: 1,
    height: 28,
    backgroundColor: '#E0E0E0',
  },
  input: {
    flex: 1,
    paddingHorizontal: 14,
    fontSize: 18,
    fontWeight: '600',
    color: THEME.text,
    height: '100%',
    letterSpacing: 0.5,
  },
  checkIcon: {
    paddingRight: 14,
  },
  devHintContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 10,
    paddingHorizontal: 4,
  },
  devHint: {
    fontSize: 13,
    color: THEME.primary,
    fontWeight: '500',
  },
  button: {
    backgroundColor: THEME.primary,
    borderRadius: 16,
    height: 58,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: THEME.primary,
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.25,
    shadowRadius: 12,
    elevation: 6,
    marginBottom: 20,
  },
  buttonInactive: {
    backgroundColor: '#F0F0F0',
    shadowOpacity: 0,
    elevation: 0,
  },
  buttonLoading: {
    backgroundColor: THEME.primaryDark,
  },
  buttonContent: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  buttonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  buttonTextInactive: {
    color: '#999',
  },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  footerText: {
    fontSize: 12,
    color: THEME.textDim,
  },
  terms: {
    paddingHorizontal: 24,
    alignItems: 'center',
  },
  termsText: {
    fontSize: 12,
    color: '#B0B0B0',
    textAlign: 'center',
    lineHeight: 18,
  },
  termsLink: {
    color: THEME.primary,
    fontWeight: '600',
  },
});
