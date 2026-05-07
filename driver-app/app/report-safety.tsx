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
    SafeAreaView
} from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import api from '@shared/api/client';
import CustomAlert, { AlertButton } from '@shared/components/CustomAlert';
import { useLocationStore } from '@shared/store/locationStore';
import useDriverStore from '../store/driverStore';

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
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [category, setCategory] = useState<SafetyCategory | null>(null);
    const [issue, setIssue] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [alert, setAlert] = useState<{
        visible: boolean; title: string; message?: string;
        variant: 'info' | 'success' | 'danger' | 'warning';
        buttons?: AlertButton[];
    }>({ visible: false, title: '', variant: 'info' });

    const showAlert = (title: string, message: string, variant: 'success' | 'danger' | 'warning' | 'info' = 'info', buttons?: AlertButton[]) => {
        setAlert({ visible: true, title, message, variant, buttons });
    };

    const location = useLocationStore(state => state.currentLocation);
    const activeRide = useDriverStore(state => state.activeRide);

    const handleSubmit = async () => {
        if (!category) {
            showAlert('Category Required', 'Please select a category for your safety report.', 'warning');
            return;
        }
        if (!issue.trim()) {
            showAlert('Description Required', 'Please describe the safety issue before submitting.', 'warning');
            return;
        }

        setSubmitting(true);

        const reportData = {
            category,
            description: issue,
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
            await api.post('/safety/report', reportData);
            showAlert(
                'Report Submitted',
                'Your safety report has been submitted. Our trust and safety team will review it promptly.',
                'success',
                [{ text: 'OK', style: 'default', onPress: () => router.back() }],
            );
        } catch (e) {
            showAlert('Error', 'Failed to submit report. Please try again.', 'danger');
            setSubmitting(false);
        }
    };

    return (
        <SafeAreaView style={styles.safeArea}>
            {/* Header */}
            <View style={styles.header}>
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

            <CustomAlert
                visible={alert.visible}
                title={alert.title}
                message={alert.message}
                variant={alert.variant}
                buttons={alert.buttons || [{ text: 'OK', style: 'default' }]}
                onClose={() => setAlert(a => ({ ...a, visible: false }))}
            />
        </SafeAreaView>
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
            paddingBottom: 40,
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
