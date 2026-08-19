import React, { useState, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TextInput,
    TouchableOpacity,
    KeyboardAvoidingView,
    ScrollView,
    Platform,
    Alert,
} from 'react-native';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
// getAuthHeader / SpinrConfig were only needed by the hand-rolled fetch()
// this screen used for photo uploads; api.post now handles the URL, auth,
// and App Check headers itself.
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../hooks/useToast';
import { useLocationStore } from '@shared/store/locationStore';
import { useDriverStore } from '../store/driverStore';

type SafetyCategory = 'Road Hazard' | 'Passenger Behaviour' | 'Vehicle Issue' | 'Other';

const CATEGORIES: SafetyCategory[] = [
    'Road Hazard',
    'Passenger Behaviour',
    'Vehicle Issue',
    'Other',
];

const CATEGORY_ICONS: Record<SafetyCategory, string> = {
    'Road Hazard': 'warning',
    'Passenger Behaviour': 'person',
    'Vehicle Issue': 'car',
    'Other': 'ellipsis-horizontal-circle',
};

export default function ReportSafetyScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [category, setCategory] = useState<SafetyCategory | null>(null);
    const [issue, setIssue] = useState('');
    const [photos, setPhotos] = useState<string[]>([]);
    const [submitting, setSubmitting] = useState(false);

    const location = useLocationStore(state => state.currentLocation);
    const activeRide = useDriverStore(state => state.activeRide);

    const addPhoto = () => {
        Alert.alert('Add Evidence', 'Choose a source', [
            { text: 'Camera', onPress: launchCamera },
            { text: 'Photo Library', onPress: launchGallery },
            { text: 'Cancel', style: 'cancel' },
        ]);
    };

    const launchCamera = async () => {
        const { status } = await ImagePicker.requestCameraPermissionsAsync();
        if (status !== 'granted') return showToast('error', 'Permission Denied', 'Camera access is needed.');
        const result = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.7 });
        if (!result.canceled && result.assets[0]) setPhotos(prev => [...prev, result.assets[0].uri]);
    };

    const launchGallery = async () => {
        const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (status !== 'granted') return showToast('error', 'Permission Denied', 'Library access is needed.');
        const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.7, allowsMultipleSelection: true, selectionLimit: 4 - photos.length });
        if (!result.canceled) setPhotos(prev => [...prev, ...result.assets.map(a => a.uri)].slice(0, 4));
    };

    const removePhoto = (index: number) => setPhotos(prev => prev.filter((_, i) => i !== index));

    const handleSubmit = async () => {
        if (!category) {
            showToast('warning', 'Category Required', 'Please select a category for your safety report.');
            return;
        }
        if (!issue.trim()) {
            showToast('warning', 'Description Required', 'Please describe the safety issue before submitting.');
            return;
        }

        setSubmitting(true);

        const reportData = {
            category,
            description: issue,
            photo_count: photos.length,
            location: (location?.latitude != null && location?.longitude != null) ? {
                latitude: location.latitude,
                longitude: location.longitude,
                timestamp: new Date().toISOString(),
            } : null,
            ride_context: activeRide ? {
                ride_id: activeRide.ride?.id,
                pickup_location: activeRide.ride?.pickup_address,
                dropoff_location: activeRide.ride?.dropoff_address,
                rider_id: activeRide.ride?.rider_id,
            } : null,
            reported_at: new Date().toISOString(),
        };

        try {
            const res = await api.post<{ incident_id?: string }>('/safety/report', reportData);
            const reportId = res.data?.incident_id;

            let failedPhotos = 0;
            if (reportId && photos.length > 0) {
                for (const [i, uri] of photos.entries()) {
                    try {
                        const fd = new FormData();
                        fd.append('file', { uri, name: `evidence_${i}.jpg`, type: 'image/jpeg' } as any);
                        // Was a hand-rolled fetch(), which omitted the
                        // X-Firebase-AppCheck header and so 401'd under
                        // enforcement. api.post now passes FormData through
                        // with the auth + App Check headers attached.
                        await api.post(`/safety/report/${reportId}/photo`, fd);
                    } catch (photoErr) {
                        // Still non-fatal — the report itself is already
                        // persisted and that is the safety-critical part. But
                        // it is no longer SILENT: losing evidence without
                        // telling anyone is what hid this bug for months.
                        failedPhotos += 1;
                        console.error('[report-safety] evidence upload failed', photoErr);
                    }
                }
            }

            if (failedPhotos > 0) {
                showToast(
                    'warning',
                    'Report Sent — Photos Failed',
                    `Your report was submitted, but ${failedPhotos} of ${photos.length} photo${photos.length === 1 ? '' : 's'} could not be attached. Our team may contact you for them.`,
                );
            } else {
                showToast('success', 'Report Submitted', 'Your safety report has been submitted. Our trust and safety team will review it promptly.');
            }
            router.back();
        } catch (e) {
            showToast('error', 'Error', getApiErrorMessage(e, 'Could not submit your report. Please try again.'));
            setSubmitting(false);
        }
    };

    return (
        <View style={styles.safeArea}>
            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Report Safety Issue</Text>
                <View style={styles.headerRight} />
            </View>

            <KeyboardAvoidingView
                style={styles.container}
                behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
            >
                <ScrollView
                    contentContainerStyle={styles.content}
                    keyboardShouldPersistTaps="handled"
                    automaticallyAdjustKeyboardInsets={true}
                    showsVerticalScrollIndicator={false}
                >
                    <View style={styles.warningBox}>
                        <Ionicons name="warning" size={24} color="#F59E0B" />
                        <Text style={styles.warningText}>
                            If this is an emergency, please contact local authorities immediately using the Emergency Assist button in settings.
                        </Text>
                    </View>

                    {/* Category Picker */}
                    <Text style={styles.label}>Category</Text>
                    <View style={styles.categoryGrid}>
                        {CATEGORIES.map((cat) => (
                            <TouchableOpacity
                                key={cat}
                                style={[
                                    styles.categoryChip,
                                    category === cat && styles.categoryChipActive,
                                ]}
                                onPress={() => setCategory(cat)}
                                disabled={submitting}
                            >
                                <Ionicons
                                    name={CATEGORY_ICONS[cat] as any}
                                    size={18}
                                    color={category === cat ? '#fff' : colors.textDim}
                                />
                                <Text
                                    style={[
                                        styles.categoryChipText,
                                        category === cat && styles.categoryChipTextActive,
                                    ]}
                                >
                                    {cat}
                                </Text>
                            </TouchableOpacity>
                        ))}
                    </View>

                    {/* Description */}
                    <Text style={[styles.label, { marginTop: 20 }]}>Describe what happened</Text>
                    <TextInput
                        style={styles.input}
                        placeholder="Please provide as much detail as possible..."
                        placeholderTextColor={colors.textDim}
                        multiline
                        numberOfLines={8}
                        textAlignVertical="top"
                        value={issue}
                        onChangeText={setIssue}
                        editable={!submitting}
                    />

                    {/* Photo Evidence */}
                    <Text style={[styles.label, { marginTop: 20 }]}>Photo Evidence (optional)</Text>
                    <View style={styles.photoGrid}>
                        {photos.map((uri, i) => (
                            <View key={i} style={styles.photoThumb}>
                                <Image source={{ uri }} style={styles.photoImage} contentFit="cover" />
                                <TouchableOpacity style={styles.photoRemove} onPress={() => removePhoto(i)}>
                                    <Ionicons name="close-circle" size={22} color="#EF4444" />
                                </TouchableOpacity>
                            </View>
                        ))}
                        {photos.length < 4 && (
                            <TouchableOpacity style={styles.photoAdd} onPress={addPhoto} disabled={submitting}>
                                <Ionicons name="camera-outline" size={28} color={colors.textDim} />
                                <Text style={styles.photoAddText}>Add</Text>
                            </TouchableOpacity>
                        )}
                    </View>

                    <TouchableOpacity
                        style={[styles.submitButton, submitting && styles.submitButtonDisabled]}
                        onPress={handleSubmit}
                        disabled={submitting}
                    >
                        <Text style={styles.submitButtonText}>
                            {submitting ? 'Submitting...' : 'Submit Report'}
                        </Text>
                    </TouchableOpacity>
                </ScrollView>
            </KeyboardAvoidingView>

        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        safeArea: {
            flex: 1,
            backgroundColor: colors.surface,
        },
        header: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingHorizontal: 20,
            paddingVertical: 16,
            borderBottomWidth: 1,
            borderBottomColor: colors.border,
            backgroundColor: colors.surface,
        },
        backButton: {
            padding: 8,
            marginLeft: -8,
        },
        headerTitle: {
            fontSize: 18,
            fontWeight: '600',
            color: colors.text,
        },
        headerRight: {
            width: 40,
        },
        container: {
            flex: 1,
            backgroundColor: colors.surface,
        },
        content: {
            padding: 24,
            paddingBottom: 140,
        },
        warningBox: {
            flexDirection: 'row',
            backgroundColor: 'rgba(245,158,11,0.1)',
            padding: 16,
            borderRadius: 12,
            marginBottom: 24,
            alignItems: 'flex-start',
            gap: 12,
        },
        warningText: {
            flex: 1,
            fontSize: 14,
            color: '#D97706',
            lineHeight: 20,
        },
        label: {
            fontSize: 15,
            fontWeight: '600',
            color: colors.text,
            marginBottom: 12,
        },
        categoryGrid: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 10,
        },
        categoryChip: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            paddingHorizontal: 16,
            paddingVertical: 10,
            borderRadius: 24,
            backgroundColor: colors.surfaceLight,
            borderWidth: 1.5,
            borderColor: colors.border,
        },
        categoryChipActive: {
            backgroundColor: colors.primary,
            borderColor: colors.primary,
        },
        categoryChipText: {
            fontSize: 14,
            fontWeight: '600',
            color: colors.textDim,
        },
        categoryChipTextActive: {
            color: '#fff',
        },
        input: {
            backgroundColor: colors.surfaceLight,
            borderWidth: 1,
            borderColor: colors.border,
            borderRadius: 12,
            padding: 16,
            fontSize: 15,
            color: colors.text,
            minHeight: 160,
            marginBottom: 24,
        },
        photoGrid: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 10,
            marginBottom: 24,
        },
        photoThumb: {
            width: 80,
            height: 80,
            borderRadius: 10,
            overflow: 'hidden',
        },
        photoImage: {
            width: 80,
            height: 80,
        },
        photoRemove: {
            position: 'absolute',
            top: 2,
            right: 2,
        },
        photoAdd: {
            width: 80,
            height: 80,
            borderRadius: 10,
            borderWidth: 1.5,
            borderColor: colors.border,
            borderStyle: 'dashed',
            justifyContent: 'center',
            alignItems: 'center',
            backgroundColor: colors.surfaceLight,
        },
        photoAddText: {
            fontSize: 11,
            color: colors.textDim,
            marginTop: 2,
        },
        submitButton: {
            backgroundColor: colors.primary,
            borderRadius: 12,
            paddingVertical: 16,
            alignItems: 'center',
        },
        submitButtonDisabled: {
            opacity: 0.7,
        },
        submitButtonText: {
            color: '#fff',
            fontSize: 16,
            fontWeight: '600',
        },
    });
}
