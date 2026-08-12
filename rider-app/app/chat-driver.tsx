import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  ScrollView,
  KeyboardAvoidingView,
  Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useRideStore } from '../store/rideStore';
import type { ChatMessage } from '../store/rideStore';
import api from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface Message {
  id: string;
  text: string;
  isUser: boolean;
  time: string;
  status?: 'sent' | 'delivered' | 'read';
}

export default function ChatDriverScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentDriver, chatMessages, addChatMessage, setChatMessages } = useRideStore();
  const scrollViewRef = useRef<ScrollView>(null);
  const [message, setMessage] = useState('');
  const [driverPhotoError, setDriverPhotoError] = useState(false);
  const [sending, setSending] = useState(false);
  // Tracks when the initial AsyncStorage read has completed so the persist
  // effect knows it is safe to write an empty array without clobbering a
  // warm cache that hasn't been loaded into the store yet.
  const cacheReadDoneRef = useRef(false);

  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const CHAT_STORAGE_KEY = rideId ? `spinr_chat_rider_${rideId}` : null;

  // Load chat history: AsyncStorage first (instant), then backend (authoritative).
  useEffect(() => {
    if (!rideId) { router.replace('/(tabs)' as any); return; }
    cacheReadDoneRef.current = false;
    (async () => {
      // 1. Seed from local cache for instant render
      try {
        if (CHAT_STORAGE_KEY) {
          const saved = await AsyncStorage.getItem(CHAT_STORAGE_KEY);
          if (saved) setChatMessages(JSON.parse(saved) as ChatMessage[]);
        }
      } catch (e) {
        console.log('[Chat] Cache read failed:', e);
      }
      cacheReadDoneRef.current = true;
      // 2. Fetch authoritative history from backend (always authoritative —
      //    replace cache even when server returns empty array, so stale data
      //    from a previous ride is not shown to the user).
      try {
        const res = await api.get<{ messages: ChatMessage[] }>(`/rides/${rideId}/messages`);
        if (res.data?.messages !== undefined) {
          setChatMessages(res.data.messages);
          if (CHAT_STORAGE_KEY) {
            AsyncStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(res.data.messages)).catch(() => {});
          }
        }
      } catch (e) {
        console.log('[Chat] Failed to load history:', e);
      }
    })();
  }, [rideId]);

  // Persist to AsyncStorage whenever the store updates. Filter to only this
  // ride's messages to prevent cross-ride contamination when rideId changes.
  // Guard: skip writing an empty array before the initial cache read completes
  // to avoid clobbering a warm cache. After that, always persist — including
  // empty arrays — so failed-send rollbacks flush stale phantom messages from cache.
  useEffect(() => {
    if (!CHAT_STORAGE_KEY || !rideId) return;
    const rideMessages = chatMessages.filter((m) => m.ride_id === rideId);
    if (!cacheReadDoneRef.current && rideMessages.length === 0) return;
    AsyncStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(rideMessages)).catch(() => {});
  }, [chatMessages, CHAT_STORAGE_KEY, rideId]);

  // Scroll to bottom when new messages arrive (via WS or local send).
  useEffect(() => {
    setTimeout(() => scrollViewRef.current?.scrollToEnd({ animated: true }), 100);
  }, [chatMessages.length]);

  // Map backend message shape to the UI's Message interface.
  const messages: Message[] = chatMessages.map((m: any) => ({
    id: m.id,
    text: m.text,
    isUser: m.sender === 'rider',
    time: m.timestamp
      ? new Date(m.timestamp).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : '',
    status: m.sender === 'rider' ? 'sent' : undefined,
  }));

  const quickReplies = [
    { id: '1', text: "\ud83d\udc4b I'm here", icon: null },
    { id: '2', text: 'Where are you?', icon: null },
    { id: '3', text: 'On my way', icon: null },
  ];

  const handleBack = () => {
    router.back();
  };

  const sendMessage = async (text: string) => {
    if (!text.trim() || !rideId || sending) return;
    const trimmed = text.trim();
    setSending(true);
    setMessage('');

    // Not a render-time call: sendMessage only runs from the send-button's
    // onPress, never during render, so Date.now()/Math.random() here can't
    // produce the re-render inconsistency react-hooks/purity guards against.
    // eslint-disable-next-line react-hooks/purity
    const optimisticId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    addChatMessage({
      id: optimisticId,
      ride_id: rideId,
      text: trimmed,
      sender: 'rider',
      timestamp: new Date().toISOString(),
    });

    try {
      const res = await api.post<{ message?: unknown }>(`/rides/${rideId}/messages`, { text: trimmed });
      if (res.data?.message) {
        const serverMsg = res.data.message as ChatMessage;
        const current = useRideStore.getState().chatMessages;
        setChatMessages(
          current
            .filter(m => m.id !== optimisticId)
            .concat(serverMsg),
        );
      }
    } catch (e: any) {
      const current = useRideStore.getState().chatMessages;
      setChatMessages(current.filter(m => m.id !== optimisticId));
      setMessage(trimmed);
      const detail = e?.response?.data?.detail || e?.message || 'Could not send message. Check your connection.';
      showToast('Send Failed', detail, 'danger');
    } finally {
      setSending(false);
    }
  };

  const handleQuickReply = (text: string) => {
    sendMessage(text);
  };

  const driverName = currentDriver?.name || 'Driver';
  const driverFirstName = driverName.split(' ')[0];

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={handleBack}>
          <Ionicons name="chevron-back" size={28} color={colors.text} />
        </TouchableOpacity>

        <View style={styles.driverHeader}>
          <View style={styles.driverAvatar}>
            {currentDriver?.photo_url && !driverPhotoError ? (
              <Image
                source={{ uri: currentDriver.photo_url }}
                style={styles.driverAvatarImg}
                resizeMode="cover"
                onError={() => setDriverPhotoError(true)}
              />
            ) : (
              <Ionicons name="person" size={22} color={colors.textDim} />
            )}
            <View style={styles.onlineDot} />
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{driverName}</Text>
            <Text style={styles.vehicleInfo}>
              {currentDriver?.vehicle_color || ''} {currentDriver?.vehicle_make || 'Unknown'} {currentDriver?.vehicle_model || 'Vehicle'} • {currentDriver?.rating || 'New'} <Ionicons name="star" size={12} color="#FFB800" />
            </Text>
          </View>
        </View>

        {/* No call button: rider↔driver contact is chat-only — phone numbers
            are never shared between parties (backend /call endpoint removed). */}
        <View style={styles.toggleContainer}>
          <View style={styles.toggleDot} />
        </View>
      </View>

      {/* Messages */}
      <KeyboardAvoidingView
        behavior="padding"
        style={styles.messagesContainer}
        keyboardVerticalOffset={0}
      >
        <ScrollView
          ref={scrollViewRef}
          style={styles.messagesList}
          contentContainerStyle={styles.messagesContent}
          showsVerticalScrollIndicator={false}
        >
          {/* System Message */}
          <View style={styles.systemMessage}>
            <Text style={styles.systemMessageText}>You are now connected with {driverFirstName}</Text>
          </View>

          {messages.map((msg) => (
            <View key={msg.id} style={[styles.messageRow, msg.isUser && styles.messageRowUser]}>
              {!msg.isUser && (
                <View style={styles.messageSenderAvatar}>
                  <Ionicons name="person" size={16} color={colors.textDim} />
                </View>
              )}
              <View>
                <View style={[styles.messageBubble, msg.isUser ? styles.userBubble : styles.driverBubble]}>
                  <Text style={[styles.messageText, msg.isUser && styles.userMessageText]}>{msg.text}</Text>
                </View>
                <Text style={[styles.messageTime, msg.isUser && styles.messageTimeUser]}>
                  {msg.time}
                  {msg.isUser && msg.status === 'read' && (
                    <Text style={styles.readIndicator}> \u2713\u2713</Text>
                  )}
                  {msg.isUser && msg.status === 'delivered' && (
                    <Text style={styles.deliveredIndicator}> \u2713</Text>
                  )}
                </Text>
              </View>
            </View>
          ))}
        </ScrollView>

        {/* Quick Replies */}
        <View style={styles.quickReplies}>
          {quickReplies.map((reply) => (
            <TouchableOpacity
              key={reply.id}
              style={styles.quickReplyButton}
              onPress={() => handleQuickReply(reply.text)}
            >
              <Text style={styles.quickReplyText}>{reply.text}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Input Area */}
        <View style={styles.inputContainer}>
          <TouchableOpacity style={styles.cameraButton}>
            <Ionicons name="camera-outline" size={24} color={colors.textDim} />
            <View style={styles.cameraBadge}>
              <Ionicons name="add" size={10} color="#FFF" />
            </View>
          </TouchableOpacity>

          <View style={styles.textInputContainer}>
            <TextInput
              style={styles.textInput}
              placeholder={`Message ${driverFirstName}...`}
              placeholderTextColor={colors.textDim}
              value={message}
              onChangeText={setMessage}
              multiline
            />
          </View>

          <TouchableOpacity
            style={[styles.sendButton, message.trim() && !sending && styles.sendButtonActive]}
            onPress={() => sendMessage(message)}
            disabled={!message.trim() || sending}
          >
            <Ionicons name="send" size={20} color="#FFF" />
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>

    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surfaceLight,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      paddingHorizontal: 12,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: {
      padding: 4,
    },
    driverHeader: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      marginLeft: 8,
    },
    driverAvatar: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: '#E8E8E8',
      justifyContent: 'center',
      alignItems: 'center',
      position: 'relative',
    },
    driverAvatarImg: {
      width: 44,
      height: 44,
      borderRadius: 22,
    },
    onlineDot: {
      position: 'absolute',
      bottom: 2,
      right: 2,
      width: 10,
      height: 10,
      borderRadius: 5,
      backgroundColor: '#10B981',
      borderWidth: 2,
      borderColor: '#FFF',
    },
    driverInfo: {
      marginLeft: 10,
    },
    driverName: {
      fontSize: 17,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    vehicleInfo: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    toggleContainer: {
      width: 36,
      height: 22,
      backgroundColor: colors.border,
      borderRadius: 11,
      justifyContent: 'center',
      alignItems: 'flex-end',
      paddingHorizontal: 3,
    },
    toggleDot: {
      width: 18,
      height: 18,
      borderRadius: 9,
      backgroundColor: colors.textDim,
    },
    messagesContainer: {
      flex: 1,
    },
    messagesList: {
      flex: 1,
    },
    messagesContent: {
      padding: 16,
      paddingBottom: 8,
    },
    systemMessage: {
      alignSelf: 'center',
      backgroundColor: '#E8E8E8',
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 16,
      marginBottom: 20,
    },
    systemMessageText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    messageRow: {
      flexDirection: 'row',
      marginBottom: 12,
      alignItems: 'flex-end',
    },
    messageRowUser: {
      justifyContent: 'flex-end',
    },
    messageSenderAvatar: {
      width: 32,
      height: 32,
      borderRadius: 16,
      backgroundColor: '#D4E4B4',
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 8,
    },
    messageBubble: {
      maxWidth: 280,
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderRadius: 20,
    },
    userBubble: {
      backgroundColor: colors.primary,
      borderBottomRightRadius: 6,
    },
    driverBubble: {
      backgroundColor: colors.surfaceLight,
      borderBottomLeftRadius: 6,
    },
    messageText: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.text,
      lineHeight: 22,
    },
    userMessageText: {
      color: '#FFF',
    },
    messageTime: {
      fontSize: 11,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 4,
      marginLeft: 4,
    },
    messageTimeUser: {
      textAlign: 'right',
      marginRight: 4,
    },
    readIndicator: {
      color: colors.primary,
    },
    deliveredIndicator: {
      color: colors.textDim,
    },
    quickReplies: {
      flexDirection: 'row',
      paddingHorizontal: 16,
      paddingVertical: 12,
      gap: 8,
    },
    quickReplyButton: {
      backgroundColor: colors.surface,
      paddingHorizontal: 16,
      paddingVertical: 10,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: colors.border,
    },
    quickReplyText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    inputContainer: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      backgroundColor: colors.surface,
      paddingHorizontal: 12,
      paddingVertical: 10,
      gap: 10,
    },
    cameraButton: {
      width: 44,
      height: 44,
      justifyContent: 'center',
      alignItems: 'center',
      position: 'relative',
    },
    cameraBadge: {
      position: 'absolute',
      top: 4,
      right: 4,
      width: 14,
      height: 14,
      borderRadius: 7,
      backgroundColor: colors.text,
      justifyContent: 'center',
      alignItems: 'center',
    },
    textInputContainer: {
      flex: 1,
      backgroundColor: colors.surfaceLight,
      borderRadius: 24,
      paddingHorizontal: 16,
      paddingVertical: 10,
      maxHeight: 120,
    },
    textInput: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.text,
      maxHeight: 100,
    },
    sendButton: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: '#CCC',
      justifyContent: 'center',
      alignItems: 'center',
    },
    sendButtonActive: {
      backgroundColor: colors.primary,
    },
  });
}
