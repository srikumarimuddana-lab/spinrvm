import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useAuthStore } from '../store/authStore';

export default function OtpScreen() {
  const router = useRouter();
  const { verificationId, phoneNumber } = useLocalSearchParams<{ verificationId: string, phoneNumber: string }>();
  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const { verifyOTP, user, error, clearError } = useAuthStore();

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
    if (!code || code.length !== 6) {
      Alert.alert('Invalid Code', 'Please enter the 6-digit code sent to your phone.');
      return;
    }

    setVerifying(true);
    clearError();
    try {
      await verifyOTP(verificationId, code);
      // Navigation is handled by the useEffect above once user is populated
    } catch (err: any) {
      Alert.alert('Verification Failed', err.message || 'Invalid code. Please try again.');
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
        placeholder="000000"
        placeholderTextColor="#ccc"
        keyboardType="number-pad"
        value={code}
        onChangeText={setCode}
        maxLength={6}
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
