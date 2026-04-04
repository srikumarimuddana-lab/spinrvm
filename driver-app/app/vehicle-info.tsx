import React, { useState, useEffect, useRef } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    TextInput,
    ScrollView,
    Alert,
    ActivityIndicator,
    Modal,
    FlatList,
    Platform,
    KeyboardAvoidingView,
    findNodeHandle,
    UIManager,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';

import SpinrConfig from '@shared/config/spinr.config';

interface VehicleType {
    id: string;
    name: string;
    description: string;
    capacity: number;
    icon: string;
}

const THEME = SpinrConfig.theme.colors;
const COLORS = {
    primary: THEME.background,
    accent: THEME.primary,
    accentDim: THEME.primaryDark,
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    success: THEME.success,
    danger: THEME.error,
    border: THEME.border,
};

export default function VehicleInfoScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { driver, fetchDriverProfile } = useAuthStore();
    const [saving, setSaving] = useState(false);

    const scrollRef = useRef<ScrollView>(null);

    const [form, setForm] = useState({
        vehicle_type_id: '',
        vehicle_make: '',
        vehicle_model: '',
        vehicle_year: '',
        vehicle_color: '',
        vehicle_vin: '',
        license_plate: '',
    });

    const [vehicleTypeName, setVehicleTypeName] = useState('');
    const [vehicleTypes, setVehicleTypes] = useState<VehicleType[]>([]);
    const [showVehicleTypePicker, setShowVehicleTypePicker] = useState(false);

    useEffect(() => {
        if (driver) {
            setForm({
                vehicle_type_id: driver.vehicle_type_id || '',
                vehicle_make: driver.vehicle_make || '',
                vehicle_model: driver.vehicle_model || '',
                vehicle_year: driver.vehicle_year?.toString() || '',
                vehicle_color: driver.vehicle_color || '',
                vehicle_vin: driver.vehicle_vin || '',
                license_plate: driver.license_plate || '',
            });
        }
        fetchVehicleTypes();
    }, [driver]);

    const fetchVehicleTypes = async () => {
        try {
            const response = await api.get('/vehicle-types');
            setVehicleTypes(response.data);
            if (driver?.vehicle_type_id) {
                const found = response.data.find((t: any) => t.id === driver.vehicle_type_id);
                if (found) setVehicleTypeName(found.name);
            }
        } catch (error) {
            console.log('Failed to fetch vehicle types');
        }
    };

    const handleVehicleTypeSelect = (vehicleType: VehicleType) => {
        setForm(prev => ({ ...prev, vehicle_type_id: vehicleType.id }));
        setVehicleTypeName(vehicleType.name);
        setShowVehicleTypePicker(false);
    };

    const handleChange = (key: string, value: string) => {
        setForm(prev => ({ ...prev, [key]: value }));
    };

    // Scroll the focused input into view so it isn't hidden behind the keyboard.
    // Extra offset ensures the input + its label are visible.
    const handleFocus = (e: any) => {
        const node = findNodeHandle(e.target);
        if (!node || !scrollRef.current) return;
        setTimeout(() => {
            UIManager.measureLayout?.(
                node as any,
                findNodeHandle(scrollRef.current as any) as any,
                () => {},
                (_x: number, y: number) => {
                    scrollRef.current?.scrollTo({ y: Math.max(0, y - 120), animated: true });
                }
            );
        }, 50);
    };

    const isFormValid =
        form.vehicle_type_id &&
        form.vehicle_make.trim() &&
        form.vehicle_model.trim() &&
        form.vehicle_year.trim() &&
        form.license_plate.trim();

    const handleSubmit = async () => {
        if (!isFormValid) {
            Alert.alert('Missing Information', 'Please fill in all required fields marked with *');
            return;
        }
        Alert.alert(
            'Update Vehicle Info',
            "Changing your vehicle information will require admin re-verification. You will obtain a 'Pending' status and cannot go online until approved. Continue?",
            [
                { text: 'Cancel', style: 'cancel' },
                {
                    text: 'Update & Verify',
                    style: 'destructive',
                    onPress: async () => {
                        setSaving(true);
                        try {
                            await api.put('/drivers/me', {
                                ...form,
                                vehicle_year: parseInt(form.vehicle_year) || 0,
                            });
                            await fetchDriverProfile();
                            Alert.alert('Success', 'Vehicle information updated. Please wait for admin approval.', [
                                { text: 'OK', onPress: () => router.back() },
                            ]);
                        } catch (err: any) {
                            Alert.alert('Error', err.response?.data?.detail || 'Failed to update vehicle info');
                        } finally {
                            setSaving(false);
                        }
                    },
                },
            ]
        );
    };

    return (
        <View style={styles.container}>
            <LinearGradient colors={[COLORS.primary, '#F8F9FA']} style={StyleSheet.absoluteFill} />

            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                    <Ionicons name="arrow-back" size={24} color={COLORS.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Vehicle Information</Text>
                <View style={{ width: 32 }} />
            </View>

            <KeyboardAvoidingView
                style={{ flex: 1 }}
                behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
                keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 0}
            >
                <ScrollView
                    ref={scrollRef}
                    contentContainerStyle={[
                        styles.content,
                        { paddingBottom: insets.bottom + 140 }, // space for sticky footer + keyboard
                    ]}
                    keyboardShouldPersistTaps="handled"
                    showsVerticalScrollIndicator={false}
                >
                    {/* Hero card */}
                    <View style={styles.heroCard}>
                        <View style={styles.heroIconWrap}>
                            <Ionicons name="car-sport" size={28} color={COLORS.accent} />
                        </View>
                        <View style={{ flex: 1 }}>
                            <Text style={styles.heroTitle}>Your Vehicle</Text>
                            <Text style={styles.heroSub}>
                                {form.vehicle_make && form.vehicle_model
                                    ? `${form.vehicle_year ? form.vehicle_year + ' ' : ''}${form.vehicle_make} ${form.vehicle_model}`
                                    : 'Add your vehicle details to go online'}
                            </Text>
                            {form.license_plate ? (
                                <View style={styles.platePill}>
                                    <Text style={styles.plateText}>{form.license_plate.toUpperCase()}</Text>
                                </View>
                            ) : null}
                        </View>
                    </View>

                    {/* Warning */}
                    <View style={styles.warningBox}>
                        <Ionicons name="information-circle" size={18} color={COLORS.accent} />
                        <Text style={styles.warningText}>
                            Updating these details triggers re-verification. You won't be able to go online until approved.
                        </Text>
                    </View>

                    {/* Section: Vehicle Type */}
                    <Text style={styles.sectionTitle}>Vehicle Class</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.vehicleTypeBox}
                            onPress={() => setShowVehicleTypePicker(true)}
                            activeOpacity={0.7}
                        >
                            <View style={styles.vehicleTypeIconBox}>
                                <Ionicons name="car" size={22} color={COLORS.accent} />
                            </View>
                            <View style={{ flex: 1 }}>
                                <Text style={styles.vehicleTypeLabel}>Vehicle Type *</Text>
                                <Text style={styles.vehicleTypeValue}>
                                    {vehicleTypeName || 'Tap to select'}
                                </Text>
                            </View>
                            <Ionicons name="chevron-forward" size={20} color={COLORS.textDim} />
                        </TouchableOpacity>
                    </View>

                    {/* Section: Vehicle Details */}
                    <Text style={styles.sectionTitle}>Make & Model</Text>
                    <View style={styles.card}>
                        <FormField
                            label="Vehicle Make *"
                            value={form.vehicle_make}
                            onChangeText={t => handleChange('vehicle_make', t)}
                            placeholder="e.g. Toyota"
                            onFocus={handleFocus}
                        />
                        <View style={styles.divider} />
                        <FormField
                            label="Vehicle Model *"
                            value={form.vehicle_model}
                            onChangeText={t => handleChange('vehicle_model', t)}
                            placeholder="e.g. Camry"
                            onFocus={handleFocus}
                        />
                        <View style={styles.divider} />
                        <View style={styles.rowSplit}>
                            <View style={{ flex: 1 }}>
                                <FormField
                                    label="Year *"
                                    value={form.vehicle_year}
                                    onChangeText={t => handleChange('vehicle_year', t)}
                                    placeholder="2020"
                                    keyboardType="numeric"
                                    maxLength={4}
                                    onFocus={handleFocus}
                                />
                            </View>
                            <View style={styles.vDivider} />
                            <View style={{ flex: 1 }}>
                                <FormField
                                    label="Color"
                                    value={form.vehicle_color}
                                    onChangeText={t => handleChange('vehicle_color', t)}
                                    placeholder="Silver"
                                    onFocus={handleFocus}
                                />
                            </View>
                        </View>
                    </View>

                    {/* Section: Registration */}
                    <Text style={styles.sectionTitle}>Registration & Identification</Text>
                    <View style={styles.card}>
                        <FormField
                            label="License Plate *"
                            value={form.license_plate}
                            onChangeText={t => handleChange('license_plate', t)}
                            placeholder="ABC 123"
                            autoCapitalize="characters"
                            onFocus={handleFocus}
                        />
                        <View style={styles.divider} />
                        <FormField
                            label="VIN Number"
                            value={form.vehicle_vin}
                            onChangeText={t => handleChange('vehicle_vin', t)}
                            placeholder="1HGBH41JXMN109186"
                            autoCapitalize="characters"
                            maxLength={17}
                            onFocus={handleFocus}
                            helper="17-character vehicle identification number"
                        />
                    </View>

                    <View style={{ height: 20 }} />
                </ScrollView>

                {/* Sticky bottom save button — always visible, even when keyboard is open */}
                <View
                    style={[
                        styles.footer,
                        { paddingBottom: Math.max(insets.bottom, 12) + 8 },
                    ]}
                >
                    <TouchableOpacity
                        style={[styles.saveButton, (!isFormValid || saving) && styles.saveButtonDisabled]}
                        onPress={handleSubmit}
                        disabled={!isFormValid || saving}
                        activeOpacity={0.85}
                    >
                        {saving ? (
                            <ActivityIndicator color="#fff" />
                        ) : (
                            <>
                                <Ionicons name="checkmark-circle" size={20} color="#fff" />
                                <Text style={styles.saveButtonText}>Save Vehicle Info</Text>
                            </>
                        )}
                    </TouchableOpacity>
                </View>
            </KeyboardAvoidingView>

            {/* Vehicle Type Picker Modal */}
            <Modal
                visible={showVehicleTypePicker}
                transparent={true}
                animationType="slide"
                onRequestClose={() => setShowVehicleTypePicker(false)}
            >
                <View style={styles.modalOverlay}>
                    <View style={[styles.modalContent, { paddingBottom: insets.bottom + 20 }]}>
                        <View style={styles.modalHandle} />
                        <View style={styles.modalHeader}>
                            <Text style={styles.modalTitle}>Select Vehicle Type</Text>
                            <TouchableOpacity onPress={() => setShowVehicleTypePicker(false)} style={styles.modalCloseBtn}>
                                <Ionicons name="close" size={22} color={COLORS.text} />
                            </TouchableOpacity>
                        </View>
                        <FlatList
                            data={vehicleTypes}
                            keyExtractor={(item) => item.id}
                            renderItem={({ item }) => (
                                <TouchableOpacity
                                    style={[
                                        styles.vehicleTypeOption,
                                        form.vehicle_type_id === item.id && styles.vehicleTypeOptionSelected,
                                    ]}
                                    onPress={() => handleVehicleTypeSelect(item)}
                                >
                                    <View style={styles.vehicleTypeOptionIcon}>
                                        <Ionicons name="car" size={22} color={COLORS.accent} />
                                    </View>
                                    <View style={styles.vehicleTypeInfo}>
                                        <Text style={styles.vehicleTypeOptionName}>{item.name}</Text>
                                        <Text style={styles.vehicleTypeOptionDesc}>{item.description}</Text>
                                    </View>
                                    {form.vehicle_type_id === item.id && (
                                        <Ionicons name="checkmark-circle" size={24} color={COLORS.accent} />
                                    )}
                                </TouchableOpacity>
                            )}
                        />
                    </View>
                </View>
            </Modal>
        </View>
    );
}

