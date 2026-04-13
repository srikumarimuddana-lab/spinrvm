import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

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
  const [sending, setSending] = useState(false);

  // Load chat history from the backend on mount.
  useEffect(() => {
    if (!rideId) return;
    (async () => {
      try {
        const api = (await import('@shared/api/client')).default;
        const res = await api.get(`/rides/${rideId}/messages`);
        if (res.data?.messages) {
          setChatMessages(res.data.messages);
        }
      } catch (e) {
        console.log('[Chat] Failed to load history:', e);
      }
    })();
  }, [rideId]);

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

  const handleCall = async () => {
    if (!rideId) return;
    try {
      const api = (await import('@shared/api/client')).default;
      const res = await api.get(`/rides/${rideId}/call`);
      if (res.data?.phone) {
        const { Linking } = require('react-native');
        Linking.openURL(`tel:${res.data.phone}`);
      }
    } catch (e: any) {
      console.log('[Chat] Call failed:', e?.response?.data?.detail || e.message);
    }
  };

  const sendMessage = async (text: string) => {
    if (!text.trim() || !rideId || sending) return;
    setSending(true);
    try {
      const api = (await import('@shared/api/client')).default;
      const res = await api.post(`/rides/${rideId}/messages`, { text: text.trim() });
      if (res.data?.message) {
        // Optimistically add to local state (deduplicated by the store).
        addChatMessage(res.data.message);
      }
    } catch (e) {
      console.log('[Chat] Send failed:', e);
    } finally {
      setSending(false);
      setMessage('');
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
          <Ionicons name="chevron-back" size={28} color="#1A1A1A" />
        </TouchableOpacity>

        <View style={styles.driverHeader}>
          <View style={styles.driverAvatar}>
            <Ionicons name="person" size={22} color="#666" />
            <View style={styles.onlineDot} />
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{driverName}</Text>
            <Text style={styles.vehicleInfo}>
              {currentDriver?.vehicle_color || ''} {currentDriver?.vehicle_make || 'Unknown'} {currentDriver?.vehicle_model || 'Vehicle'} • {currentDriver?.rating || 'New'} <Ionicons name="star" size={12} color="#FFB800" />
            </Text>
          </View>
        </View>

        <TouchableOpacity style={styles.callButton} onPress={handleCall}>
          <Ionicons name="call" size={22} color={SpinrConfig.theme.colors.primary} />
        </TouchableOpacity>

        <View style={styles.toggleContainer}>
          <View style={styles.toggleDot} />
        </View>
      </View>

      {/* Messages */}
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
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
                  <Ionicons name="person" size={16} color="#666" />
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
            <Ionicons name="camera-outline" size={24} color="#666" />
            <View style={styles.cameraBadge}>
              <Ionicons name="add" size={10} color="#FFF" />
            </View>
          </TouchableOpacity>

          <View style={styles.textInputContainer}>
            <TextInput
              style={styles.textInput}
              placeholder={`Message ${driverFirstName}...`}
              placeholderTextColor="#999"
              value={message}
              onChangeText={setMessage}
              multiline
            />
          </View>

          <TouchableOpacity
            style={[styles.sendButton, message.trim() && styles.sendButtonActive]}
            onPress={() => sendMessage(message)}
            disabled={!message.trim()}
          >
            <Ionicons name="send" size={20} color="#FFF" />
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F5F5F5',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#F0F0F0',
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
    color: '#1A1A1A',
  },
  vehicleInfo: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
  },
  callButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF0F0',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 8,
  },
  toggleContainer: {
    width: 36,
    height: 22,
    backgroundColor: '#E0E0E0',
    borderRadius: 11,
    justifyContent: 'center',
    alignItems: 'flex-end',
    paddingHorizontal: 3,
  },
  toggleDot: {
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: '#999',
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
    color: '#666',
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
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderBottomRightRadius: 6,
  },
  driverBubble: {
    backgroundColor: '#FFF',
    borderBottomLeftRadius: 6,
  },
  messageText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#1A1A1A',
    lineHeight: 22,
  },
  userMessageText: {
    color: '#FFF',
  },
  messageTime: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
    marginTop: 4,
    marginLeft: 4,
  },
  messageTimeUser: {
    textAlign: 'right',
    marginRight: 4,
  },
  readIndicator: {
    color: SpinrConfig.theme.colors.primary,
  },
  deliveredIndicator: {
    color: '#999',
  },
  quickReplies: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingVertical: 12,
    gap: 8,
  },
  quickReplyButton: {
    backgroundColor: '#FFF',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#E0E0E0',
  },
  quickReplyText: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    backgroundColor: '#FFF',
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
    backgroundColor: '#1A1A1A',
    justifyContent: 'center',
    alignItems: 'center',
  },
  textInputContainer: {
    flex: 1,
    backgroundColor: '#F5F5F5',
    borderRadius: 24,
    paddingHorizontal: 16,
    paddingVertical: 10,
    maxHeight: 120,
  },
  textInput: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#1A1A1A',
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
    backgroundColor: SpinrConfig.theme.colors.primary,
  },
});
