import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    TextInput,
    ActivityIndicator,
    LayoutAnimation,
    Platform,
    UIManager,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
    UIManager.setLayoutAnimationEnabledExperimental(true);
}

interface FaqItem {
    id: string;
    question: string;
    answer: string;
    category: string;
    sort_order: number;
}

const CATEGORY_ORDER = ['Onboarding', 'Payments', 'Documents', 'Technical'];
const CATEGORY_ICONS: Record<string, string> = {
    Onboarding: 'rocket',
    Payments: 'wallet',
    Documents: 'document-text',
    Technical: 'settings',
    general: 'help-circle',
};

export default function FaqScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [faqs, setFaqs] = useState<FaqItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

    useEffect(() => {
        fetchFaqs();
    }, []);

    const fetchFaqs = async () => {
        try {
            const res = await api.get('/faqs', { params: { audience: 'driver' } });
            setFaqs(res.data || []);
        } catch (err) {
            console.log('FAQ fetch error:', err);
        } finally {
            setLoading(false);
        }
    };

    const toggleItem = useCallback((id: string) => {
        LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
        setExpandedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    }, []);

    const filteredFaqs = useMemo(() => {
        if (!search.trim()) return faqs;
        const q = search.toLowerCase();
        return faqs.filter(
            (f) =>
                f.question.toLowerCase().includes(q) ||
                f.answer.toLowerCase().includes(q),
        );
    }, [faqs, search]);

    const grouped = useMemo(() => {
        const map = new Map<string, FaqItem[]>();
        for (const faq of filteredFaqs) {
            const cat = faq.category || 'general';
            const key = cat.charAt(0).toUpperCase() + cat.slice(1);
            if (!map.has(key)) map.set(key, []);
            map.get(key)!.push(faq);
        }
        // Sort categories: known order first, then alphabetical
        const keys = Array.from(map.keys()).sort((a, b) => {
            const ia = CATEGORY_ORDER.indexOf(a);
            const ib = CATEGORY_ORDER.indexOf(b);
            if (ia !== -1 && ib !== -1) return ia - ib;
            if (ia !== -1) return -1;
            if (ib !== -1) return 1;
            return a.localeCompare(b);
        });
        return keys.map((k) => ({ category: k, items: map.get(k)! }));
    }, [filteredFaqs]);

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient
                colors={[colors.surface, colors.background]}
                style={[styles.header, { paddingTop: insets.top + 12 }]}
            >
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Help Center</Text>
                    <TouchableOpacity
                        onPress={() => router.push('/driver/help' as any)}
                        style={styles.chatBtn}
                    >
                        <Ionicons name="chatbubble-ellipses-outline" size={22} color={colors.primary} />
                    </TouchableOpacity>
                </View>

                {/* Search bar */}
                <View style={styles.searchRow}>
                    <Ionicons name="search" size={18} color={colors.textDim} style={styles.searchIcon} />
                    <TextInput
                        style={styles.searchInput}
                        placeholder="Search questions..."
                        placeholderTextColor={colors.textDim}
                        value={search}
                        onChangeText={setSearch}
                        returnKeyType="search"
                        clearButtonMode="while-editing"
                    />
                    {search.length > 0 && (
                        <TouchableOpacity onPress={() => setSearch('')} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                            <Ionicons name="close-circle" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                    )}
                </View>
            </LinearGradient>

            {loading ? (
                <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: 60 }} />
            ) : (
                <ScrollView
                    contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 40, paddingTop: 16 }}
                    showsVerticalScrollIndicator={false}
                    keyboardShouldPersistTaps="handled"
                >
                    {grouped.length === 0 ? (
                        <View style={styles.emptyState}>
                            <Ionicons name="search-outline" size={56} color={colors.border} />
                            <Text style={styles.emptyTitle}>No results found</Text>
                            <Text style={styles.emptySub}>Try a different keyword or browse all categories.</Text>
                        </View>
                    ) : (
                        grouped.map(({ category, items }) => (
                            <View key={category} style={styles.categorySection}>
                                <View style={styles.categoryHeader}>
                                    <View style={styles.categoryIconBox}>
                                        <Ionicons
                                            name={(CATEGORY_ICONS[category] ?? 'help-circle') as any}
                                            size={16}
                                            color={colors.primary}
                                        />
                                    </View>
                                    <Text style={styles.categoryTitle}>{category}</Text>
                                    <Text style={styles.categoryCount}>{items.length}</Text>
                                </View>

                                <View style={styles.accordion}>
                                    {items.map((faq, idx) => {
                                        const isOpen = expandedIds.has(faq.id);
                                        return (
                                            <View key={faq.id}>
                                                {idx > 0 && <View style={styles.itemDivider} />}
                                                <TouchableOpacity
                                                    style={styles.questionRow}
                                                    onPress={() => toggleItem(faq.id)}
                                                    activeOpacity={0.7}
                                                >
                                                    <Text style={styles.questionText}>{faq.question}</Text>
                                                    <Ionicons
                                                        name={isOpen ? 'chevron-up' : 'chevron-down'}
                                                        size={18}
                                                        color={colors.textDim}
                                                    />
                                                </TouchableOpacity>
                                                {isOpen && (
                                                    <View style={styles.answerBox}>
                                                        <Text style={styles.answerText}>{faq.answer}</Text>
                                                    </View>
                                                )}
                                            </View>
                                        );
                                    })}
                                </View>
                            </View>
                        ))
                    )}

                    {/* Still need help? */}
                    <View style={styles.stillNeedHelp}>
                        <Text style={styles.stillNeedHelpTitle}>Still need help?</Text>
                        <Text style={styles.stillNeedHelpSub}>Ask our AI support assistant or contact us directly.</Text>
                        <TouchableOpacity
                            style={styles.chatBotBtn}
                            onPress={() => router.push('/driver/help' as any)}
                        >
                            <Ionicons name="chatbubble-ellipses" size={18} color="#fff" />
                            <Text style={styles.chatBotBtnText}>Chat with Support Bot</Text>
                        </TouchableOpacity>
                    </View>
                </ScrollView>
            )}
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },

        header: {
            paddingBottom: 16,
            paddingHorizontal: 16,
        },
        headerRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 14,
        },
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },
        chatBtn: {
            width: 40,
            height: 40,
            justifyContent: 'center',
            alignItems: 'center',
        },

        searchRow: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: colors.surface,
            borderRadius: 12,
            paddingHorizontal: 12,
            borderWidth: 1,
            borderColor: colors.border,
            height: 44,
        },
        searchIcon: { marginRight: 8 },
        searchInput: {
            flex: 1,
            color: colors.text,
            fontSize: 15,
            paddingVertical: 0,
        },

        categorySection: { marginBottom: 20 },
        categoryHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            marginBottom: 10,
        },
        categoryIconBox: {
            width: 28,
            height: 28,
            borderRadius: 8,
            backgroundColor: `${colors.primary}15`,
            justifyContent: 'center',
            alignItems: 'center',
            marginRight: 8,
        },
        categoryTitle: {
            flex: 1,
            color: colors.text,
            fontSize: 16,
            fontWeight: '700',
        },
        categoryCount: {
            color: colors.textDim,
            fontSize: 13,
            fontWeight: '600',
        },

        accordion: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            overflow: 'hidden',
            borderWidth: 1,
            borderColor: colors.border,
        },
        itemDivider: {
            height: 1,
            backgroundColor: colors.border,
            marginHorizontal: 16,
        },
        questionRow: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 16,
            paddingVertical: 14,
            gap: 12,
        },
        questionText: {
            flex: 1,
            color: colors.text,
            fontSize: 14,
            fontWeight: '600',
            lineHeight: 20,
        },
        answerBox: {
            paddingHorizontal: 16,
            paddingBottom: 16,
        },
        answerText: {
            color: colors.textDim,
            fontSize: 14,
            lineHeight: 22,
        },

        emptyState: {
            alignItems: 'center',
            paddingVertical: 60,
        },
        emptyTitle: {
            color: colors.text,
            fontSize: 18,
            fontWeight: '600',
            marginTop: 16,
        },
        emptySub: {
            color: colors.textDim,
            fontSize: 14,
            marginTop: 6,
            textAlign: 'center',
        },

        stillNeedHelp: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 20,
            alignItems: 'center',
            marginTop: 8,
            marginBottom: 16,
            borderWidth: 1,
            borderColor: colors.border,
        },
        stillNeedHelpTitle: {
            color: colors.text,
            fontSize: 16,
            fontWeight: '700',
            marginBottom: 6,
        },
        stillNeedHelpSub: {
            color: colors.textDim,
            fontSize: 13,
            textAlign: 'center',
            marginBottom: 16,
            lineHeight: 18,
        },
        chatBotBtn: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            backgroundColor: colors.primary,
            paddingHorizontal: 20,
            paddingVertical: 12,
            borderRadius: 24,
        },
        chatBotBtnText: {
            color: '#fff',
            fontSize: 14,
            fontWeight: '700',
        },
    });
}
