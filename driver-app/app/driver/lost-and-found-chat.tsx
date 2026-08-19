/**
 * Driver Lost & Found chat screen.
 *
 * Two modes:
 *   caseId  — open an existing case (rider-reported or admin-created)
 *   rideId  — start a new case (driver found something in the vehicle)
 *
 * When opening an existing case that is in 'reported' or 'driver_notified'
 * status, the found/not-found response buttons are shown above the chat.
 * Once responded they collapse and normal chat resumes.
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Alert,
  AppState,
  Image,
  Modal,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../../hooks/useToast';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface LostFoundCase {
  id: string;
  ride_id?: string;
  item_description: string;
  item_category?: string;
  status: string;
  reporter_type: string;
}

interface Message {
  id: string;
  lost_and_found_id: string;
  sender_id: string | null;
  sender_role: 'rider' | 'driver' | 'admin' | 'system';
  message: string;
  created_at: string;
  /** Short-lived signed URL, minted per read by the backend (bucket is private). */
  image_url?: string;
  /** Local file URI while an optimistic image message is still uploading. */
  local_image_uri?: string;
}

const VALID_CATEGORIES = ['electronics', 'clothing', 'bag', 'document', 'keys', 'other'] as const;
type Category = (typeof VALID_CATEGORIES)[number];

const CATEGORY_LABELS: Record<Category, string> = {
  electronics: 'Electronics',
  clothing: 'Clothing',
  bag: 'Bag',
  document: 'Document',
  keys: 'Keys',
  other: 'Other',
};

const AWAITING_RESPONSE = new Set(['reported', 'driver_notified']);
const CLOSED = new Set(['not_found', 'returned', 'resolved', 'unresolved', 'closed']);

