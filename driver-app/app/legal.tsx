import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    ScrollView,
    TouchableOpacity,
    ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SpinrConfig } from '@shared/config/spinr.config';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

// Previously this fallback held hardcoded ToS/Privacy Policy text that was
// factually wrong for Spinr's actual business (Ontario governing law, a
// 21+ age minimum, and a "platform service fee" deducted from earnings —
// contradicting the real Saskatchewan-first, 0%-commission model). Showing
// wrong legal text is worse than showing an honest "not available yet"
// message, so this is now a neutral placeholder, matching what
// rider-app/app/legal.tsx already shows when no content is published.
// Do not restore fabricated legal text here — see docs/legal/terms-of-service.md
// and docs/legal/privacy-policy.md for the actual drafts, which are pending
// counsel review before publication (see docs/legal/legal-text-publication-checklist.md).
const FALLBACK_PRIVACY_POLICY = 'No Privacy Policy has been added yet.';
const FALLBACK_TERMS_OF_SERVICE = 'No Terms of Service have been added yet.';

export default function LegalScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const params = useLocalSearchParams();
    const type = params.type as 'tos' | 'privacy' | undefined;
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const scrollRef = useRef<ScrollView>(null);
    const privacyY = useRef(0);
    const tosY = useRef(0);

    const [privacyText, setPrivacyText] = useState('');
    const [tosText, setTosText] = useState('');
    const [loading, setLoading] = useState(true);

    // Declared before the `useEffect` below (react-hooks/immutability /
    // React Compiler flags referencing a function before its source-order
    // declaration) — same expression, same effect timing, no behavior change.
    //
    // Reads the per-audience /legal-documents endpoint instead of the legacy
    // shared /settings/legal blob, so drivers see driver-audience content
    // (Terms of Service Part A+B) once it's published, distinct from what
    // riders see. The endpoint itself falls back to the legacy shared blob
    // server-side when no per-audience row exists yet, so this is a
    // non-breaking switch — same displayed content today, correct content
    // once the admin dashboard publishes per-audience rows.
    const fetchLegalContent = async () => {
        try {
            // Public endpoint — no auth, no /api/v1 prefix (mounted at root).
            const [privacyRes, tosRes] = await Promise.all([
                fetch(`${SpinrConfig.backendUrl}/legal-documents?audience=driver&type=privacy`),
                fetch(`${SpinrConfig.backendUrl}/legal-documents?audience=driver&type=tos`),
            ]);
            const [privacyData, tosData] = await Promise.all([privacyRes.json(), tosRes.json()]);
            setPrivacyText(privacyData.content || FALLBACK_PRIVACY_POLICY);
            setTosText(tosData.content || FALLBACK_TERMS_OF_SERVICE);
        } catch {
            setPrivacyText(FALLBACK_PRIVACY_POLICY);
            setTosText(FALLBACK_TERMS_OF_SERVICE);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        // Mount-only fetch; fetchLegalContent sets state after its own await
        // (or in its catch fallback), not synchronously at the top of the
        // effect. Empty deps, runs once.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        fetchLegalContent();
    }, []);

    useEffect(() => {
        if (!loading && type && scrollRef.current) {
            const delay = setTimeout(() => {
                if (type === 'privacy') {
                    scrollRef.current?.scrollTo({ y: privacyY.current, animated: true });
                } else if (type === 'tos') {
                    scrollRef.current?.scrollTo({ y: tosY.current, animated: true });
                }
            }, 150);
            return () => clearTimeout(delay);
        }
    }, [loading, type]);

    return (
        <View style={styles.safeArea}>
            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Legal</Text>
                <View style={styles.headerRight} />
            </View>

            {loading ? (
                <View style={styles.loadingContainer}>
                    <ActivityIndicator size="large" color={colors.primary} />
                </View>
            ) : (
                <ScrollView
                    ref={scrollRef}
                    style={styles.container}
                    contentContainerStyle={styles.contentContainer}
                    showsVerticalScrollIndicator={false}
                >
                    {/* Privacy Policy Section */}
                    <View
                        onLayout={(e) => { privacyY.current = e.nativeEvent.layout.y; }}
                    >
                        <View style={styles.sectionHeader}>
                            <Ionicons name="shield-checkmark" size={20} color={colors.primary} />
                            <Text style={styles.sectionTitle}>Privacy Policy</Text>
                        </View>
                        <Text style={styles.textContent}>{privacyText}</Text>
                    </View>

                    <View style={styles.divider} />

                    {/* Terms of Service Section */}
                    <View
                        onLayout={(e) => { tosY.current = e.nativeEvent.layout.y; }}
                    >
                        <View style={styles.sectionHeader}>
                            <Ionicons name="document-text" size={20} color={colors.primary} />
                            <Text style={styles.sectionTitle}>Terms of Service</Text>
                        </View>
                        <Text style={styles.textContent}>{tosText}</Text>
                    </View>
                </ScrollView>
            )}
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
        loadingContainer: {
            flex: 1,
            justifyContent: 'center',
            alignItems: 'center',
        },
        container: {
            flex: 1,
            backgroundColor: colors.surface,
        },
        contentContainer: {
            padding: 24,
            paddingBottom: 60,
        },
        sectionHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            marginBottom: 16,
        },
        sectionTitle: {
            fontSize: 20,
            fontWeight: '700',
            color: colors.text,
        },
        textContent: {
            fontSize: 15,
            lineHeight: 24,
            color: colors.text,
        },
        divider: {
            height: 1,
            backgroundColor: colors.border,
            marginVertical: 32,
        },
    });
}
