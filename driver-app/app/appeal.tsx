import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput, ActivityIndicator } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../hooks/useToast';

// Driver-facing side of docs/legal/driver-deactivation-appeals-policy.md.
// Lets a driver whose account is suspended/banned/under review submit an
// appeal and see the outcome of prior ones. Backend derives the appeal
// type and original reason from the driver's actual current status
// server-side (routes/drivers/appeals.py) — this screen only collects the
// driver's message, so there's no client-side way to appeal a status the
// driver isn't actually in.
//
// NOTE: reached from driver/settings.tsx, same as crc-consent.tsx — not
// yet surfaced automatically from the suspension/ban toast in
// useDriverDashboard.ts's Go-Online handler. Wiring that up means touching
// that hook's existing status-toast logic, which is a separate, more
// invasive change than this screen itself. Flagged as a deliberate
// follow-up, not an oversight.

type Appeal = {
    id: string;
    appeal_type: string;
    driver_message: string;
    status: 'pending' | 'approved' | 'denied';
    admin_note: string | null;
    created_at: string;
    resolved_at: string | null;
};

const STATUS_LABEL: Record<Appeal['status'], string> = {
    pending: 'Under review',
    approved: 'Approved',
    denied: 'Denied',
};

export default function AppealScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [appeals, setAppeals] = useState<Appeal[]>([]);
    const [loading, setLoading] = useState(true);
    const [message, setMessage] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [ineligible, setIneligible] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.get<Appeal[]>('/drivers/appeals');
            setAppeals(res.data || []);
        } catch (err: any) {
            showToast('error', 'Could not load', getApiErrorMessage(err, 'Please check your connection and try again.'));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        load();
    }, [load]);

    const hasPending = appeals.some((a) => a.status === 'pending');

    const submit = async () => {
        if (!message.trim()) return;
        setSubmitting(true);
        setIneligible(null);
        try {
            await api.post('/drivers/appeals', { message: message.trim() });
            setMessage('');
            showToast('success', 'Appeal submitted', "We'll review it and get back to you.");
            await load();
        } catch (err: any) {
            const status = err?.response?.status ?? err?.status;
            if (status === 400) {
                setIneligible(getApiErrorMessage(err, "Your account isn't currently under review."));
            } else {
                showToast('error', 'Could not submit', getApiErrorMessage(err, 'Please try again.'));
            }
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <View style={styles.safeArea}>
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Appeal Your Account Status</Text>
                <View style={styles.headerRight} />
            </View>

            {loading ? (
                <View style={styles.loadingContainer}>
                    <ActivityIndicator size="large" color={colors.primary} />
                </View>
            ) : (
                <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
                    {hasPending ? (
                        <View style={styles.pendingBanner}>
                            <Ionicons name="time" size={18} color={colors.primary} />
                            <Text style={styles.pendingBannerText}>
                                Your appeal is under review. We'll notify you once a decision is made.
                            </Text>
                        </View>
                    ) : (
                        <>
                            <Text style={styles.sectionTitle}>Submit an appeal</Text>
                            <Text style={styles.helperText}>
                                Tell us why you believe your account's current status should be reviewed. A different
                                reviewer than the one who made the original decision will look at your appeal.
                            </Text>
                            <TextInput
                                style={styles.textInput}
                                value={message}
                                onChangeText={setMessage}
                                placeholder="Explain your situation..."
                                placeholderTextColor={colors.textDim}
                                multiline
                                numberOfLines={6}
                                textAlignVertical="top"
                            />
                            {ineligible && <Text style={styles.errorText}>{ineligible}</Text>}
                            <TouchableOpacity
                                style={[
                                    styles.submitButton,
                                    { backgroundColor: colors.primary },
                                    !message.trim() || submitting ? styles.submitButtonDisabled : null,
                                ]}
                                disabled={!message.trim() || submitting}
                                onPress={submit}
                            >
                                {submitting ? (
                                    <ActivityIndicator size="small" color="#fff" />
                                ) : (
                                    <Text style={styles.submitButtonText}>Submit Appeal</Text>
                                )}
                            </TouchableOpacity>
                        </>
                    )}

                    {appeals.length > 0 && (
                        <>
                            <Text style={[styles.sectionTitle, { marginTop: 28 }]}>Your appeal history</Text>
                            {appeals.map((a) => (
                                <View key={a.id} style={styles.historyCard}>
                                    <View style={styles.historyHeaderRow}>
                                        <Text style={styles.historyStatus}>{STATUS_LABEL[a.status]}</Text>
                                        <Text style={styles.historyDate}>{new Date(a.created_at).toLocaleDateString()}</Text>
                                    </View>
                                    <Text style={styles.historyMessage}>{a.driver_message}</Text>
                                    {a.admin_note && <Text style={styles.historyNote}>Response: {a.admin_note}</Text>}
                                </View>
                            ))}
                        </>
                    )}
                </ScrollView>
            )}
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        safeArea: { flex: 1, backgroundColor: colors.surface },
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
        backButton: { padding: 8, marginLeft: -8 },
        headerTitle: { fontSize: 16, fontWeight: '600', color: colors.text, flex: 1, textAlign: 'center' },
        headerRight: { width: 40 },
        loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
        container: { flex: 1, backgroundColor: colors.surface },
        contentContainer: { padding: 20, paddingBottom: 40 },
        pendingBanner: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            backgroundColor: colors.surfaceLight,
            borderRadius: 12,
            padding: 14,
        },
        pendingBannerText: { flex: 1, fontSize: 13, lineHeight: 19, color: colors.text },
        sectionTitle: { fontSize: 16, fontWeight: '700', color: colors.text, marginBottom: 8 },
        helperText: { fontSize: 13, lineHeight: 19, color: colors.textSecondary, marginBottom: 12 },
        textInput: {
            borderWidth: 1,
            borderColor: colors.border,
            borderRadius: 12,
            padding: 14,
            fontSize: 14,
            color: colors.text,
            minHeight: 130,
            marginBottom: 8,
        },
        errorText: { fontSize: 13, color: colors.error || '#DC2626', marginBottom: 8 },
        submitButton: {
            borderRadius: 12,
            paddingVertical: 14,
            alignItems: 'center',
            justifyContent: 'center',
            marginTop: 8,
        },
        submitButtonDisabled: { opacity: 0.5 },
        submitButtonText: { color: '#fff', fontSize: 15, fontWeight: '600' },
        historyCard: {
            backgroundColor: colors.surfaceLight,
            borderRadius: 12,
            padding: 14,
            marginBottom: 10,
        },
        historyHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
        historyStatus: { fontSize: 13, fontWeight: '600', color: colors.text },
        historyDate: { fontSize: 12, color: colors.textDim },
        historyMessage: { fontSize: 13, lineHeight: 19, color: colors.textSecondary },
        historyNote: { fontSize: 12, lineHeight: 18, color: colors.textDim, marginTop: 6, fontStyle: 'italic' },
    });
}
