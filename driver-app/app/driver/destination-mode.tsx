import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    ActivityIndicator,
    TextInput,
    Platform,
    KeyboardAvoidingView,
    Alert,
} from 'react-native';
import { showToast } from '../../hooks/useToast';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useLanguageStore } from '../../store/languageStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { newPlacesSessionToken } from '@shared/utils/placesSession';

interface AutocompletePrediction {
    place_id: string;
    description: string;
}

/** Geocode a free-text address into {lat, lng} via the backend Places proxy.
 *  Mirrors addresses.tsx's geocodeAddress — one Places API (New) autocomplete
 *  → details session instead of a direct Geocoding API call. */
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

interface DestinationState {
    destination_mode: boolean;
    destination_address: string | null;
    destination_lat: number | null;
    destination_lng: number | null;
}

export default function DestinationModeScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { t } = useLanguageStore();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [clearing, setClearing] = useState(false);
    const [state, setState] = useState<DestinationState>({
        destination_mode: false,
        destination_address: null,
        destination_lat: null,
        destination_lng: null,
    });
    const [addressInput, setAddressInput] = useState('');

    // Declared before the `useEffect` below (react-hooks/immutability /
    // React Compiler flags referencing a function before its source-order
    // declaration) — same expression, same effect timing, no behavior change.
    const fetchDestination = async () => {
        setLoading(true);
        try {
            const res = await api.get<DestinationState>('/drivers/destination');
            const data = res.data;
            if (data) {
                setState(data);
                setAddressInput(data.destination_address || '');
            }
        } catch (err: any) {
            showToast('error', t('destinationMode.loadFailedTitle'), getApiErrorMessage(err, t('destinationMode.loadFailedMsg')));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchDestination();
    }, []);

    const handleSave = async () => {
        const trimmed = addressInput.trim();
        if (!trimmed) {
            showToast('warning', t('destinationMode.missingAddressTitle'), t('destinationMode.missingAddressMsg'));
            return;
        }

        setSaving(true);
        try {
            const coords = await geocodeAddress(trimmed);
            if (!coords) {
                showToast('warning', t('destinationMode.notFoundTitle'), t('destinationMode.notFoundMsg'));
                return;
            }

            await api.post('/drivers/destination', {
                address: trimmed,
                lat: coords.lat,
                lng: coords.lng,
            });
            setState({
                destination_mode: true,
                destination_address: trimmed,
                destination_lat: coords.lat,
                destination_lng: coords.lng,
            });
            showToast('success', t('destinationMode.savedTitle'), t('destinationMode.savedMsg'));
        } catch (err: any) {
            showToast('error', t('destinationMode.saveFailedTitle'), getApiErrorMessage(err, t('destinationMode.saveFailedMsg')));
        } finally {
            setSaving(false);
        }
    };

    const handleClear = () => {
        Alert.alert(
            t('destinationMode.clearConfirmTitle'),
            t('destinationMode.clearConfirmMsg'),
            [
                { text: t('destinationMode.cancel'), style: 'cancel' },
                {
                    text: t('destinationMode.clear'),
                    style: 'destructive',
                    onPress: async () => {
                        setClearing(true);
                        try {
                            await api.delete('/drivers/destination');
                            setState({
                                destination_mode: false,
                                destination_address: null,
                                destination_lat: null,
                                destination_lng: null,
                            });
                            setAddressInput('');
                            showToast('success', t('destinationMode.clearedTitle'), t('destinationMode.clearedMsg'));
                        } catch (err: any) {
                            showToast('error', t('destinationMode.clearFailedTitle'), getApiErrorMessage(err, t('destinationMode.clearFailedMsg')));
                        } finally {
                            setClearing(false);
                        }
                    },
                },
            ],
        );
    };

    return (
        <View style={styles.container}>
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>{t('destinationMode.title')}</Text>
                <View style={{ width: 40 }} />
            </View>

            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
                <ScrollView style={styles.content} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: insets.bottom + 40 }}>
                    {loading ? (
                        <View style={styles.loadingContainer}>
                            <ActivityIndicator color={colors.primary} size="large" />
                        </View>
                    ) : (
                        <>
                            <View style={styles.statusCard}>
                                <View style={[styles.statusIcon, { backgroundColor: state.destination_mode ? `${colors.primary}15` : colors.surfaceLight }]}>
                                    <Ionicons name="navigate" size={20} color={state.destination_mode ? colors.primary : colors.textDim} />
                                </View>
                                <View style={{ flex: 1 }}>
                                    <Text style={styles.statusLabel}>
                                        {state.destination_mode ? t('destinationMode.activeLabel') : t('destinationMode.inactiveLabel')}
                                    </Text>
                                    {state.destination_mode && state.destination_address && (
                                        <Text style={styles.statusValue} numberOfLines={2}>{state.destination_address}</Text>
                                    )}
                                </View>
                            </View>

                            <Text style={styles.explainer}>{t('destinationMode.explainer')}</Text>

                            <View style={styles.inputGroup}>
                                <Text style={styles.inputLabel}>{t('destinationMode.addressLabel')}</Text>
                                <TextInput
                                    style={styles.input}
                                    placeholder={t('destinationMode.addressPlaceholder')}
                                    placeholderTextColor={colors.textDim}
                                    value={addressInput}
                                    onChangeText={setAddressInput}
                                    multiline
                                    editable={!saving && !clearing}
                                />
                            </View>

                            <TouchableOpacity
                                style={[styles.saveBtn, (saving || clearing) && styles.btnDisabled]}
                                onPress={handleSave}
                                disabled={saving || clearing}
                            >
                                {saving ? (
                                    <ActivityIndicator color="#fff" size="small" />
                                ) : (
                                    <Text style={styles.saveBtnText}>
                                        {state.destination_mode ? t('destinationMode.updateBtn') : t('destinationMode.activateBtn')}
                                    </Text>
                                )}
                            </TouchableOpacity>

                            {state.destination_mode && (
                                <TouchableOpacity
                                    style={[styles.clearBtn, (saving || clearing) && styles.btnDisabled]}
                                    onPress={handleClear}
                                    disabled={saving || clearing}
                                >
                                    {clearing ? (
                                        <ActivityIndicator color={colors.error} size="small" />
                                    ) : (
                                        <>
                                            <Ionicons name="close-circle-outline" size={18} color={colors.error} />
                                            <Text style={styles.clearBtnText}>{t('destinationMode.clearBtn')}</Text>
                                        </>
                                    )}
                                </TouchableOpacity>
                            )}
                        </>
                    )}
                </ScrollView>
            </KeyboardAvoidingView>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        header: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingHorizontal: 16,
            paddingBottom: 16,
            borderBottomWidth: 1,
            borderBottomColor: colors.border,
        },
        headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surface,
            justifyContent: 'center',
            alignItems: 'center',
        },
        content: { flex: 1, padding: 16 },
        loadingContainer: {
            flex: 1,
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: 200,
        },
        statusCard: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
            borderWidth: 1,
            borderColor: colors.border,
            marginTop: 8,
        },
        statusIcon: {
            width: 40,
            height: 40,
            borderRadius: 20,
            justifyContent: 'center',
            alignItems: 'center',
        },
        statusLabel: { fontSize: 15, fontWeight: '600', color: colors.text },
        statusValue: { fontSize: 13, color: colors.textDim, marginTop: 2 },
        explainer: {
            fontSize: 13,
            color: colors.textDim,
            lineHeight: 18,
            marginTop: 16,
            marginBottom: 20,
        },
        inputGroup: { marginBottom: 20 },
        inputLabel: { fontSize: 14, fontWeight: '500', color: colors.text, marginBottom: 8 },
        input: {
            backgroundColor: colors.surface,
            borderRadius: 12,
            padding: 14,
            fontSize: 15,
            color: colors.text,
            borderWidth: 1,
            borderColor: colors.border,
            minHeight: 44,
        },
        saveBtn: {
            backgroundColor: colors.primary,
            borderRadius: 12,
            paddingVertical: 14,
            alignItems: 'center',
        },
        clearBtn: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            marginTop: 14,
            paddingVertical: 14,
            borderRadius: 12,
            backgroundColor: 'rgba(255,71,87,0.08)',
            borderWidth: 1,
            borderColor: 'rgba(255,71,87,0.2)',
        },
        clearBtnText: { color: colors.error, fontSize: 14, fontWeight: '600' },
        saveBtnText: { fontSize: 15, fontWeight: '600', color: '#fff' },
        btnDisabled: { opacity: 0.6 },
    });
}
