import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    ActivityIndicator,
    Alert,
    TextInput,
    Modal,
    Pressable,
    Platform,
    KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useLanguageStore } from '../../store/languageStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';

/** Geocode a free-text address into {lat, lng}.
 *  Falls back to null if the API is unavailable or returns no results. */
async function geocodeAddress(address: string): Promise<{ lat: number; lng: number } | null> {
    if (!GOOGLE_MAPS_API_KEY) return null;
    try {
        const encoded = encodeURIComponent(address);
        const res = await fetch(
            `https://maps.googleapis.com/maps/api/geocode/json?address=${encoded}&key=${GOOGLE_MAPS_API_KEY}`
        );
        const data = await res.json();
        if (data.status === 'OK' && data.results?.length > 0) {
            const { lat, lng } = data.results[0].geometry.location;
            return { lat, lng };
        }
    } catch {
        // Network or parse error — caller handles null
    }
    return null;
}

interface SavedAddress {
    id: string;
    name: string;
    address: string;
    lat: number;
    lng: number;
    icon?: string;
}

export default function AddressesScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { t } = useLanguageStore();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [addresses, setAddresses] = useState<SavedAddress[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAddModal, setShowAddModal] = useState(false);
    const [newAddress, setNewAddress] = useState({ name: '', address: '' });

    useEffect(() => {
        fetchAddresses();
    }, []);

    const fetchAddresses = async () => {
        setLoading(true);
        try {
            const res = await api.get('/addresses');
            setAddresses(res.data || []);
        } catch (err: any) {
            console.log('Error fetching addresses:', err);
            Alert.alert('Error', 'Failed to load saved addresses');
        } finally {
            setLoading(false);
        }
    };

    const handleDelete = (id: string) => {
        Alert.alert(
            'Delete Address',
            'Are you sure you want to delete this address?',
            [
                { text: 'Cancel', style: 'cancel' },
                {
                    text: 'Delete',
                    style: 'destructive',
                    onPress: async () => {
                        try {
                            await api.delete(`/addresses/${id}`);
                            await fetchAddresses();
                            Alert.alert('Success', 'Address deleted');
                        } catch (err: any) {
                            Alert.alert('Error', 'Failed to delete address');
                        }
                    },
                },
            ]
        );
    };

    const handleAddAddress = async () => {
        if (!newAddress.name.trim() || !newAddress.address.trim()) {
            Alert.alert('Error', 'Please fill in both fields');
            return;
        }

        try {
            // Geocode the address to get real coordinates
            const coords = await geocodeAddress(newAddress.address.trim());
            if (!coords) {
                Alert.alert(
                    'Address not found',
                    'We could not locate that address on the map. Please enter a more specific address (include city/province).'
                );
                return;
            }

            await api.post('/addresses', {
                name: newAddress.name.trim(),
                address: newAddress.address.trim(),
                lat: coords.lat,
                lng: coords.lng,
                icon: 'home',
            });
            setShowAddModal(false);
            setNewAddress({ name: '', address: '' });
            await fetchAddresses();
            Alert.alert('Success', 'Address saved');
        } catch (err: any) {
            const errorMessage = err.response?.data?.detail || 'Failed to save address';
            Alert.alert('Error', errorMessage);
        }
    };

    const handleUseAddress = (address: SavedAddress) => {
        // Navigate to driver home with this address as destination
        router.back();
        // In a full implementation, you would pass the address to the main screen
    };

    return (
        <View style={styles.container}>
            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Saved Addresses</Text>
                <TouchableOpacity
                    style={styles.addBtn}
                    onPress={() => setShowAddModal(true)}
                >
                    <Ionicons name="add" size={24} color={colors.primary} />
                </TouchableOpacity>
            </View>

            <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
                {loading ? (
                    <View style={styles.loadingContainer}>
                        <ActivityIndicator color={colors.primary} size="large" />
                    </View>
                ) : addresses.length > 0 ? (
                    <View style={styles.addressList}>
                        {addresses.map((address) => (
                            <View key={address.id} style={styles.addressCard}>
                                <TouchableOpacity
                                    style={styles.addressContent}
                                    onPress={() => handleUseAddress(address)}
                                >
                                    <View style={styles.addressIcon}>
                                        <Ionicons
                                            name={address.icon === 'work' ? 'briefcase' : 'home'}
                                            size={20}
                                            color={colors.primary}
                                        />
                                    </View>
                                    <View style={styles.addressInfo}>
                                        <Text style={styles.addressName}>{address.name}</Text>
                                        <Text style={styles.addressText} numberOfLines={1}>
                                            {address.address}
                                        </Text>
                                    </View>
                                </TouchableOpacity>
                                <TouchableOpacity
                                    style={styles.deleteBtn}
                                    onPress={() => handleDelete(address.id)}
                                >
                                    <Ionicons name="trash-outline" size={20} color={colors.danger} />
                                </TouchableOpacity>
                            </View>
                        ))}
                    </View>
                ) : (
                    <View style={styles.emptyState}>
                        <Ionicons name="location-outline" size={64} color={colors.surfaceLight} />
                        <Text style={styles.emptyTitle}>No saved addresses</Text>
                        <Text style={styles.emptyText}>
                            Save your frequent addresses for quick access
                        </Text>
                        <TouchableOpacity
                            style={styles.emptyBtn}
                            onPress={() => setShowAddModal(true)}
                        >
                            <Ionicons name="add" size={20} color={colors.primary} />
                            <Text style={styles.emptyBtnText}>Add Address</Text>
                        </TouchableOpacity>
                    </View>
                )}
            </ScrollView>

            {/* Add Address Modal */}
            <Modal
                visible={showAddModal}
                transparent
                animationType="slide"
                onRequestClose={() => setShowAddModal(false)}
            >
                <KeyboardAvoidingView
                    style={{ flex: 1 }}
                    behavior={Platform.OS === 'ios' ? 'padding' : undefined}
                >
                <Pressable style={styles.modalOverlay} onPress={() => setShowAddModal(false)}>
                    <Pressable style={[styles.modalContent, { paddingBottom: Math.max(insets.bottom + 12, 24) }]} onPress={(e) => e.stopPropagation()}>
                        <Text style={styles.modalTitle}>Add New Address</Text>

                        <View style={styles.inputGroup}>
                            <Text style={styles.inputLabel}>Name (e.g., Home, Work)</Text>
                            <TextInput
                                style={styles.input}
                                placeholder="Enter name"
                                placeholderTextColor={colors.textDim}
                                value={newAddress.name}
                                onChangeText={(text) => setNewAddress({ ...newAddress, name: text })}
                            />
                        </View>

                        <View style={styles.inputGroup}>
                            <Text style={styles.inputLabel}>Address</Text>
                            <TextInput
                                style={styles.input}
                                placeholder="Enter full address"
                                placeholderTextColor={colors.textDim}
                                value={newAddress.address}
                                onChangeText={(text) => setNewAddress({ ...newAddress, address: text })}
                                multiline
                            />
                        </View>

                        <View style={styles.modalActions}>
                            <TouchableOpacity
                                style={[styles.modalBtn, styles.cancelBtn]}
                                onPress={() => setShowAddModal(false)}
                            >
                                <Text style={styles.cancelBtnText}>Cancel</Text>
                            </TouchableOpacity>
                            <TouchableOpacity
                                style={[styles.modalBtn, styles.saveBtn]}
                                onPress={handleAddAddress}
                            >
                                <Text style={styles.saveBtnText}>Save</Text>
                            </TouchableOpacity>
                        </View>
                    </Pressable>
                </Pressable>
                </KeyboardAvoidingView>
            </Modal>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: colors.background,
    },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingHorizontal: 16,
        paddingBottom: 16,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
    },
    headerTitle: {
        fontSize: 18,
        fontWeight: '700',
        color: colors.text,
    },
    backBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: colors.surface,
        justifyContent: 'center',
        alignItems: 'center',
    },
    addBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: colors.surface,
        justifyContent: 'center',
        alignItems: 'center',
    },
    content: {
        flex: 1,
        padding: 16,
    },
    loadingContainer: {
        flex: 1,
        justifyContent: 'center',
        alignItems: 'center',
        height: 300,
    },
    addressList: {
        gap: 12,
    },
    addressCard: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: colors.surface,
        borderRadius: 16,
        padding: 16,
        borderWidth: 1,
        borderColor: colors.border,
    },
    addressContent: {
        flex: 1,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
    },
    addressIcon: {
        width: 44,
        height: 44,
        borderRadius: 22,
        backgroundColor: `${colors.primary}15`,
        justifyContent: 'center',
        alignItems: 'center',
    },
    addressInfo: {
        flex: 1,
    },
    addressName: {
        fontSize: 16,
        fontWeight: '600',
        color: colors.text,
        marginBottom: 4,
    },
    addressText: {
        fontSize: 14,
        color: colors.textDim,
    },
    deleteBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: `${colors.danger}15`,
        justifyContent: 'center',
        alignItems: 'center',
    },
    emptyState: {
        flex: 1,
        alignItems: 'center',
        justifyContent: 'center',
        paddingVertical: 80,
    },
    emptyTitle: {
        fontSize: 18,
        fontWeight: '600',
        color: colors.text,
        marginTop: 16,
    },
    emptyText: {
        fontSize: 14,
        color: colors.textDim,
        marginTop: 8,
        textAlign: 'center',
    },
    emptyBtn: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
        marginTop: 24,
        paddingHorizontal: 20,
        paddingVertical: 12,
        borderRadius: 24,
        backgroundColor: `${colors.primary}15`,
    },
    emptyBtnText: {
        fontSize: 15,
        fontWeight: '600',
        color: colors.primary,
    },
    modalOverlay: {
        flex: 1,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        justifyContent: 'flex-end',
    },
    modalContent: {
        backgroundColor: colors.surface,
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        padding: 24,
    },
    modalTitle: {
        fontSize: 20,
        fontWeight: '700',
        color: colors.text,
        marginBottom: 24,
        textAlign: 'center',
    },
    inputGroup: {
        marginBottom: 20,
    },
    inputLabel: {
        fontSize: 14,
        fontWeight: '500',
        color: colors.text,
        marginBottom: 8,
    },
    input: {
        backgroundColor: colors.background,
        borderRadius: 12,
        padding: 14,
        fontSize: 15,
        color: colors.text,
        borderWidth: 1,
        borderColor: colors.border,
        minHeight: 44,
    },
    modalActions: {
        flexDirection: 'row',
        gap: 12,
        marginTop: 8,
    },
    modalBtn: {
        flex: 1,
        paddingVertical: 14,
        borderRadius: 12,
        alignItems: 'center',
    },
    cancelBtn: {
        backgroundColor: colors.background,
        borderWidth: 1,
        borderColor: colors.border,
    },
    cancelBtnText: {
        fontSize: 15,
        fontWeight: '600',
        color: colors.text,
    },
    saveBtn: {
        backgroundColor: colors.primary,
    },
    saveBtnText: {
        fontSize: 15,
        fontWeight: '600',
        color: '#fff',
    },
    });
}
