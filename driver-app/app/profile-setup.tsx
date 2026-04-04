import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  Keyboard,
  TouchableWithoutFeedback,
  ActivityIndicator,
  ScrollView,
  Animated,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '@shared/store/authStore';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';

const THEME = SpinrConfig.theme.colors;

export default function ProfileSetupScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [isCheckingExisting, setIsCheckingExisting] = useState(true);
  
  const { user, token, createProfile, logout, isLoading: authLoading } = useAuthStore();

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [gender, setGender] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Focus states for animations
  const [focusedField, setFocusedField] = useState<string | null>(null);

  // Alert state
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: { text: string; style: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', message: '', variant: 'info' });

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
        const res = await api.get('/auth/me');
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

  const handleChangeNumber = () => {
    setAlertState({
      visible: true,
      title: 'Change phone number?',
      message: 'This will sign you out and return to the login screen. Any progress here will be lost.',
      variant: 'warning',
      buttons: [
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
    });
  };

  const validateEmail = (email: string): boolean => {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
  };

  const isEmailValid = email.length > 0 && validateEmail(email);
  const isFirstNameValid = firstName.trim().length > 1;
  const isLastNameValid = lastName.trim().length > 1;
  const isFormValid = isFirstNameValid && isLastNameValid && isEmailValid && gender;

  const handleSubmit = async () => {
    if (!isFormValid) {
      setAlertState({
        visible: true,
        title: 'Missing Info',
        message: 'Please complete all required fields.',
        variant: 'warning',
      });
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
      });
      router.replace('/driver' as any);
    } catch (err: any) {
      setAlertState({
        visible: true,
        title: 'Error',
        message: err.message || 'Failed to create profile. Please try again.',
        variant: 'danger',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderInput = (
    label: string, 
    value: string, 
    setValue: (val: string) => void, 
    placeholder: string, 
    icon: keyof typeof Ionicons.glyphMap,
    fieldKey: string,
    isValid: boolean,
    keyboardType: 'default' | 'email-address' = 'default'
  ) => {
    const isFocused = focusedField === fieldKey;
    return (
      <View style={styles.inputGroup}>
        <Text style={styles.label}>{label}</Text>
        <View style={[
          styles.inputContainer,
          isFocused && styles.inputContainerFocused,
          value.length > 0 && isValid && styles.inputContainerValid
        ]}>
          <View style={styles.inputIconContainer}>
            <Ionicons 
              name={icon} 
              size={20} 
              color={isFocused ? THEME.primary : (value ? THEME.text : '#A0A0A0')} 
            />
          </View>
          <TextInput
            style={styles.input}
            value={value}
            onChangeText={setValue}
            placeholder={placeholder}
            placeholderTextColor="#B0B0B0"
            autoCapitalize={keyboardType === 'email-address' ? 'none' : 'words'}
            keyboardType={keyboardType}
            autoCorrect={false}
            onFocus={() => setFocusedField(fieldKey)}
            onBlur={() => setFocusedField(null)}
          />
          {value.length > 0 && isValid && (
            <View style={styles.checkIcon}>
              <Ionicons name="checkmark-circle" size={20} color={THEME.success} />
            </View>
          )}
        </View>
      </View>
    );
  };

  if (isCheckingExisting) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={THEME.primary} />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      style={styles.container}
    >
      <TouchableWithoutFeedback onPress={Keyboard.dismiss}>
        <ScrollView
          style={styles.scrollView}
          contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 40 }]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* Top pill for logged in status */}
          <View style={styles.signedInRow}>
            <View style={styles.signedInAvatar}>
              <Ionicons name="call" size={12} color={THEME.primary} />
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
              Let's get to know you better. This info will be shown to your riders.
            </Text>
          </View>

          {/* Form */}
          <View style={styles.form}>
            <View style={styles.row}>
              <View style={{ flex: 1, marginRight: 12 }}>
                {renderInput('First Name', firstName, setFirstName, 'John', 'person-outline', 'fn', isFirstNameValid)}
              </View>
              <View style={{ flex: 1 }}>
                {renderInput('Last Name', lastName, setLastName, 'Doe', 'person-outline', 'ln', isLastNameValid)}
              </View>
            </View>

            {renderInput('Email Address', email, setEmail, 'john.doe@example.com', 'mail-outline', 'email', isEmailValid, 'email-address')}

            <View style={styles.inputGroup}>
              <Text style={styles.label}>Gender</Text>
              <View style={styles.genderOptions}>
                {['Male', 'Female', 'Other'].map((option) => (
                  <TouchableOpacity
                    key={option}
                    style={[
                      styles.genderOption,
                      gender === option && styles.genderOptionSelected
                    ]}
                    onPress={() => setGender(option)}
                    activeOpacity={0.8}
                  >
                    {gender === option && (
                      <Ionicons name="checkmark" size={16} color={THEME.primary} style={{ marginRight: 4 }} />
                    )}
                    <Text style={[
                      styles.genderOptionText,
                      gender === option && styles.genderOptionTextSelected
                    ]}>
                      {option}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
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
      </TouchableWithoutFeedback>

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
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
    backgroundColor: '#F8F9FA',
    padding: 12,
    borderRadius: 16,
    marginBottom: 32,
    borderWidth: 1,
    borderColor: '#F0F0F0',
  },
  signedInAvatar: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: `${THEME.primary}1A`,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  signedInInfo: {
    flex: 1,
  },
  signedInLabel: {
    fontSize: 11,
    color: THEME.textDim,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    fontWeight: '700',
  },
  signedInPhone: {
    fontSize: 14,
    color: THEME.text,
    fontWeight: '700',
    marginTop: 2,
  },
  changeBtn: {
    backgroundColor: '#fff',
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
    color: THEME.text,
    fontWeight: '600',
  },
  // Header
  header: {
    marginBottom: 36,
  },
  title: {
    fontSize: 32,
    fontWeight: '800',
    color: THEME.text,
    letterSpacing: -0.5,
    marginBottom: 10,
  },
  subtitle: {
    fontSize: 15,
    color: THEME.textDim,
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
    color: THEME.text,
    marginBottom: 8,
    paddingLeft: 4,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F8F9FA',
    borderWidth: 1.5,
    borderColor: '#F0F0F0',
    borderRadius: 16,
    height: 56,
  },
  inputContainerFocused: {
    borderColor: THEME.primary,
    backgroundColor: '#fff',
    shadowColor: THEME.primary,
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
    color: THEME.text,
  },
  checkIcon: {
    paddingRight: 16,
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
    backgroundColor: '#F8F9FA',
    borderWidth: 1.5,
    borderColor: '#F0F0F0',
    borderRadius: 16,
  },
  genderOptionSelected: {
    backgroundColor: `${THEME.primary}0D`,
    borderColor: THEME.primary,
  },
  genderOptionText: {
    fontSize: 14,
    fontWeight: '600',
    color: THEME.textDim,
  },
  genderOptionTextSelected: {
    color: THEME.primary,
    fontWeight: '700',
  },
  // Submit
  submitButton: {
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
    marginBottom: 24,
  },
  submitButtonDisabled: {
    backgroundColor: '#F0F0F0',
    shadowOpacity: 0,
    elevation: 0,
  },
  submitButtonLoading: {
    backgroundColor: THEME.primaryDark,
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
    color: '#999',
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
});
