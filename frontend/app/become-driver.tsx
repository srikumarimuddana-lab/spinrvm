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
  Image,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as DocumentPicker from 'expo-document-picker';
import { useAuthStore } from '../store/authStore';
import SpinrConfig from '../config/spinr.config';
import { uploadFile } from '../api/upload';

// Steps: 0=Intro, 1=Personal, 2=Vehicle, 3=Docs, 4=Review
const STEPS = ['Intro', 'Personal', 'Vehicle', 'Documents', 'Review'];

export default function BecomeDriverScreen() {
  const router = useRouter();
  const { registerDriver, isLoading, error, clearError, user } = useAuthStore();

  const [currentStep, setCurrentStep] = useState(0);

  // Personal Info
  const [firstName, setFirstName] = useState(user?.first_name || '');
  const [lastName, setLastName] = useState(user?.last_name || '');
  const [email, setEmail] = useState(user?.email || '');
  const [city, setCity] = useState(user?.city || 'Saskatoon');

  // Vehicle Info
  const [vehicleMake, setVehicleMake] = useState('');
  const [vehicleModel, setVehicleModel] = useState('');
  const [vehicleColor, setVehicleColor] = useState('');
  const [vehicleYear, setVehicleYear] = useState('');
  const [licensePlate, setLicensePlate] = useState('');
  const [vehicleVin, setVehicleVin] = useState('');
  const [vehicleType, setVehicleType] = useState('');
  const [licenseNumber, setLicenseNumber] = useState('');

  // Documents (URLs)
  const [docs, setDocs] = useState({
    license_front: '',
    license_back: '',
    insurance: '',
    registration: '',
    inspection: '',
    background_check: '',
  });

  // Expiry Dates (YYYY-MM-DD strings for complexity reduction)
  const [dates, setDates] = useState({
    license_expiry: '',
    insurance_expiry: '',
    inspection_expiry: '',
    background_check_expiry: '',
  });

  // UI State
  const [vehicleTypes, setVehicleTypes] = useState<any[]>([]);
  const [loadingTypes, setLoadingTypes] = useState(false);
  const [uploadingDoc, setUploadingDoc] = useState<string | null>(null);

  useEffect(() => {
    fetchVehicleTypes();
  }, []);

  const fetchVehicleTypes = async () => {
    setLoadingTypes(true);
    try {
      const response = await fetch(`${SpinrConfig.backendUrl}/api/vehicle-types`);
      const data = await response.json();
      setVehicleTypes(data);
    } catch (e) {
      console.log('Error fetching vehicle types:', e);
    } finally {
      setLoadingTypes(false);
    }
  };

  const handleUpload = async (key: keyof typeof docs) => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['image/*', 'application/pdf'],
        copyToCacheDirectory: true,
      });

      if (result.canceled) return;

      const asset = result.assets[0];
      setUploadingDoc(key);

      const url = await uploadFile(asset.uri, asset.name, asset.mimeType || 'image/jpeg');

      setDocs(prev => ({ ...prev, [key]: url }));
      Alert.alert('Success', 'Document uploaded successfully');
    } catch (err: any) {
      Alert.alert('Upload Failed', err.message);
    } finally {
      setUploadingDoc(null);
    }
  };

  const validateStep = (step: number) => {
    switch (step) {
      case 1: // Personal
        return firstName && lastName && email && city;
      case 2: // Vehicle
        const year = parseInt(vehicleYear);
        const currentYear = new Date().getFullYear();
        if (!vehicleYear || isNaN(year) || year < currentYear - 9) {
          Alert.alert('Invalid Year', 'Vehicle must be 9 years old or newer.');
          return false;
        }
        return vehicleMake && vehicleModel && vehicleColor && licensePlate && vehicleVin && vehicleType;
      case 3: // Docs
        // Basic check: all required docs + dates
        const missing = [];
        if (!docs.license_front) missing.push('License Front');
        if (!docs.license_back) missing.push('License Back');
        if (!licenseNumber) missing.push('License Number');
        if (!docs.insurance) missing.push('Insurance');
        if (!docs.registration) missing.push('Registration');
        if (!docs.inspection) missing.push('Inspection');
        if (!docs.background_check) missing.push('Background Check');

        if (!dates.license_expiry) missing.push('License Expiry');
        if (!dates.insurance_expiry) missing.push('Insurance Expiry');
        if (!dates.inspection_expiry) missing.push('Inspection Expiry');
        if (!dates.background_check_expiry) missing.push('Background Check Expiry');

        if (missing.length > 0) {
          Alert.alert('Missing Documents', `Please upload: ${missing.join(', ')}`);
          return false;
        }
        return true;
      default:
        return true;
    }
  };

  const nextStep = () => {
    if (validateStep(currentStep)) {
      setCurrentStep(prev => prev + 1);
    }
  };

  const prevStep = () => setCurrentStep(prev => prev - 1);

  const handleSubmit = async () => {
    try {
      await registerDriver({
        // Personal
        first_name: firstName,
        last_name: lastName,
        email,
        city,
        // Vehicle
        vehicle_make: vehicleMake,
        vehicle_model: vehicleModel,
        vehicle_color: vehicleColor,
        vehicle_year: parseInt(vehicleYear),
        license_plate: licensePlate,
        vehicle_vin: vehicleVin,
        vehicle_type_id: vehicleType,
        // Docs & Dates
        license_number: licenseNumber,

        license_expiry_date: new Date(dates.license_expiry).toISOString(),
        insurance_expiry_date: new Date(dates.insurance_expiry).toISOString(),
        vehicle_inspection_expiry_date: new Date(dates.inspection_expiry).toISOString(),
        background_check_expiry_date: new Date(dates.background_check_expiry).toISOString(),

        documents: docs,
      });

      Alert.alert('Success', 'Application submitted! Waiting for approval.', [
        { text: 'OK', onPress: () => router.replace('/(driver)') }
      ]);
    } catch (err: any) {
      Alert.alert('Registration Failed', err.message);
    }
  };

  const renderInput = (label: string, value: string, setter: (v: string) => void, placeholder: string, keyboardType: any = 'default', maxLength?: number) => (
    <View style={styles.inputGroup}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={setter}
        placeholder={placeholder}
        placeholderTextColor="#999"
        keyboardType={keyboardType}
        maxLength={maxLength}
      />
    </View>
  );

  const renderDocUpload = (label: string, docKey: keyof typeof docs, dateKey?: keyof typeof dates, dateLabel?: string) => (
    <View style={styles.docContainer}>
      <Text style={styles.label}>{label}</Text>

      <TouchableOpacity
        style={[styles.uploadButton, docs[docKey] ? styles.uploadSuccess : {}]}
        onPress={() => handleUpload(docKey)}
        disabled={!!uploadingDoc}
      >
        {uploadingDoc === docKey ? (
          <ActivityIndicator color={SpinrConfig.theme.colors.primary} />
        ) : docs[docKey] ? (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Ionicons name="checkmark-circle" size={20} color="green" />
            <Text style={[styles.uploadText, { color: 'green', marginLeft: 8 }]}>Uploaded</Text>
          </View>
        ) : (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Ionicons name="cloud-upload-outline" size={20} color="#666" />
            <Text style={styles.uploadText}>Select Document</Text>
          </View>
        )}
      </TouchableOpacity>

      {dateKey && (
        <View style={{ marginTop: 10 }}>
          <Text style={styles.subLabel}>{dateLabel || 'Expiry Date (YYYY-MM-DD)'}</Text>
          <TextInput
            style={styles.dateInput}
            value={dates[dateKey]}
            onChangeText={(t) => setDates(prev => ({ ...prev, [dateKey]: t }))}
            placeholder="2025-12-31"
            placeholderTextColor="#999"
          />
        </View>
      )}
    </View>
  );

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => currentStep > 0 ? prevStep() : router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>
          <View>
            <Text style={styles.title}>Become a Driver</Text>
            <Text style={styles.stepIndicator}>Step {currentStep} of {STEPS.length - 1}</Text>
          </View>
        </View>

        <ScrollView contentContainerStyle={styles.scrollContent}>

          {currentStep === 0 && (
            <View>
              <Text style={styles.sectionTitle}>Welcome to Spinr Driver</Text>
              <Text style={styles.subtitle}>
                To maintain safety and compliance with Saskatchewan regulations, we need to collect your personal, vehicle, and document information.
              </Text>
              <Text style={styles.subtitle}>
                Your vehicle must be 9 years old or newer (2017+).
              </Text>
              <TouchableOpacity style={styles.primaryButton} onPress={() => setCurrentStep(1)}>
                <Text style={styles.primaryButtonText}>Get Started</Text>
              </TouchableOpacity>
            </View>
          )}

          {currentStep === 1 && (
            <View>
              <Text style={styles.sectionTitle}>Personal Info</Text>
              {renderInput('First Name', firstName, setFirstName, 'John')}
              {renderInput('Last Name', lastName, setLastName, 'Doe')}
              {renderInput('Email', email, setEmail, 'john@example.com', 'email-address')}
              {renderInput('City', city, setCity, 'Saskatoon')}
              <TouchableOpacity style={styles.primaryButton} onPress={nextStep}>
                <Text style={styles.primaryButtonText}>Next: Vehicle</Text>
              </TouchableOpacity>
            </View>
          )}

          {currentStep === 2 && (
            <View>
              <Text style={styles.sectionTitle}>Vehicle Info</Text>
              {renderInput('Year', vehicleYear, setVehicleYear, '2019', 'numeric', 4)}
              {renderInput('Make', vehicleMake, setVehicleMake, 'Toyota')}
              {renderInput('Model', vehicleModel, setVehicleModel, 'Camry')}
              {renderInput('Color', vehicleColor, setVehicleColor, 'Silver')}
              {renderInput('License Plate', licensePlate, setLicensePlate, 'ABC 123')}
              {renderInput('VIN', vehicleVin, setVehicleVin, '1G1...')}

              <Text style={styles.label}>Vehicle Type</Text>
              {loadingTypes ? <ActivityIndicator /> : (
                <View style={styles.typeContainer}>
                  {vehicleTypes.map(vt => (
                    <TouchableOpacity
                      key={vt.id}
                      style={[styles.typeOption, vehicleType === vt.id && styles.typeSelected]}
                      onPress={() => setVehicleType(vt.id)}
                    >
                      <Text style={[styles.typeText, vehicleType === vt.id && styles.typeTextSelected]}>{vt.name}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              )}

              <TouchableOpacity style={styles.primaryButton} onPress={nextStep}>
                <Text style={styles.primaryButtonText}>Next: Documents</Text>
              </TouchableOpacity>
            </View>
          )}

          {currentStep === 3 && (
            <View>
              <Text style={styles.sectionTitle}>Documents</Text>
              <Text style={styles.subtitle}>Please upload clear photos.</Text>

              {renderInput('Driver License Number', licenseNumber, setLicenseNumber, 'S1234-5678-9012', 'default')}

              {renderDocUpload('License Front', 'license_front', 'license_expiry', 'License Expiry')}
              {renderDocUpload('License Back', 'license_back')}
              {renderDocUpload('Insurance', 'insurance', 'insurance_expiry', 'Insurance Expiry')}
              {renderDocUpload('Registration', 'registration')}
              {renderDocUpload('Inspection', 'inspection', 'inspection_expiry', 'Inspection Expiry')}
              {renderDocUpload('Background Check', 'background_check', 'background_check_expiry', 'Check Expiry')}

              <TouchableOpacity style={styles.primaryButton} onPress={nextStep}>
                <Text style={styles.primaryButtonText}>Review Application</Text>
              </TouchableOpacity>
            </View>
          )}

          {currentStep === 4 && (
            <View>
              <Text style={styles.sectionTitle}>Review & Submit</Text>
              <View style={styles.reviewCard}>
                <Text style={styles.reviewRow}><Text style={{ fontWeight: 'bold' }}>Name:</Text> {firstName} {lastName}</Text>
                <Text style={styles.reviewRow}><Text style={{ fontWeight: 'bold' }}>Vehicle:</Text> {vehicleYear} {vehicleColor} {vehicleMake} {vehicleModel}</Text>
                <Text style={styles.reviewRow}><Text style={{ fontWeight: 'bold' }}>Plate:</Text> {licensePlate}</Text>
                <Text style={styles.reviewRow}><Text style={{ fontWeight: 'bold' }}>Docs Uploaded:</Text> {Object.values(docs).filter(Boolean).length}/6</Text>
              </View>

              <TouchableOpacity
                style={[styles.primaryButton, isLoading && styles.disabledButton]}
                onPress={handleSubmit}
                disabled={isLoading}
              >
                {isLoading ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryButtonText}>Submit Application</Text>}
              </TouchableOpacity>
            </View>
          )}

        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  header: { padding: 20, flexDirection: 'row', alignItems: 'center' },
  backButton: { marginRight: 20 },
  title: { fontSize: 24, fontFamily: 'PlusJakartaSans', fontWeight: 'bold' },
  stepIndicator: { color: '#666', fontSize: 14 },
  scrollContent: { padding: 20, paddingBottom: 50 },
  sectionTitle: { fontSize: 22, fontWeight: 'bold', marginBottom: 15, color: '#1A1A1A' },
  subtitle: { fontSize: 16, color: '#666', marginBottom: 20, lineHeight: 22 },

  inputGroup: { marginBottom: 15 },
  label: { fontSize: 14, fontWeight: '600', marginBottom: 5, color: '#333' },
  subLabel: { fontSize: 12, color: '#666', marginBottom: 5 },
  input: {
    borderWidth: 1, borderColor: '#E0E0E0', borderRadius: 12, padding: 12,
    fontSize: 16, color: '#000', fontFamily: 'PlusJakartaSans'
  },
  dateInput: {
    borderWidth: 1, borderColor: '#E0E0E0', borderRadius: 8, padding: 10,
    fontSize: 14
  },

  docContainer: {
    marginBottom: 20, padding: 15, backgroundColor: '#F9FAFB', borderRadius: 12, borderWidth: 1, borderColor: '#F0F0F0'
  },
  uploadButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    padding: 12, borderRadius: 8, borderWidth: 1, borderColor: '#D1D5DB',
    backgroundColor: '#fff'
  },
  uploadSuccess: {
    borderColor: 'green', backgroundColor: '#F0FDF4'
  },
  uploadText: { marginLeft: 10, fontSize: 14, fontWeight: '500' },

  typeContainer: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 20 },
  typeOption: {
    paddingHorizontal: 15, paddingVertical: 10, borderRadius: 20, borderWidth: 1, borderColor: '#E0E0E0'
  },
  typeSelected: { backgroundColor: SpinrConfig.theme.colors.primary, borderColor: SpinrConfig.theme.colors.primary },
  typeText: { color: '#333' },
  typeTextSelected: { color: '#fff', fontWeight: 'bold' },

  primaryButton: {
    backgroundColor: SpinrConfig.theme.colors.primary, borderRadius: 30, padding: 18,
    alignItems: 'center', marginTop: 20
  },
  disabledButton: { opacity: 0.7 },
  primaryButtonText: { color: '#fff', fontSize: 18, fontWeight: 'bold' },

  reviewCard: { padding: 20, backgroundColor: '#F3F4F6', borderRadius: 12 },
  reviewRow: { fontSize: 16, marginBottom: 10 }
});
