import React, { useState } from 'react';
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

const THEME = SpinrConfig.theme.colors;

export default function SupportScreen() {
    const router = useRouter();
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
        // Submit to the support endpoint
        try {
            await fetch(`${SpinrConfig.backendUrl}/support/tickets`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: 'App Support Request', message: issue, category: 'general' })
            });

            setAlertState({
                visible: true,
                title: 'Request Submitted',
                message: 'Your support request has been submitted. Our team will get back to you shortly.',
                variant: 'success',
                buttons: [{ text: 'OK', onPress: () => router.back() }],
            });
        } catch (e) {
            setAlertState({ visible: true, title: 'Error', message: 'Failed to submit request. Please try again.', variant: 'danger' });
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
                    <Ionicons name="arrow-back" size={24} color={THEME.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Contact Support</Text>
                <View style={styles.headerRight} />
            </View>

            <KeyboardAvoidingView
                style={styles.container}
                behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
            >
                <View style={styles.content}>
                    <Text style={styles.label}>How can we help you today?</Text>
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

                    {/* Company Info */}
                    <View style={styles.companyCard}>
                        <Text style={styles.companyTitle}>SPINR TECHNOLOGIES INC.</Text>
                        <View style={styles.companyRow}>
                            <Ionicons name="location-outline" size={16} color="#999" />
                            <Text style={styles.companyText}>Saskatoon, SK, Canada</Text>
                        </View>
                        <View style={styles.companyRow}>
                            <Ionicons name="mail-outline" size={16} color="#999" />
                            <Text style={styles.companyText}>support@spinr.ca</Text>
                        </View>
                        <View style={styles.companyRow}>
                            <Ionicons name="call-outline" size={16} color="#999" />
                            <Text style={styles.companyText}>+1 (306) 555-0199</Text>
                        </View>
                        <View style={styles.companyRow}>
                            <Ionicons name="globe-outline" size={16} color="#999" />
                            <Text style={styles.companyText}>www.spinr.ca</Text>
                        </View>
                        <Text style={styles.companyHours}>Mon–Fri 9am–6pm CST</Text>
                    </View>
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

const styles = StyleSheet.create({
    safeArea: {
        flex: 1,
        backgroundColor: '#fff',
    },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingHorizontal: 20,
        paddingVertical: 16,
        borderBottomWidth: 1,
        borderBottomColor: '#F3F4F6',
        backgroundColor: '#fff',
    },
    backButton: {
        padding: 8,
        marginLeft: -8,
    },
    headerTitle: {
        fontSize: 18,
        fontWeight: '600',
        color: THEME.text,
    },
    headerRight: {
        width: 40,
    },
    container: {
        flex: 1,
        backgroundColor: '#fff',
    },
    content: {
        padding: 24,
        flex: 1,
    },
    label: {
        fontSize: 16,
        fontWeight: '500',
        color: THEME.text,
        marginBottom: 12,
    },
    input: {
        backgroundColor: '#F9FAFB',
        borderWidth: 1,
        borderColor: '#E5E7EB',
        borderRadius: 12,
        padding: 16,
        fontSize: 16,
        color: THEME.text,
        minHeight: 160,
        marginBottom: 24,
    },
    submitButton: {
        backgroundColor: THEME.primary,
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
    companyCard: {
        marginTop: 32,
        backgroundColor: '#F9F9F9',
        borderRadius: 16,
        padding: 20,
    },
    companyTitle: {
        fontSize: 12,
        fontWeight: '700',
        color: THEME.primary,
        letterSpacing: 0.5,
        marginBottom: 14,
    },
    companyRow: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10,
        marginBottom: 10,
    },
    companyText: {
        fontSize: 14,
        color: '#444',
    },
    companyHours: {
        fontSize: 12,
        color: '#999',
        marginTop: 8,
        fontStyle: 'italic',
    },
});
