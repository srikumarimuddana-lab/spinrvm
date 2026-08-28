import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  FlatList,
  ActivityIndicator,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
type IoniconName = React.ComponentProps<typeof Ionicons>['name'];
import api from '@shared/api/client';
import CustomAlert from '@shared/components/CustomAlert';
import AiAuroraBackground from '@shared/components/AiAuroraBackground';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

// ── Types ────────────────────────────────────────────────────────────────────
type Role = 'rider' | 'driver';
type Tab = 'faq' | 'chat' | 'contact';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
}

interface Faq {
  id: string;
  question: string;
  answer: string;
  category?: string;
}

interface CompanyInfo {
  name?: string;
  address?: string;
  phone?: string;
  email?: string;
  website?: string;
}

const SUPPORT_PHONE_DISPLAY = '1-800-SPINR';
const SUPPORT_EMAIL = 'support@spinr.ca';

const WELCOME_MESSAGES: Record<Role, string> = {
  rider: "Hi! I'm Spinr's AI assistant. Ask me anything about your rides, payments, account, or how the app works.",
  driver: "Hi! I'm Spinr's AI assistant. Ask me anything about onboarding, payouts, documents, or how the app works.",
};

/** Reject cached fixes older than this so FAQ location scoping reflects where
 * the user actually is. */
const LOCATION_MAX_AGE_MS = 5 * 60 * 1000;

/** Last-known device position, only when permission is already granted — the
 * help screen never triggers a permission prompt. Null on any failure. Lets
 * the public /faqs endpoint scope area-specific FAQs (e.g. SGI content) to the
 * user's region without the client having to know its service area. */
async function deviceLocation(): Promise<{ lat: number; lng: number } | null> {
  try {
    const { granted } = await Location.getForegroundPermissionsAsync();
    if (!granted) return null;
    const pos = await Location.getLastKnownPositionAsync({ maxAge: LOCATION_MAX_AGE_MS });
    if (!pos) return null;
    return { lat: pos.coords.latitude, lng: pos.coords.longitude };
  } catch {
    return null;
  }
}

// ── AI assistant (server-side) ───────────────────────────────────────────────
// All AI inference happens behind the authenticated backend (/ai/chat) —
// prompts, tools and provider keys live server-side. No API key ships in the
// app bundle. The backend picks the rider/driver tool set from the user row.
async function askAssistant(
  message: string,
  conversationId: string | null,
): Promise<{ reply: string; conversationId: string | null }> {
  const res = await api.post<{ reply?: string; conversation_id?: string | null }>('/ai/chat', {
    message,
    conversation_id: conversationId,
    stream: false,
  });
  return {
    reply:
      res.data?.reply ||
      "I'm sorry, I couldn't process that. Please try again or contact support@spinr.ca.",
    conversationId: res.data?.conversation_id ?? conversationId,
  };
}

// ── Component ────────────────────────────────────────────────────────────────
interface Props {
  role: Role;
  /** Initial tab when the screen mounts. Defaults to 'faq'. */
  initialTab?: Tab;
  /** Pre-fill the contact-form issue text (e.g. from a stuck payment screen). */
  initialIssue?: string;
  /** Category for a pre-filled ticket. Defaults to 'general'. */
  initialCategory?: string;
}

