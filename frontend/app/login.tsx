import React, { useState, useRef } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, KeyboardAvoidingView, Platform } from 'react-native';
import { useRouter } from 'expo-router';
import { FirebaseRecaptchaVerifierModal } from 'expo-firebase-recaptcha';
import { PhoneAuthProvider } from 'firebase/auth';
import { auth } from '../config/firebaseConfig';

export default function LoginScreen() {
  const router = useRouter();
  const [phoneNumber, setPhoneNumber] = useState('');
  const [loading, setLoading] = useState(false);
  const recaptchaVerifier = useRef(null);

  const handleSendCode = async () => {
    if (!phoneNumber || phoneNumber.length < 10) {
      Alert.alert('Invalid Number', 'Please enter a valid 10-digit phone number.');
      return;
    }

    setLoading(true);
    try {
      // Format number to E.164 format (assuming Canadian +1)
      const formattedNumber = `+1${phoneNumber.replace(/\D/g, '')}`;

      const phoneProvider = new PhoneAuthProvider(auth);
      const verificationId = await phoneProvider.verifyPhoneNumber(
        formattedNumber,
        recaptchaVerifier.current!
      );

      // Navigate to OTP screen with the verification ID
      router.push({
        pathname: '/otp',
        params: { verificationId, phoneNumber: formattedNumber }
      });
    } catch (error: any) {
      console.error(error);
      Alert.alert('Login Failed', error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      style={styles.container}
    >
      <FirebaseRecaptchaVerifierModal
        ref={recaptchaVerifier}
        firebaseConfig={auth.app.options}
      // attemptInvisibleVerification={true} 
      />

      <View style={styles.content}>
        <Text style={styles.title}>Enter your mobile number</Text>

        <View style={styles.inputContainer}>
          <View style={styles.flagContainer}>
            <Text style={styles.flag}>🇨🇦 +1</Text>
          </View>
          <TextInput
            style={styles.input}
            placeholder="204 555 0123"
            placeholderTextColor="#999"
            keyboardType="number-pad"
            value={phoneNumber}
            onChangeText={setPhoneNumber}
            maxLength={10}
            editable={!loading}
          />
        </View>

        <TouchableOpacity
          style={[styles.button, loading && styles.buttonDisabled]}
          onPress={handleSendCode}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Continue</Text>
          )}
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  content: { flex: 1, padding: 24, justifyContent: 'center' },
  title: { fontSize: 24, fontWeight: 'bold', marginBottom: 24, color: '#333' },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 8,
    marginBottom: 24,
    height: 56,
  },
  flagContainer: {
    paddingHorizontal: 16,
    borderRightWidth: 1,
    borderRightColor: '#ddd',
    justifyContent: 'center',
    height: '100%'
  },
  flag: { fontSize: 16, fontWeight: '600' },
  input: { flex: 1, paddingHorizontal: 16, fontSize: 18, height: '100%' },
  button: { backgroundColor: '#000', borderRadius: 8, height: 56, justifyContent: 'center', alignItems: 'center' },
  buttonDisabled: { backgroundColor: '#666' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
});