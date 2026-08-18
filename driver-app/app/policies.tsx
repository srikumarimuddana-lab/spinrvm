import React, { useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { LEGAL_DOC_TITLES, legalDocTypesForAudience, type LegalDocType } from '@shared/config/legalDocs';

// One entry point for every driver-facing legal/policy page (see
// shared/config/legalDocs.ts), navigating to legal.tsx?type=<slug> for
// each — replaces having to add a new Settings row per document as the
// legal-documentation set grows. The existing "/legal" row in
// driver/settings.tsx (combined ToS + Privacy view) is left in place
// (additive, not a replacement) so no existing link breaks.
const ICONS: Partial<Record<LegalDocType, keyof typeof Ionicons.glyphMap>> = {
    tos: 'document-text',
    privacy: 'eye',
    'community-guidelines': 'people',
    'non-discrimination': 'shield-checkmark',
    accessibility: 'accessibility',
    'cancellation-fees': 'card',
    'promotions-referral': 'gift',
    'insurance-periods': 'car',
    'deactivation-appeals': 'alert-circle',
};

export default function PoliciesScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const docTypes = legalDocTypesForAudience('driver');

    return (
        <View style={styles.safeArea}>
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Legal & Policies</Text>
                <View style={styles.headerRight} />
            </View>

            <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
                <View style={styles.card}>
                    {docTypes.map((docType, i) => (
                        <React.Fragment key={docType}>
                            <TouchableOpacity
                                style={styles.row}
                                onPress={() => router.push(`/legal?type=${docType}` as any)}
                            >
                                <View style={[styles.rowIcon, { backgroundColor: `${colors.primary}12` }]}>
                                    <Ionicons
                                        name={ICONS[docType] || 'document-text'}
                                        size={18}
                                        color={colors.primary}
                                    />
                                </View>
                                <Text style={styles.rowTitle}>{LEGAL_DOC_TITLES[docType]}</Text>
                                <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                            </TouchableOpacity>
                            {i < docTypes.length - 1 && <View style={styles.divider} />}
                        </React.Fragment>
                    ))}
                </View>
            </ScrollView>
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
        contentContainer: {
            padding: 16,
            paddingBottom: 40,
        },
        card: {
            backgroundColor: colors.surface,
            borderRadius: 14,
            borderWidth: 1,
            borderColor: colors.border,
            overflow: 'hidden',
        },
        row: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            paddingHorizontal: 16,
            paddingVertical: 14,
        },
        rowIcon: {
            width: 32,
            height: 32,
            borderRadius: 8,
            alignItems: 'center',
            justifyContent: 'center',
        },
        rowTitle: {
            flex: 1,
            fontSize: 15,
            fontWeight: '500',
            color: colors.text,
        },
        divider: {
            height: 1,
            backgroundColor: colors.border,
            marginLeft: 60,
        },
    });
}
