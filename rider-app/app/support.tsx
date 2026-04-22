import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';
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
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SpinrConfig } from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAuthStore } from '@shared/store/authStore';
import api from '@shared/api/client';

// ── Static FAQ data ───────────────────────────────────────────────────────────
const FAQS = [
  {
    q: 'How do I dispute a charge?',
    a: 'Go to Activity → tap the ride → select "Dispute Charge". Our team reviews disputes within 24 hours. You can also contact support@spinr.ca directly.',
  },
  {
    q: 'What is the cancellation policy?',
    a: 'You can cancel for free within 2 minutes of driver acceptance. After that, a small fee ($3–$5) applies to compensate the driver for their time.',
  },
  {
    q: 'How do I add or change a payment method?',
    a: 'Go to Settings → Payment Methods → Add Card. You can also manage cards from your Wallet screen.',
  },
  {
    q: 'My driver never showed up — what do I do?',
    a: 'If the driver marked the trip as a no-show, you will not be charged. Contact support with your ride code (shown on the receipt, e.g. SPR-XXXXXX) and we will investigate.',
  },
  {
    q: 'How does Spinr Wallet work?',
    a: 'Wallet lets you pre-load funds (top up via card) and pay for rides instantly without entering card details each time. Transfers to other riders are also supported.',
  },
  {
    q: 'How do I earn and redeem loyalty points?',
    a: 'You earn points on every completed ride. Points can be redeemed for ride credits in the Rewards screen. Tier multipliers (Bronze → Gold → Platinum) increase your earning rate.',
  },
  {
    q: 'Is Spinr available outside Saskatoon?',
    a: 'Spinr currently operates in Saskatoon and Regina. Expansion to other Saskatchewan cities is planned for 2025.',
  },
  {
    q: 'How do I schedule a ride in advance?',
    a: 'On the ride options screen, toggle "Schedule later" and pick your date and time. Rides can be scheduled up to 7 days ahead. You\'ll receive a push reminder 15 minutes before.',
  },
];

// ── Claude-powered chat ───────────────────────────────────────────────────────
interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
}

const SYSTEM_PROMPT = `You are Spinr's friendly AI support assistant for riders. Spinr is a ride-hailing app operating in Saskatoon and Regina, Saskatchewan, Canada.

Key facts:
- Spinr charges 0% commission to drivers (unique value proposition)
- Cancellation is free within 2 minutes of driver acceptance
- The app supports loyalty points, wallet top-ups, fare splitting, promo codes, scheduled rides, and carpool
- Support email: support@spinr.ca
- Support hours: Mon–Fri 9am–6pm CST

Answer rider questions concisely and helpfully. For sensitive account-specific issues (billing disputes, account bans), direct them to the human support form or support@spinr.ca. Keep responses under 150 words.`;

async function askClaude(messages: ChatMessage[]): Promise<string> {
  const apiMessages = messages.map(m => ({
    role: m.role,
    content: m.content,
  }));

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': process.env.EXPO_PUBLIC_ANTHROPIC_API_KEY ?? '',
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 300,
      system: SYSTEM_PROMPT,
      messages: apiMessages,
    }),
  });

  if (!res.ok) {
    throw new Error(`API error ${res.status}`);
  }

  const data = await res.json();
  return data.content?.[0]?.text ?? "I'm sorry, I couldn't process that. Please try again or contact support@spinr.ca.";
}

// ── Component ─────────────────────────────────────────────────────────────────
type Tab = 'faq' | 'chat' | 'contact';

