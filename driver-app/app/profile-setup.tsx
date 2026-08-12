import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  Keyboard,
  ActivityIndicator,
  ScrollView,
  Alert,
  BackHandler,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { useAuthStore, type User } from '@shared/store/authStore';
import api from '@shared/api/client';
import { showToast } from '../hooks/useToast';
import { notifyError } from '../lib/notifyError';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';


const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function ProfileSetupScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [isCheckingExisting, setIsCheckingExisting] = useState(true);

  const { user, token, createProfile, registerDriver, logout, isLoading: authLoading } = useAuthStore();

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [gender, setGender] = useState('');
  const [serviceAreaId, setServiceAreaId] = useState('');
  const [serviceAreas, setServiceAreas] = useState<any[]>([]);
  const [city, setCity] = useState('');
  const [referralCode, setReferralCode] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);


  const handleGenderMale = useCallback(() => {
    setGender('Male');
  }, []);

  const handleGenderFemale = useCallback(() => {
    setGender('Female');
  }, []);

  const handleGenderOther = useCallback(() => {
    setGender('Other');
  }, []);

  // ── Auth guard ──
  useEffect(() => {
    if (!token && !user) {
      router.replace('/login' as any);
    }
  }, [token, user]);

  useEffect(() => {
    if (!token && !user) return;
    let cancelled = false;
    (async () => {
      if (user?.first_name && user?.last_name && user?.email) {
        router.replace('/driver' as any);
        return;
      }
      try {
        const res = await api.get<User>('/auth/me');
        const fresh = res.data;
        if (cancelled) return;
        if (fresh?.first_name && fresh?.last_name && fresh?.email) {
          useAuthStore.setState({ user: fresh });
          router.replace('/driver' as any);
          return;
        }
      } catch (err: any) {
        console.log('[ProfileSetup] /auth/me refetch failed:', err?.message || err);
      }
      if (!cancelled) setIsCheckingExisting(false);
    })();
    return () => { cancelled = true; };
  }, []);

  // Fetch service areas and auto-select based on user location
  useEffect(() => {
    (async () => {
      // Fetch service areas
      let areas: any[] = [];
      try {
        // Public endpoint — returns only active areas, no admin auth required.
        const areasRes = await api.get<any[]>('/service-areas');
        areas = areasRes.data || [];
      } catch {
        areas = [
          { id: 'saskatoon', name: 'Saskatoon, SK', city: 'Saskatoon' },
          { id: 'regina', name: 'Regina, SK', city: 'Regina' },
        ];
      }
      setServiceAreas(areas);

      // Try to auto-select service area based on current location
      try {
        let { status } = await Location.getForegroundPermissionsAsync();
        if (status !== 'granted') {
          const res = await Location.requestForegroundPermissionsAsync();
          status = res.status;
        }
        if (status === 'granted') {
          const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          const geocode = await Location.reverseGeocodeAsync({
            latitude: loc.coords.latitude,
            longitude: loc.coords.longitude,
          });
          if (geocode.length > 0) {
            const userCity = geocode[0].city || geocode[0].subregion || '';
            // Match against service areas by city name
            const match = areas.find((a: any) =>
              (a.city || a.name || '').toLowerCase().includes(userCity.toLowerCase()) ||
              userCity.toLowerCase().includes((a.city || a.name || '').toLowerCase().split(',')[0])
            );
            if (match) {
              setServiceAreaId(match.id);
              setCity(match.city || match.name);
            }
          }
        }
      } catch (e) {
        console.log('[ProfileSetup] Location auto-select failed:', e);
      }

      // If no auto-selection and only one area, select it
      if (!serviceAreaId && areas.length === 1) {
        setServiceAreaId(areas[0].id);
        setCity(areas[0].city || areas[0].name);
      }
    })();
  }, []);

  const handleChangeNumber = useCallback(() => {
    Alert.alert(
      'Change phone number?',
      'This will sign you out and return to the login screen. Any progress here will be lost.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Sign Out',
          style: 'destructive',
          onPress: async () => {
            await logout();
            router.replace('/login' as any);
          },
        },
      ],
    );
  }, [logout, router]);

  // Android hardware back: there is no plain "back" out of profile setup.
  // The user is already authenticated (OTP set their token) but has no
  // profile yet, so popping to the login screen would silently strand a
  // half-finished session — and make the "Change" button look redundant.
  // Route the back press through the same sign-out confirmation as Change so
  // the two behave identically. (iOS swipe-back is already disabled via
  // gestureEnabled:false in _layout.tsx.)
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      handleChangeNumber();
      return true;
    });
    return () => sub.remove();
  }, [handleChangeNumber]);

  const validateEmail = (email: string): boolean => {
    return EMAIL_REGEX.test(email);
  };

  const isEmailValid = email.length > 0 && validateEmail(email);
  const isFirstNameValid = firstName.trim().length > 1;
  const isLastNameValid = lastName.trim().length > 1;
  const isServiceAreaValid = serviceAreaId.length > 0;
  const isFormValid = isFirstNameValid && isLastNameValid && isEmailValid && gender && isServiceAreaValid;

  const handleSubmit = async () => {
    // Field-specific validation: name the exact field and problem — a blanket
    // "complete all required fields" leaves the driver hunting for what's wrong.
    if (!isFirstNameValid) {
      showToast('warning', 'First Name Required', 'Please enter your first name (at least 2 letters).');
      return;
    }
    if (!isLastNameValid) {
      showToast('warning', 'Last Name Required', 'Please enter your last name (at least 2 letters).');
      return;
    }
    if (!email.trim()) {
      showToast('warning', 'Email Required', 'Please enter your email address.');
      return;
    }
    if (!isEmailValid) {
      showToast('warning', 'Invalid Email', 'That email doesn’t look right — e.g. name@example.com.');
      return;
    }
    if (!gender) {
      showToast('warning', 'Gender Required', 'Please select your gender.');
      return;
    }
    if (!isServiceAreaValid) {
      showToast('warning', 'Service Area Required', 'Please select the area where you plan to drive.');
      return;
    }

    Keyboard.dismiss();
    setIsSubmitting(true);

    try {
      await createProfile({
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        email: email.trim().toLowerCase(),
        gender,
        city: city || undefined,
        service_area_id: serviceAreaId || undefined,
      });
      // Auto-create the driver record so the user lands on the home screen
      // ready to go — no separate "become a driver" step required.
      try {
        await registerDriver({
          service_area_id: serviceAreaId || undefined,
          city: city || undefined,
        });
      } catch (regErr: any) {
        // Non-fatal: the driver record might already exist (idempotent register)
        // or will be created on the next refresh. Don't block navigation.
        if (__DEV__) console.log('[ProfileSetup] auto-register result:', regErr?.message);
      }

      // Optional referral code — best-effort, never blocks signup. Profile
      // creation already succeeded above (so the network is up), which means a
      // failed apply is almost always a transient blip — retry a few times
      // rather than silently dropping the referrer credit. "Already applied"
      // counts as success (idempotent); only a genuine 4xx rejection toasts.
      const code = referralCode.trim();
      if (code) {
        let applied = false;
        for (let attempt = 0; attempt < 3 && !applied; attempt++) {
          try {
            await api.post('/drivers/referral/apply', { referral_code: code });
            applied = true;
          } catch (refErr: any) {
            const status = refErr?.response?.status;
            const detail: string = refErr?.response?.data?.detail || '';
            if (status === 400 && /already applied/i.test(detail)) {
              applied = true; // the code was already attributed — success
            } else if (typeof status === 'number' && status >= 400 && status < 500) {
              showToast('error', 'Referral code', detail || "That referral code couldn't be applied.");
              break; // permanent rejection (bad code) — retrying won't help
            } else if (attempt < 2) {
              await new Promise((r) => setTimeout(r, 400 * (attempt + 1))); // transient — back off and retry
            }
          }
        }
        if (applied) showToast('success', 'Referral applied', 'Your referral code was added.');
      }
      router.replace('/driver' as any);
    } catch (err: any) {
      // Title + severity come from the backend error code/field (e.g. a
      // duplicate-email 400 → "Email Already In Use"); the body is the
      // server's specific reason. No regex-sniffing the message.
      notifyError(err, { fallbackTitle: 'Profile Not Saved', fallbackMessage: 'Failed to create profile. Please try again.' });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingExisting) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      style={styles.container}
    >
      <ScrollView
        style={styles.scrollView}
        contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 16 }]}
        keyboardShouldPersistTaps="handled"
                    automaticallyAdjustKeyboardInsets={true}
        showsVerticalScrollIndicator={false}
      >
        {/* Top pill for logged in status */}
        <View style={styles.signedInRow}>
          <View style={styles.signedInAvatar}>
            <Ionicons name="call" size={12} color={colors.primary} />
          </View>
          <View style={styles.signedInInfo}>
            <Text style={styles.signedInLabel}>Signed in with</Text>
            <Text style={styles.signedInPhone}>{user?.phone || 'Unknown'}</Text>
          </View>
          <TouchableOpacity onPress={handleChangeNumber} style={styles.changeBtn}>
            <Text style={styles.changeBtnText}>Change</Text>
          </TouchableOpacity>
        </View>

        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>Welcome! 🎉</Text>
          <Text style={styles.subtitle}>
            Let&apos;s get to know you better. This info will be shown to your riders.
          </Text>
        </View>

        {/* Form */}
        <View style={styles.form}>
          <View style={styles.row}>
            <View style={{ flex: 1, marginRight: 12 }}>
              <View style={styles.inputGroup}>
                <Text style={styles.label}>First Name</Text>
                <View style={[
                  styles.inputContainer,
                  firstName.length > 0 && isFirstNameValid && styles.inputContainerValid
                ]}>
                  <View style={styles.inputIconContainer}>
                    <Ionicons
                      name="person-outline"
                      size={20}
                      color={firstName ? colors.text : '#A0A0A0'}
                    />
                  </View>
                  <TextInput
                    style={styles.input}
                    value={firstName}
                    onChangeText={setFirstName}
                    placeholder="John"
                    placeholderTextColor="#B0B0B0"
                    autoCapitalize="words"
                    autoCorrect={false}
                  />
                  <View style={styles.checkIconWrapper}>
                    {firstName.length > 0 && isFirstNameValid ? (
                      <Ionicons name="checkmark-circle" size={20} color={colors.success} />
                    ) : null}
                  </View>
                </View>
              </View>
            </View>
            <View style={{ flex: 1 }}>
              <View style={styles.inputGroup}>
                <Text style={styles.label}>Last Name</Text>
                <View style={[
                  styles.inputContainer,
                  lastName.length > 0 && isLastNameValid && styles.inputContainerValid
                ]}>
                  <View style={styles.inputIconContainer}>
                    <Ionicons
                      name="person-outline"
                      size={20}
                      color={lastName ? colors.text : '#A0A0A0'}
                    />
                  </View>
                  <TextInput
                    style={styles.input}
                    value={lastName}
                    onChangeText={setLastName}
                    placeholder="Doe"
                    placeholderTextColor="#B0B0B0"
                    autoCapitalize="words"
                    autoCorrect={false}
                  />
                  <View style={styles.checkIconWrapper}>
                    {lastName.length > 0 && isLastNameValid ? (
                      <Ionicons name="checkmark-circle" size={20} color={colors.success} />
                    ) : null}
                  </View>
                </View>
              </View>
            </View>
          </View>

          <View style={styles.inputGroup}>
            <Text style={styles.label}>Email Address</Text>
            <View style={[
              styles.inputContainer,
              email.length > 0 && isEmailValid && styles.inputContainerValid
            ]}>
              <View style={styles.inputIconContainer}>
                <Ionicons
                  name="mail-outline"
                  size={20}
                  color={email ? colors.text : '#A0A0A0'}
                />
              </View>
              <TextInput
                style={styles.input}
                value={email}
                onChangeText={setEmail}
                placeholder="john.doe@example.com"
                placeholderTextColor="#B0B0B0"
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
              />
              <View style={styles.checkIconWrapper}>
                {email.length > 0 && isEmailValid ? (
                  <Ionicons name="checkmark-circle" size={20} color={colors.success} />
                ) : null}
              </View>
            </View>
          </View>

          <View style={styles.inputGroup}>
            <Text style={styles.label}>Gender</Text>
            <View style={styles.genderOptions}>
              <TouchableOpacity
                style={[
                  styles.genderOption,
                  gender === 'Male' && styles.genderOptionSelected
                ]}
                onPress={handleGenderMale}
                activeOpacity={0.8}
              >
                {gender === 'Male' && (
                  <Ionicons name="checkmark" size={16} color={colors.primary} style={{ marginRight: 4 }} />
                )}
                <Text style={[
                  styles.genderOptionText,
                  gender === 'Male' && styles.genderOptionTextSelected
                ]}>
                  Male
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[
                  styles.genderOption,
                  gender === 'Female' && styles.genderOptionSelected
                ]}
                onPress={handleGenderFemale}
                activeOpacity={0.8}
              >
                {gender === 'Female' && (
                  <Ionicons name="checkmark" size={16} color={colors.primary} style={{ marginRight: 4 }} />
                )}
                <Text style={[
                  styles.genderOptionText,
                  gender === 'Female' && styles.genderOptionTextSelected
                ]}>
                  Female
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[
                  styles.genderOption,
                  gender === 'Other' && styles.genderOptionSelected
                ]}
                onPress={handleGenderOther}
                activeOpacity={0.8}
              >
                {gender === 'Other' && (
                  <Ionicons name="checkmark" size={16} color={colors.primary} style={{ marginRight: 4 }} />
                )}
                <Text style={[
                  styles.genderOptionText,
                  gender === 'Other' && styles.genderOptionTextSelected
                ]}>
                  Other
                </Text>
              </TouchableOpacity>
            </View>
          </View>

          {/* Service Area Selector */}
          <View style={styles.inputGroup}>
            <Text style={styles.label}>Service Area *</Text>
            <View style={styles.serviceAreaList}>
              {serviceAreas.map((area) => (
                <TouchableOpacity
                  key={area.id}
                  style={[
                    styles.serviceAreaChip,
                    serviceAreaId === area.id && styles.serviceAreaChipActive
                  ]}
                  onPress={() => { setServiceAreaId(area.id); setCity(area.city || area.name); }}
                  activeOpacity={0.8}
                >
                  <Ionicons
                    name="location"
                    size={16}
                    color={serviceAreaId === area.id ? '#FFF' : '#A0A0A0'}
                  />
                  <Text style={[
                    styles.serviceAreaChipText,
                    serviceAreaId === area.id && styles.serviceAreaChipTextActive
                  ]}>
                    {area.name}
                  </Text>
                  {serviceAreaId === area.id && (
                    <Ionicons name="checkmark" size={14} color="#FFF" />
                  )}
                </TouchableOpacity>
              ))}
            </View>
            {serviceAreas.length === 0 && (
              <ActivityIndicator size="small" color={colors.primary} style={{ marginTop: 8 }} />
            )}
            <Text style={styles.serviceAreaHint}>
              {serviceAreaId ? 'You can only operate in your selected area' : 'Select your service area to continue'}
            </Text>
          </View>

          {/* Referral code (optional) — invited by another driver? */}
          <View style={styles.inputGroup}>
            <Text style={styles.label}>Referral Code (optional)</Text>
            <View style={styles.inputContainer}>
              <View style={styles.inputIconContainer}>
                <Ionicons name="gift-outline" size={20} color={referralCode ? colors.text : '#A0A0A0'} />
              </View>
              <TextInput
                style={styles.input}
                value={referralCode}
                onChangeText={(t) => setReferralCode(t.toUpperCase())}
                placeholder="e.g. DRV-7K9M2P"
                placeholderTextColor="#B0B0B0"
                autoCapitalize="characters"
                autoCorrect={false}
              />
            </View>
            <Text style={styles.serviceAreaHint}>Invited by a driver? Enter their code to credit them.</Text>
          </View>
        </View>

        {/* Submit Button */}
        <TouchableOpacity
          style={[
            styles.submitButton,
            !isFormValid && styles.submitButtonDisabled,
            (isSubmitting || authLoading) && styles.submitButtonLoading
          ]}
          onPress={handleSubmit}
          disabled={!isFormValid || isSubmitting || authLoading}
          activeOpacity={0.85}
        >
          {isSubmitting || authLoading ? (
            <ActivityIndicator color="#FFFFFF" size="small" />
          ) : (
            <View style={styles.submitBtnContent}>
              <Text style={[styles.submitButtonText, !isFormValid && styles.submitButtonTextDisabled]}>
                Create Profile
              </Text>
              <Ionicons name="arrow-forward" size={20} color={isFormValid ? '#fff' : '#999'} />
            </View>
          )}
        </TouchableOpacity>

        <View style={styles.footer}>
          <Ionicons name="shield-checkmark" size={14} color="#A0A0A0" />
          <Text style={styles.footerText}>Your data is securely encrypted</Text>
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
  scrollView: {
    flex: 1,
  },
  scrollContent: {
    paddingHorizontal: 24,
  },
  // Signed In Pill
  signedInRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surfaceLight,
    padding: 12,
    borderRadius: 16,
    marginBottom: 32,
    borderWidth: 1,
    borderColor: colors.border,
  },
  signedInAvatar: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: `${colors.primary}1A`,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  signedInInfo: {
    flex: 1,
  },
  signedInLabel: {
    fontSize: 11,
    color: colors.textDim,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    fontWeight: '700',
  },
  signedInPhone: {
    fontSize: 14,
    color: colors.text,
    fontWeight: '700',
    marginTop: 2,
  },
  changeBtn: {
    backgroundColor: colors.surface,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 2,
  },
  changeBtnText: {
    fontSize: 13,
    color: colors.text,
    fontWeight: '600',
  },
  // Header
  header: {
    marginBottom: 36,
  },
  title: {
    fontSize: 32,
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
  // Form elements
  form: {
    marginBottom: 32,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  inputGroup: {
    marginBottom: 20,
  },
  label: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
    marginBottom: 8,
    paddingLeft: 4,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surfaceLight,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: 16,
    height: 56,
  },
  inputContainerFocused: {
    borderColor: colors.primary,
    backgroundColor: colors.surface,
    shadowColor: colors.primary,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.1,
    shadowRadius: 8,
    elevation: 2,
  },
  inputContainerValid: {
    borderColor: '#EFEFEF',
    backgroundColor: '#FDFDFD',
  },
  inputIconContainer: {
    width: 48,
    justifyContent: 'center',
    alignItems: 'center',
  },
  input: {
    flex: 1,
    height: '100%',
    fontSize: 16,
    fontWeight: '600',
    color: colors.text,
  },
  checkIconWrapper: {
    width: 36,
    justifyContent: 'center',
    alignItems: 'flex-start',
  },
  // Gender Toggle
  genderOptions: {
    flexDirection: 'row',
    gap: 8,
  },
  genderOption: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    height: 50,
    backgroundColor: colors.surfaceLight,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: 16,
  },
  genderOptionSelected: {
    backgroundColor: `${colors.primary}0D`,
    borderColor: colors.primary,
  },
  genderOptionText: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textDim,
  },
  genderOptionTextSelected: {
    color: colors.primary,
    fontWeight: '700',
  },
  // Submit
  submitButton: {
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
  submitButtonDisabled: {
    backgroundColor: colors.border,
    shadowOpacity: 0,
    elevation: 0,
  },
  submitButtonLoading: {
    backgroundColor: colors.primaryDark,
  },
  submitBtnContent: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  submitButtonText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  submitButtonTextDisabled: {
    color: colors.textDim,
  },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  footerText: {
    fontSize: 13,
    color: '#A0A0A0',
    fontWeight: '500',
  },
  // Service Area
  serviceAreaList: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  serviceAreaChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 16,
    backgroundColor: colors.surfaceLight,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  serviceAreaChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  serviceAreaChipText: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textDim,
  },
  serviceAreaChipTextActive: {
    color: '#FFFFFF',
  },
  serviceAreaHint: {
    fontSize: 11,
    color: '#A0A0A0',
    marginTop: 8,
    paddingLeft: 4,
    fontStyle: 'italic',
  },
  });
}