export default function DriverLostAndFoundChatScreen() {
  const router = useRouter();
  const { caseId, rideId } = useLocalSearchParams<{ caseId?: string; rideId?: string }>();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Existing-case state
  const [lostCase, setLostCase] = useState<LostFoundCase | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(!!caseId);
  const [responding, setResponding] = useState(false);

  // New-case (driver report) state
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<Category>('other');
  const [submitting, setSubmitting] = useState(false);

  // Chat state
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const flatRef = useRef<FlatList>(null);

  const loadCase = useCallback(async (id: string) => {
    try {
      const [caseRes, msgRes] = await Promise.all([
        api.get<{ case: LostFoundCase }>(`/lost-and-found/${id}`),
        api.get<{ messages: Message[] }>(`/lost-and-found/${id}/messages`),
      ]);
      setLostCase(caseRes.data?.case ?? null);
      setMessages(msgRes.data?.messages ?? []);
    } catch (e: any) {
      showToast('error', 'Could not load case.', getApiErrorMessage(e, 'Pull to refresh.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // loadCase is useCallback([]) — stable identity, so this only re-fires
    // when the caseId route param actually changes. It sets state after its
    // own await, not synchronously at the top of the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (caseId) loadCase(caseId);
  }, [caseId, loadCase]);

  useEffect(() => {
    if (rideId && !caseId && !lostCase) {
      (async () => {
        try {
          const res = await api.get<{ cases: LostFoundCase[] }>('/lost-and-found');
          const existing = (res.data?.cases ?? []).find(
            (c) => c.ride_id === rideId,
          );
          if (existing) {
            setLostCase(existing);
            setLoading(true);
            await loadCase(existing.id);
          }
        } catch {
          // no existing case — show report form
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rideId, caseId, loadCase]);

  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => flatRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [messages.length]);

  const activeCaseId = lostCase?.id ?? null;
  useFocusEffect(
    useCallback(() => {
      if (!activeCaseId) return;
      const interval = setInterval(() => {
        if (AppState.currentState === 'active') {
          api.get<{ messages: Message[] }>(`/lost-and-found/${activeCaseId}/messages`)
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
    }, [activeCaseId]),
  );

  // Submit a new driver-found case
  const submitReport = async () => {
    if (!description.trim() || !rideId || submitting) return;
    setSubmitting(true);
    try {
      const res = await api.post<{ case: LostFoundCase }>('/lost-and-found/driver-report', {
        ride_id: rideId,
        item_description: description.trim(),
        item_category: category,
      });
      const newCase = res.data?.case;
      if (newCase) {
        setLostCase(newCase);
        setLoading(true);
        await loadCase(newCase.id);
      }
    } catch (e: any) {
      showToast('error', 'Submit Failed', getApiErrorMessage(e, 'Could not submit report. Please try again.'));
    } finally {
      setSubmitting(false);
    }
  };

  // Driver responds found / not_found on a rider-reported case
  const respond = async (found: boolean) => {
    if (!lostCase || responding) return;
    const label = found ? 'confirm the item is in your vehicle' : 'mark it as not found';
    Alert.alert(
      found ? 'Confirm Item Found' : 'Item Not Found',
      `Are you sure you want to ${label}?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Yes',
          onPress: async () => {
            setResponding(true);
            try {
              await api.put(`/lost-and-found/${lostCase.id}/respond`, { found });
              await loadCase(lostCase.id);
            } catch (e: any) {
              showToast('error', 'Update Failed', getApiErrorMessage(e, 'Could not update status. Please try again.'));
            } finally {
              setResponding(false);
            }
          },
        },
      ],
    );
  };

  const sendMessage = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending || !lostCase) return;
    setSending(true);
    setText('');

    const optimistic: Message = {
      id: `local-${Date.now()}`,
      lost_and_found_id: lostCase.id,
      sender_id: 'me',
      sender_role: 'driver',
      message: trimmed,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);

    try {
      const res = await api.post<{ message: Message }>(`/lost-and-found/${lostCase.id}/messages`, {
        message: trimmed,
      });
      if (res.data?.message) {
        setMessages(prev => prev.filter(m => m.id !== optimistic.id).concat(res.data.message));
      }
    } catch (e: any) {
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      setText(trimmed);
      showToast('error', 'Send Failed', getApiErrorMessage(e, 'Could not send message. Please try again.'));
    } finally {
      setSending(false);
    }
  };

  // — Photo attachment —
  // A picture of what was actually found is the most useful thing a driver can
  // give a rider trying to identify their property, so this sits next to the
  // text input rather than behind a support flow.
  const attachPhoto = () => {
    if (!lostCase || isClosed) return;
    Alert.alert('Add a Photo', 'Show the rider what you found', [
      { text: 'Camera', onPress: () => pickPhoto('camera') },
      { text: 'Photo Library', onPress: () => pickPhoto('library') },
      { text: 'Cancel', style: 'cancel' },
    ]);
  };

  const pickPhoto = async (source: 'camera' | 'library') => {
    const perm = source === 'camera'
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (perm.status !== 'granted') {
      showToast('error', 'Permission Needed',
        source === 'camera' ? 'Camera access is needed to take a photo.' : 'Photo access is needed to attach an image.');
      return;
    }
    const result = source === 'camera'
      ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.7 })
      : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.7 });
    if (result.canceled || !result.assets?.[0]) return;
    await uploadPhoto(result.assets[0].uri);
  };

  const uploadPhoto = async (uri: string) => {
    if (!lostCase || uploadingPhoto) return;
    setUploadingPhoto(true);

    const optimistic: Message = {
      id: `local-img-${Date.now()}`,
      lost_and_found_id: lostCase.id,
      sender_id: 'me',
      sender_role: 'driver',
      message: '',
      local_image_uri: uri,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);

    try {
      const fd = new FormData();
      fd.append('file', { uri, name: 'item.jpg', type: 'image/jpeg' } as any);
      // api.post carries the auth + App Check headers and now passes FormData
      // through untouched; a hand-rolled fetch() here would skip App Check.
      const res = await api.post<{ message: Message }>(
        `/lost-and-found/${lostCase.id}/messages/image`,
        fd,
      );
      if (res.data?.message) {
        setMessages(prev => prev.filter(m => m.id !== optimistic.id).concat(res.data.message));
      }
    } catch (e: any) {
      // Drop the optimistic bubble — leaving it would tell the driver the
      // rider can see a photo that was never stored.
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      showToast('error', 'Upload Failed', getApiErrorMessage(e, 'Could not send the photo. Please try again.'));
    } finally {
      setUploadingPhoto(false);
    }
  };

  const renderMessage = ({ item }: { item: Message }) => {
    const isSystem = item.sender_role === 'system' || item.sender_role === 'admin';
    const isMe = item.sender_role === 'driver';

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
    const photoUri = item.local_image_uri ?? item.image_url;
    const hasText = !!item.message?.trim();

    return (
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && (
          <View style={styles.avatar}>
            <Ionicons name="person" size={14} color={colors.textDim} />
          </View>
        )}
        <View style={[styles.bubble, isMe ? styles.bubbleMe : styles.bubbleThem]}>
          {photoUri && (
            <TouchableOpacity
              activeOpacity={0.9}
              onPress={() => setPreviewUri(photoUri)}
              accessibilityRole="imagebutton"
              accessibilityLabel="Item photo — tap to enlarge"
            >
              <Image source={{ uri: photoUri }} style={styles.msgImage} resizeMode="cover" />
              {!!item.local_image_uri && (
                <View style={styles.msgImagePending}>
                  <ActivityIndicator size="small" color="#fff" />
                </View>
              )}
            </TouchableOpacity>
          )}
          {hasText && (
            <Text style={[styles.bubbleText, isMe && styles.bubbleTextMe]}>{item.message}</Text>
          )}
          <Text style={[styles.timeText, isMe && styles.timeTextMe]}>{time}</Text>
        </View>
      </View>
    );
  };

  // — New case report form (rideId mode, no case yet) —
  if (rideId && !lostCase) {
    return (
      <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.back}>
            <Ionicons name="chevron-back" size={28} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Report Found Item</Text>
          <View style={{ width: 44 }} />
        </View>

        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
          <FlatList
            data={[]}
            renderItem={null}
            ListHeaderComponent={
              <View style={styles.reportForm}>
                <Text style={styles.formLabel}>What did you find?</Text>
                <TextInput
                  style={styles.formInput}
                  value={description}
                  onChangeText={setDescription}
                  placeholder="e.g. Black iPhone with cracked screen"
                  placeholderTextColor={colors.textDim}
                  multiline
                  maxLength={500}
                />

                <Text style={styles.formLabel}>Category</Text>
                <View style={styles.catGrid}>
                  {VALID_CATEGORIES.map(cat => (
                    <TouchableOpacity
                      key={cat}
                      style={[styles.catPill, category === cat && styles.catPillSelected]}
                      onPress={() => setCategory(cat)}
                    >
                      <Text style={[styles.catText, category === cat && styles.catTextSelected]}>
                        {CATEGORY_LABELS[cat]}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>

                <TouchableOpacity
                  style={[styles.submitBtn, { opacity: !description.trim() || submitting ? 0.5 : 1 }]}
                  onPress={submitReport}
                  disabled={!description.trim() || submitting}
                >
                  {submitting
                    ? <ActivityIndicator size="small" color="#fff" />
                    : <Text style={styles.submitBtnText}>Submit & Open Chat</Text>}
                </TouchableOpacity>
              </View>
            }
          />
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  const awaitingResponse = lostCase ? AWAITING_RESPONSE.has(lostCase.status) : false;
  const isClosed = lostCase ? CLOSED.has(lostCase.status) : false;

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <Ionicons name="chevron-back" size={28} color={colors.text} />
        </TouchableOpacity>
        <View style={styles.headerInfo}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {lostCase?.item_description ?? 'Lost & Found'}
          </Text>
          <Text style={styles.headerStatus}>{lostCase?.status ?? ''}</Text>
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
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
          keyboardVerticalOffset={0}
        >
          {/* Found / Not Found response prompt */}
          {awaitingResponse && (
            <View style={styles.respondBanner}>
              <Text style={styles.respondQuestion}>
                Is this item in your vehicle?
              </Text>
              <View style={styles.respondBtns}>
                <TouchableOpacity
                  style={[styles.respondBtn, styles.respondBtnYes]}
                  onPress={() => respond(true)}
                  disabled={responding}
                >
                  {responding
                    ? <ActivityIndicator size="small" color="#fff" />
                    : <Text style={styles.respondBtnText}>Yes, I have it</Text>}
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.respondBtn, styles.respondBtnNo]}
                  onPress={() => respond(false)}
                  disabled={responding}
                >
                  <Text style={[styles.respondBtnText, { color: '#EF4444' }]}>Not in my vehicle</Text>
                </TouchableOpacity>
              </View>
            </View>
          )}

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
                  {awaitingResponse
                    ? 'Respond above, then you can chat with the rider.'
                    : 'No messages yet.'}
                </Text>
              </View>
            }
          />

          {isClosed ? (
            <View style={styles.closedBanner}>
              <Text style={styles.closedText}>This case is closed.</Text>
            </View>
          ) : (
            <View style={styles.inputRow}>
              <TouchableOpacity
                style={[styles.attachBtn, { opacity: uploadingPhoto ? 0.4 : 1 }]}
                onPress={attachPhoto}
                disabled={uploadingPhoto}
                accessibilityRole="button"
                accessibilityLabel="Attach a photo of the item"
              >
                {uploadingPhoto
                  ? <ActivityIndicator size="small" color={colors.primary} />
                  : <Ionicons name="camera" size={20} color={colors.primary} />}
              </TouchableOpacity>
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

      {/* Full-screen photo preview — item photos are often small details
          (a phone case, a name on a tag) that a chat-sized thumbnail hides. */}
      <Modal
        visible={!!previewUri}
        transparent
        animationType="fade"
        onRequestClose={() => setPreviewUri(null)}
      >
        <TouchableOpacity
          style={styles.previewBackdrop}
          activeOpacity={1}
          onPress={() => setPreviewUri(null)}
          accessibilityRole="button"
          accessibilityLabel="Close photo preview"
        >
          {!!previewUri && (
            <Image source={{ uri: previewUri }} style={styles.previewImage} resizeMode="contain" />
          )}
        </TouchableOpacity>
      </Modal>
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
    headerStatus: { fontSize: 12, color: colors.textDim, marginTop: 1, textTransform: 'capitalize' },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
    emptyText: { fontSize: 14, color: colors.textDim, textAlign: 'center', lineHeight: 20 },

    // Report form
    reportForm: { padding: 20, gap: 12 },
    formLabel: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      marginBottom: 4,
    },
    formInput: {
      backgroundColor: colors.surface,
      borderRadius: 12,
      padding: 14,
      fontSize: 15,
      color: colors.text,
      minHeight: 80,
      borderWidth: 1,
      borderColor: colors.border,
      textAlignVertical: 'top',
    },
    catGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    catPill: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.surface,
    },
    catPillSelected: { borderColor: colors.primary, backgroundColor: `${colors.primary}18` },
    catText: { fontSize: 13, color: colors.textDim },
    catTextSelected: { color: colors.primary, fontFamily: 'PlusJakartaSans_600SemiBold' },
    submitBtn: {
      backgroundColor: colors.primary,
      borderRadius: 14,
      padding: 16,
      alignItems: 'center',
      marginTop: 8,
    },
    submitBtnText: { color: '#fff', fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold' },

    // Respond banner
    respondBanner: {
      padding: 16,
      backgroundColor: 'rgba(245, 158, 11, 0.08)',
      borderBottomWidth: 1,
      borderBottomColor: 'rgba(245, 158, 11, 0.3)',
      gap: 10,
    },
    respondQuestion: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      textAlign: 'center',
    },
    respondBtns: { flexDirection: 'row', gap: 10 },
    respondBtn: {
      flex: 1,
      paddingVertical: 12,
      borderRadius: 12,
      alignItems: 'center',
      borderWidth: 1,
    },
    respondBtnYes: { backgroundColor: '#10B981', borderColor: '#10B981' },
    respondBtnNo: { backgroundColor: colors.surface, borderColor: '#EF4444' },
    respondBtnText: { fontSize: 14, fontFamily: 'PlusJakartaSans_600SemiBold', color: '#fff' },

    // Chat
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
    bubble: { maxWidth: '75%', borderRadius: 16, padding: 10, gap: 2 },
    bubbleMe: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
    bubbleThem: {
      backgroundColor: colors.surface,
      borderBottomLeftRadius: 4,
      borderWidth: 1,
      borderColor: colors.border,
    },
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
    attachBtn: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: `${colors.primary}18`,
      alignItems: 'center',
      justifyContent: 'center',
    },

    // Image messages
    msgImage: {
      width: 200,
      height: 200,
      borderRadius: 10,
      backgroundColor: colors.surfaceLight,
    },
    msgImagePending: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      borderRadius: 10,
      backgroundColor: 'rgba(0,0,0,0.35)',
      alignItems: 'center',
      justifyContent: 'center',
    },
    previewBackdrop: {
      flex: 1,
      backgroundColor: 'rgba(0,0,0,0.92)',
      alignItems: 'center',
      justifyContent: 'center',
    },
    previewImage: { width: '100%', height: '80%' },
  });
}
