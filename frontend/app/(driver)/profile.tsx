import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Image } from 'react-native';
import { useAuthStore } from '../../store/authStore';
import { useRouter } from 'expo-router';
import SpinrConfig from '../../config/spinr.config';

export default function DriverProfile() {
  const { driver, user, toggleDriverMode, logout } = useAuthStore();
  const router = useRouter();

  const handleSwitch = () => {
      toggleDriverMode();
      router.replace('/(tabs)');
  };

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Driver Profile</Text>

      <View style={styles.info}>
        <Text style={styles.label}>Name</Text>
        <Text style={styles.value}>{driver?.name || user?.first_name}</Text>

        <Text style={styles.label}>Vehicle</Text>
        <Text style={styles.value}>{driver?.vehicle_make} {driver?.vehicle_model}</Text>

        <Text style={styles.label}>Plate</Text>
        <Text style={styles.value}>{driver?.license_plate}</Text>

        <Text style={styles.label}>Rating</Text>
        <Text style={styles.value}>{driver?.rating} ⭐</Text>
      </View>

      <TouchableOpacity style={styles.switchButton} onPress={handleSwitch}>
        <Text style={styles.switchButtonText}>Switch to Rider Mode</Text>
      </TouchableOpacity>

      <TouchableOpacity style={styles.logoutButton} onPress={logout}>
        <Text style={styles.logoutButtonText}>Log Out</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, backgroundColor: '#fff' },
  header: { fontSize: 24, fontWeight: 'bold', marginBottom: 30 },
  info: { marginBottom: 40 },
  label: { fontSize: 14, color: '#666', marginBottom: 4 },
  value: { fontSize: 18, fontWeight: '500', marginBottom: 20 },
  switchButton: {
    backgroundColor: '#333',
    padding: 16,
    borderRadius: 12,
    alignItems: 'center',
    marginBottom: 16
  },
  switchButtonText: { color: 'white', fontWeight: 'bold', fontSize: 16 },
  logoutButton: {
    padding: 16,
    alignItems: 'center'
  },
  logoutButtonText: { color: 'red', fontSize: 16 }
});
