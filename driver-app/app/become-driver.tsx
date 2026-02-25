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
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import DateTimePicker from '@react-native-community/datetimepicker';
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
  const [gender, setGender] = useState(user?.gender || '');
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

  const [showDatePicker, setShowDatePicker] = useState(false);
  const [datePickerTarget, setDatePickerTarget] = useState<string | null>(null); // reqId

  useEffect(() => {
    fetchVehicleTypes();
    fetchRequirements();
    loadDraft();
  }, []);

  const onDateChange = (event: any, selectedDate?: Date) => {
    setShowDatePicker(Platform.OS === 'ios'); // Keep open on iOS, close on Android
    if (event.type === 'dismissed') {
      setShowDatePicker(false);
      setDatePickerTarget(null);
      return;
    }

    if (selectedDate && datePickerTarget) {
      // Enforce future date
      const today = new Date();
      today.setHours(0, 0, 0, 0);

      if (selectedDate < today) {
        Alert.alert('Invalid Date', 'Expiry date must be in the future.');
        return; // Do not set
      }

      // Format YYYY-MM-DD
      const dateString = selectedDate.toISOString().split('T')[0];

      setDocs(prev => ({
        ...prev,
        [datePickerTarget]: { ...prev[datePickerTarget], expiry: dateString }
      }));

      if (Platform.OS === 'android') {
        setShowDatePicker(false);
        setDatePickerTarget(null);
      }
    }
  };

  const openDatePicker = (reqId: string) => {
    setDatePickerTarget(reqId);
    setShowDatePicker(true);
  };

  // Save draft whenever relevant state changes
  useEffect(() => {
    saveDraft();
  }, [currentStep, firstName, lastName, email, gender, city, vehicleMake, vehicleModel, vehicleColor, vehicleYear, licensePlate, vehicleVin, vehicleType, licenseNumber, docs]);

  const loadDraft = async () => {
    try {
      const savedDraft = await AsyncStorage.getItem('driver_application_draft');
      if (savedDraft) {
        const data = JSON.parse(savedDraft);
        // Only load if it matches the current user (if we stored user ID, but for now assuming local device is 1 user)
        // Or better yet, we can clear it on logout.

        if (data.step !== undefined) setCurrentStep(data.step);
        if (data.personal) {
          setFirstName(data.personal.firstName || '');
          setLastName(data.personal.lastName || '');
          setEmail(data.personal.email || '');
          setGender(data.personal.gender || '');
          setCity(data.personal.city || '');
        }
        if (data.vehicle) {
          setVehicleMake(data.vehicle.make || '');
          setVehicleModel(data.vehicle.model || '');
          setVehicleColor(data.vehicle.color || '');
          setVehicleYear(data.vehicle.year || '');
          setLicensePlate(data.vehicle.plate || '');
          setVehicleVin(data.vehicle.vin || '');
          setVehicleType(data.vehicle.type || '');
        }
        if (data.docs) {
          setLicenseNumber(data.docs.licenseNumber || '');
          setDocs(data.docs.files || {});
        }
      }
    } catch (e) {
      console.log('Failed to load draft:', e);
    }
  };

  const saveDraft = async () => {
    try {
      const draftData = {
        step: currentStep,
        personal: { firstName, lastName, email, gender, city },
        vehicle: { make: vehicleMake, model: vehicleModel, color: vehicleColor, year: vehicleYear, plate: licensePlate, vin: vehicleVin, type: vehicleType },
        docs: { licenseNumber, files: docs }
      };
      await AsyncStorage.setItem('driver_application_draft', JSON.stringify(draftData));
    } catch (e) {
      console.log('Failed to save draft:', e);
    }
  };

  const fetchVehicleTypes = async () => {
    setLoadingTypes(true);
    console.log('Fetching vehicle types from:', `${SpinrConfig.backendUrl}/api/vehicle-types`);
    try {
      const response = await fetch(`${SpinrConfig.backendUrl}/api/vehicle-types`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setVehicleTypes(data);
    } catch (e: any) {
      console.log('Error fetching vehicle types:', e);
      Alert.alert('Connection Error', `Failed to load vehicle types from ${SpinrConfig.backendUrl}. Please check your internet connection or server status. Error: ${e.message}`);
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

  const processUpload = async (uri: string, name: string, mimeType: string, reqId: string, side: 'front' | 'back') => {
    try {
      setUploadingDoc(`${reqId}_${side}`);
      const url = await uploadFile(uri, name, mimeType);

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

  const pickImage = async (reqId: string, side: 'front' | 'back', useCamera: boolean) => {
    try {
      if (useCamera) {
        const { status } = await ImagePicker.requestCameraPermissionsAsync();
        if (status !== 'granted') {
          Alert.alert('Permission needed', 'Camera permission is required to take photos.');
          return;
        }
      } else {
        const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (status !== 'granted') {
          Alert.alert('Permission needed', 'Gallery permission is required to upload photos.');
          return;
        }
      }

      const result = useCamera
        ? await ImagePicker.launchCameraAsync({
          mediaTypes: ImagePicker.MediaTypeOptions.Images,
          quality: 0.8,
          allowsEditing: true,
        })
        : await ImagePicker.launchImageLibraryAsync({
          mediaTypes: ImagePicker.MediaTypeOptions.Images,
          quality: 0.8,
          allowsEditing: false,
        });

      if (!result.canceled && result.assets && result.assets.length > 0) {
        const asset = result.assets[0];
        // Generate a name if missing (common with camera)
        const name = asset.fileName || `photo_${Date.now()}.jpg`;
        const mimeType = asset.type === 'image' || !asset.type ? 'image/jpeg' : asset.type;

        await processUpload(asset.uri, name, mimeType, reqId, side);
      }
    } catch (e) {
      Alert.alert('Error', 'Failed to pick image');
    }
  };

  const pickFile = async (reqId: string, side: 'front' | 'back') => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['image/*', 'application/pdf'],
        copyToCacheDirectory: true,
      });

      if (result.canceled) return;

      const asset = result.assets[0];
      await processUpload(asset.uri, asset.name, asset.mimeType || 'image/jpeg', reqId, side);
    } catch (e) {
      Alert.alert('Error', 'Failed to pick file');
    }
  };

  const handleUpload = async (reqId: string, side: 'front' | 'back') => {
    if (Platform.OS === 'ios') {
      Alert.alert(
        'Upload Document',
        'Choose a source',
        [
          { text: 'Camera', onPress: () => pickImage(reqId, side, true) },
          { text: 'Gallery', onPress: () => pickImage(reqId, side, false) },
          { text: 'File', onPress: () => pickFile(reqId, side) },
          { text: 'Cancel', style: 'cancel' }
        ]
      );
    } else {
      // Android strict 3 buttons or simple choice
      Alert.alert(
        'Upload Document',
        'Choose a source',
        [
          { text: 'Camera', onPress: () => pickImage(reqId, side, true) },
          { text: 'Gallery', onPress: () => pickImage(reqId, side, false) },
          { text: 'File / Cancel', onPress: () => pickFile(reqId, side) }, // Combine or let back button cancel
        ],
        { cancelable: true }
      );
    }
  };

  const validateStep = (step: number) => {
    switch (step) {
      case 1: // Personal
        return !!(firstName && lastName && email && gender && city);
      case 2: // Vehicle
        // Determine if user has entered ANY vehicle info
        const hasVehicleInfo = vehicleMake || vehicleModel || vehicleColor || vehicleYear || licensePlate || vehicleVin || vehicleType;
        if (!hasVehicleInfo) return true; // Allow proceeding if completely empty (skip)

        // If partial info, enforce valid year and other fields? 
        // For simplification, let's just warn but allow if they explicitly skip. 
        // But here we are in "Next", so maybe enforce completeness if started.
        const year = parseInt(vehicleYear);
        const currentYear = new Date().getFullYear();
        if (vehicleYear && (isNaN(year) || year < currentYear - 9)) {
          Alert.alert('Invalid Year', 'Vehicle must be 9 years old or newer.');
          return false;
        }
        // If they started entering info, require basic fields or use Skip
        if (hasVehicleInfo && (!vehicleMake || !vehicleModel || !licensePlate || !vehicleType)) {
          Alert.alert('Incomplete Vehicle Info', 'Please complete all vehicle fields or use "Skip for now".');
          return false;
        }
        return true;
      case 3: // Docs
        // Similar logic: if they uploaded some but not all, warn? 
        // Or just let them proceed. Backend validates "Go Online".
        // Let's allow partial upload.
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

  const skipStep = () => {
    setCurrentStep(prev => prev + 1);
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

      const parsedYear = parseInt(vehicleYear);

      await registerDriver({
        // Personal
        first_name: firstName,
        last_name: lastName,
        email,
        gender,
        city,
        // Vehicle (send undefined if empty to avoid validation errors or "None" strings)
        vehicle_make: vehicleMake || undefined,
        vehicle_model: vehicleModel || undefined,
        vehicle_color: vehicleColor || undefined,
        vehicle_year: isNaN(parsedYear) ? undefined : parsedYear,
        license_plate: licensePlate || undefined,
        vehicle_vin: vehicleVin || undefined,
        vehicle_type_id: vehicleType || undefined,

        // Legacy/Top-level Fields
        license_number: licenseNumber || undefined,
        license_expiry_date: licenseExpiry,
        insurance_expiry_date: insuranceExpiry,
        vehicle_inspection_expiry_date: inspectionExpiry,
        background_check_expiry_date: backgroundExpiry,

        // New Dynamic Docs
        documents: documentsPayload,
      });

      Alert.alert('Success', 'Application submitted! You can now log in, but you must complete your profile to go online.', [
        {
          text: 'OK', onPress: async () => {
            await AsyncStorage.removeItem('driver_application_draft');
            router.replace('/driver/' as any);
          }
        }
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
          {currentStep > 0 ? (
            <TouchableOpacity onPress={prevStep} style={styles.backButton}>
              <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
            </TouchableOpacity>
          ) : (
            <TouchableOpacity onPress={() => useAuthStore.getState().logout().then(() => router.replace('/login'))} style={styles.backButton}>
              <Ionicons name="log-out-outline" size={24} color="#1A1A1A" />
            </TouchableOpacity>
          )}
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

              <Text style={styles.label}>Gender</Text>
              <View style={styles.typeContainer}>
                {['Male', 'Female', 'Other'].map(g => (
                  <TouchableOpacity
                    key={g}
                    style={[styles.typeOption, gender === g && styles.typeSelected]}
                    onPress={() => setGender(g)}
                  >
                    <Text style={[styles.typeText, gender === g && styles.typeTextSelected]}>{g}</Text>
                  </TouchableOpacity>
                ))}
              </View>

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
              <TouchableOpacity style={styles.secondaryButton} onPress={skipStep}>
                <Text style={styles.secondaryButtonText}>Skip for now</Text>
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
                  {/* Expiry Date - Show for all, or configured ones */}
                  <View style={{ marginTop: 10 }}>
                    <Text style={styles.subLabel}>Expiry Date (YYYY-MM-DD)</Text>
                    <TouchableOpacity
                      style={styles.dateInput}
                      onPress={() => openDatePicker(req.id)}
                    >
                      <Text style={{ color: docs[req.id]?.expiry ? '#000' : '#999' }}>
                        {docs[req.id]?.expiry || 'Select Expiry Date'}
                      </Text>
                      <Ionicons name="calendar-outline" size={20} color="#666" style={{ position: 'absolute', right: 10 }} />
                    </TouchableOpacity>

                    {/* Render Picker Inline for better visibility */}
                    {showDatePicker && datePickerTarget === req.id && (
                      <View>
                        <DateTimePicker
                          value={docs[req.id]?.expiry ? new Date(docs[req.id].expiry!) : new Date()}
                          mode="date"
                          display={Platform.OS === 'ios' ? 'inline' : 'default'}
                          minimumDate={new Date()}
                          onChange={onDateChange}
                          style={{ marginTop: 10 }}
                        />
                        {Platform.OS === 'ios' && (
                          <TouchableOpacity
                            style={{ alignSelf: 'flex-end', padding: 10, marginTop: 5 }}
                            onPress={() => {
                              setShowDatePicker(false);
                              setDatePickerTarget(null);
                            }}
                          >
                            <Text style={{ color: SpinrConfig.theme.colors.primary, fontWeight: 'bold' }}>Done</Text>
                          </TouchableOpacity>
                        )}
                      </View>
                    )}
                  </View>
                </View>
              ))}

              <TouchableOpacity style={styles.primaryButton} onPress={nextStep}>
                <Text style={styles.primaryButtonText}>Review Application</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.secondaryButton} onPress={skipStep}>
                <Text style={styles.secondaryButtonText}>Skip for now</Text>
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
  reviewRow: { fontSize: 16, marginBottom: 10 },

  secondaryButton: {
    backgroundColor: 'transparent', borderRadius: 30, padding: 15,
    alignItems: 'center', marginTop: 10
  },
  secondaryButtonText: { color: '#666', fontSize: 16, fontWeight: '600' }
});
