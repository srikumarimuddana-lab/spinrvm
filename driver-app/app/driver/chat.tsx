import React, { useState, useRef, useEffect, useCallback } from 'react';
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
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useDriverStore } from '../../store/driverStore';
import { API_URL } from '@shared/config';

import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
    primary: THEME.background, // Background (white)
    accent: THEME.primary, // Action/brand color (red)
    accentDim: THEME.primaryDark,
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    myBubble: THEME.primary,
    theirBubble: THEME.surfaceLight,
    border: THEME.border,
};

interface ChatMessage {
    id: string;
    text: string;
    sender: 'driver' | 'rider';
    timestamp: string;
}

const QUICK_MESSAGES = [
    'On my way!',
    'I have arrived',
    'Running a few minutes late',
    'I\'m at the pickup location',
    'Please confirm your location',
    'Thank you!',
];

export default function ChatScreen() {
    const router = useRouter();
    const { user, driver } = useAuthStore();
    const { activeRide } = useDriverStore();
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [inputText, setInputText] = useState('');
    const [showQuickReplies, setShowQuickReplies] = useState(true);
    const flatListRef = useRef<FlatList>(null);
    const wsRef = useRef<WebSocket | null>(null);

    const riderName = activeRide?.rider?.first_name || activeRide?.rider?.name || 'Rider';
    const rideId = activeRide?.ride?.id;

    useEffect(() => {
        // Connect to chat WebSocket
        if (!rideId) return;

        const wsUrl = API_URL.replace('http', 'ws') + `/ws/chat/${rideId}`;
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({
                type: 'auth',
                token: useAuthStore.getState().token,
                role: 'driver',
            }));
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'chat_message') {
                    setMessages((prev) => [...prev, {
                        id: Date.now().toString(),
                        text: data.message,
                        sender: data.sender === 'driver' ? 'driver' : 'rider',
                        timestamp: new Date().toISOString(),
                    }]);
                }
            } catch { }
        };

        return () => {
            ws.close();
            wsRef.current = null;
        };
    }, [rideId]);

    const sendMessage = useCallback((text: string) => {
        if (!text.trim()) return;

        const newMsg: ChatMessage = {
            id: Date.now().toString(),
            text: text.trim(),
            sender: 'driver',
            timestamp: new Date().toISOString(),
        };

        setMessages((prev) => [...prev, newMsg]);
        setInputText('');
        setShowQuickReplies(false);

        // Send via WebSocket
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({
                type: 'chat_message',
                message: text.trim(),
                ride_id: rideId,
            }));
        }

        setTimeout(() => {
            flatListRef.current?.scrollToEnd({ animated: true });
        }, 100);
    }, [rideId]);

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
                        <Ionicons name="person" size={14} color={COLORS.textDim} />
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
            <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={COLORS.text} />
                    </TouchableOpacity>
                    <View style={styles.headerInfo}>
                        <Text style={styles.headerName}>{riderName}</Text>
                        <Text style={styles.headerSub}>
                            {rideId ? 'Active Ride' : 'No active ride'}
                        </Text>
                    </View>
                    <TouchableOpacity style={styles.callBtn}>
                        <Ionicons name="call" size={20} color={COLORS.accent} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            {/* Messages */}
            <FlatList
                ref={flatListRef}
                data={messages}
                renderItem={renderMessage}
                keyExtractor={(item) => item.id}
                contentContainerStyle={styles.messageList}
                showsVerticalScrollIndicator={false}
                ListEmptyComponent={
                    <View style={styles.emptyChat}>
                        <Ionicons name="chatbubbles-outline" size={48} color={COLORS.surfaceLight} />
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
            <View style={styles.inputContainer}>
                <TouchableOpacity
                    style={styles.quickToggle}
                    onPress={() => setShowQuickReplies(!showQuickReplies)}
                >
                    <Ionicons
                        name={showQuickReplies ? 'chevron-down' : 'chevron-up'}
                        size={20}
                        color={COLORS.textDim}
                    />
                </TouchableOpacity>
                <TextInput
                    style={styles.input}
                    placeholder="Type a message..."
                    placeholderTextColor={COLORS.textDim}
                    value={inputText}
                    onChangeText={setInputText}
                    onFocus={() => setShowQuickReplies(false)}
                />
                <TouchableOpacity
                    style={[styles.sendBtn, inputText.trim() && styles.sendBtnActive]}
                    onPress={() => sendMessage(inputText)}
                    disabled={!inputText.trim()}
                >
                    <Ionicons name="send" size={18} color={inputText.trim() ? '#fff' : COLORS.textDim} />
                </TouchableOpacity>
            </View>
        </KeyboardAvoidingView>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        paddingTop: Platform.OS === 'ios' ? 55 : 35,
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
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    headerInfo: { flex: 1 },
    headerName: { color: COLORS.text, fontSize: 17, fontWeight: '700' },
    headerSub: { color: COLORS.textDim, fontSize: 12, marginTop: 1 },
    callBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: 'rgba(0,212,170,0.1)',
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
        backgroundColor: COLORS.surfaceLight,
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
        backgroundColor: COLORS.myBubble,
        borderBottomRightRadius: 4,
    },
    theirBubble: {
        backgroundColor: COLORS.theirBubble,
        borderBottomLeftRadius: 4,
    },
    bubbleText: { color: COLORS.text, fontSize: 14, lineHeight: 20 },
    myBubbleText: { color: COLORS.primary },
    bubbleTime: { color: COLORS.textDim, fontSize: 10, marginTop: 4, textAlign: 'right' },
    myBubbleTime: { color: 'rgba(10,14,33,0.5)' },
    emptyChat: {
        alignItems: 'center',
        justifyContent: 'center',
        paddingVertical: 80,
        gap: 8,
    },
    emptyChatText: { color: COLORS.textDim, fontSize: 16, fontWeight: '600' },
    emptyChatSub: { color: COLORS.surfaceLight, fontSize: 13 },
    quickReplies: {
        paddingVertical: 10,
        borderTopWidth: 1,
        borderTopColor: 'rgba(255,255,255,0.04)',
    },
    quickReplyBtn: {
        paddingHorizontal: 14,
        paddingVertical: 8,
        backgroundColor: COLORS.surface,
        borderRadius: 20,
        borderWidth: 1,
        borderColor: 'rgba(255,255,255,0.06)',
    },
    quickReplyText: { color: COLORS.text, fontSize: 13, fontWeight: '500' },
    inputContainer: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 12,
        paddingVertical: 10,
        paddingBottom: Platform.OS === 'ios' ? 30 : 14,
        backgroundColor: COLORS.surface,
        borderTopWidth: 1,
        borderTopColor: 'rgba(255,255,255,0.06)',
        gap: 8,
    },
    quickToggle: { padding: 6 },
    input: {
        flex: 1,
        backgroundColor: COLORS.surfaceLight,
        borderRadius: 24,
        paddingHorizontal: 16,
        paddingVertical: 10,
        color: COLORS.text,
        fontSize: 14,
    },
    sendBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    sendBtnActive: {
        backgroundColor: COLORS.accent,
    },
});
