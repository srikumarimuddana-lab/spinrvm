import React, { useState, useEffect } from 'react';
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
import { useAuthStore } from '../store/authStore';
import SpinrConfig from '../config/spinr.config';

export default function BecomeDriverScreen() {
  const router = useRouter();
  const { registerDriver, isLoading, error, clearError, token } = useAuthStore();

  const [vehicleMake, setVehicleMake] = useState('');
  const [vehicleModel, setVehicleModel] = useState('');
  const [vehicleColor, setVehicleColor] = useState('');
  const [licensePlate, setLicensePlate] = useState('');
  const [vehicleType, setVehicleType] = useState('');
  const [vehicleTypes, setVehicleTypes] = useState<any[]>([]);
  const [showTypePicker, setShowTypePicker] = useState(false);
  const [loadingTypes, setLoadingTypes] = useState(true);

  useEffect(() => {
    fetchVehicleTypes();
  }, []);

  const fetchVehicleTypes = async () => {
    try {
      const response = await fetch(`${SpinrConfig.backendUrl}/api/vehicle-types`);
      const data = await response.json();
      setVehicleTypes(data);
    } catch (e) {
      console.log('Error fetching vehicle types:', e);
      Alert.alert('Error', 'Failed to load vehicle types');
    } finally {
      setLoadingTypes(false);
    }
  };

  const handleSubmit = async () => {
    if (!vehicleMake.trim() || !vehicleModel.trim() || !vehicleColor.trim() || !licensePlate.trim() || !vehicleType) {
      Alert.alert('Missing Info', 'Please fill in all fields');
      return;
    }

    try {
      await registerDriver({
        vehicle_make: vehicleMake.trim(),
        vehicle_model: vehicleModel.trim(),
        vehicle_color: vehicleColor.trim(),
        license_plate: licensePlate.trim(),
        vehicle_type_id: vehicleType,
      });

      Alert.alert('Success', 'You are now a driver!', [
        { text: 'Go to Dashboard', onPress: () => router.replace('/(driver)') }
      ]);
    } catch (err: any) {
      Alert.alert('Registration Failed', err.message || 'Could not register as driver');
    }
  };

  const isFormValid = vehicleMake && vehicleModel && vehicleColor && licensePlate && vehicleType;

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
            <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
              <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
            </TouchableOpacity>

            <View style={styles.header}>
              <Text style={styles.title}>Become a Driver</Text>
              <Text style={styles.subtitle}>
                Earn money on your schedule. Enter your vehicle details to get started.
              </Text>
            </View>

            <View style={styles.form}>
              <View style={styles.inputGroup}>
                <Text style={styles.label}>Vehicle Make</Text>
                <TextInput
                  style={styles.input}
                  value={vehicleMake}
                  onChangeText={setVehicleMake}
                  placeholder="e.g. Toyota"
                  placeholderTextColor="#999"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>Vehicle Model</Text>
                <TextInput
                  style={styles.input}
                  value={vehicleModel}
                  onChangeText={setVehicleModel}
                  placeholder="e.g. Camry"
                  placeholderTextColor="#999"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>Vehicle Color</Text>
                <TextInput
                  style={styles.input}
                  value={vehicleColor}
                  onChangeText={setVehicleColor}
                  placeholder="e.g. Silver"
                  placeholderTextColor="#999"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>License Plate</Text>
                <TextInput
                  style={styles.input}
                  value={licensePlate}
                  onChangeText={setLicensePlate}
                  placeholder="e.g. ABC 123"
                  placeholderTextColor="#999"
                  autoCapitalize="characters"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.label}>Vehicle Type</Text>
                <TouchableOpacity
                  style={styles.selector}
                  onPress={() => setShowTypePicker(!showTypePicker)}
                  activeOpacity={0.7}
                >
                  <Text style={[styles.selectorText, !vehicleType && styles.placeholder]}>
                    {vehicleType ? vehicleTypes.find(v => v.id === vehicleType)?.name : 'Select vehicle type'}
                  </Text>
                  <Ionicons
                    name={showTypePicker ? 'chevron-up' : 'chevron-down'}
                    size={20}
                    color="#666"
                  />
                </TouchableOpacity>

                {showTypePicker && (
                  <View style={styles.dropdown}>
                    {loadingTypes ? (
                      <ActivityIndicator style={{ padding: 20 }} />
                    ) : (
                      vehicleTypes.map((vt) => (
                        <TouchableOpacity
                          key={vt.id}
                          style={[
                            styles.option,
                            vehicleType === vt.id && styles.optionSelected,
                          ]}
                          onPress={() => {
                            setVehicleType(vt.id);
                            setShowTypePicker(false);
                          }}
                        >
                          <View>
                            <Text style={[
                              styles.optionText,
                              vehicleType === vt.id && styles.optionTextSelected,
                            ]}>
                              {vt.name}
                            </Text>
                            <Text style={styles.optionSubtext}>{vt.description}</Text>
                          </View>
                          {vehicleType === vt.id && (
                            <Ionicons name="checkmark" size={20} color={SpinrConfig.theme.colors.primary} />
                          )}
                        </TouchableOpacity>
                      ))
                    )}
                  </View>
                )}
              </View>
            </View>

            <TouchableOpacity
              style={[
                styles.submitButton,
                (!isFormValid || isLoading) && styles.submitButtonDisabled,
              ]}
              onPress={handleSubmit}
              disabled={!isFormValid || isLoading}
              activeOpacity={0.8}
            >
              {isLoading ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.submitButtonText}>Register Vehicle</Text>
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
    paddingTop: 20,
    paddingBottom: 40,
  },
  backButton: {
    marginBottom: 20,
  },
  header: {
    marginBottom: 30,
  },
  title: {
    fontSize: 28,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666666',
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
  selector: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  selectorText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  placeholder: {
    color: '#999',
  },
  dropdown: {
    marginTop: 8,
    backgroundColor: '#FFF',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#E0E0E0',
    overflow: 'hidden',
  },
  option: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#F0F0F0',
  },
  optionSelected: {
    backgroundColor: '#FFF5F5',
  },
  optionText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  optionTextSelected: {
    color: SpinrConfig.theme.colors.primary,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  optionSubtext: {
    fontSize: 12,
    color: '#666',
    marginTop: 2,
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
