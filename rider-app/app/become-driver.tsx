import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  Alert,
  ScrollView,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as DocumentPicker from 'expo-document-picker';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';
import { uploadFile } from '@shared/api/upload';

// Steps: 0=Intro, 1=Personal, 2=Vehicle, 3=Docs, 4=Review
const STEPS = ['Intro', 'Personal', 'Vehicle', 'Documents', 'Review'];

interface Requirement {
  id: string;
  name: string;
  description: string;
  is_mandatory: boolean;
  requires_back_side: boolean;
}

interface DocState {
  front?: string;
  back?: string;
  expiry?: string;
}

export default function BecomeDriverScreen() {
  const router = useRouter();
  const { registerDriver, isLoading, user } = useAuthStore();

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

  // Dynamic Requirements
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [docs, setDocs] = useState<Record<string, DocState>>({});

  // UI State
  const [vehicleTypes, setVehicleTypes] = useState<any[]>([]);
  const [loadingTypes, setLoadingTypes] = useState(false);
  const [uploadingDoc, setUploadingDoc] = useState<string | null>(null); // reqId_side

  useEffect(() => {
    fetchVehicleTypes();
    fetchRequirements();
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

  const fetchRequirements = async () => {
    try {
      const response = await fetch(`${SpinrConfig.backendUrl}/api/drivers/requirements`);
      if (response.ok) {
        const data = await response.json();
        setRequirements(data);
      }
    } catch (e) {
      console.log('Error fetching requirements:', e);
    }
  };

  const handleUpload = async (reqId: string, side: 'front' | 'back') => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['image/*', 'application/pdf'],
        copyToCacheDirectory: true,
      });

      if (result.canceled) return;

      const asset = result.assets[0];
      setUploadingDoc(`${reqId}_${side}`);

      const url = await uploadFile(asset.uri, asset.name, asset.mimeType || 'image/jpeg');

      setDocs(prev => ({
        ...prev,
        [reqId]: {
          ...prev[reqId],
          [side]: url
        }
      }));
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
        const missing: string[] = [];
        if (!licenseNumber) missing.push('Driver License Number');

        requirements.forEach(req => {
          if (req.is_mandatory) {
            const uploaded = docs[req.id];
            if (!uploaded?.front) {
              missing.push(`${req.name} (Front)`);
            }
            if (req.requires_back_side && !uploaded?.back) {
              missing.push(`${req.name} (Back)`);
            }
            // Check expiry if we want to enforce it for everything
            // For now, let's enforce expiry only for License and Insurance which are critical
            if (['Driving License', 'Vehicle Insurance'].includes(req.name) && !uploaded?.expiry) {
              missing.push(`${req.name} Expiry Date`);
            }
          }
        });

        if (missing.length > 0) {
          Alert.alert('Missing Documents', `Please provide: ${missing.join(', ')}`);
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
      // Map expiry dates to legacy fields
      const getExpiry = (name: string) => {
        const req = requirements.find(r => r.name === name);
        return req && docs[req.id]?.expiry ? new Date(docs[req.id].expiry!).toISOString() : undefined;
      };

      const licenseExpiry = getExpiry('Driving License');
      const insuranceExpiry = getExpiry('Vehicle Insurance');
      const inspectionExpiry = getExpiry('Vehicle Inspection');
      const backgroundExpiry = getExpiry('Background Check');

      // Construct dynamic documents list
      const documentsPayload: any[] = [];
      Object.entries(docs).forEach(([reqId, data]) => {
        const req = requirements.find(r => r.id === reqId);
        if (data.front) {
          documentsPayload.push({
            requirement_id: reqId,
            document_url: data.front,
            side: 'front',
            document_type: req?.name
          });
        }
        if (data.back) {
          documentsPayload.push({
            requirement_id: reqId,
            document_url: data.back,
            side: 'back',
            document_type: req?.name
          });
        }
      });

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

        // Legacy/Top-level Fields
        license_number: licenseNumber,
        license_expiry_date: licenseExpiry,
        insurance_expiry_date: insuranceExpiry,
        vehicle_inspection_expiry_date: inspectionExpiry,
        background_check_expiry_date: backgroundExpiry,

        // New Dynamic Docs
        documents: documentsPayload,
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

              {requirements.map(req => (
                <View key={req.id} style={styles.docContainer}>
                  <Text style={styles.label}>{req.name} {req.is_mandatory && '*'}</Text>

                  {/* Front Side */}
                  <TouchableOpacity
                    style={[styles.uploadButton, docs[req.id]?.front ? styles.uploadSuccess : {}]}
                    onPress={() => handleUpload(req.id, 'front')}
                    disabled={!!uploadingDoc}
                  >
                    {uploadingDoc === `${req.id}_front` ? (
                      <ActivityIndicator color={SpinrConfig.theme.colors.primary} />
                    ) : docs[req.id]?.front ? (
                      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                        <Ionicons name="checkmark-circle" size={20} color="green" />
                        <Text style={[styles.uploadText, { color: 'green' }]}>{req.requires_back_side ? 'Front Uploaded' : 'Uploaded'}</Text>
                      </View>
                    ) : (
                      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                        <Ionicons name="cloud-upload-outline" size={20} color="#666" />
                        <Text style={styles.uploadText}>{req.requires_back_side ? 'Upload Front' : 'Upload Document'}</Text>
                      </View>
                    )}
                  </TouchableOpacity>

                  {/* Back Side */}
                  {req.requires_back_side && (
                    <TouchableOpacity
                      style={[styles.uploadButton, { marginTop: 10 }, docs[req.id]?.back ? styles.uploadSuccess : {}]}
                      onPress={() => handleUpload(req.id, 'back')}
                      disabled={!!uploadingDoc}
                    >
                      {uploadingDoc === `${req.id}_back` ? (
                        <ActivityIndicator color={SpinrConfig.theme.colors.primary} />
                      ) : docs[req.id]?.back ? (
                        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                          <Ionicons name="checkmark-circle" size={20} color="green" />
                          <Text style={[styles.uploadText, { color: 'green' }]}>Back Uploaded</Text>
                        </View>
                      ) : (
                        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                          <Ionicons name="cloud-upload-outline" size={20} color="#666" />
                          <Text style={styles.uploadText}>Upload Back</Text>
                        </View>
                      )}
                    </TouchableOpacity>
                  )}

                  {/* Expiry Date - Show for all, or configured ones */}
                  <View style={{ marginTop: 10 }}>
                    <Text style={styles.subLabel}>Expiry Date (YYYY-MM-DD)</Text>
                    <TextInput
                      style={styles.dateInput}
                      value={docs[req.id]?.expiry || ''}
                      onChangeText={(t) => setDocs(p => ({
                        ...p,
                        [req.id]: { ...p[req.id], expiry: t }
                      }))}
                      placeholder="2025-12-31"
                      placeholderTextColor="#999"
                    />
                  </View>
                </View>
              ))}

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
                <Text style={styles.reviewRow}><Text style={{ fontWeight: 'bold' }}>Docs Set:</Text> {Object.values(docs).filter(d => d.front).length} / {requirements.length}</Text>
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
    fontSize: 14, color: '#000'
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
