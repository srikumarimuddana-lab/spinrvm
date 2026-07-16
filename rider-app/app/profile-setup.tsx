import React, { useState, useMemo, useEffect, useCallback } from 'react';
import * as ImagePicker from 'expo-image-picker';
import { Image } from 'expo-image';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Keyboard,
  ActivityIndicator,
  ScrollView,
  Linking,
  Alert,
  BackHandler,
} from 'react-native';
import { useRouter, useNavigation } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '@shared/store/authStore';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function ProfileSetupScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, createProfile, logout, updateProfileImage, isLoading: authLoading } = useAuthStore();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const isEditing = !!(user?.profile_complete || user?.first_name);

  const [form, setForm] = useState({
    firstName: user?.first_name || '',
    lastName: user?.last_name || '',
    email: user?.email || '',
    gender: user?.gender || '',
    referralCode: '',
  });

  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [showGenderPicker, setShowGenderPicker] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploadingPhoto, setIsUploadingPhoto] = useState(false);
  const [tosAccepted, setTosAccepted] = useState(false);
  const [showPhotoPicker, setShowPhotoPicker] = useState(false);

  const updateForm = (key: keyof typeof form, value: string) => {
    setForm(prev => ({ ...prev, [key]: value }));
  };

  const launchCamera = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') return showToast('Permission Denied', 'Camera access is needed.', 'warning');
    const result = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 0.7 });
    if (!result.canceled && result.assets[0]) uploadPhoto(result.assets[0].uri);
  };

  const launchGallery = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') return showToast('Permission Denied', 'Library access is needed.', 'warning');
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 0.7 });
    if (!result.canceled && result.assets[0]) uploadPhoto(result.assets[0].uri);
  };

  const uploadPhoto = async (uri: string) => {
    setIsUploadingPhoto(true);
    try {
      await updateProfileImage(uri);
      showToast('Photo Updated', 'Your profile photo has been updated.', 'success');
    } catch (err: any) {
      showToast('Upload Failed', getApiErrorMessage(err, 'Could not upload your photo. Please try again.'), 'danger');
    } finally {
      setIsUploadingPhoto(false);
    }
  };

  const handleSubmit = async () => {
    // Field-specific validation: name the exact field and problem — a blanket
    // "fill in all fields" leaves the user hunting for what's wrong.
    if (!form.firstName.trim()) {
      return showToast('First Name Required', 'Please enter your first name.', 'warning');
    }
    if (!form.lastName.trim()) {
      return showToast('Last Name Required', 'Please enter your last name.', 'warning');
    }
    if (!form.email.trim()) {
      return showToast('Email Required', 'Please enter your email address.', 'warning');
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      return showToast('Invalid Email', 'That email doesn’t look right — e.g. name@example.com.', 'warning');
    }
    if (!form.gender) {
      return showToast('Gender Required', 'Please select your gender.', 'warning');
    }

    Keyboard.dismiss();
    setIsSubmitting(true);
    try {
      await createProfile({
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        email: form.email.trim().toLowerCase(),
        gender: form.gender,
      });
      if (isEditing) {
        router.back();
      } else {
        // Optional referral code — best-effort, never blocks signup. Profile
        // save already succeeded above (network is up), so a failed apply is
        // almost always a transient blip — retry a few times rather than
        // silently dropping the referrer credit. "Already applied" counts as
        // success (idempotent); only a genuine 4xx rejection toasts.
        const code = form.referralCode.trim();
        if (code) {
          let applied = false;
          for (let attempt = 0; attempt < 3 && !applied; attempt++) {
            try {
              await api.post('/users/referral/apply', { referral_code: code });
              applied = true;
            } catch (refErr: any) {
              const status = refErr?.response?.status;
              const detail: string = refErr?.response?.data?.detail || '';
              if (status === 400 && /already applied/i.test(detail)) {
                applied = true; // already attributed — success
              } else if (typeof status === 'number' && status >= 400 && status < 500) {
                showToast('Referral code', detail || "That code couldn't be applied.", 'warning');
                break; // permanent rejection (bad code) — retrying won't help
              } else if (attempt < 2) {
                await new Promise((r) => setTimeout(r, 400 * (attempt + 1))); // transient — back off and retry
              }
            }
          }
          if (applied) showToast('Referral applied', 'Your referral code was added.', 'success');
        }
        router.replace('/(tabs)' as any);
      }
    } catch (err: any) {
      // Surface the backend's specific reason instead of a blanket "try
      // again" — a duplicate email will never succeed on retry. When the
      // failure is about the email itself, say so in the title too.
      const message = getApiErrorMessage(err, 'Failed to save your profile. Please try again.');
      const title = /email/i.test(message) && /already/i.test(message)
        ? 'Email Already In Use'
        : 'Profile Not Saved';
      showToast(title, message, 'danger');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleLogout = useCallback(async () => {
    await logout();
    router.replace('/login');
  }, [logout, router]);

  // Setup mode is a forced step: the rider is authenticated (OTP set their
  // token) but has no profile yet, which is why the back button is hidden
  // here. Hardware back (Android) and the swipe-back gesture (iOS) are still
  // active, though, and would pop to the login screen while leaving that
  // half-finished session alive. Disable the swipe gesture and route the
  // hardware back through a sign-out confirmation (matching "Change") so the
  // only way out of setup is finishing the profile or signing out. In edit
  // mode, back stays a normal "return to settings".
  const navigation = useNavigation();
  useEffect(() => {
    navigation.setOptions({ gestureEnabled: isEditing });
  }, [navigation, isEditing]);

  useEffect(() => {
    if (isEditing) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      Alert.alert(
        'Discard sign-up?',
        'You need to finish your profile to use Spinr. Going back will sign you out.',
        [
          { text: 'Keep editing', style: 'cancel' },
          { text: 'Sign out', style: 'destructive', onPress: handleLogout },
        ],
      );
      return true;
    });
    return () => sub.remove();
  }, [isEditing, handleLogout]);

  const isFormValid = form.firstName.trim() && form.lastName.trim() && form.email.trim() && form.gender && (isEditing || tosAccepted);
  const genderOptions = ['Male', 'Female', 'Other'];

  const renderInput = (
    key: keyof typeof form,
    label: string,
    placeholder: string,
    icon: keyof typeof Ionicons.glyphMap,
    keyboardType: 'default' | 'email-address' = 'default',
    autoCapitalize: 'none' | 'sentences' | 'words' | 'characters' = 'words',
  ) => {
    const isFocused = focusedField === key;
    return (
      <View style={styles.inputContainer}>
        <Text style={styles.inputLabel}>{label}</Text>
        <View style={[styles.inputBox, isFocused && styles.inputBoxFocused]}>
          <Ionicons name={icon} size={20} color={isFocused ? colors.primary : colors.textDim} style={styles.inputIcon} />
          <TextInput
            style={styles.textInput}
            value={form[key]}
            onChangeText={val => updateForm(key, val)}
            placeholder={placeholder}
            placeholderTextColor={colors.textDim}
            keyboardType={keyboardType}
            autoCapitalize={autoCapitalize}
            onFocus={() => setFocusedField(key)}
            onBlur={() => setFocusedField(null)}
            autoCorrect={false}
          />
        </View>
      </View>
    );
  };

  const contentNode = (
    <ScrollView
      style={styles.scrollView}
      contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 40 }]}
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.headerContainer}>
        {isEditing && (
          <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
        )}

        <TouchableOpacity style={styles.avatarContainer} activeOpacity={0.8} onPress={() => setShowPhotoPicker(true)}>
          {isUploadingPhoto ? (
            <View style={[styles.avatarCircle, { backgroundColor: colors.surface }]}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          ) : user?.profile_image ? (
            <Image source={{ uri: user.profile_image }} style={styles.avatarCircle} contentFit="cover" transition={200} />
          ) : (
            <View style={styles.avatarCircle}>
              <Ionicons name="person" size={40} color={colors.primary} />
            </View>
          )}
          <View style={styles.badge}>
            <Ionicons name="camera" size={14} color="#FFF" />
          </View>
        </TouchableOpacity>

        <Text style={styles.title}>{isEditing ? 'Edit Profile' : 'Complete Profile'}</Text>
        <Text style={styles.subtitle}>
          {isEditing ? 'Update your personal details below.' : 'Please provide your details to get started.'}
        </Text>

        {!isEditing && (
          <View style={styles.signedInCard}>
            <Ionicons name="phone-portrait-outline" size={18} color={colors.primary} />
            <Text style={styles.signedInText}>
              Signed in as <Text style={styles.signedInPhone}>{user?.phone || 'Unknown'}</Text>
            </Text>
            <TouchableOpacity onPress={handleLogout}>
              <Text style={styles.changeText}>Change</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

      <View style={styles.formCard}>
        {renderInput('firstName', 'FIRST NAME', 'Enter your first name', 'person-outline')}
        {renderInput('lastName', 'LAST NAME', 'Enter your last name', 'people-outline')}
        {renderInput('email', 'EMAIL ADDRESS', 'name@example.com', 'mail-outline', 'email-address', 'none')}

        <View style={styles.inputContainer}>
          <Text style={styles.inputLabel}>GENDER</Text>
          <TouchableOpacity
            style={[styles.inputBox, showGenderPicker && styles.inputBoxFocused]}
            onPress={() => setShowGenderPicker(!showGenderPicker)}
            activeOpacity={0.7}
          >
            <Ionicons name="male-female-outline" size={20} color={showGenderPicker ? colors.primary : colors.textDim} style={styles.inputIcon} />
            <Text style={[styles.textInput, !form.gender && { color: colors.textDim }]}>
              {form.gender || 'Select your gender'}
            </Text>
            <Ionicons name={showGenderPicker ? 'chevron-up' : 'chevron-down'} size={20} color={colors.textDim} />
          </TouchableOpacity>

          {showGenderPicker && (
            <View style={styles.dropdownContainer}>
              {genderOptions.map((g, index) => (
                <TouchableOpacity
                  key={g}
                  style={[
                    styles.dropdownOption,
                    index !== genderOptions.length - 1 && styles.dropdownOptionBorder,
                    form.gender === g && styles.dropdownOptionSelected,
                  ]}
                  onPress={() => { updateForm('gender', g); setShowGenderPicker(false); }}
                >
                  <Text style={[styles.dropdownOptionText, form.gender === g && styles.dropdownOptionTextActive]}>{g}</Text>
                  {form.gender === g && <Ionicons name="checkmark-circle" size={20} color={colors.primary} />}
                </TouchableOpacity>
              ))}
            </View>
          )}
        </View>

        {!isEditing && renderInput('referralCode', 'REFERRAL CODE (OPTIONAL)', 'e.g. RIDE3F8A9C21', 'gift-outline', 'default', 'characters')}
      </View>

      {!isEditing && (
        <TouchableOpacity style={styles.tosContainer} activeOpacity={0.8} onPress={() => setTosAccepted(!tosAccepted)}>
          <View style={[styles.checkbox, tosAccepted && styles.checkboxActive]}>
            {tosAccepted && <Ionicons name="checkmark" size={16} color="#FFF" />}
          </View>
          <Text style={styles.tosText}>
            I agree to Spinr's{' '}
            <Text style={styles.link} onPress={() => Linking.openURL('https://spinr.ca/terms')}>Terms</Text>
            {' '}and{' '}
            <Text style={styles.link} onPress={() => Linking.openURL('https://spinr.ca/privacy')}>Privacy Policy</Text>
          </Text>
        </TouchableOpacity>
      )}

      <TouchableOpacity
        style={[styles.submitBtn, (!isFormValid || isSubmitting || authLoading) && styles.submitBtnDisabled]}
        onPress={handleSubmit}
        disabled={!isFormValid || isSubmitting || authLoading}
        activeOpacity={0.8}
      >
        {isSubmitting || authLoading ? (
          <ActivityIndicator color="#FFF" />
        ) : (
          <>
            <Text style={styles.submitBtnText}>{isEditing ? 'Save Changes' : 'Create Profile'}</Text>
            <Ionicons name="arrow-forward" size={20} color="#FFF" />
          </>
        )}
      </TouchableOpacity>
    </ScrollView>
  );

  return (
    <View style={styles.container}>
      {/* "padding" lifts the form above the keyboard via JS keyboard events on
          both platforms, so the lower fields and the Create Profile button stay
          reachable regardless of the native windowSoftInputMode — works on
          OTA-only builds. (Previously Android had no keyboard handling, so the
          lower fields and submit button sat behind the keyboard.) */}
      <KeyboardAvoidingView
        behavior="padding"
        style={styles.container}
      >
        {contentNode}
      </KeyboardAvoidingView>
      <ConfirmSheet
        visible={showPhotoPicker}
        title="Update Photo"
        message="Choose how to update your profile photo."
        variant="info"
        buttons={[
          { text: 'Take Photo', style: 'default', onPress: () => { setShowPhotoPicker(false); launchCamera(); } },
          { text: 'Choose from Library', style: 'default', onPress: () => { setShowPhotoPicker(false); launchGallery(); } },
          { text: 'Cancel', style: 'cancel' },
        ]}
        onClose={() => setShowPhotoPicker(false)}
      />
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surfaceLight },
    scrollView: { flex: 1 },
    scrollContent: { flexGrow: 1, paddingHorizontal: 24 },
    headerContainer: { alignItems: 'center', marginBottom: 32, position: 'relative' },
    backButton: {
      position: 'absolute', left: 0, top: 0, padding: 8,
      backgroundColor: colors.surface, borderRadius: 12,
      shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.05, shadowRadius: 4, elevation: 2,
    },
    avatarContainer: { position: 'relative', marginBottom: 16 },
    avatarCircle: {
      width: 90, height: 90, borderRadius: 45,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: colors.primary,
    },
    badge: {
      position: 'absolute', bottom: 0, right: 0,
      backgroundColor: colors.primary, width: 28, height: 28, borderRadius: 14,
      justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: colors.surfaceLight,
    },
    title: { fontSize: 28, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text, marginBottom: 6 },
    subtitle: { fontSize: 15, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, textAlign: 'center' },
    signedInCard: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, paddingHorizontal: 16, paddingVertical: 12,
      borderRadius: 16, marginTop: 20,
      shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.05, shadowRadius: 8, elevation: 2, width: '100%',
    },
    signedInText: { flex: 1, fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim, marginLeft: 10 },
    signedInPhone: { color: colors.text, fontFamily: 'PlusJakartaSans_700Bold' },
    changeText: { color: colors.primary, fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13 },
    formCard: {
      backgroundColor: colors.surface, borderRadius: 24, padding: 24,
      shadowColor: '#000', shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.05, shadowRadius: 12, elevation: 3, marginBottom: 24,
    },
    inputContainer: { marginBottom: 20 },
    inputLabel: {
      fontSize: 11, fontFamily: 'PlusJakartaSans_700Bold', color: colors.textDim,
      letterSpacing: 1, marginBottom: 8, marginLeft: 4,
    },
    inputBox: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight, borderWidth: 1.5, borderColor: 'transparent',
      borderRadius: 16, height: 56, paddingHorizontal: 16,
    },
    inputBoxFocused: { borderColor: colors.primary, backgroundColor: `${colors.primary}05` },
    inputIcon: { marginRight: 12 },
    textInput: {
      flex: 1, fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text, includeFontPadding: false,
    },
    dropdownContainer: { marginTop: 8, backgroundColor: colors.surfaceLight, borderRadius: 16, overflow: 'hidden' },
    dropdownOption: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 14, paddingHorizontal: 16 },
    dropdownOptionBorder: { borderBottomWidth: 1, borderBottomColor: colors.border },
    dropdownOptionSelected: { backgroundColor: `${colors.primary}10` },
    dropdownOptionText: { fontSize: 15, fontFamily: 'PlusJakartaSans_500Medium', color: colors.text },
    dropdownOptionTextActive: { fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary },
    tosContainer: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 32, paddingHorizontal: 8 },
    checkbox: {
      width: 24, height: 24, borderRadius: 8, borderWidth: 2, borderColor: colors.textDim,
      justifyContent: 'center', alignItems: 'center', marginRight: 12, marginTop: 2,
    },
    checkboxActive: { backgroundColor: colors.primary, borderColor: colors.primary },
    tosText: { flex: 1, fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, lineHeight: 22 },
    link: { color: colors.primary, fontFamily: 'PlusJakartaSans_600SemiBold' },
    submitBtn: {
      backgroundColor: colors.primary, height: 60, borderRadius: 20,
      flexDirection: 'row', justifyContent: 'center', alignItems: 'center',
      shadowColor: colors.primary, shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.3, shadowRadius: 12, elevation: 6,
    },
    submitBtnDisabled: { backgroundColor: colors.border, shadowOpacity: 0, elevation: 0 },
    submitBtnText: { color: '#FFF', fontSize: 17, fontFamily: 'PlusJakartaSans_700Bold', marginRight: 8 },
  });
}
