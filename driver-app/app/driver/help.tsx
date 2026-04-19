import React, { useState, useRef, useMemo, useCallback } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    TextInput,
    FlatList,
    ActivityIndicator,
    KeyboardAvoidingView,
    Platform,
    Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const SUPPORT_PHONE = '1-800-SPINR';
const SUPPORT_EMAIL = 'support@spinr.ca';

interface Message {
    id: string;
    role: 'user' | 'bot';
    text: string;
    loading?: boolean;
}

const WELCOME_MESSAGE: Message = {
    id: 'welcome',
    role: 'bot',
    text: "Hi! I'm the Spinr support assistant. I can help you with onboarding, payments, documents, and technical issues. What can I help you with today?",
};

export default function HelpScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const { user } = useAuthStore();

    const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
    const [input, setInput] = useState('');
    const [sending, setSending] = useState(false);
    const listRef = useRef<FlatList>(null);

    const scrollToBottom = useCallback(() => {
        setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 80);
    }, []);

    const sendMessage = useCallback(async () => {
        const text = input.trim();
        if (!text || sending) return;

        setInput('');
        setSending(true);

        const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', text };
        const loadingMsg: Message = { id: `l-${Date.now()}`, role: 'bot', text: '', loading: true };

        setMessages((prev) => [...prev, userMsg, loadingMsg]);
        scrollToBottom();

        try {
            const res = await api.post('/support/chat', {
                message: text,
                driver_id: user?.id ?? '',
            });
            const reply = res.data?.reply ?? "I couldn't get a response. Please try again.";

            setMessages((prev) =>
                prev.map((m) =>
                    m.id === loadingMsg.id ? { ...m, text: reply, loading: false } : m,
                ),
            );
        } catch {
            setMessages((prev) =>
                prev.map((m) =>
                    m.id === loadingMsg.id
                        ? {
                              ...m,
                              text: "I'm having trouble connecting right now. Please call 1-800-SPINR or email support@spinr.ca.",
                              loading: false,
                          }
                        : m,
                ),
            );
        } finally {
            setSending(false);
            scrollToBottom();
        }
    }, [input, sending, user, scrollToBottom]);

    const renderMessage = useCallback(
        ({ item }: { item: Message }) => {
            const isUser = item.role === 'user';
            return (
                <View style={[styles.messageWrapper, isUser && styles.messageWrapperUser]}>
                    {!isUser && (
                        <View style={styles.botAvatar}>
                            <Ionicons name="sparkles" size={14} color="#fff" />
                        </View>
                    )}
                    <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleBot]}>
                        {item.loading ? (
                            <View style={styles.loadingDots}>
                                <ActivityIndicator size="small" color={colors.textDim} />
                            </View>
                        ) : (
                            <Text style={[styles.bubbleText, isUser && styles.bubbleTextUser]}>
                                {item.text}
                            </Text>
                        )}
                    </View>

                    {/* Contact buttons below every non-loading bot reply */}
                    {!isUser && !item.loading && item.id !== 'welcome' && (
                        <View style={styles.contactRow}>
                            <TouchableOpacity
                                style={styles.contactChip}
                                onPress={() => Linking.openURL(`tel:${SUPPORT_PHONE.replace(/-/g, '')}`)}
                            >
                                <Ionicons name="call-outline" size={13} color={colors.primary} />
                                <Text style={styles.contactChipText}>{SUPPORT_PHONE}</Text>
                            </TouchableOpacity>
                            <TouchableOpacity
                                style={styles.contactChip}
                                onPress={() => Linking.openURL(`mailto:${SUPPORT_EMAIL}`)}
                            >
                                <Ionicons name="mail-outline" size={13} color={colors.primary} />
                                <Text style={styles.contactChipText}>{SUPPORT_EMAIL}</Text>
                            </TouchableOpacity>
                        </View>
                    )}
                </View>
            );
        },
        [styles, colors],
    );

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
                    <View style={styles.headerCenter}>
                        <View style={styles.botDot} />
                        <Text style={styles.headerTitle}>Support Bot</Text>
                    </View>
                    <TouchableOpacity
                        onPress={() => router.push('/driver/faq' as any)}
                        style={styles.faqBtn}
                    >
                        <Ionicons name="help-circle-outline" size={22} color={colors.primary} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            <KeyboardAvoidingView
                style={{ flex: 1 }}
                behavior={Platform.OS === 'ios' ? 'padding' : undefined}
                keyboardVerticalOffset={0}
            >
                <FlatList
                    ref={listRef}
                    data={messages}
                    renderItem={renderMessage}
                    keyExtractor={(m) => m.id}
                    contentContainerStyle={[
                        styles.messageList,
                        { paddingBottom: insets.bottom + 90 },
                    ]}
                    showsVerticalScrollIndicator={false}
                    removeClippedSubviews={true}
                    onContentSizeChange={scrollToBottom}
                />

                {/* Input bar */}
                <View
                    style={[
                        styles.inputBar,
                        { paddingBottom: insets.bottom + 8 },
                    ]}
                >
                    <TextInput
                        style={styles.textInput}
                        placeholder="Ask a question..."
                        placeholderTextColor={colors.textDim}
                        value={input}
                        onChangeText={setInput}
                        onSubmitEditing={sendMessage}
                        returnKeyType="send"
                        multiline
                        maxLength={500}
                        editable={!sending}
                    />
                    <TouchableOpacity
                        style={[styles.sendBtn, (!input.trim() || sending) && styles.sendBtnDisabled]}
                        onPress={sendMessage}
                        disabled={!input.trim() || sending}
                    >
                        <Ionicons name="send" size={18} color="#fff" />
                    </TouchableOpacity>
                </View>
            </KeyboardAvoidingView>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },

        header: {
            paddingBottom: 12,
            paddingHorizontal: 16,
        },
        headerRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
        },
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerCenter: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
        },
        botDot: {
            width: 8,
            height: 8,
            borderRadius: 4,
            backgroundColor: colors.success,
        },
        headerTitle: {
            color: colors.text,
            fontSize: 18,
            fontWeight: '700',
        },
        faqBtn: {
            width: 40,
            height: 40,
            justifyContent: 'center',
            alignItems: 'center',
        },

        messageList: {
            paddingHorizontal: 16,
            paddingTop: 16,
        },

        messageWrapper: {
            marginBottom: 16,
            alignItems: 'flex-start',
            maxWidth: '85%',
        },
        messageWrapperUser: {
            alignSelf: 'flex-end',
            alignItems: 'flex-end',
        },
        botAvatar: {
            width: 28,
            height: 28,
            borderRadius: 14,
            backgroundColor: colors.primary,
            justifyContent: 'center',
            alignItems: 'center',
            marginBottom: 6,
        },
        bubble: {
            borderRadius: 18,
            paddingHorizontal: 14,
            paddingVertical: 10,
            maxWidth: '100%',
        },
        bubbleBot: {
            backgroundColor: colors.surface,
            borderWidth: 1,
            borderColor: colors.border,
            borderTopLeftRadius: 4,
        },
        bubbleUser: {
            backgroundColor: colors.primary,
            borderTopRightRadius: 4,
        },
        bubbleText: {
            color: colors.text,
            fontSize: 15,
            lineHeight: 22,
        },
        bubbleTextUser: {
            color: '#fff',
        },
        loadingDots: {
            paddingVertical: 4,
            paddingHorizontal: 4,
        },

        contactRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 8,
            marginTop: 8,
        },
        contactChip: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 5,
            backgroundColor: `${colors.primary}15`,
            paddingHorizontal: 10,
            paddingVertical: 6,
            borderRadius: 16,
            borderWidth: 1,
            borderColor: `${colors.primary}30`,
        },
        contactChipText: {
            color: colors.primary,
            fontSize: 12,
            fontWeight: '600',
        },

        inputBar: {
            flexDirection: 'row',
            alignItems: 'flex-end',
            paddingHorizontal: 16,
            paddingTop: 10,
            borderTopWidth: 1,
            borderTopColor: colors.border,
            backgroundColor: colors.background,
            gap: 10,
        },
        textInput: {
            flex: 1,
            backgroundColor: colors.surface,
            borderRadius: 22,
            paddingHorizontal: 16,
            paddingVertical: 10,
            fontSize: 15,
            color: colors.text,
            borderWidth: 1,
            borderColor: colors.border,
            maxHeight: 120,
        },
        sendBtn: {
            width: 42,
            height: 42,
            borderRadius: 21,
            backgroundColor: colors.primary,
            justifyContent: 'center',
            alignItems: 'center',
            marginBottom: 1,
        },
        sendBtnDisabled: {
            opacity: 0.4,
        },
    });
}
