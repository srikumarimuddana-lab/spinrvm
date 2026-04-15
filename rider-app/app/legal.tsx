import React, { useEffect, useState, useMemo } from 'react';
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

export default function LegalScreen() {
    const router = useRouter();
    const params = useLocalSearchParams();
    const type = params.type as 'tos' | 'privacy';
    const { colors, isDark } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [content, setContent] = useState<string>('');
    const [loading, setLoading] = useState(true);

    const title = type === 'tos' ? 'Terms of Service' : 'Privacy Policy';

    useEffect(() => {
        fetchLegalText();
    }, [type]);

    const fetchLegalText = async () => {
        try {
            const response = await fetch(`${SpinrConfig.backendUrl}/settings/legal`);
            const data = await response.json();
            if (type === 'tos') {
                setContent(data.terms_of_service_text || 'No Terms of Service have been added yet.');
            } else {
                setContent(data.privacy_policy_text || 'No Privacy Policy has been added yet.');
            }
        } catch (e) {
            console.error(e);
            setContent('Failed to load document. Please check your connection.');
        } finally {
            setLoading(false);
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
