/**
 * AI assistant — dedicated chat screen.
 *
 * Streaming bubbles from aiChatStore, quick-prompt chips, a transient tool
 * status line, action bubbles (support deep-link; the booking card lands in
 * M5.4), and a persistent disclaimer footer. All AI inference is
 * server-side (/ai/chat) — this screen only renders frames.
 */
import React, { useCallback, useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { AiChatMessage } from '@shared/types/ai';
import { useAiChatStore } from '../store/aiChatStore';

const QUICK_PROMPTS = [
  "Where's my driver?",
  'Explain my last fare',
  "What's my wallet balance?",
  'Do I have any promos?',
  'Book me a ride home',
];

export default function AiAssistantScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = createStyles(colors);
  const listRef = useRef<FlatList>(null);

  const messages = useAiChatStore((s) => s.messages);
  const isStreaming = useAiChatStore((s) => s.isStreaming);
  const toolStatus = useAiChatStore((s) => s.toolStatus);
  const disclaimer = useAiChatStore((s) => s.disclaimer);
  const sendMessage = useAiChatStore((s) => s.sendMessage);
  const stopStreaming = useAiChatStore((s) => s.stopStreaming);
  const startNewConversation = useAiChatStore((s) => s.startNewConversation);
  const loadHistory = useAiChatStore((s) => s.loadHistory);
  const loadConfig = useAiChatStore((s) => s.loadConfig);

  const [input, setInput] = React.useState('');

  useEffect(() => {
    loadConfig();
    loadHistory();
  }, [loadConfig, loadHistory]);

  useEffect(() => {
    const id = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 80);
    return () => clearTimeout(id);
  }, [messages, toolStatus]);

  const handleSend = useCallback(
    (text?: string) => {
      const value = (text ?? input).trim();
      if (!value) return;
      setInput('');
      sendMessage(value);
    },
    [input, sendMessage],
  );

  const renderMessage = ({ item }: { item: AiChatMessage }) => {
    if (item.kind === 'support_action' && item.action?.type === 'open_support') {
      const link = item.action.link === '/lost-and-found' ? '/lost-and-found' : '/support';
      return (
        <TouchableOpacity
          style={styles.actionCard}
          onPress={() => router.push(link as never)}
          accessibilityRole="button"
          accessibilityLabel="Contact support"
        >
          <Ionicons name="headset-outline" size={18} color={colors.primary} />
          <Text style={styles.actionCardText}>
            {item.action.link === '/lost-and-found' ? 'Open Lost & Found' : 'Contact support'}
          </Text>
          <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
        </TouchableOpacity>
      );
    }
    if (item.kind === 'booking_proposal') {
      // Booking confirmation card lands with BookingProposalCard (M5.4);
      // until then, point the rider at the standard booking flow.
      return (
        <TouchableOpacity style={styles.actionCard} onPress={() => router.push('/' as never)}>
          <Ionicons name="car-outline" size={18} color={colors.primary} />
          <Text style={styles.actionCardText}>Review this trip in the booking screen</Text>
          <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
        </TouchableOpacity>
      );
    }

    const isUser = item.role === 'user';
    return (
      <View style={[styles.bubbleRow, isUser && styles.bubbleRowUser]}>
        {!isUser && (
          <View style={styles.aiAvatar}>
            <Ionicons name="sparkles" size={13} color={colors.primary} />
          </View>
        )}
        <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAssistant]}>
          <Text style={[styles.bubbleText, isUser && styles.bubbleTextUser]}>{item.content}</Text>
        </View>
      </View>
    );
  };

  const showWelcome = messages.length === 0;

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.headerButton} accessibilityLabel="Back">
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <View style={styles.headerTitleWrap}>
          <Ionicons name="sparkles" size={16} color={colors.primary} />
          <Text style={styles.headerTitle}>Spinr Assistant</Text>
        </View>
        <TouchableOpacity
          onPress={startNewConversation}
          style={styles.headerButton}
          accessibilityLabel="New conversation"
        >
          <Ionicons name="create-outline" size={22} color={colors.text} />
        </TouchableOpacity>
      </View>

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 8 : 0}
      >
        {showWelcome ? (
          <View style={styles.welcomeWrap}>
            <View style={styles.welcomeIcon}>
              <Ionicons name="sparkles" size={28} color={colors.primary} />
            </View>
            <Text style={styles.welcomeTitle}>Hi! I&apos;m your Spinr assistant.</Text>
            <Text style={styles.welcomeSubtitle}>
              Ask about your rides, fares, wallet or promos — or ask me to get you a ride quote.
            </Text>
            <View style={styles.chipsWrap}>
              {QUICK_PROMPTS.map((prompt) => (
                <TouchableOpacity key={prompt} style={styles.chip} onPress={() => handleSend(prompt)}>
                  <Text style={styles.chipText}>{prompt}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(m) => m.id}
            renderItem={renderMessage}
            contentContainerStyle={styles.listContent}
            onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
          />
        )}

        {toolStatus ? (
          <View style={styles.toolStatusRow}>
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={styles.toolStatusText}>{toolStatus}</Text>
          </View>
        ) : null}

        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="Ask me anything…"
            placeholderTextColor={colors.textDim}
            multiline
            maxLength={1000}
            editable={!isStreaming}
            onSubmitEditing={() => handleSend()}
          />
          {isStreaming ? (
            <TouchableOpacity style={styles.sendButton} onPress={stopStreaming} accessibilityLabel="Stop">
              <Ionicons name="stop" size={18} color="#fff" />
            </TouchableOpacity>
          ) : (
            <TouchableOpacity
              style={[styles.sendButton, !input.trim() && styles.sendButtonDisabled]}
              onPress={() => handleSend()}
              disabled={!input.trim()}
              accessibilityLabel="Send"
            >
              <Ionicons name="arrow-up" size={18} color="#fff" />
            </TouchableOpacity>
          )}
        </View>

        <Text style={styles.disclaimer}>
          {disclaimer || 'AI answers can be inaccurate. For emergencies, call 911 or use the SOS button.'}
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    flex: { flex: 1 },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 8,
      paddingVertical: 10,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: colors.border,
    },
    headerButton: { padding: 8 },
    headerTitleWrap: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    headerTitle: { fontSize: 17, fontWeight: '600', color: colors.text },
    welcomeWrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
    welcomeIcon: {
      width: 64,
      height: 64,
      borderRadius: 32,
      backgroundColor: colors.surfaceLight,
      alignItems: 'center',
      justifyContent: 'center',
      marginBottom: 16,
    },
    welcomeTitle: { fontSize: 20, fontWeight: '700', color: colors.text, marginBottom: 8 },
    welcomeSubtitle: {
      fontSize: 14,
      color: colors.textDim,
      textAlign: 'center',
      marginBottom: 24,
      lineHeight: 20,
    },
    chipsWrap: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 8 },
    chip: {
      paddingHorizontal: 14,
      paddingVertical: 9,
      borderRadius: 18,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
    },
    chipText: { fontSize: 13, color: colors.text },
    listContent: { padding: 16, gap: 10 },
    bubbleRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
    bubbleRowUser: { justifyContent: 'flex-end' },
    aiAvatar: {
      width: 26,
      height: 26,
      borderRadius: 13,
      backgroundColor: colors.surfaceLight,
      alignItems: 'center',
      justifyContent: 'center',
    },
    bubble: { maxWidth: '80%', borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10 },
    bubbleAssistant: { backgroundColor: colors.surface, borderBottomLeftRadius: 4 },
    bubbleUser: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
    bubbleText: { fontSize: 15, lineHeight: 21, color: colors.text },
    bubbleTextUser: { color: '#fff' },
    actionCard: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      marginLeft: 34,
      paddingHorizontal: 14,
      paddingVertical: 12,
      borderRadius: 12,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      alignSelf: 'flex-start',
    },
    actionCardText: { fontSize: 14, fontWeight: '600', color: colors.text },
    toolStatusRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      paddingHorizontal: 20,
      paddingVertical: 6,
    },
    toolStatusText: { fontSize: 13, color: colors.textDim, fontStyle: 'italic' },
    inputRow: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: 8,
      paddingHorizontal: 12,
      paddingTop: 8,
    },
    input: {
      flex: 1,
      minHeight: 42,
      maxHeight: 110,
      borderRadius: 21,
      paddingHorizontal: 16,
      paddingVertical: 10,
      backgroundColor: colors.surface,
      color: colors.text,
      fontSize: 15,
    },
    sendButton: {
      width: 42,
      height: 42,
      borderRadius: 21,
      backgroundColor: colors.primary,
      alignItems: 'center',
      justifyContent: 'center',
    },
    sendButtonDisabled: { opacity: 0.4 },
    disclaimer: {
      fontSize: 11,
      color: colors.textDim,
      textAlign: 'center',
      paddingHorizontal: 24,
      paddingTop: 6,
      paddingBottom: 4,
    },
  });
