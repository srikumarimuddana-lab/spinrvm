import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, Platform } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useAuthStore } from '../store/authStore';
import api from '../api/client';

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
      console.log('Storage setItem error:', e);
    }
  }
};

export default function OtpScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ verificationId?: string, phoneNumber: string, mode?: string }>();
  const { phoneNumber, verificationId, mode } = params;
  const isBackendMode = mode === 'backend' || !verificationId;
  const codeLength = isBackendMode ? 4 : 6;

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const { verifyOTP, user, initialize, clearError } = useAuthStore();

  useEffect(() => {
    if (user) {
      if (user.profile_complete) {
        router.replace('/(tabs)/activity');
      } else {
        router.replace('/profile-setup');
      }
    }
  }, [user]);

  const handleVerify = async () => {
    if (!code || code.length !== codeLength) {
      Alert.alert('Invalid Code', `Please enter the ${codeLength}-digit code sent to your phone.`);
      return;
    }

    setVerifying(true);
    clearError();

    try {
      if (isBackendMode) {
        // Backend OTP verification
        const response = await api.post('/auth/verify-otp', {
          phone: phoneNumber,
          code: code
        });

        const { token } = response.data;
        if (token) {
          await storage.setItem('auth_token', token);
          // Re-initialize auth store with the new token
          await initialize();
        }
      } else {
        // Firebase OTP verification
        await verifyOTP(verificationId!, code);
      }
      // Navigation is handled by the useEffect above once user is populated
    } catch (err: any) {
      const message = err.response?.data?.detail || err.message || 'Invalid code. Please try again.';
      Alert.alert('Verification Failed', message);
    } finally {
      setVerifying(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Enter verification code</Text>
      <Text style={styles.subtitle}>Sent to {phoneNumber}</Text>

      <TextInput
        style={styles.input}
        placeholder={isBackendMode ? '1234' : '000000'}
        placeholderTextColor="#ccc"
        keyboardType="number-pad"
        value={code}
        onChangeText={setCode}
        maxLength={codeLength}
        autoFocus
        textAlign="center"
      />

      <TouchableOpacity
        style={[styles.button, verifying && styles.buttonDisabled]}
        onPress={handleVerify}
        disabled={verifying}
      >
        {verifying ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Verify</Text>
        )}
      </TouchableOpacity>

      <TouchableOpacity onPress={() => router.back()} style={styles.resendLink}>
        <Text style={styles.resendText}>Change phone number</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff', padding: 24, paddingTop: 80 },
  title: { fontSize: 24, fontWeight: 'bold', marginBottom: 8, color: '#333', textAlign: 'center' },
  subtitle: { fontSize: 16, color: '#666', marginBottom: 32, textAlign: 'center' },
  input: {
    fontSize: 32,
    fontWeight: 'bold',
    letterSpacing: 8,
    borderBottomWidth: 2,
    borderBottomColor: '#000',
    marginBottom: 40,
    paddingVertical: 10,
    color: '#000'
  },
  button: {
    backgroundColor: '#000',
    borderRadius: 8,
    height: 56,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20
  },
  buttonDisabled: { backgroundColor: '#666' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
  resendLink: { alignItems: 'center' },
  resendText: { color: '#007AFF', fontSize: 16 },
});
