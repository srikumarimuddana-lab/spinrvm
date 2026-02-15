import React, { useState } from 'react';
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
  Alert,
  ScrollView,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

export default function ProfileSetupScreen() {
  const router = useRouter();
  const { user, createProfile, isLoading: authLoading, error: authError } = useAuthStore();

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [gender, setGender] = useState('');
  const [showGenderPicker, setShowGenderPicker] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const validateEmail = (email: string): boolean => {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
  };

  const handleSubmit = async () => {
    if (!firstName.trim() || !lastName.trim() || !email.trim() || !gender) {
      Alert.alert('Missing Info', 'Please fill in all fields');
      return;
    }

    if (!validateEmail(email)) {
      Alert.alert('Invalid Email', 'Please enter a valid email address');
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
      router.replace('/(tabs)');
    } catch (err: any) {
      Alert.alert('Error', err.message || 'Failed to create profile');
    } finally {
      setIsSubmitting(false);
    }
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
            {/* Header */}
            <View style={styles.header}>
              <Text style={styles.title}>Complete your{"\n"}profile</Text>
              <Text style={styles.subtitle}>
                We need a few details to get you started with Spinr.
              </Text>
            </View>

            {/* Form */}
            <View style={styles.form}>
              <View style={styles.inputGroup}>
                <Text style={styles.label}>First Name</Text>
                <TextInput
                  style={styles.input}
                  value={firstName}
                  onChangeText={setFirstName}
                  placeholder="Enter your first name"
                  placeholderTextColor="#999"
                  autoCapitalize="words"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>Last Name</Text>
                <TextInput
                  style={styles.input}
                  value={lastName}
                  onChangeText={setLastName}
                  placeholder="Enter your last name"
                  placeholderTextColor="#999"
                  autoCapitalize="words"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>Email</Text>
                <TextInput
                  style={styles.input}
                  value={email}
                  onChangeText={setEmail}
                  placeholder="email@example.com"
                  placeholderTextColor="#999"
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>Gender</Text>
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
                    color="#666"
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
                          <Ionicons name="checkmark" size={20} color={SpinrConfig.theme.colors.primary} />
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
                !isFormValid && styles.submitButtonDisabled,
              ]}
              onPress={handleSubmit}
              disabled={!isFormValid || isSubmitting || authLoading}
              activeOpacity={0.8}
            >
              {isSubmitting || authLoading ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.submitButtonText}>Get Started</Text>
              )}
            </TouchableOpacity>
          </ScrollView>
        </TouchableWithoutFeedback>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
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
  header: {
    marginBottom: 40,
  },
  title: {
    fontSize: 32,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    lineHeight: 40,
  },
  subtitle: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666666',
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
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginBottom: 8,
  },
  input: {
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  citySelector: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  citySelectorText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  placeholder: {
    color: '#999',
  },
  cityDropdown: {
    marginTop: 8,
    backgroundColor: '#FFF',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#E0E0E0',
    overflow: 'hidden',
  },
  cityOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#F0F0F0',
  },
  cityOptionSelected: {
    backgroundColor: '#FFF5F5',
  },
  cityOptionText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  cityOptionTextSelected: {
    color: SpinrConfig.theme.colors.primary,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  submitButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 28,
    paddingVertical: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  submitButtonDisabled: {
    backgroundColor: '#FFAAAA',
  },
  submitButtonText: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFFFFF',
  },
});
