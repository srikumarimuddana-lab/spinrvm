import React, { useState, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TextInput,
    TouchableOpacity,
    KeyboardAvoidingView,
    Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SpinrConfig } from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function ReportSafetyScreen() {
    const router = useRouter();
    const { colors, isDark } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [issue, setIssue] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [alertState, setAlertState] = useState<{
        visible: boolean;
        title: string;
        message: string;
        variant: 'info' | 'warning' | 'danger' | 'success';
        buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
    }>({ visible: false, title: '', message: '', variant: 'info' });

    const handleSubmit = async () => {
        if (!issue.trim()) {
            setAlertState({ visible: true, title: 'Error', message: 'Please describe the safety issue before submitting.', variant: 'warning' });
            return;
        }

        setSubmitting(true);
        // Submit to the safety-report endpoint
        try {
            await fetch(`${SpinrConfig.backendUrl}/support/tickets/safety-report`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ description: issue })
            });


            setAlertState({
                visible: true,
                title: 'Report Submitted',
                message: 'Your safety report has been submitted. Our trust and safety team will review it immediately.',
                variant: 'success',
                buttons: [{ text: 'OK', onPress: () => router.back() }],
            });
        } catch (e) {
            setAlertState({ visible: true, title: 'Error', message: 'Failed to submit report. Please try again.', variant: 'danger' });
            setSubmitting(false);
        }
    };

    return (
        <SafeAreaView style={styles.safeArea}>
            {/* Header */}
            <View style={styles.header}>
                <TouchableOpacity
                    style={styles.backButton}
                    onPress={() => router.back()}
                >
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Report Safety Issue</Text>
                <View style={styles.headerRight} />
            </View>

            <KeyboardAvoidingView
                style={styles.container}
                behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
            >
                <View style={styles.content}>
                    <View style={styles.warningBox}>
                        <Ionicons name="warning" size={24} color="#F59E0B" />
                        <Text style={styles.warningText}>
                            If this is an emergency, please contact local authorities immediately using the Emergency Assist button in settings.
                        </Text>
                    </View>

                    <Text style={styles.label}>Please describe what happened:</Text>
                    <TextInput
                        style={styles.input}
                        placeholder="Type your description here..."
                        placeholderTextColor="#9CA3AF"
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
                </View>
            </KeyboardAvoidingView>
            <CustomAlert
                visible={alertState.visible}
                title={alertState.title}
                message={alertState.message}
                variant={alertState.variant}
                buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
                onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
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
            flex: 1,
        },
        warningBox: {
            flexDirection: 'row',
            backgroundColor: 'rgba(245,158,11,0.1)',
            padding: 16,
            borderRadius: 12,
            marginBottom: 24,
            alignItems: 'flex-start',
        },
        warningText: {
            flex: 1,
            marginLeft: 12,
            fontSize: 14,
            color: '#D97706',
            lineHeight: 20,
        },
        label: {
            fontSize: 16,
            fontWeight: '500',
            color: colors.text,
            marginBottom: 12,
        },
        input: {
            backgroundColor: colors.surfaceLight,
            borderWidth: 1,
            borderColor: colors.border,
            borderRadius: 12,
            padding: 16,
            fontSize: 16,
            color: colors.text,
            minHeight: 160,
            marginBottom: 24,
        },
        submitButton: {
            backgroundColor: colors.primary,
            borderRadius: 12,
            paddingVertical: 16,
            alignItems: 'center',
            marginTop: 'auto',
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