// ─── Reusable form field ─────────────────────────────────────────────
interface FormFieldProps {
    label: string;
    value: string;
    onChangeText: (t: string) => void;
    placeholder?: string;
    keyboardType?: 'default' | 'numeric';
    autoCapitalize?: 'none' | 'characters' | 'words' | 'sentences';
    maxLength?: number;
    helper?: string;
    onFocus?: (e: any) => void;
}

const FormField: React.FC<FormFieldProps> = ({
    label,
    value,
    onChangeText,
    placeholder,
    keyboardType,
    autoCapitalize,
    maxLength,
    helper,
    onFocus,
}) => (
    <View style={styles.field}>
        <Text style={styles.fieldLabel}>{label}</Text>
        <TextInput
            style={styles.fieldInput}
            value={value}
            onChangeText={onChangeText}
            placeholder={placeholder}
            placeholderTextColor="#B0B7C0"
            keyboardType={keyboardType}
            autoCapitalize={autoCapitalize}
            maxLength={maxLength}
            onFocus={onFocus}
        />
        {helper ? <Text style={styles.fieldHelper}>{helper}</Text> : null}
    </View>
);

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingBottom: 16,
        paddingHorizontal: 20,
        backgroundColor: '#fff',
        borderBottomWidth: 1,
        borderBottomColor: '#F3F4F6',
    },
    backBtn: { padding: 4, width: 32 },
    headerTitle: { fontSize: 18, fontWeight: '700', color: COLORS.text, flex: 1, textAlign: 'center' },

    content: { padding: 20 },

    heroCard: {
        flexDirection: 'row',
        backgroundColor: '#fff',
        padding: 18,
        borderRadius: 18,
        marginBottom: 16,
        alignItems: 'center',
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.06,
        shadowRadius: 8,
        elevation: 2,
    },
    heroIconWrap: {
        width: 52,
        height: 52,
        borderRadius: 14,
        backgroundColor: 'rgba(255,59,48,0.08)',
        justifyContent: 'center',
        alignItems: 'center',
        marginRight: 14,
    },
    heroTitle: { fontSize: 12, fontWeight: '700', color: COLORS.textDim, letterSpacing: 0.8, textTransform: 'uppercase' },
    heroSub: { fontSize: 16, fontWeight: '600', color: COLORS.text, marginTop: 2 },
    platePill: {
        alignSelf: 'flex-start',
        marginTop: 6,
        backgroundColor: '#1A1A1A',
        paddingHorizontal: 10,
        paddingVertical: 4,
        borderRadius: 6,
    },
    plateText: { color: '#FFD700', fontSize: 12, fontWeight: '800', letterSpacing: 1 },

    warningBox: {
        flexDirection: 'row',
        backgroundColor: 'rgba(255,59,48,0.06)',
        padding: 12,
        borderRadius: 12,
        marginBottom: 20,
        alignItems: 'flex-start',
        borderWidth: 1,
        borderColor: 'rgba(255,59,48,0.15)',
        gap: 8,
    },
    warningText: { color: COLORS.accent, flex: 1, fontSize: 12, lineHeight: 16, fontWeight: '500' },

    sectionTitle: {
        fontSize: 12,
        fontWeight: '700',
        color: COLORS.textDim,
        letterSpacing: 0.8,
        textTransform: 'uppercase',
        marginBottom: 8,
        marginTop: 4,
        paddingHorizontal: 4,
    },
    card: {
        backgroundColor: '#fff',
        borderRadius: 16,
        marginBottom: 20,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 1 },
        shadowOpacity: 0.04,
        shadowRadius: 4,
        elevation: 1,
        overflow: 'hidden',
    },
    divider: { height: 1, backgroundColor: '#F3F4F6', marginHorizontal: 16 },
    vDivider: { width: 1, backgroundColor: '#F3F4F6' },
    rowSplit: { flexDirection: 'row' },

    field: { paddingHorizontal: 16, paddingVertical: 12 },
    fieldLabel: { fontSize: 11, fontWeight: '700', color: COLORS.textDim, letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 4 },
    fieldInput: { fontSize: 16, color: COLORS.text, padding: 0, fontWeight: '500' },
    fieldHelper: { fontSize: 11, color: COLORS.textDim, marginTop: 4 },

    vehicleTypeBox: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 16,
        paddingVertical: 14,
        gap: 12,
    },
    vehicleTypeIconBox: {
        width: 40,
        height: 40,
        borderRadius: 10,
        backgroundColor: 'rgba(255,59,48,0.08)',
        justifyContent: 'center',
        alignItems: 'center',
    },
    vehicleTypeLabel: { fontSize: 11, fontWeight: '700', color: COLORS.textDim, letterSpacing: 0.6, textTransform: 'uppercase' },
    vehicleTypeValue: { fontSize: 16, fontWeight: '600', color: COLORS.text, marginTop: 2 },

    // Sticky footer
    footer: {
        backgroundColor: '#fff',
        paddingHorizontal: 20,
        paddingTop: 12,
        borderTopWidth: 1,
        borderTopColor: '#F3F4F6',
    },
    saveButton: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: COLORS.accent,
        borderRadius: 14,
        paddingVertical: 16,
        gap: 8,
        shadowColor: COLORS.accent,
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.3,
        shadowRadius: 8,
        elevation: 4,
    },
    saveButtonDisabled: {
        backgroundColor: '#D1D5DB',
        shadowOpacity: 0,
        elevation: 0,
    },
    saveButtonText: { color: '#fff', fontSize: 16, fontWeight: '700' },

    // Modal
    modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end' },
    modalContent: {
        backgroundColor: '#fff',
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        maxHeight: '75%',
    },
    modalHandle: {
        width: 40,
        height: 4,
        backgroundColor: '#E5E7EB',
        borderRadius: 2,
        alignSelf: 'center',
        marginTop: 10,
    },
    modalHeader: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: 20,
        paddingBottom: 12,
    },
    modalCloseBtn: {
        width: 32,
        height: 32,
        borderRadius: 16,
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    modalTitle: { fontSize: 20, fontWeight: '700', color: COLORS.text },
    vehicleTypeOption: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 20,
        paddingVertical: 14,
        gap: 12,
        borderTopWidth: 1,
        borderTopColor: '#F3F4F6',
    },
    vehicleTypeOptionSelected: { backgroundColor: 'rgba(255,59,48,0.04)' },
    vehicleTypeOptionIcon: {
        width: 44,
        height: 44,
        borderRadius: 12,
        backgroundColor: 'rgba(255,59,48,0.08)',
        justifyContent: 'center',
        alignItems: 'center',
    },
    vehicleTypeInfo: { flex: 1 },
    vehicleTypeOptionName: { fontSize: 16, fontWeight: '700', color: COLORS.text },
    vehicleTypeOptionDesc: { fontSize: 13, color: COLORS.textDim, marginTop: 2 },
});
