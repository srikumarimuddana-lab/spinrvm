import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    ActivityIndicator,
    TextInput,
    Modal,
    Pressable,
    Platform,
    KeyboardAvoidingView,
    Alert,
} from 'react-native';
import { showToast } from '../../hooks/useToast';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { newPlacesSessionToken } from '@shared/utils/placesSession';

interface AutocompletePrediction {
    place_id: string;
    description: string;
}

/** Geocode a free-text address into {lat, lng} via the backend Places proxy.
 *  Uses one Places API (New) autocomplete → details session instead of a
 *  direct Geocoding API call. Falls back to null on no result. */
async function geocodeAddress(address: string): Promise<{ lat: number; lng: number } | null> {
    const sessionToken = newPlacesSessionToken();
    try {
        const acParams = new URLSearchParams({ input: address, session_token: sessionToken });
        const ac = await api.get<{ predictions: AutocompletePrediction[] }>(
            `/maps/places/autocomplete?${acParams.toString()}`,
        );
        const placeId = ac.data?.predictions?.[0]?.place_id;
        if (!placeId) return null;

        const detailsParams = new URLSearchParams({ place_id: placeId, session_token: sessionToken });
        const details = await api.get<{ lat: number | null; lng: number | null }>(
            `/maps/places/details?${detailsParams.toString()}`,
        );
        const { lat, lng } = details.data || {};
        if (lat == null || lng == null) return null;
        return { lat, lng };
    } catch {
        // Network, auth, or budget-circuit-breaker error — caller handles null
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
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [addresses, setAddresses] = useState<SavedAddress[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAddModal, setShowAddModal] = useState(false);
    const [newAddress, setNewAddress] = useState({ name: '', address: '' });

    // Declared before the `useEffect` below (react-hooks/immutability /
    // React Compiler flags referencing a function before its source-order
    // declaration) — same expression, same effect timing, no behavior change.
    const fetchAddresses = async () => {
        setLoading(true);
        try {
            const res = await api.get<SavedAddress[]>('/addresses');
            setAddresses(res.data || []);
        } catch (err: any) {
            // PIPEDA: never spread the raw error body into logs — backend error
            // payloads can contain saved-address strings or user identifiers.
            console.error('Error fetching addresses', { code: err?.code, status: err?.status });
            showToast('error', 'Load Failed', getApiErrorMessage(err, 'Could not load your saved addresses. Please try again.'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        // Mount-only fetch; fetchAddresses sets state after its own await,
        // not synchronously at the top of the effect. Empty deps, runs once.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        fetchAddresses();
    }, []);

    const handleDelete = (id: string) => {
        Alert.alert('Delete Address', 'Are you sure you want to delete this address?', [
            { text: 'Cancel', style: 'cancel' },
            {
                text: 'Delete',
                style: 'destructive',
                onPress: async () => {
                    try {
                        await api.delete(`/addresses/${id}`);
                        await fetchAddresses();
                        showToast('success', 'Address Deleted', 'Address has been removed.');
                    } catch (err: any) {
                        showToast('error', 'Delete Failed', getApiErrorMessage(err, 'Could not delete the address. Please try again.'));
                    }
                },
            },
        ]);
    };

    const handleAddAddress = async () => {
        if (!newAddress.name.trim() || !newAddress.address.trim()) {
            showToast('error', 'Missing Fields', 'Please fill in both fields');
            return;
        }

        try {
            // Geocode the address to get real coordinates
            const coords = await geocodeAddress(newAddress.address.trim());
            if (!coords) {
                showToast('warning', 'Address not found', 'We could not locate that address on the map. Please enter a more specific address (include city/province).');
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
            showToast('success', 'Address Saved', 'Address has been saved.');
        } catch (err: any) {
            showToast('error', 'Save Failed', getApiErrorMessage(err, 'Could not save your address. Please try again.'));
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
                    behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
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
        minHeight: 200,
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
