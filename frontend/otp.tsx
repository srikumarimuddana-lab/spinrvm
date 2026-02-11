import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { PhoneAuthProvider, signInWithCredential } from 'firebase/auth';
import { auth } from '../config/firebase';

export default function DriverOtpScreen() {
  const router = useRouter();
  const { verificationId, phoneNumber } = useLocalSearchParams<{ verificationId: string, phoneNumber: string }>();
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);

  const handleVerify = async () => {
    if (!code || code.length !== 6) {
      Alert.alert('Invalid Code', 'Please enter the 6-digit code sent to your phone.');
      return;
    }

    setLoading(true);
    try {
      const credential = PhoneAuthProvider.credential(
        verificationId,
        code
      );
      
      await signInWithCredential(auth, credential);
      
      // Navigate to dashboard (assuming (tabs) exists or will be created)
      router.replace('/(tabs)');
    } catch (error: any) {
      console.error(error);
      Alert.alert('Verification Failed', 'Invalid code. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Verification Code</Text>
      <Text style={styles.subtitle}>Sent to {phoneNumber}</Text>

      <TextInput
        style={styles.input}
        placeholder="000000"
        placeholderTextColor="#444"
        keyboardType="number-pad"
        value={code}
        onChangeText={setCode}
        maxLength={6}
        autoFocus
        textAlign="center"
      />

      <TouchableOpacity 
        style={[styles.button, loading && styles.buttonDisabled]} 
        onPress={handleVerify}
        disabled={loading}
      >
        {loading ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Verify & Login</Text>
        )}
      </TouchableOpacity>

      <TouchableOpacity onPress={() => router.back()} style={styles.resendLink}>
        <Text style={styles.resendText}>Change phone number</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1A1A1A', padding: 24, paddingTop: 80 },
  title: { fontSize: 24, fontWeight: 'bold', marginBottom: 8, color: '#FFFFFF', textAlign: 'center' },
  subtitle: { fontSize: 16, color: '#CCCCCC', marginBottom: 32, textAlign: 'center' },
  input: { 
    fontSize: 32, 
    fontWeight: 'bold', 
    letterSpacing: 8,
    borderBottomWidth: 2, 
    borderBottomColor: '#10B981', 
    marginBottom: 40,
    paddingVertical: 10,
    color: '#FFFFFF'
  },
  button: { 
    backgroundColor: '#10B981', 
    borderRadius: 12, 
    height: 56, 
    justifyContent: 'center', 
    alignItems: 'center',
    marginBottom: 20
  },
  buttonDisabled: { backgroundColor: '#059669', opacity: 0.7 },
  buttonText: { color: '#fff', fontSize: 18, fontWeight: '600' },
  resendLink: { alignItems: 'center' },
  resendText: { color: '#10B981', fontSize: 16 },
});