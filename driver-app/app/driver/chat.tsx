import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    FlatList,
    TextInput,
    Platform,
    KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import type { ChatMessage } from '../../store/driverStore';
import api from '@shared/api/client';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const QUICK_MESSAGES = [
    'On my way!',
    'I have arrived',
    'Running a few minutes late',
    "I'm at the pickup location",
    'Please confirm your location',
    'Thank you!',
];

export default function ChatScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const { activeRide, chatMessages, addChatMessage, setChatMessages } = useDriverStore();
    const [inputText, setInputText] = useState('');
    const [showQuickReplies, setShowQuickReplies] = useState(true);
    const [sending, setSending] = useState(false);
    const flatListRef = useRef<FlatList>(null);

    const riderName = activeRide?.rider?.first_name || activeRide?.rider?.name || 'Rider';
    const rideId = activeRide?.ride?.id;
    const CHAT_STORAGE_KEY = rideId ? `spinr_chat_${rideId}` : null;

    // Load chat history: AsyncStorage first (instant), then backend (authoritative).
    // Real-time incoming messages are pushed via WS → useDriverDashboard → driverStore.
    useEffect(() => {
        if (!rideId) return;

        (async () => {
            // 1. Seed from local cache for instant render
            try {
                if (CHAT_STORAGE_KEY) {
                    const saved = await AsyncStorage.getItem(CHAT_STORAGE_KEY);
                    if (saved) setChatMessages(JSON.parse(saved));
                }
            } catch {}

            // 2. Fetch authoritative history from backend
            try {
                const res = await api.get(`/rides/${rideId}/messages`);
                if (res.data?.messages?.length) {
                    setChatMessages(res.data.messages);
                    if (CHAT_STORAGE_KEY) {
                        await AsyncStorage.setItem(
                            CHAT_STORAGE_KEY,
                            JSON.stringify(res.data.messages),
                        );
                    }
                }
            } catch (e) {
                console.log('[Chat] Failed to load history:', e);
            }
        })();
    }, [rideId]);

    // Persist to AsyncStorage whenever the store updates (keeps cache fresh
    // for the next cold-start without an extra fetch).
    useEffect(() => {
        if (!CHAT_STORAGE_KEY || chatMessages.length === 0) return;
        AsyncStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chatMessages)).catch(() => {});
    }, [chatMessages, CHAT_STORAGE_KEY]);

    // Scroll to bottom on new messages
    useEffect(() => {
        if (chatMessages.length > 0) {
            setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 80);
        }
    }, [chatMessages.length]);

    const sendMessage = useCallback(async (text: string) => {
        if (!text.trim() || !rideId || sending) return;
        setSending(true);
        const trimmed = text.trim();
        setInputText('');
        setShowQuickReplies(false);

        try {
            const res = await api.post(`/rides/${rideId}/messages`, { text: trimmed });
            if (res.data?.message) {
                // Backend's REST handler also broadcasts via WS, so addChatMessage
                // deduplication ensures no double-render even if we add optimistically.
                addChatMessage(res.data.message);
            }
        } catch (e) {
            console.log('[Chat] Send failed:', e);
        } finally {
            setSending(false);
        }
    }, [rideId, sending, addChatMessage]);

    const formatTime = (dateStr: string) => {
        return new Date(dateStr).toLocaleTimeString('en', {
            hour: '2-digit',
            minute: '2-digit',
        });
    };

    const renderMessage = ({ item }: { item: ChatMessage }) => {
        const isMe = item.sender === 'driver';
        return (
            <View style={[styles.messageBubbleRow, isMe && styles.myMessageRow]}>
                {!isMe && (
                    <View style={styles.avatarSmall}>
                        <Ionicons name="person" size={14} color={colors.textDim} />
                    </View>
                )}
                <View style={[styles.bubble, isMe ? styles.myBubble : styles.theirBubble]}>
                    <Text style={[styles.bubbleText, isMe && styles.myBubbleText]}>{item.text}</Text>
                    <Text style={[styles.bubbleTime, isMe && styles.myBubbleTime]}>
                        {formatTime(item.timestamp)}
                    </Text>
                </View>
            </View>
        );
    };

    return (
        <KeyboardAvoidingView
            style={styles.container}
            behavior={Platform.OS === 'ios' ? 'padding' : undefined}
            keyboardVerticalOffset={0}
        >
            {/* Header */}
            <LinearGradient
                colors={[colors.surface, colors.background]}
                style={[styles.header, { paddingTop: insets.top + 12 }]}
            >
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <View style={styles.headerInfo}>
                        <Text style={styles.headerName}>{riderName}</Text>
                        <Text style={styles.headerSub}>
                            {rideId ? 'Active Ride' : 'No active ride'}
                        </Text>
                    </View>
                    <TouchableOpacity
                        style={styles.callBtn}
                        onPress={async () => {
                            if (!rideId) return;
                            try {
                                const res = await api.get(`/rides/${rideId}/call`);
                                if (res.data?.phone) {
                                    const { Linking } = require('react-native');
                                    Linking.openURL(`tel:${res.data.phone}`);
                                }
                            } catch (e: any) {
                                console.log('[Chat] Call failed:', e?.response?.data?.detail || e.message);
                            }
                        }}
                    >
                        <Ionicons name="call" size={20} color={colors.primary} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            {/* Messages */}
            <FlatList
                ref={flatListRef}
                data={chatMessages}
                renderItem={renderMessage}
                keyExtractor={(item) => item.id}
                contentContainerStyle={styles.messageList}
                showsVerticalScrollIndicator={false}
                ListEmptyComponent={
                    <View style={styles.emptyChat}>
                        <Ionicons name="chatbubbles-outline" size={48} color={colors.surfaceLight} />
                        <Text style={styles.emptyChatText}>No messages yet</Text>
                        <Text style={styles.emptyChatSub}>Send a quick message to your rider</Text>
                    </View>
                }
            />

            {/* Quick Replies */}
            {showQuickReplies && (
                <View style={styles.quickReplies}>
                    <FlatList
                        horizontal
                        data={QUICK_MESSAGES}
                        renderItem={({ item }) => (
                            <TouchableOpacity
                                style={styles.quickReplyBtn}
                                onPress={() => sendMessage(item)}
                            >
                                <Text style={styles.quickReplyText}>{item}</Text>
                            </TouchableOpacity>
                        )}
                        keyExtractor={(item) => item}
                        showsHorizontalScrollIndicator={false}
                        contentContainerStyle={{ paddingHorizontal: 12, gap: 8 }}
                    />
                </View>
            )}

            {/* Input */}
            <View style={[styles.inputContainer, { paddingBottom: Math.max(insets.bottom, 14) }]}>
                <TouchableOpacity
                    style={styles.quickToggle}
                    onPress={() => setShowQuickReplies(!showQuickReplies)}
                >
                    <Ionicons
                        name={showQuickReplies ? 'chevron-down' : 'chevron-up'}
                        size={20}
                        color={colors.textDim}
                    />
                </TouchableOpacity>
                <TextInput
                    style={styles.input}
                    placeholder="Type a message..."
                    placeholderTextColor={colors.textDim}
                    value={inputText}
                    onChangeText={setInputText}
                    onFocus={() => setShowQuickReplies(false)}
                />
                <TouchableOpacity
                    style={[styles.sendBtn, inputText.trim() && styles.sendBtnActive]}
                    onPress={() => sendMessage(inputText)}
                    disabled={!inputText.trim()}
                >
                    <Ionicons
                        name="send"
                        size={18}
                        color={inputText.trim() ? '#fff' : colors.textDim}
                    />
                </TouchableOpacity>
            </View>
        </KeyboardAvoidingView>
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
            gap: 12,
        },
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerInfo: { flex: 1 },
        headerName: { color: colors.text, fontSize: 17, fontWeight: '700' },
        headerSub: { color: colors.textDim, fontSize: 12, marginTop: 1 },
        callBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: `${colors.primary}1A`,
            justifyContent: 'center',
            alignItems: 'center',
        },
        messageList: {
            paddingHorizontal: 16,
            paddingTop: 16,
            paddingBottom: 8,
            flexGrow: 1,
        },
        messageBubbleRow: {
            flexDirection: 'row',
            alignItems: 'flex-end',
            marginBottom: 10,
            gap: 8,
        },
        myMessageRow: {
            justifyContent: 'flex-end',
        },
        avatarSmall: {
            width: 28,
            height: 28,
            borderRadius: 14,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        bubble: {
            maxWidth: '75%',
            paddingHorizontal: 14,
            paddingVertical: 10,
            borderRadius: 18,
        },
        myBubble: {
            backgroundColor: colors.primary,
            borderBottomRightRadius: 4,
        },
        theirBubble: {
            backgroundColor: colors.surfaceLight,
            borderBottomLeftRadius: 4,
        },
        bubbleText: { color: colors.text, fontSize: 14, lineHeight: 20 },
        myBubbleText: { color: '#fff' },
        bubbleTime: { color: colors.textDim, fontSize: 10, marginTop: 4, textAlign: 'right' },
        myBubbleTime: { color: 'rgba(255,255,255,0.6)' },
        emptyChat: {
            alignItems: 'center',
            justifyContent: 'center',
            paddingVertical: 80,
            gap: 8,
        },
        emptyChatText: { color: colors.textDim, fontSize: 16, fontWeight: '600' },
        emptyChatSub: { color: colors.textSecondary, fontSize: 13 },
        quickReplies: {
            paddingVertical: 10,
            borderTopWidth: 1,
            borderTopColor: colors.border,
        },
        quickReplyBtn: {
            paddingHorizontal: 14,
            paddingVertical: 8,
            backgroundColor: colors.surface,
            borderRadius: 20,
            borderWidth: 1,
            borderColor: colors.border,
        },
        quickReplyText: { color: colors.text, fontSize: 13, fontWeight: '500' },
        inputContainer: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 12,
            paddingVertical: 10,
            backgroundColor: colors.surface,
            borderTopWidth: 1,
            borderTopColor: colors.border,
            gap: 8,
        },
        quickToggle: { padding: 6 },
        input: {
            flex: 1,
            backgroundColor: colors.surfaceLight,
            borderRadius: 24,
            paddingHorizontal: 16,
            paddingVertical: 10,
            color: colors.text,
            fontSize: 14,
        },
        sendBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        sendBtnActive: {
            backgroundColor: colors.primary,
        },
    });
}