export default function SupportScreen({
  role,
  initialTab = 'faq',
  initialIssue = '',
  initialCategory = 'general',
}: Props) {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [activeTab, setActiveTab] = useState<Tab>(initialTab);

  // AI kill switch: 'enabled' shows the AI Chat tab; 'coming_soon' shows it
  // with a placeholder; 'hidden' removes it. Defaults to hidden until
  // /ai/config resolves so a disabled assistant never flashes into view.
  const [aiMode, setAiMode] = useState<'enabled' | 'coming_soon' | 'hidden'>('hidden');
  const aiEnabled = aiMode === 'enabled';
  const showChatTab = aiMode !== 'hidden';

  // FAQ — fetched from API filtered by audience.
  const [faqs, setFaqs] = useState<Faq[]>([]);
  const [faqsLoading, setFaqsLoading] = useState(true);
  const [expandedFaq, setExpandedFaq] = useState<string | null>(null);
  const [faqSearch, setFaqSearch] = useState('');

  // Public company info shown at the bottom of the FAQ tab.
  const [companyInfo, setCompanyInfo] = useState<CompanyInfo>({});

  // Chat
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: '0',
      role: 'assistant',
      content: WELCOME_MESSAGES[role],
      timestamp: new Date(),
    },
  ]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const chatListRef = useRef<FlatList>(null);
  // Server-side conversation thread for the AI chat tab (multi-turn context
  // lives in the backend, not in this component).
  const conversationIdRef = useRef<string | null>(null);

  // Contact form
  const [issue, setIssue] = useState(initialIssue);
  const [submitting, setSubmitting] = useState(false);

  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{
      text: string;
      style?: 'default' | 'cancel' | 'destructive';
      onPress?: () => void;
    }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  // ── Load FAQs + company info on mount ──
  useEffect(() => {
    let cancelled = false;
    setFaqsLoading(true);
    // Attach device location (when already permitted) so area-scoped FAQs for
    // the user's region are included; without it the backend returns global
    // FAQs only.
    deviceLocation()
      .then((loc) => {
        const params = new URLSearchParams({ audience: role });
        if (loc) {
          params.set('lat', String(loc.lat));
          params.set('lng', String(loc.lng));
        }
        return api.get(`/faqs?${params.toString()}`);
      })
      .then((r) => {
        if (!cancelled) setFaqs(Array.isArray(r?.data) ? r.data : []);
      })
      .catch(() => {
        if (!cancelled) setFaqs([]);
      })
      .finally(() => {
        if (!cancelled) setFaqsLoading(false);
      });

    api
      .get('/company-info')
      .then((r) => !cancelled && setCompanyInfo(r?.data || {}))
      .catch((e) => console.warn('[Support] company-info fetch failed:', e?.message ?? e));

    // AI availability drives whether the chat tab/CTA render.
    api
      .get<{ enabled?: boolean; mode?: string }>('/ai/config')
      .then((r) => {
        if (cancelled) return;
        const enabled = !!r?.data?.enabled;
        setAiMode((r?.data?.mode as 'enabled' | 'coming_soon' | 'hidden') ?? (enabled ? 'enabled' : 'coming_soon'));
      })
      .catch(() => !cancelled && setAiMode('hidden'));

    return () => {
      cancelled = true;
    };
  }, [role]);

  // If the assistant is fully hidden, never leave the user stranded on the
  // chat tab (e.g. when opened via initialTab='chat').
  useEffect(() => {
    if (!showChatTab && activeTab === 'chat') setActiveTab('faq');
  }, [showChatTab, activeTab]);

  const filteredFaqs = useMemo(() => {
    if (!faqSearch.trim()) return faqs;
    const q = faqSearch.toLowerCase();
    return faqs.filter(
      (f) =>
        f.question?.toLowerCase().includes(q) ||
        f.answer?.toLowerCase().includes(q),
    );
  }, [faqs, faqSearch]);

  // ── Handlers ──
  const handleSubmitTicket = async () => {
    if (!issue.trim()) {
      setAlertState({
        visible: true,
        title: 'Error',
        message: 'Please describe your issue.',
        variant: 'warning',
      });
      return;
    }
    setSubmitting(true);
    try {
      await api.post('/tickets', {
        subject: initialCategory === 'payment_failed' ? 'Payment Issue' : 'App Support Request',
        message: issue,
        category: initialCategory,
      });
      setIssue('');
      setAlertState({
        visible: true,
        title: 'Request Submitted',
        message: 'Our team will respond within 24 hours.',
        variant: 'success',
        buttons: [{ text: 'OK', onPress: () => router.back() }],
      });
    } catch {
      setAlertState({
        visible: true,
        title: 'Error',
        message: 'Failed to submit. Please try again.',
        variant: 'danger',
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleSendChat = useCallback(async () => {
    const text = chatInput.trim();
    if (!text || chatLoading) return;

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    };

    setChatMessages((prev) => [...prev, userMsg]);
    setChatInput('');
    setChatLoading(true);

    setTimeout(() => chatListRef.current?.scrollToEnd({ animated: true }), 100);

    try {
      const { reply, conversationId } = await askAssistant(text, conversationIdRef.current);
      conversationIdRef.current = conversationId;
      setChatMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content: reply,
          timestamp: new Date(),
        },
      ]);
    } catch {
      setChatMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content:
            "I'm having trouble connecting right now. Please try again or contact support@spinr.ca.",
          timestamp: new Date(),
        },
      ]);
    } finally {
      setChatLoading(false);
      setTimeout(() => chatListRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [chatInput, chatLoading]);

  const renderChatMessage = ({ item }: { item: ChatMessage }) => {
    const isUser = item.role === 'user';
    return (
      <View style={[styles.chatBubbleRow, isUser && styles.chatBubbleRowUser]}>
        {!isUser && (
          <View style={styles.aiAvatar}>
            <Ionicons name="sparkles" size={14} color={colors.primary} />
          </View>
        )}
        <View
          style={[styles.chatBubble, isUser ? styles.chatBubbleUser : styles.chatBubbleAI]}
        >
          <Text style={[styles.chatBubbleText, isUser && styles.chatBubbleTextUser]}>
            {item.content}
          </Text>
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Help & Support</Text>
        <View style={styles.headerRight} />
      </View>

      {/* Tabs */}
      <View style={styles.tabs}>
        {(
          [
            { key: 'faq', label: 'FAQ', icon: 'help-circle-outline' },
            ...(showChatTab
              ? [{ key: 'chat', label: 'AI Chat', icon: 'sparkles-outline' }]
              : []),
            { key: 'contact', label: 'Contact', icon: 'mail-outline' },
          ] as { key: Tab; label: string; icon: IoniconName }[]
        ).map((tab) => (
          <TouchableOpacity
            key={tab.key}
            style={[styles.tab, activeTab === tab.key && styles.tabActive]}
            onPress={() => setActiveTab(tab.key)}
          >
            <Ionicons
              name={tab.icon}
              size={16}
              color={activeTab === tab.key ? colors.primary : colors.textDim}
            />
            <Text
              style={[styles.tabText, activeTab === tab.key && styles.tabTextActive]}
            >
              {tab.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* FAQ Tab */}
      {activeTab === 'faq' && (
        <ScrollView
          contentContainerStyle={styles.faqList}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.searchRow}>
            <Ionicons
              name="search"
              size={16}
              color={colors.textDim}
              style={{ marginRight: 8 }}
            />
            <TextInput
              style={styles.searchInput}
              placeholder="Search questions..."
              placeholderTextColor={colors.textDim}
              value={faqSearch}
              onChangeText={setFaqSearch}
              returnKeyType="search"
              clearButtonMode="while-editing"
            />
          </View>

          {faqsLoading ? (
            <ActivityIndicator
              size="large"
              color={colors.primary}
              style={{ marginTop: 40 }}
            />
          ) : filteredFaqs.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="search-outline" size={48} color={colors.border} />
              <Text style={styles.emptyTitle}>
                {faqs.length === 0 ? 'No FAQs available yet' : 'No results found'}
              </Text>
              {faqs.length > 0 && (
                <Text style={styles.emptySub}>Try a different keyword.</Text>
              )}
            </View>
          ) : (
            filteredFaqs.map((faq) => {
              const isOpen = expandedFaq === faq.id;
              return (
                <TouchableOpacity
                  key={faq.id}
                  style={styles.faqCard}
                  onPress={() => setExpandedFaq(isOpen ? null : faq.id)}
                  activeOpacity={0.7}
                >
                  <View style={styles.faqHeader}>
                    <Text
                      style={styles.faqQuestion}
                      numberOfLines={isOpen ? undefined : 2}
                    >
                      {faq.question}
                    </Text>
                    <Ionicons
                      name={isOpen ? 'chevron-up' : 'chevron-down'}
                      size={18}
                      color={colors.textDim}
                    />
                  </View>
                  {isOpen && <Text style={styles.faqAnswer}>{faq.answer}</Text>}
                </TouchableOpacity>
              );
            })
          )}

          {aiEnabled && (
            <TouchableOpacity
              style={styles.stillNeedHelp}
              onPress={() => setActiveTab('chat')}
            >
              <Ionicons name="sparkles" size={18} color={colors.primary} />
              <Text style={styles.stillNeedHelpText}>
                Still need help? Ask our AI assistant
              </Text>
              <Ionicons name="chevron-forward" size={16} color={colors.primary} />
            </TouchableOpacity>
          )}

          {(companyInfo.address ||
            companyInfo.phone ||
            companyInfo.email ||
            companyInfo.website) && (
            <View style={styles.companySection}>
              <Text style={styles.companyName}>{companyInfo.name || 'Spinr'}</Text>
              {!!companyInfo.address && (
                <Text style={styles.companyLine}>{companyInfo.address}</Text>
              )}
              {!!companyInfo.phone && (
                <Text style={styles.companyLine}>{companyInfo.phone}</Text>
              )}
              {!!companyInfo.email && (
                <Text style={styles.companyLine}>{companyInfo.email}</Text>
              )}
              {!!companyInfo.website && (
                <Text style={styles.companyLine}>{companyInfo.website}</Text>
              )}
            </View>
          )}
        </ScrollView>
      )}

      {/* AI Chat Tab — coming-soon placeholder while disabled (not hidden) */}
      {activeTab === 'chat' && !aiEnabled && (
        <View style={styles.comingSoon}>
          <View style={styles.comingSoonIcon}>
            <Ionicons name="sparkles" size={32} color={colors.primary} />
          </View>
          <Text style={styles.comingSoonTitle}>AI Assistant coming soon</Text>
          <Text style={styles.comingSoonSub}>
            In the meantime, browse the FAQ or reach us from the Contact tab.
          </Text>
          <TouchableOpacity style={styles.comingSoonBtn} onPress={() => setActiveTab('contact')}>
            <Ionicons name="mail-outline" size={16} color={colors.primary} />
            <Text style={styles.comingSoonBtnText}>Contact support</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* AI Chat Tab */}
      {activeTab === 'chat' && aiEnabled && (
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          {/* Ambient gradient on the idle chat (only the welcome message, no
              draft typed); fades to plain background once the conversation
              starts — same behaviour as the rider AI assistant. */}
          <AiAuroraBackground
            visible={chatMessages.length <= 1 && chatInput.trim().length === 0}
          />
          <FlatList
            ref={chatListRef}
            data={chatMessages}
            keyExtractor={(item) => item.id}
            renderItem={renderChatMessage}
            contentContainerStyle={styles.chatList}
            onContentSizeChange={() =>
              chatListRef.current?.scrollToEnd({ animated: false })
            }
            showsVerticalScrollIndicator={false}
            ListFooterComponent={
              chatLoading ? (
                <View style={styles.chatBubbleRow}>
                  <View style={styles.aiAvatar}>
                    <Ionicons name="sparkles" size={14} color={colors.primary} />
                  </View>
                  <View style={styles.chatBubbleAI}>
                    <ActivityIndicator size="small" color={colors.primary} />
                  </View>
                </View>
              ) : null
            }
          />
          <View style={styles.chatInputRow}>
            <TextInput
              style={styles.chatInput}
              placeholder="Ask a question..."
              placeholderTextColor={colors.textDim}
              value={chatInput}
              onChangeText={setChatInput}
              multiline
              maxLength={400}
              onSubmitEditing={handleSendChat}
              returnKeyType="send"
              blurOnSubmit={false}
            />
            <TouchableOpacity
              style={[
                styles.chatSendBtn,
                (!chatInput.trim() || chatLoading) && styles.chatSendBtnDisabled,
              ]}
              onPress={handleSendChat}
              disabled={!chatInput.trim() || chatLoading}
            >
              <Ionicons name="send" size={20} color="#FFF" />
            </TouchableOpacity>
          </View>
          <Text style={styles.chatDisclaimer}>
            AI responses are for guidance only. For account-specific issues, use Contact.
          </Text>
        </KeyboardAvoidingView>
      )}

      {/* Contact Tab */}
      {activeTab === 'contact' && (
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          <ScrollView
            contentContainerStyle={styles.contactContent}
            keyboardShouldPersistTaps="handled"
          >
            <Text style={styles.label}>How can we help you today?</Text>
            <TextInput
              style={styles.input}
              placeholder="Describe your issue in detail..."
              placeholderTextColor="#9CA3AF"
              multiline
              numberOfLines={8}
              textAlignVertical="top"
              value={issue}
              onChangeText={setIssue}
              editable={!submitting}
            />
            <TouchableOpacity
              style={[styles.submitButton, submitting && styles.submitButtonDisabled]}
              onPress={handleSubmitTicket}
              disabled={submitting}
            >
              <Text style={styles.submitButtonText}>
                {submitting ? 'Submitting...' : 'Submit Report'}
              </Text>
            </TouchableOpacity>

            <View style={styles.contactQuickRow}>
              <TouchableOpacity
                style={styles.contactChip}
                onPress={() =>
                  Linking.openURL(`tel:${SUPPORT_PHONE_DISPLAY.replace(/-/g, '')}`)
                }
              >
                <Ionicons name="call-outline" size={14} color={colors.primary} />
                <Text style={styles.contactChipText}>{SUPPORT_PHONE_DISPLAY}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.contactChip}
                onPress={() => Linking.openURL(`mailto:${SUPPORT_EMAIL}`)}
              >
                <Ionicons name="mail-outline" size={14} color={colors.primary} />
                <Text style={styles.contactChipText}>{SUPPORT_EMAIL}</Text>
              </TouchableOpacity>
            </View>

            <View style={styles.companyCard}>
              <Text style={styles.companyTitle}>
                {companyInfo.name || 'SPINR MOBILITY INC.'}
              </Text>
              {([
                {
                  icon: 'location-outline',
                  text: companyInfo.address || 'Saskatoon, SK, Canada',
                },
                { icon: 'mail-outline', text: companyInfo.email || SUPPORT_EMAIL },
                {
                  icon: 'call-outline',
                  text: companyInfo.phone || SUPPORT_PHONE_DISPLAY,
                },
                { icon: 'globe-outline', text: companyInfo.website || 'www.spinr.ca' },
              ] as { icon: IoniconName; text: string }[]).map((row) => (
                <View key={String(row.icon)} style={styles.companyRow}>
                  <Ionicons name={row.icon} size={16} color={colors.textDim} />
                  <Text style={styles.companyText}>{row.text}</Text>
                </View>
              ))}
              <Text style={styles.companyHours}>Mon–Fri 9am–6pm CST</Text>
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      )}

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState((prev) => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    safeArea: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 20,
      paddingVertical: 16,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: { padding: 8, marginLeft: -8 },
    headerTitle: { fontSize: 18, fontWeight: '600', color: colors.text },
    headerRight: { width: 40 },

    // Tabs
    tabs: {
      flexDirection: 'row',
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
      backgroundColor: colors.surface,
    },
    tab: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingVertical: 12,
      borderBottomWidth: 2,
      borderBottomColor: 'transparent',
    },
    tabActive: { borderBottomColor: colors.primary },
    tabText: { fontSize: 13, fontWeight: '600', color: colors.textDim },
    tabTextActive: { color: colors.primary },

    // FAQ
    faqList: { padding: 20, paddingBottom: 40 },
    searchRow: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      paddingHorizontal: 12,
      paddingVertical: 10,
      marginBottom: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    searchInput: { flex: 1, fontSize: 14, color: colors.text, paddingVertical: 0 },
    faqCard: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 16,
      marginBottom: 10,
    },
    faqHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
    faqQuestion: { flex: 1, fontSize: 15, fontWeight: '600', color: colors.text },
    faqAnswer: { fontSize: 14, color: colors.textSecondary, lineHeight: 21, marginTop: 12 },
    emptyState: { alignItems: 'center', paddingVertical: 40 },
    emptyTitle: {
      color: colors.text,
      fontSize: 16,
      fontWeight: '600',
      marginTop: 12,
    },
    emptySub: { color: colors.textDim, fontSize: 13, marginTop: 4 },
    stillNeedHelp: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      marginTop: 8,
      backgroundColor: `${colors.primary}10`,
      borderRadius: 14,
      padding: 16,
    },
    stillNeedHelpText: {
      flex: 1,
      fontSize: 14,
      fontWeight: '600',
      color: colors.primary,
    },
    companySection: {
      marginTop: 24,
      paddingTop: 16,
      paddingBottom: 24,
      alignItems: 'center',
    },
    companyName: {
      color: colors.textDim,
      fontSize: 13,
      fontWeight: '700',
      marginBottom: 6,
    },
    companyLine: {
      color: colors.textDim,
      fontSize: 11,
      marginTop: 2,
      textAlign: 'center',
    },

    // Coming-soon (AI disabled)
    comingSoon: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      padding: 32,
      gap: 8,
    },
    comingSoonIcon: {
      width: 64,
      height: 64,
      borderRadius: 32,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 8,
    },
    comingSoonTitle: { fontSize: 17, fontWeight: '700', color: colors.text },
    comingSoonSub: {
      fontSize: 14,
      color: colors.textDim,
      textAlign: 'center',
      lineHeight: 20,
    },
    comingSoonBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      marginTop: 12,
      paddingHorizontal: 16,
      paddingVertical: 10,
      borderRadius: 20,
      backgroundColor: `${colors.primary}15`,
    },
    comingSoonBtnText: { color: colors.primary, fontSize: 14, fontWeight: '600' },

    // Chat
    chatList: { padding: 16, paddingBottom: 8 },
    chatBubbleRow: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      marginBottom: 12,
      gap: 8,
    },
    chatBubbleRowUser: { flexDirection: 'row-reverse' },
    aiAvatar: {
      width: 28,
      height: 28,
      borderRadius: 14,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center',
      alignItems: 'center',
    },
    chatBubble: { maxWidth: '78%', borderRadius: 18, padding: 12, paddingHorizontal: 14 },
    chatBubbleAI: { backgroundColor: colors.surfaceLight },
    chatBubbleUser: { backgroundColor: colors.primary },
    chatBubbleText: { fontSize: 15, color: colors.text, lineHeight: 21 },
    chatBubbleTextUser: { color: '#FFF' },
    chatInputRow: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: 10,
      paddingHorizontal: 16,
      paddingVertical: 10,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    chatInput: {
      flex: 1,
      backgroundColor: colors.surfaceLight,
      borderRadius: 20,
      paddingHorizontal: 16,
      paddingVertical: 10,
      fontSize: 15,
      color: colors.text,
      maxHeight: 100,
      borderWidth: 1,
      borderColor: colors.border,
    },
    chatSendBtn: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
    },
    chatSendBtnDisabled: { opacity: 0.4 },
    chatDisclaimer: {
      fontSize: 11,
      color: colors.textDim,
      textAlign: 'center',
      paddingHorizontal: 20,
      paddingBottom: 8,
    },

    // Contact
    contactContent: { padding: 24 },
    label: { fontSize: 16, fontWeight: '500', color: colors.text, marginBottom: 12 },
    input: {
      backgroundColor: colors.surfaceLight,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      padding: 16,
      fontSize: 16,
      color: colors.text,
      minHeight: 160,
      marginBottom: 24,
    },
    submitButton: {
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 16,
      alignItems: 'center',
    },
    submitButtonDisabled: { opacity: 0.7 },
    submitButtonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
    contactQuickRow: {
      flexDirection: 'row',
      gap: 10,
      marginTop: 16,
      flexWrap: 'wrap',
    },
    contactChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      backgroundColor: `${colors.primary}15`,
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderRadius: 18,
      borderWidth: 1,
      borderColor: `${colors.primary}30`,
    },
    contactChipText: { color: colors.primary, fontSize: 13, fontWeight: '600' },
    companyCard: {
      marginTop: 24,
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 20,
    },
    companyTitle: {
      fontSize: 12,
      fontWeight: '700',
      color: colors.primary,
      letterSpacing: 0.5,
      marginBottom: 14,
    },
    companyRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
    companyText: { fontSize: 14, color: colors.textSecondary },
    companyHours: {
      fontSize: 12,
      color: colors.textDim,
      marginTop: 8,
      fontStyle: 'italic',
    },
  });
}
