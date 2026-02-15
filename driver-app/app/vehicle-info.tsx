
import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, TextInput, ScrollView, Alert, ActivityIndicator, Modal, FlatList, Platform } from 'react-native';
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
    // Map old "primary" (background) to new light background
    primary: THEME.background,
    // Map old "accent" (action) to new primary (Red)
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
    const { driver, fetchDriverProfile } = useAuthStore();
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);

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
            // Fetch vehicle type name
            fetchVehicleTypeName(driver.vehicle_type_id);
        }
        // Fetch all vehicle types for the picker
        fetchVehicleTypes();
    }, [driver]);

    const fetchVehicleTypes = async () => {
        try {
            const response = await api.get('/vehicle-types');
            setVehicleTypes(response.data);
        } catch (error) {
            console.log('Failed to fetch vehicle types');
        }
    };

    const fetchVehicleTypeName = async (vehicleTypeId: string) => {
        if (!vehicleTypeId) return;
        try {
            const response = await api.get('/vehicle-types');
            const types = response.data;
            const found = types.find((t: any) => t.id === vehicleTypeId);
            if (found) {
                setVehicleTypeName(found.name);
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

    const handleSubmit = async () => {
        Alert.alert(
            "Update Vehicle Info",
            "Changing your vehicle information will require admin re-verification. You will obtain a 'Pending' status and cannot go online until approved. Continue?",
            [
                { text: "Cancel", style: "cancel" },
                {
                    text: "Update & Verify",
                    style: 'destructive',
                    onPress: async () => {
                        setSaving(true);
                        try {
                            await api.put('/drivers/me', {
                                ...form,
                                vehicle_year: parseInt(form.vehicle_year) || 0,
                            });
                            await fetchDriverProfile(); // Refresh store
                            Alert.alert("Success", "Vehicle information updated. Please wait for admin approval.", [
                                { text: "OK", onPress: () => router.back() }
                            ]);
                        } catch (err: any) {
                            Alert.alert("Error", err.response?.data?.detail || "Failed to update vehicle info");
                        } finally {
                            setSaving(false);
                        }
                    }
                }
            ]
        );
    };

    return (
        <View style={styles.container}>
            <LinearGradient colors={[COLORS.primary, '#F8F9FA']} style={StyleSheet.absoluteFill} />

            <View style={styles.header}>
                <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                    <Ionicons name="arrow-back" size={24} color="#000" />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Vehicle Information</Text>
                <TouchableOpacity onPress={handleSubmit} disabled={saving}>
                    {saving ? <ActivityIndicator color={COLORS.accent} /> : <Text style={styles.saveText}>Save</Text>}
                </TouchableOpacity>
            </View>

            <ScrollView contentContainerStyle={styles.content}>
                {/* Vehicle Type Selector */}
                <View style={styles.formGroup}>
                    <Text style={styles.label}>Vehicle Type</Text>
                    <TouchableOpacity
                        style={styles.vehicleTypeBox}
                        onPress={() => setShowVehicleTypePicker(true)}
                    >
                        <Ionicons name="car" size={24} color={COLORS.accent} />
                        <Text style={styles.vehicleTypeText}>{vehicleTypeName || 'Select vehicle type'}</Text>
                        <Ionicons name="chevron-down" size={20} color={COLORS.textDim} />
                    </TouchableOpacity>
                </View>

                <View style={styles.warningBox}>
                    <Ionicons name="alert-circle" size={20} color={COLORS.danger} />
                    <Text style={styles.warningText}>
                        Updating these details will trigger a re-verification process.
                    </Text>
                </View>

                <View style={styles.formGroup}>
                    <Text style={styles.label}>Vehicle Make</Text>
                    <TextInput
                        style={styles.input}
                        value={form.vehicle_make}
                        onChangeText={t => handleChange('vehicle_make', t)}
                        placeholder="e.g. Toyota"
                        placeholderTextColor={COLORS.textDim}
                    />
                </View>

                <View style={styles.formGroup}>
                    <Text style={styles.label}>Vehicle Model</Text>
                    <TextInput
                        style={styles.input}
                        value={form.vehicle_model}
                        onChangeText={t => handleChange('vehicle_model', t)}
                        placeholder="e.g. Camry"
                        placeholderTextColor={COLORS.textDim}
                    />
                </View>

                <View style={styles.row}>
                    <View style={[styles.formGroup, { flex: 1, marginRight: 10 }]}>
                        <Text style={styles.label}>Year</Text>
                        <TextInput
                            style={styles.input}
                            value={form.vehicle_year}
                            onChangeText={t => handleChange('vehicle_year', t)}
                            placeholder="e.g. 2020"
                            placeholderTextColor={COLORS.textDim}
                            keyboardType="numeric"
                        />
                    </View>
                    <View style={[styles.formGroup, { flex: 1 }]}>
                        <Text style={styles.label}>Color</Text>
                        <TextInput
                            style={styles.input}
                            value={form.vehicle_color}
                            onChangeText={t => handleChange('vehicle_color', t)}
                            placeholder="e.g. Silver"
                            placeholderTextColor={COLORS.textDim}
                        />
                    </View>
                </View>

                <View style={styles.formGroup}>
                    <Text style={styles.label}>License Plate</Text>
                    <TextInput
                        style={styles.input}
                        value={form.license_plate}
                        onChangeText={t => handleChange('license_plate', t)}
                        placeholder="e.g. ABC 123"
                        placeholderTextColor={COLORS.textDim}
                        autoCapitalize="characters"
                    />
                </View>

                <View style={styles.formGroup}>
                    <Text style={styles.label}>VIN Number</Text>
                    <TextInput
                        style={styles.input}
                        value={form.vehicle_vin}
                        onChangeText={t => handleChange('vehicle_vin', t)}
                        placeholder="e.g. 1HGBH41JXMN109186"
                        placeholderTextColor={COLORS.textDim}
                        autoCapitalize="characters"
                        maxLength={17}
                    />
                </View>

            </ScrollView>

            {/* Vehicle Type Picker Modal */}
            <Modal
                visible={showVehicleTypePicker}
                transparent={true}
                animationType="slide"
                onRequestClose={() => setShowVehicleTypePicker(false)}
            >
                <View style={styles.modalOverlay}>
                    <View style={styles.modalContent}>
                        <View style={styles.modalHeader}>
                            <Text style={styles.modalTitle}>Select Vehicle Type</Text>
                            <TouchableOpacity onPress={() => setShowVehicleTypePicker(false)}>
                                <Ionicons name="close" size={24} color={COLORS.text} />
                            </TouchableOpacity>
                        </View>
                        <FlatList
                            data={vehicleTypes}
                            keyExtractor={(item) => item.id}
                            renderItem={({ item }) => (
                                <TouchableOpacity
                                    style={[
                                        styles.vehicleTypeOption,
                                        form.vehicle_type_id === item.id && styles.vehicleTypeOptionSelected
                                    ]}
                                    onPress={() => handleVehicleTypeSelect(item)}
                                >
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

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingTop: Platform.OS === 'ios' ? 60 : 20,
        paddingBottom: 20,
        paddingHorizontal: 20,
        backgroundColor: '#fff',
        borderBottomWidth: 1,
        borderBottomColor: '#F3F4F6',
    },
    backBtn: { padding: 4 },
    headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
    saveText: { color: COLORS.accent, fontWeight: '600', fontSize: 16 },
    content: { padding: 20 },
    warningBox: {
        flexDirection: 'row',
        backgroundColor: 'rgba(255, 71, 87, 0.1)',
        padding: 12,
        borderRadius: 8,
        marginBottom: 24,
        alignItems: 'center',
        borderWidth: 1,
        borderColor: 'rgba(255, 71, 87, 0.2)',
    },
    warningText: { color: COLORS.danger, marginLeft: 10, flex: 1, fontSize: 13 },
    formGroup: { marginBottom: 20 },
    label: { color: COLORS.textDim, marginBottom: 8, fontSize: 13, fontWeight: '600', textTransform: 'uppercase' },
    input: {
        backgroundColor: COLORS.surfaceLight,
        color: COLORS.text,
        padding: 14,
        borderRadius: 10,
        fontSize: 16,
        borderWidth: 1,
        borderColor: COLORS.border,
    },
    vehicleTypeBox: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: COLORS.surfaceLight,
        padding: 14,
        borderRadius: 10,
        borderWidth: 1,
        borderColor: COLORS.border,
    },
    vehicleTypeText: {
        color: COLORS.text,
        fontSize: 16,
        marginLeft: 12,
        fontWeight: '500',
        flex: 1,
    },
    row: { flexDirection: 'row' },
    // Modal styles
    modalOverlay: {
        flex: 1,
        backgroundColor: 'rgba(0,0,0,0.5)',
        justifyContent: 'flex-end',
    },
    modalContent: {
        backgroundColor: COLORS.primary,
        borderTopLeftRadius: 20,
        borderTopRightRadius: 20,
        paddingBottom: 40,
        maxHeight: '70%',
    },
    modalHeader: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: 20,
        borderBottomWidth: 1,
        borderBottomColor: COLORS.border,
    },
    modalTitle: {
        fontSize: 18,
        fontWeight: '700',
        color: COLORS.text,
    },
    vehicleTypeOption: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: 16,
        borderBottomWidth: 1,
        borderBottomColor: COLORS.border,
    },
    vehicleTypeOptionSelected: {
        backgroundColor: COLORS.surfaceLight,
    },
    vehicleTypeInfo: {
        flex: 1,
    },
    vehicleTypeOptionName: {
        fontSize: 16,
        fontWeight: '600',
        color: COLORS.text,
    },
    vehicleTypeOptionDesc: {
        fontSize: 13,
        color: COLORS.textDim,
        marginTop: 2,
    },
});
