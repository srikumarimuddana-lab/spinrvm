import React, { useCallback, useEffect, useState, useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SpinrConfig } from '@shared/config/spinr.config';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../hooks/useToast';

// Purpose-built consent screen for the Criminal Record Check / Vulnerable
// Sector Check — separate from the general Terms/Privacy acceptance flow,
// because criminal-record data is a sensitive PIPEDA category that deserves
// its own explicit, versioned consent record (see
// backend/services/driver_crc_consent.py, migration 319, and
// docs/legal/background-check-consent.md). Reached from driver/settings.tsx
// directly — not from the general policies.tsx hub, since agreeing here is
// a required action with a consent record, not just reading a policy page.
//
// NOTE: this screen is not yet wired into the become-driver.tsx onboarding
// wizard's step gating (i.e. a driver isn't currently blocked from
// proceeding past the Documents step without visiting this screen). Adding
// that gate means touching the existing 5-step wizard's flow-control logic,
// which is out of scope for this pass — flagged as a deliberate follow-up,
// not an oversight. Today this screen is reachable, self-contained, and its
// consent record is correctly captured whenever a driver does visit it.
export default function CrcConsentScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [consentText, setConsentText] = useState('');
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [checked, setChecked] = useState(false);
    const [alreadyCurrent, setAlreadyCurrent] = useState(false);
    const [consentedAt, setConsentedAt] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            type ConsentStatus = { is_current?: boolean; consented_at?: string | null };
            const [docRes, statusRes]: [{ content?: string }, ConsentStatus] = await Promise.all([
                fetch(
                    `${SpinrConfig.backendUrl}/legal-documents?audience=driver&type=background-check-consent`
                ).then((r) => r.json()),
                api.get<ConsentStatus>('/drivers/crc-consent').then((r) => r.data),
            ]);
            setConsentText(docRes.content || 'This consent form has not been published yet. Contact support before continuing.');
            setAlreadyCurrent(!!statusRes.is_current);
            setConsentedAt(statusRes.consented_at || null);
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

    const submit = async () => {
        setSubmitting(true);
        try {
            await api.post('/drivers/crc-consent');
            showToast('success', 'Consent recorded', 'Thank you.');
            router.back();
        } catch (err: any) {
            showToast('error', 'Could not submit', getApiErrorMessage(err, 'Please try again.'));
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
                <Text style={styles.headerTitle}>Background Check Consent</Text>
                <View style={styles.headerRight} />
            </View>

            {loading ? (
                <View style={styles.loadingContainer}>
                    <ActivityIndicator size="large" color={colors.primary} />
                </View>
            ) : (
                <>
                    <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
                        {alreadyCurrent && (
                            <View style={styles.currentBanner}>
                                <Ionicons name="checkmark-circle" size={18} color={colors.success || colors.primary} />
                                <Text style={styles.currentBannerText}>
                                    {consentedAt
                                        ? `You already gave this consent on ${new Date(consentedAt).toLocaleDateString()}.`
                                        : 'You already gave this consent.'}
                                </Text>
                            </View>
                        )}
                        <Text style={styles.textContent}>{consentText}</Text>
                    </ScrollView>

                    <View style={[styles.footer, { paddingBottom: insets.bottom + 16 }]}>
                        {!alreadyCurrent && (
                            <TouchableOpacity
                                style={styles.checkboxRow}
                                onPress={() => setChecked((c) => !c)}
                                accessibilityRole="checkbox"
                                accessibilityState={{ checked }}
                            >
                                <Ionicons
                                    name={checked ? 'checkbox' : 'square-outline'}
                                    size={22}
                                    color={checked ? colors.primary : colors.textDim}
                                />
                                <Text style={styles.checkboxLabel}>
                                    I consent to a Criminal Record Check and Vulnerable Sector Check as described above.
                                </Text>
                            </TouchableOpacity>
                        )}
                        <TouchableOpacity
                            style={[
                                styles.submitButton,
                                { backgroundColor: colors.primary },
                                (!alreadyCurrent && !checked) || submitting ? styles.submitButtonDisabled : null,
                            ]}
                            disabled={(!alreadyCurrent && !checked) || submitting}
                            onPress={alreadyCurrent ? () => router.back() : submit}
                        >
                            {submitting ? (
                                <ActivityIndicator size="small" color="#fff" />
                            ) : (
                                <Text style={styles.submitButtonText}>
                                    {alreadyCurrent ? 'Continue' : 'I Consent & Continue'}
                                </Text>
                            )}
                        </TouchableOpacity>
                    </View>
                </>
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
        headerTitle: { fontSize: 18, fontWeight: '600', color: colors.text },
        headerRight: { width: 40 },
        loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
        container: { flex: 1, backgroundColor: colors.surface },
        contentContainer: { padding: 24, paddingBottom: 24 },
        currentBanner: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            backgroundColor: colors.surfaceLight,
            borderRadius: 10,
            padding: 12,
            marginBottom: 20,
        },
        currentBannerText: { flex: 1, fontSize: 13, color: colors.textSecondary },
        textContent: { fontSize: 15, lineHeight: 24, color: colors.text },
        footer: {
            paddingHorizontal: 20,
            paddingTop: 12,
            borderTopWidth: 1,
            borderTopColor: colors.border,
            backgroundColor: colors.surface,
            gap: 14,
        },
        checkboxRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
        checkboxLabel: { flex: 1, fontSize: 13, lineHeight: 19, color: colors.text },
        submitButton: {
            borderRadius: 12,
            paddingVertical: 14,
            alignItems: 'center',
            justifyContent: 'center',
        },
        submitButtonDisabled: { opacity: 0.5 },
        submitButtonText: { color: '#fff', fontSize: 15, fontWeight: '600' },
    });
}
