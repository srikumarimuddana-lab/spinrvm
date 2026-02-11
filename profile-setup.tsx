import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, ScrollView } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useUserStore } from '../store/userStore';
import { supabase } from '../config/supabase';

export default function DriverProfileSetup() {
  const router = useRouter();
  const { user, setProfile } = useUserStore();
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [vehicleModel, setVehicleModel] = useState('');
  const [licensePlate, setLicensePlate] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!firstName || !lastName || !vehicleModel || !licensePlate) {
      Alert.alert('Missing Info', 'Please fill in all fields');
      return;
    }

    setLoading(true);
    try {
      // 1. Create/Update Base Profile
      const { error: profileError } = await supabase
        .from('profiles')
        .upsert({
          id: user.uid,
          first_name: firstName,
          last_name: lastName,
          phone: user.phoneNumber,
        });

      if (profileError) throw profileError;

      // 2. Create Driver Entry
      const { data: driver, error: driverError } = await supabase
        .from('drivers')
        .upsert({
          id: user.uid,
          vehicle_model: vehicleModel,
          license_plate: licensePlate,
          is_online: false,
        })
        .select()
        .single();

      if (driverError) throw driverError;

      setProfile(driver);
      router.replace('/(tabs)');
    } catch (error: any) {
      console.error(error);
      Alert.alert('Error', error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Driver Registration</Text>
        <Text style={styles.subtitle}>Complete your profile to start driving</Text>

        <View style={styles.form}>
          <Text style={styles.label}>First Name</Text>
          <TextInput style={styles.input} value={firstName} onChangeText={setFirstName} placeholder="John" placeholderTextColor="#666" />

          <Text style={styles.label}>Last Name</Text>
          <TextInput style={styles.input} value={lastName} onChangeText={setLastName} placeholder="Doe" placeholderTextColor="#666" />

          <Text style={styles.label}>Vehicle Model</Text>
          <TextInput style={styles.input} value={vehicleModel} onChangeText={setVehicleModel} placeholder="Toyota Camry" placeholderTextColor="#666" />

          <Text style={styles.label}>License Plate</Text>
          <TextInput style={styles.input} value={licensePlate} onChangeText={setLicensePlate} placeholder="ABC-123" placeholderTextColor="#666" />
        </View>

        <TouchableOpacity style={styles.button} onPress={handleSubmit} disabled={loading}>
          {loading ? <ActivityIndicator color="#FFF" /> : <Text style={styles.buttonText}>Complete Setup</Text>}
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1A1A1A' },
  content: { padding: 24 },
  title: { fontSize: 28, fontWeight: 'bold', color: '#FFF', marginBottom: 8 },
  subtitle: { fontSize: 16, color: '#CCC', marginBottom: 32 },
  form: { marginBottom: 32 },
  label: { color: '#FFF', fontSize: 14, marginBottom: 8, fontWeight: '600' },
  input: {
    backgroundColor: '#333',
    borderRadius: 12,
    padding: 16,
    color: '#FFF',
    fontSize: 16,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: '#444'
  },
  button: {
    backgroundColor: '#10B981',
    padding: 18,
    borderRadius: 12,
    alignItems: 'center',
  },
  buttonText: {
    color: '#FFF',
    fontSize: 18,
    fontWeight: 'bold',
  },
});