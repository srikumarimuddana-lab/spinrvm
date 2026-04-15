import React, { useState, useMemo } from 'react';
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
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '@shared/store/authStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function ProfileSetupScreen() {
  const router = useRouter();
  const { user, createProfile, logout, isLoading: authLoading, error: authError } = useAuthStore();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Detect if editing existing profile vs first-time setup
  const isEditing = !!(user?.profile_complete || user?.first_name);

  const [firstName, setFirstName] = useState(user?.first_name || '');
  const [lastName, setLastName] = useState(user?.last_name || '');
  const [email, setEmail] = useState(user?.email || '');
  const [gender, setGender] = useState(user?.gender || '');
  const [showGenderPicker, setShowGenderPicker] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const validateEmail = (email: string): boolean => {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
  };

  const handleSubmit = async () => {
    if (!firstName.trim() || !lastName.trim() || !email.trim() || !gender) {
      setAlertState({ visible: true, title: 'Missing Info', message: 'Please fill in all fields', variant: 'warning' });
      return;
    }

    if (!validateEmail(email)) {
      setAlertState({ visible: true, title: 'Invalid Email', message: 'Please enter a valid email address', variant: 'warning' });
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
      if (isEditing) {
        router.back();
      } else {
        router.replace('/(tabs)');
      }
    } catch (err: any) {
      setAlertState({ visible: true, title: 'Error', message: err.message || 'Failed to save profile', variant: 'danger' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    router.replace('/login');
  };

  const isFormValid = firstName.trim() && lastName.trim() && email.trim() && gender;

  const genderOptions = [
    { label: 'Male', value: 'Male' },
    { label: 'Female', value: 'Female' },
    { label: 'Other', value: 'Other' },
  ];

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.keyboardView}
      >
        <TouchableWithoutFeedback onPress={Keyboard.dismiss}>
          <ScrollView
            style={styles.scrollView}
            contentContainerStyle={styles.scrollContent}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            {/* Signed-in-as pill — only show during initial onboarding */}
            {!isEditing && (
              <View style={styles.signedInRow}>
                <View style={styles.signedInInfo}>
                  <Ionicons name="call" size={14} color={colors.textDim} />
                  <Text style={styles.signedInText} numberOfLines={1}>
                    Signed in as{' '}
                    <Text style={styles.signedInPhone}>{user?.phone || 'your number'}</Text>
                  </Text>
                </View>
                <TouchableOpacity
                  onPress={() => {
                    setAlertState({
                      visible: true,
                      title: 'Change phone number?',
                      message: 'This will sign you out and return to the phone entry screen. Any progress here will be lost.',
                      variant: 'warning',
                      buttons: [
                        { text: 'Cancel', style: 'cancel' },
                        {
                          text: 'Change Number',
                          style: 'destructive',
                          onPress: async () => {
                            await logout();
                            router.replace('/login' as any);
                          },
                        },
                      ],
                    });
                  }}
                  hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                >
                  <Text style={styles.changeNumberLink}>Change</Text>
                </TouchableOpacity>
              </View>
            )}

            {/* Header */}
            <View style={styles.header}>
              {isEditing && (
                <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                  <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
              )}
              <Text style={styles.title}>
                {isEditing ? 'Edit Profile' : 'Complete your\nprofile'}
              </Text>
              <Text style={styles.subtitle}>
                {isEditing
                  ? 'Update your personal details.'
                  : 'We need a few details to get you started with Spinr.'}
              </Text>
            </View>

            {/* Form */}
            <View style={styles.form}>
              <View style={styles.inputGroup}>
                <Text style={styles.label}>FIRST NAME</Text>
                <View style={[styles.inputWrapper, focusedField === 'firstName' && styles.inputWrapperFocused]}>
                  <TextInput
                    style={styles.input}
                    value={firstName}
                    onChangeText={setFirstName}
                    placeholder="Enter your first name"
                    placeholderTextColor={colors.textDim}
                    autoCapitalize="words"
                    onFocus={() => setFocusedField('firstName')}
                    onBlur={() => setFocusedField(null)}
                  />
                </View>
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>LAST NAME</Text>
                <View style={[styles.inputWrapper, focusedField === 'lastName' && styles.inputWrapperFocused]}>
                  <TextInput
                    style={styles.input}
                    value={lastName}
                    onChangeText={setLastName}
                    placeholder="Enter your last name"
                    placeholderTextColor={colors.textDim}
                    autoCapitalize="words"
                    onFocus={() => setFocusedField('lastName')}
                    onBlur={() => setFocusedField(null)}
                  />
                </View>
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>EMAIL</Text>
                <View style={[styles.inputWrapper, focusedField === 'email' && styles.inputWrapperFocused]}>
                  <TextInput
                    style={styles.input}
                    value={email}
                    onChangeText={setEmail}
                    placeholder="email@example.com"
                    placeholderTextColor={colors.textDim}
                    keyboardType="email-address"
                    autoCapitalize="none"
                    autoCorrect={false}
                    onFocus={() => setFocusedField('email')}
                    onBlur={() => setFocusedField(null)}
                  />
                </View>
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>GENDER</Text>
                <TouchableOpacity
                  style={styles.citySelector}
                  onPress={() => setShowGenderPicker(!showGenderPicker)}
                  activeOpacity={0.7}
                >
                  <Text style={[styles.citySelectorText, !gender && styles.placeholder]}>
                    {gender || 'Select your gender'}
                  </Text>
                  <Ionicons
                    name={showGenderPicker ? 'chevron-up' : 'chevron-down'}
                    size={20}
                    color={colors.textDim}
                  />
                </TouchableOpacity>

                {showGenderPicker && (
                  <View style={styles.cityDropdown}>
                    {genderOptions.map((g) => (
                      <TouchableOpacity
                        key={g.value}
                        style={[
                          styles.cityOption,
                          gender === g.value && styles.cityOptionSelected,
                        ]}
                        onPress={() => {
                          setGender(g.value);
                          setShowGenderPicker(false);
                        }}
                      >
                        <Text
                          style={[
                            styles.cityOptionText,
                            gender === g.value && styles.cityOptionTextSelected,
                          ]}
                        >
                          {g.label}
                        </Text>
                        {gender === g.value && (
                          <Ionicons name="checkmark" size={20} color={colors.primary} />
                        )}
                      </TouchableOpacity>
                    ))}
                  </View>
                )}
              </View>
            </View>

            {/* Submit Button */}
            <TouchableOpacity
              style={[
                styles.submitButton,
                (isSubmitting || authLoading) && styles.submitButtonLoading,
                !isFormValid && styles.submitButtonDisabled,
              ]}
              onPress={handleSubmit}
              disabled={!isFormValid || isSubmitting || authLoading}
              activeOpacity={0.8}
            >
              {isSubmitting || authLoading ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <View style={styles.submitButtonContent}>
                  <Text style={[styles.submitButtonText, !isFormValid && styles.submitButtonTextDisabled]}>
                    {isEditing ? 'Save Changes' : 'Get Started'}
                  </Text>
                  <Ionicons
                    name="arrow-forward"
                    size={18}
                    color={!isFormValid ? colors.textDim : '#fff'}
                  />
                </View>
              )}
            </TouchableOpacity>

            {/* Logout / Change Number Button — only on first setup */}
            {!isEditing && (
              <TouchableOpacity
                style={styles.logoutButton}
                onPress={handleLogout}
                disabled={isSubmitting || authLoading}
              >
                <Text style={styles.logoutButtonText}>Not you? Change phone number</Text>
              </TouchableOpacity>
            )}
          </ScrollView>
        </TouchableWithoutFeedback>
      </KeyboardAvoidingView>
      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
    },
    keyboardView: {
      flex: 1,
    },
    scrollView: {
      flex: 1,
    },
    scrollContent: {
      paddingHorizontal: 24,
      paddingTop: 40,
      paddingBottom: 40,
    },
    backBtn: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceLight,
      justifyContent: 'center', alignItems: 'center', marginBottom: 12,
    },
    signedInRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 14,
      paddingVertical: 10,
      borderRadius: 12,
      marginBottom: 24,
      gap: 8,
    },
    signedInInfo: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      flex: 1,
    },
    signedInText: { fontSize: 13, color: colors.textDim, flex: 1 },
    signedInPhone: { color: colors.text, fontWeight: '700' },
    changeNumberLink: {
      fontSize: 13,
      color: colors.primary,
      fontWeight: '700',
    },
    header: {
      marginBottom: 40,
    },
    title: {
      fontSize: 28,
      fontWeight: '800',
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      lineHeight: 36,
      letterSpacing: -0.5,
    },
    subtitle: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 12,
      lineHeight: 22,
    },
    form: {
      marginBottom: 32,
    },
    inputGroup: {
      marginBottom: 20,
    },
    label: {
      fontSize: 11,
      fontWeight: '700',
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.textDim,
      letterSpacing: 1,
      marginBottom: 8,
    },
    inputWrapper: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      height: 60,
      borderWidth: 1.5,
      borderColor: colors.border,
      justifyContent: 'center',
      paddingHorizontal: 16,
    },
    inputWrapperFocused: {
      borderColor: colors.primary,
      backgroundColor: colors.surface,
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.12,
      shadowRadius: 8,
      elevation: 3,
    },
    input: {
      fontSize: 18,
      fontWeight: '600',
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      padding: 0,
    },
    citySelector: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      borderWidth: 1.5,
      borderColor: colors.border,
      borderRadius: 16,
      paddingHorizontal: 16,
      paddingVertical: 14,
    },
    citySelectorText: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    placeholder: {
      color: colors.textDim,
    },
    cityDropdown: {
      marginTop: 8,
      backgroundColor: colors.surface,
      borderRadius: 16,
      borderWidth: 1,
      borderColor: colors.border,
      overflow: 'hidden',
    },
    cityOption: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 14,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    cityOptionSelected: {
      backgroundColor: `${colors.primary}14`,
    },
    cityOptionText: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    cityOptionTextSelected: {
      color: colors.primary,
      fontFamily: 'PlusJakartaSans_600SemiBold',
    },
    submitButton: {
      backgroundColor: colors.primary,
      borderRadius: 16,
      height: 58,
      alignItems: 'center',
      justifyContent: 'center',
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.25,
      shadowRadius: 12,
      elevation: 6,
    },
    submitButtonLoading: {
      backgroundColor: colors.primaryDark,
    },
    submitButtonDisabled: {
      backgroundColor: colors.border,
      shadowOpacity: 0,
      elevation: 0,
    },
    submitButtonContent: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    submitButtonText: {
      fontSize: 16,
      fontWeight: '700',
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#fff',
    },
    submitButtonTextDisabled: {
      color: colors.textDim,
    },
    logoutButton: {
      marginTop: 16,
      paddingVertical: 12,
      alignItems: 'center',
      justifyContent: 'center',
    },
    logoutButtonText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim,
    },
  });
}
