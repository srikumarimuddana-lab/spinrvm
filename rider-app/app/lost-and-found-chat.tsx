import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  FlatList,
  KeyboardAvoidingView,
  ActivityIndicator,
  AppState,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import { Ionicons } from '@expo/vector-icons';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface LostFoundCase {
  id: string;
  item_description: string;
  item_category?: string;
  status: string;
  reporter_type: string;
  created_at: string;
}

interface Message {
  id: string;
  lost_and_found_id: string;
  sender_id: string | null;
  sender_role: 'rider' | 'driver' | 'admin' | 'system';
  message: string;
  created_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  reported: 'Awaiting driver response',
  driver_notified: 'Driver has been notified',
  driver_found: 'Driver found an item',
  found: 'Driver confirmed — item found',
  not_found: 'Driver: item not in vehicle',
  returned: 'Item returned',
  unclaimed: 'Unclaimed',
  resolved: 'Resolved',
  closed: 'Closed',
  admin_created: 'Case opened by support',
};

const CLOSED_STATUSES = new Set(['not_found', 'returned', 'resolved', 'unresolved', 'closed']);

export default function LostAndFoundChatScreen() {
  const router = useRouter();
  const { caseId } = useLocalSearchParams<{ caseId: string }>();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [lostCase, setLostCase] = useState<LostFoundCase | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const flatRef = useRef<FlatList>(null);

  const loadAll = useCallback(async () => {
    if (!caseId) return;
    try {
      const [caseRes, msgRes] = await Promise.all([
        api.get<{ case: LostFoundCase }>(`/lost-and-found/${caseId}`),
        api.get<{ messages: Message[] }>(`/lost-and-found/${caseId}/messages`),
      ]);
      setLostCase(caseRes.data?.case ?? null);
      setMessages(msgRes.data?.messages ?? []);
    } catch (err) {
      showToast("Couldn't Load Case", getApiErrorMessage(err, 'Could not load case. Pull to refresh.'), 'danger');
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  // loadAll is a useCallback keyed only on caseId, so this fires once per
  // caseId value; the state it sets (lostCase/messages/loading) isn't a dep.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadAll(); }, [loadAll]);

  useFocusEffect(
    useCallback(() => {
      const interval = setInterval(() => {
        if (caseId && AppState.currentState === 'active') {
          api.get<{ messages: Message[] }>(`/lost-and-found/${caseId}/messages`)
            .then(res => {
              const fresh = res.data?.messages ?? [];
              setMessages(prev => {
                if (fresh.length !== prev.length) return fresh;
                const lastFresh = fresh[fresh.length - 1]?.id;
                const lastPrev = prev[prev.length - 1]?.id;
                return lastFresh !== lastPrev ? fresh : prev;
              });
            })
            .catch(() => {});
        }
      }, 10_000);
      return () => clearInterval(interval);
    }, [caseId]),
  );

  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => flatRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [messages.length]);

  const sendMessage = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending || !caseId) return;
    setSending(true);
    setText('');

    const optimistic: Message = {
      id: `local-${Date.now()}`,
      lost_and_found_id: caseId,
      sender_id: 'me',
      sender_role: 'rider',
      message: trimmed,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);

    try {
      const res = await api.post<{ message: Message }>(`/lost-and-found/${caseId}/messages`, {
        message: trimmed,
      });
      if (res.data?.message) {
        setMessages(prev => prev.filter(m => m.id !== optimistic.id).concat(res.data.message));
      }
    } catch (e: any) {
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      setText(trimmed);
      showToast('Send Failed', getApiErrorMessage(e, 'Could not send message.'), 'danger');
    } finally {
      setSending(false);
    }
  };

  const renderMessage = ({ item }: { item: Message }) => {
    const isSystem = item.sender_role === 'system' || item.sender_role === 'admin';
    const isMe = item.sender_role === 'rider';

    if (isSystem) {
      return (
        <View style={styles.systemRow}>
          <Text style={styles.systemText}>{item.message}</Text>
        </View>
      );
    }

    const time = new Date(item.created_at).toLocaleTimeString([], {
      hour: 'numeric', minute: '2-digit',
    });

    return (
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && (
          <View style={styles.avatar}>
            <Ionicons name="person" size={14} color={colors.textDim} />
          </View>
        )}
        <View style={[styles.bubble, isMe ? styles.bubbleMe : styles.bubbleThem]}>
          <Text style={[styles.bubbleText, isMe && styles.bubbleTextMe]}>{item.message}</Text>
          <Text style={[styles.timeText, isMe && styles.timeTextMe]}>{time}</Text>
        </View>
      </View>
    );
  };

  const isClosed = lostCase ? CLOSED_STATUSES.has(lostCase.status) : false;
  const statusLabel = lostCase ? (STATUS_LABELS[lostCase.status] ?? lostCase.status) : '';

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <Ionicons name="chevron-back" size={28} color={colors.text} />
        </TouchableOpacity>
        <View style={styles.headerInfo}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {lostCase?.item_description ?? 'Lost & Found'}
          </Text>
          <Text style={styles.headerStatus}>{statusLabel}</Text>
        </View>
        <View style={{ width: 44 }} />
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior="padding"
          keyboardVerticalOffset={0}
        >
          <FlatList
            ref={flatRef}
            data={messages}
            keyExtractor={m => m.id}
            renderItem={renderMessage}
            contentContainerStyle={styles.msgList}
            showsVerticalScrollIndicator={false}
            ListEmptyComponent={
              <View style={styles.center}>
                <Text style={styles.emptyText}>
                  {isClosed
                    ? 'This case is closed.'
                    : 'Waiting for driver to respond. You can send a message below.'}
                </Text>
              </View>
            }
          />

          {isClosed ? (
            <View style={styles.closedBanner}>
              <Text style={styles.closedText}>This case is closed — messaging is disabled.</Text>
            </View>
          ) : (
            <View style={styles.inputRow}>
              <TextInput
                style={styles.input}
                value={text}
                onChangeText={setText}
                placeholder="Type a message…"
                placeholderTextColor={colors.textDim}
                multiline
                maxLength={1000}
                returnKeyType="send"
                onSubmitEditing={sendMessage}
              />
              <TouchableOpacity
                style={[styles.sendBtn, { opacity: !text.trim() || sending ? 0.4 : 1 }]}
                onPress={sendMessage}
                disabled={!text.trim() || sending}
              >
                {sending
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <Ionicons name="send" size={18} color="#fff" />}
              </TouchableOpacity>
            </View>
          )}
        </KeyboardAvoidingView>
      )}
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 12,
      paddingVertical: 10,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
      gap: 8,
    },
    back: { padding: 4 },
    headerInfo: { flex: 1 },
    headerTitle: { fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    headerStatus: { fontSize: 12, color: colors.textDim, marginTop: 1 },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
    emptyText: { fontSize: 14, color: colors.textDim, textAlign: 'center', lineHeight: 20 },
    msgList: { padding: 16, gap: 8, flexGrow: 1 },
    systemRow: { alignItems: 'center', marginVertical: 8 },
    systemText: {
      fontSize: 12,
      color: colors.textDim,
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 12,
      paddingVertical: 4,
      borderRadius: 12,
      textAlign: 'center',
    },
    msgRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 6, marginVertical: 3 },
    msgRowMe: { flexDirection: 'row-reverse' },
    avatar: {
      width: 28,
      height: 28,
      borderRadius: 14,
      backgroundColor: colors.surfaceLight,
      alignItems: 'center',
      justifyContent: 'center',
    },
    bubble: {
      maxWidth: '75%',
      borderRadius: 16,
      padding: 10,
      gap: 2,
    },
    bubbleMe: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
    bubbleThem: { backgroundColor: colors.surface, borderBottomLeftRadius: 4, borderWidth: 1, borderColor: colors.border },
    bubbleText: { fontSize: 15, color: colors.text, lineHeight: 21 },
    bubbleTextMe: { color: '#fff' },
    timeText: { fontSize: 10, color: colors.textDim, alignSelf: 'flex-end' },
    timeTextMe: { color: 'rgba(255,255,255,0.6)' },
    closedBanner: {
      padding: 14,
      backgroundColor: colors.surfaceLight,
      borderTopWidth: 1,
      borderTopColor: colors.border,
      alignItems: 'center',
    },
    closedText: { fontSize: 13, color: colors.textDim },
    inputRow: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      padding: 10,
      gap: 8,
      borderTopWidth: 1,
      borderTopColor: colors.border,
      backgroundColor: colors.surface,
    },
    input: {
      flex: 1,
      backgroundColor: colors.background,
      borderRadius: 20,
      paddingHorizontal: 14,
      paddingVertical: 10,
      fontSize: 15,
      color: colors.text,
      maxHeight: 100,
      borderWidth: 1,
      borderColor: colors.border,
    },
    sendBtn: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: colors.primary,
      alignItems: 'center',
      justifyContent: 'center',
    },
  });
}
