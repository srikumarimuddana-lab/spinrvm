import React, { useCallback, useEffect, useState, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    ScrollView,
    TouchableOpacity,
    ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SpinrConfig } from '@shared/config/spinr.config';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { isValidLegalDocType, legalDocFallbackText, legalDocTitle } from '@shared/config/legalDocs';

export default function LegalScreen() {
    const router = useRouter();
    const params = useLocalSearchParams();
    // Accepts any published doc type (see shared/config/legalDocs.ts), not
    // just 'tos'/'privacy' — defaults to 'tos' for back-compat with any
    // existing deep link that omits the param.
    const rawType = (params.type as string) || 'tos';
    const docType = isValidLegalDocType(rawType) ? rawType : 'tos';
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [content, setContent] = useState<string>('');
    const [loading, setLoading] = useState(true);

    const title = legalDocTitle(docType);

    // Wrapped in useCallback (deps: [docType]) so the effect below can
    // safely depend on it — as a plain function it would have been
    // recreated every render, which would have made the effect refetch
    // every render too.
    //
    // Reads the per-audience /legal-documents endpoint instead of the legacy
    // shared /settings/legal blob, so riders see rider-audience content
    // (Terms of Service Part A only, no driver-specific terms) once it's
    // published. The endpoint falls back to the legacy shared blob
    // server-side when no per-audience row exists yet (tos/privacy only),
    // so this is a non-breaking switch — same displayed content today,
    // correct audience-scoped content once the admin dashboard publishes it.
    const fetchLegalText = useCallback(async () => {
        try {
            const response = await fetch(
                `${SpinrConfig.backendUrl}/legal-documents?audience=rider&type=${docType}`
            );
            const data = await response.json();
            setContent(data.content || legalDocFallbackText(docType));
        } catch (e) {
            console.error(e);
            setContent('Failed to load document. Please check your connection.');
        } finally {
            setLoading(false);
        }
    }, [docType]);

    useEffect(() => {
        // Re-fetches only when the `type` route param changes (fetchLegalText
        // is itself keyed on [type], so this is equivalent to depending on
        // [type] directly); the loading/text state this sets isn't in the
        // dep array.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        fetchLegalText();
    }, [fetchLegalText]);

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
                <Text style={styles.headerTitle}>{title}</Text>
                <View style={styles.headerRight} />
            </View>

            {/* Content */}
            <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
                {loading ? (
                    <View style={styles.loadingContainer}>
                        <ActivityIndicator size="large" color={colors.primary} />
                    </View>
                ) : (
                    <Text style={styles.textContent}>{content}</Text>
                )}
            </ScrollView>
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
        contentContainer: {
            padding: 24,
            paddingBottom: 40,
        },
        loadingContainer: {
            paddingTop: 100,
            alignItems: 'center',
        },
        textContent: {
            fontSize: 15,
            lineHeight: 24,
            color: colors.text,
        },
    });
}