export default function SupportScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { user } = useAuthStore();

  const [activeTab, setActiveTab] = useState<Tab>('faq');
  const [expandedFaq, setExpandedFaq] = useState<number | null>(null);
  // Public company info — surfaced at the bottom of both tabs so
  // riders always see the phone/email/address for live support
  // reachable from anywhere in the app.
  const [companyInfo, setCompanyInfo] = useState<{
    name?: string; address?: string; phone?: string; email?: string; website?: string;
  }>({});
  useEffect(() => {
    api.get('/company-info').then(r => setCompanyInfo(r?.data || {})).catch(() => {});
  }, []);

  // Contact form
  const [issue, setIssue] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Chat
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: '0',
      role: 'assistant',
      content: "Hi! I'm Spinr's AI assistant. Ask me anything about your rides, payments, account, or how the app works.",
      timestamp: new Date(),
    },
  ]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const chatListRef = useRef<FlatList>(null);

  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const handleSubmitTicket = async () => {
    if (!issue.trim()) {
      setAlertState({ visible: true, title: 'Error', message: 'Please describe your issue.', variant: 'warning' });
      return;
    }
    setSubmitting(true);
    try {
      await fetch(`${SpinrConfig.backendUrl}/support/tickets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject: 'App Support Request', message: issue, category: 'general' }),
      });
      setAlertState({
        visible: true, title: 'Request Submitted',
        message: 'Our team will respond within 24 hours.',
        variant: 'success',
        buttons: [{ text: 'OK', onPress: () => router.back() }],
      });
    } catch {
      setAlertState({ visible: true, title: 'Error', message: 'Failed to submit. Please try again.', variant: 'danger' });
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

    const updated = [...chatMessages, userMsg];
    setChatMessages(updated);
    setChatInput('');
    setChatLoading(true);

    setTimeout(() => chatListRef.current?.scrollToEnd({ animated: true }), 100);

    try {
      const reply = await askClaude(updated);
      setChatMessages(prev => [
        ...prev,
        { id: Date.now().toString(), role: 'assistant', content: reply, timestamp: new Date() },
      ]);
    } catch {
      setChatMessages(prev => [
        ...prev,
        {
          id: Date.now().toString(), role: 'assistant',
          content: "I'm having trouble connecting right now. Please try again or contact support@spinr.ca.",
          timestamp: new Date(),
        },
      ]);
    } finally {
      setChatLoading(false);
      setTimeout(() => chatListRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [chatInput, chatMessages, chatLoading]);

  const renderChatMessage = ({ item }: { item: ChatMessage }) => {
    const isUser = item.role === 'user';
    return (
      <View style={[styles.chatBubbleRow, isUser && styles.chatBubbleRowUser]}>
        {!isUser && (
          <View style={styles.aiAvatar}>
            <Ionicons name="sparkles" size={14} color={colors.primary} />
          </View>
        )}
        <View style={[styles.chatBubble, isUser ? styles.chatBubbleUser : styles.chatBubbleAI]}>
          <Text style={[styles.chatBubbleText, isUser && styles.chatBubbleTextUser]}>{item.content}</Text>
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
        {([
          { key: 'faq', label: 'FAQ', icon: 'help-circle-outline' },
          { key: 'chat', label: 'AI Chat', icon: 'sparkles-outline' },
          { key: 'contact', label: 'Contact', icon: 'mail-outline' },
        ] as { key: Tab; label: string; icon: string }[]).map(tab => (
          <TouchableOpacity
            key={tab.key}
            style={[styles.tab, activeTab === tab.key && styles.tabActive]}
            onPress={() => setActiveTab(tab.key)}
          >
            <Ionicons name={tab.icon as any} size={16} color={activeTab === tab.key ? colors.primary : colors.textDim} />
            <Text style={[styles.tabText, activeTab === tab.key && styles.tabTextActive]}>{tab.label}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* FAQ Tab */}
      {activeTab === 'faq' && (
        <ScrollView contentContainerStyle={styles.faqList} showsVerticalScrollIndicator={false}>
          {FAQS.map((faq, idx) => (
            <TouchableOpacity
              key={idx}
              style={styles.faqCard}
              onPress={() => setExpandedFaq(expandedFaq === idx ? null : idx)}
              activeOpacity={0.7}
            >
              <View style={styles.faqHeader}>
                <Text style={styles.faqQuestion} numberOfLines={expandedFaq === idx ? undefined : 2}>{faq.q}</Text>
                <Ionicons
                  name={expandedFaq === idx ? 'chevron-up' : 'chevron-down'}
                  size={18}
                  color={colors.textDim}
                />
              </View>
              {expandedFaq === idx && (
                <Text style={styles.faqAnswer}>{faq.a}</Text>
              )}
            </TouchableOpacity>
          ))}
          <TouchableOpacity style={styles.stillNeedHelp} onPress={() => setActiveTab('chat')}>
            <Ionicons name="sparkles" size={18} color={colors.primary} />
            <Text style={styles.stillNeedHelpText}>Still need help? Ask our AI assistant</Text>
            <Ionicons name="chevron-forward" size={16} color={colors.primary} />
          </TouchableOpacity>

          {(companyInfo.address || companyInfo.phone || companyInfo.email || companyInfo.website) && (
            <View style={styles.companySection}>
              <Text style={styles.companyName}>{companyInfo.name || 'Spinr'}</Text>
              {!!companyInfo.address && <Text style={styles.companyLine}>{companyInfo.address}</Text>}
              {!!companyInfo.phone && <Text style={styles.companyLine}>{companyInfo.phone}</Text>}
              {!!companyInfo.email && <Text style={styles.companyLine}>{companyInfo.email}</Text>}
              {!!companyInfo.website && <Text style={styles.companyLine}>{companyInfo.website}</Text>}
            </View>
          )}
        </ScrollView>
      )}

      {/* AI Chat Tab */}
      {activeTab === 'chat' && (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
          <FlatList
            ref={chatListRef}
            data={chatMessages}
            keyExtractor={item => item.id}
            renderItem={renderChatMessage}
            contentContainerStyle={styles.chatList}
            onContentSizeChange={() => chatListRef.current?.scrollToEnd({ animated: false })}
            showsVerticalScrollIndicator={false}
            ListFooterComponent={chatLoading ? (
              <View style={styles.chatBubbleRow}>
                <View style={styles.aiAvatar}>
                  <Ionicons name="sparkles" size={14} color={colors.primary} />
                </View>
                <View style={styles.chatBubbleAI}>
                  <ActivityIndicator size="small" color={colors.primary} />
                </View>
              </View>
            ) : null}
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
              style={[styles.chatSendBtn, (!chatInput.trim() || chatLoading) && styles.chatSendBtnDisabled]}
              onPress={handleSendChat}
              disabled={!chatInput.trim() || chatLoading}
            >
              <Ionicons name="send" size={20} color="#FFF" />
            </TouchableOpacity>
          </View>
          <Text style={styles.chatDisclaimer}>AI responses are for guidance only. For account-specific issues, use Contact.</Text>
        </KeyboardAvoidingView>
      )}

      {/* Contact Tab */}
      {activeTab === 'contact' && (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
          <ScrollView contentContainerStyle={styles.contactContent} keyboardShouldPersistTaps="handled">
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

            <View style={styles.companyCard}>
              <Text style={styles.companyTitle}>SPINR TECHNOLOGIES INC.</Text>
              {[
                { icon: 'location-outline', text: 'Saskatoon, SK, Canada' },
                { icon: 'mail-outline',     text: 'support@spinr.ca' },
                { icon: 'call-outline',     text: '+1 (306) 555-0199' },
                { icon: 'globe-outline',    text: 'www.spinr.ca' },
              ].map(row => (
                <View key={row.icon} style={styles.companyRow}>
                  <Ionicons name={row.icon as any} size={16} color={colors.textDim} />
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
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    safeArea: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 20, paddingVertical: 16,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backButton: { padding: 8, marginLeft: -8 },
    headerTitle: { fontSize: 18, fontWeight: '600', color: colors.text },
    headerRight: { width: 40 },

    // Tabs
    tabs: {
      flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.border,
      backgroundColor: colors.surface,
    },
    tab: {
      flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 6, paddingVertical: 12,
      borderBottomWidth: 2, borderBottomColor: 'transparent',
    },
    tabActive: { borderBottomColor: colors.primary },
    tabText: { fontSize: 13, fontWeight: '600', color: colors.textDim },
    tabTextActive: { color: colors.primary },

    // FAQ
    faqList: { padding: 20, paddingBottom: 40 },
    faqCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 16, marginBottom: 10,
    },
    faqHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
    faqQuestion: { flex: 1, fontSize: 15, fontWeight: '600', color: colors.text },
    faqAnswer: {
      fontSize: 14, color: colors.textSecondary, lineHeight: 21, marginTop: 12,
    },
    stillNeedHelp: {
      flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 8,
      backgroundColor: `${colors.primary}10`, borderRadius: 14, padding: 16,
    },
    stillNeedHelpText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.primary },
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

    // Chat
    chatList: { padding: 16, paddingBottom: 8 },
    chatBubbleRow: { flexDirection: 'row', alignItems: 'flex-end', marginBottom: 12, gap: 8 },
    chatBubbleRowUser: { flexDirection: 'row-reverse' },
    aiAvatar: {
      width: 28, height: 28, borderRadius: 14,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center', alignItems: 'center',
    },
    chatBubble: { maxWidth: '78%', borderRadius: 18, padding: 12, paddingHorizontal: 14 },
    chatBubbleAI: { backgroundColor: colors.surfaceLight },
    chatBubbleUser: { backgroundColor: colors.primary },
    chatBubbleText: { fontSize: 15, color: colors.text, lineHeight: 21 },
    chatBubbleTextUser: { color: '#FFF' },
    chatInputRow: {
      flexDirection: 'row', alignItems: 'flex-end', gap: 10,
      paddingHorizontal: 16, paddingVertical: 10,
      borderTopWidth: 1, borderTopColor: colors.border,
    },
    chatInput: {
      flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 20, paddingHorizontal: 16,
      paddingVertical: 10, fontSize: 15, color: colors.text, maxHeight: 100,
      borderWidth: 1, borderColor: colors.border,
    },
    chatSendBtn: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primary,
      justifyContent: 'center', alignItems: 'center',
    },
    chatSendBtnDisabled: { opacity: 0.4 },
    chatDisclaimer: {
      fontSize: 11, color: colors.textDim, textAlign: 'center',
      paddingHorizontal: 20, paddingBottom: 8,
    },

    // Contact
    contactContent: { padding: 24 },
    label: { fontSize: 16, fontWeight: '500', color: colors.text, marginBottom: 12 },
    input: {
      backgroundColor: colors.surfaceLight, borderWidth: 1, borderColor: colors.border,
      borderRadius: 12, padding: 16, fontSize: 16, color: colors.text,
      minHeight: 160, marginBottom: 24,
    },
    submitButton: {
      backgroundColor: colors.primary, borderRadius: 12, paddingVertical: 16, alignItems: 'center',
    },
    submitButtonDisabled: { opacity: 0.7 },
    submitButtonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
    companyCard: { marginTop: 32, backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 20 },
    companyTitle: {
      fontSize: 12, fontWeight: '700', color: colors.primary, letterSpacing: 0.5, marginBottom: 14,
    },
    companyRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
    companyText: { fontSize: 14, color: colors.textSecondary },
    companyHours: { fontSize: 12, color: colors.textDim, marginTop: 8, fontStyle: 'italic' },
  });
}
