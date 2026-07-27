/**
 * AI assistant — dedicated chat screen.
 *
 * Streaming bubbles from aiChatStore, quick-prompt chips, a transient tool
 * status line, action bubbles (support deep-link; the booking card lands in
 * M5.4), and a persistent disclaimer footer. All AI inference is
 * server-side (/ai/chat) — this screen only renders frames.
 *
 * Stays reachable during an active ride (the rider can ask "where's my
 * driver?" — the ride banner below mirrors live status), but leaving the
 * chat always lands on the screen that owns the ride, never home: both the
 * header back button and Android hardware back route through
 * activeRideRouteFor while a ride is active.
 */
import React, { useCallback, useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  BackHandler,
  FlatList,
  KeyboardAvoidingView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';

import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { AiChatMessage, LocationSuggestionCandidate } from '@shared/types/ai';
import { useAuthStore } from '@shared/store/authStore';
import BookingProposalCard from '../components/BookingProposalCard';
import { buildLocationSelectionMessage, buildQuoteBookingMessage } from '../components/bookingProposal';
import FareQuoteCard from '../components/FareQuoteCard';
import AiAuroraBackground from '../components/AiAuroraBackground';
import AiWelcomeOrb from '../components/AiWelcomeOrb';
import { useAiChatStore } from '../store/aiChatStore';
import { useRideStore } from '../store/rideStore';
import { showToast } from '../store/toastStore';
import { activeRideRouteFor } from '../utils/activeRideRoute';

// expo-speech-recognition is a native module: it exists only in binaries
// built after it was added (EAS dev-client / store builds). The guarded
// require keeps Expo Go and older installed builds working — they simply
// don't render the mic button.
let SpeechRecognition: any = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  SpeechRecognition = require('expo-speech-recognition').ExpoSpeechRecognitionModule;
} catch {
  SpeechRecognition = null;
}

const QUICK_PROMPTS = [
  "Where's my driver?",
  'Explain my last fare',
  "What's my wallet balance?",
  'Do I have any promos?',
  'Book me a ride home',
];

/**
 * Live ride status inside the chat — fed by rideStore via the global
 * useRiderSocket mount, zero AI involvement. Answers "searching… / driver
 * found" and offers the trip-share link + tracking deep-link.
 */
function RideStatusBanner({ colors, styles }: { colors: ThemeColors; styles: ReturnType<typeof createStyles> }) {
  const router = useRouter();
  const currentRide = useRideStore((s) => s.currentRide);
  const currentDriver = useRideStore((s) => s.currentDriver);

  const status = currentRide?.status ?? '';
  const trackRoute = activeRideRouteFor(status);
  if (!currentRide || !trackRoute) return null;

  let statusText = 'Trip in progress';
  if (status === 'searching') statusText = 'Searching for a driver…';
  else if (status === 'driver_assigned' || status === 'driver_accepted') {
    statusText = currentDriver
      ? `Driver found: ${currentDriver.name} — ${[currentDriver.vehicle_color, currentDriver.vehicle_make, currentDriver.vehicle_model].filter(Boolean).join(' ')}${currentDriver.license_plate ? `, plate ${currentDriver.license_plate}` : ''}`
      : 'Driver found — on the way!';
  } else if (status === 'driver_arrived') statusText = 'Your driver has arrived!';

  const handleShare = async () => {
    try {
      const res = await api.get<{ share_url?: string }>(`/rides/${currentRide.id}/share`);
      const url = res.data?.share_url;
      if (url) await Share.share({ message: `Follow my Spinr trip live: ${url}` });
    } catch (error) {
      console.error('[ai-assistant] share link failed:', error);
    }
  };

  return (
    <View style={styles.rideBanner}>
      <View style={styles.rideBannerTextRow}>
        {status === 'searching' ? (
          <ActivityIndicator size="small" color={colors.primary} />
        ) : (
          <Ionicons name="car" size={16} color={colors.primary} />
        )}
        <Text style={styles.rideBannerText} numberOfLines={2}>
          {statusText}
        </Text>
      </View>
      <View style={styles.rideBannerButtons}>
        {status !== 'searching' && (
          <TouchableOpacity style={styles.rideBannerButton} onPress={handleShare} accessibilityLabel="Share trip">
            <Ionicons name="share-outline" size={14} color={colors.primary} />
            <Text style={styles.rideBannerButtonText}>Share trip</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={styles.rideBannerButton}
          onPress={() =>
            router.replace({ pathname: trackRoute, params: { rideId: currentRide.id } } as never)
          }
          accessibilityLabel="Track ride"
        >
          <Ionicons name="navigate-outline" size={14} color={colors.primary} />
          <Text style={styles.rideBannerButtonText}>Track ride</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

function LocationSuggestionsCard({
  item,
  onSelect,
  colors,
  styles,
}: {
  item: AiChatMessage;
  onSelect: (candidate: LocationSuggestionCandidate) => void;
  colors: ThemeColors;
  styles: ReturnType<typeof createStyles>;
}) {
  if (item.action?.type !== 'location_suggestions') return null;
  const role = item.action.location_role;
  const title =
    role === 'pickup'
      ? 'Choose your pickup'
      : role === 'dropoff'
        ? 'Choose your dropoff'
        : 'Choose a location';

  return (
    <View style={styles.locationCard}>
      <View style={styles.locationHeader}>
        <Ionicons name="location-outline" size={17} color={colors.primary} />
        <Text style={styles.locationTitle}>{title}</Text>
      </View>
      {item.action.candidates.slice(0, 10).map((candidate, index) => {
        const primary = candidate.name || candidate.address || `Option ${index + 1}`;
        const secondary = candidate.name && candidate.address ? candidate.address : candidate.service_area;
        const routeSummary =
          candidate.driving_distance_km != null
            ? `${candidate.driving_distance_km.toFixed(1)} km by road${
                candidate.driving_duration_minutes != null ? ` · about ${candidate.driving_duration_minutes} min` : ''
              }`
            : null;
        return (
          <TouchableOpacity
            key={`${candidate.lat}:${candidate.lng}:${index}`}
            style={styles.locationOption}
            onPress={() => onSelect(candidate)}
            accessibilityRole="button"
            accessibilityLabel={`Use ${primary}`}
          >
            <View style={styles.locationPin}>
              <Text style={styles.locationPinText}>{index + 1}</Text>
            </View>
            <View style={styles.locationCopy}>
              <Text style={styles.locationPrimary} numberOfLines={1}>
                {primary}
              </Text>
              {secondary ? (
                <Text style={styles.locationSecondary} numberOfLines={2}>
                  {secondary}
                </Text>
              ) : null}
              {routeSummary ? (
                <Text style={styles.locationRoute} numberOfLines={1}>
                  {index === 0 ? `Closest · ${routeSummary}` : routeSummary}
                </Text>
              ) : null}
            </View>
            <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

export default function AiAssistantScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = createStyles(colors);
  const listRef = useRef<FlatList>(null);

  const firstName = useAuthStore((s) => s.user?.first_name);

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
  const [isListening, setIsListening] = React.useState(false);

  // Voice input: partial transcripts stream into the input field while
  // listening; recognition ends itself on silence (continuous: false), so
  // the rider reviews the text and taps send — voice never auto-sends.
  useEffect(() => {
    if (!SpeechRecognition) return;
    const subs = [
      SpeechRecognition.addListener('result', (e: any) => {
        const transcript = e?.results?.[0]?.transcript;
        if (typeof transcript === 'string') setInput(transcript);
      }),
      SpeechRecognition.addListener('end', () => setIsListening(false)),
      SpeechRecognition.addListener('error', (e: any) => {
        setIsListening(false);
        // 'aborted' is the rider tapping stop; 'no-speech' is silence —
        // neither is a failure worth a toast.
        if (e?.error && e.error !== 'aborted' && e.error !== 'no-speech') {
          showToast('Voice input failed', 'Could not capture audio — please try again or type instead.', 'warning');
        }
      }),
    ];
    return () => subs.forEach((s) => s?.remove?.());
  }, []);

  const handleMicPress = useCallback(async () => {
    if (!SpeechRecognition) return;
    if (isListening) {
      SpeechRecognition.stop();
      return;
    }
    try {
      const perms = await SpeechRecognition.requestPermissionsAsync();
      if (!perms?.granted) {
        showToast('Microphone needed', 'Allow microphone access in Settings to use voice input.', 'warning');
        return;
      }
      setIsListening(true);
      SpeechRecognition.start({ lang: 'en-CA', interimResults: true, continuous: false });
    } catch {
      setIsListening(false);
      showToast('Voice input failed', 'Could not start voice capture — please type instead.', 'warning');
    }
  }, [isListening]);

  // Leaving the chat during an active ride lands on the screen that owns
  // the ride — never home. replace (not back/pop) so the chat doesn't
  // linger under the ride flow.
  const handleBack = useCallback(() => {
    const { currentRide: ride } = useRideStore.getState();
    const pathname = activeRideRouteFor(ride?.status);
    if (pathname && ride?.id) {
      router.replace({ pathname, params: { rideId: ride.id } } as never);
      return true;
    }
    return false;
  }, [router]);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', handleBack);
    return () => sub.remove();
  }, [handleBack]);

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
    if (item.kind === 'map_picker' && item.action?.type === 'open_map_picker') {
      const picker = item.action;
      const hasApprox = typeof picker.approx_lat === 'number' && typeof picker.approx_lng === 'number';
      return (
        <TouchableOpacity
          style={styles.actionCard}
          onPress={() =>
            router.push({
              pathname: '/pick-on-map',
              params: {
                field: picker.location_role,
                ai: '1',
                ...(hasApprox
                  ? { aiLat: String(picker.approx_lat), aiLng: String(picker.approx_lng) }
                  : {}),
              },
            } as never)
          }
          accessibilityRole="button"
          accessibilityLabel={`Drop a pin for your ${picker.location_role}`}
          accessibilityHint="Opens a map — place the pin at the exact spot and confirm"
        >
          <Ionicons name="location-outline" size={18} color={colors.primary} />
          <Text style={styles.actionCardText} numberOfLines={2}>
            {picker.label ? `Drop a pin — ${picker.label}` : `Drop a pin for your ${picker.location_role}`}
          </Text>
          <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
        </TouchableOpacity>
      );
    }
    if (item.kind === 'booking_proposal' && item.action?.type === 'booking_proposal') {
      return <BookingProposalCard proposal={item.action.proposal} />;
    }
    if (item.kind === 'fare_quote' && item.action?.type === 'fare_quote') {
      const quote = item.action;
      return (
        <FareQuoteCard
          quote={quote}
          onSelect={(option) => {
            // Self-contained message carrying the priced [lat,lng] verbatim —
            // the assistant's next turn sees only message text, and must not
            // re-geocode the trip the rider just saw priced.
            handleSend(buildQuoteBookingMessage(quote, option));
          }}
        />
      );
    }
    if (item.kind === 'location_suggestions' && item.action?.type === 'location_suggestions') {
      return (
        <LocationSuggestionsCard
          item={item}
          colors={colors}
          styles={styles}
          onSelect={(candidate) => {
            const role = item.action?.type === 'location_suggestions' ? item.action.location_role : null;
            const message = buildLocationSelectionMessage(candidate, role);
            if (message) handleSend(message);
          }}
        />
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
      {/* Ambient gradient lives on the idle welcome screen; the moment the
          rider starts typing (or a conversation exists) it fades to plain
          white, mirroring Gemini's home → chat transition. */}
      <AiAuroraBackground visible={showWelcome && input.length === 0} />
      <View style={styles.header}>
        <TouchableOpacity
          onPress={() => {
            if (!handleBack()) router.back();
          }}
          style={styles.headerButton}
          accessibilityLabel="Back"
        >
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
        behavior="padding"
      >
        {showWelcome ? (
          <View style={styles.welcomeWrap}>
            <AiWelcomeOrb />
            <Text style={styles.welcomeTitle}>
              {firstName ? `Hi ${firstName}, let's get going` : "Hi! Let's get going"}
            </Text>
            <Text style={styles.welcomeSubtitle}>
              Ask about your rides, fares, wallet or promos — or ask me to get you a ride quote.
            </Text>
            <View style={styles.chipsWrap}>
              {QUICK_PROMPTS.map((prompt) => (
                <TouchableOpacity
                  key={prompt}
                  style={styles.chip}
                  onPress={() => handleSend(prompt)}
                  activeOpacity={0.7}
                >
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

        <RideStatusBanner colors={colors} styles={styles} />

        {toolStatus ? (
          <View style={styles.toolStatusRow}>
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={styles.toolStatusText}>{toolStatus}</Text>
          </View>
        ) : null}

        <View style={styles.inputRow}>
          <View style={styles.inputPill}>
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
              returnKeyType="send"
              blurOnSubmit={false}
            />
            {SpeechRecognition && !isStreaming ? (
              <TouchableOpacity
                style={[styles.micButton, isListening && styles.micButtonActive]}
                onPress={handleMicPress}
                accessibilityLabel={isListening ? 'Stop voice input' : 'Start voice input'}
                activeOpacity={0.7}
              >
                <Ionicons
                  name={isListening ? 'mic' : 'mic-outline'}
                  size={20}
                  color={isListening ? '#fff' : colors.textDim}
                />
              </TouchableOpacity>
            ) : null}
            {isStreaming ? (
              <TouchableOpacity style={styles.stopButton} onPress={stopStreaming} accessibilityLabel="Stop">
                <Ionicons name="stop" size={18} color="#fff" />
              </TouchableOpacity>
            ) : (
              <TouchableOpacity
                onPress={() => handleSend()}
                disabled={!input.trim()}
                accessibilityLabel="Send"
                activeOpacity={0.8}
              >
                <LinearGradient
                  colors={
                    input.trim()
                      ? [colors.primary, colors.orange]
                      : [colors.border, colors.border]
                  }
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 1 }}
                  style={styles.sendButton}
                >
                  <Ionicons name="arrow-up" size={20} color="#fff" />
                </LinearGradient>
              </TouchableOpacity>
            )}
          </View>
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
    // Transparent so the animated aurora (rendered behind) shows through.
    container: { flex: 1, backgroundColor: colors.background },
    flex: { flex: 1 },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 8,
      paddingVertical: 10,
    },
    headerButton: { padding: 8 },
    headerTitleWrap: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    headerTitle: { fontSize: 17, fontWeight: '600', color: colors.text },
    welcomeWrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
    welcomeTitle: {
      fontSize: 27,
      fontWeight: '600',
      color: colors.text,
      marginTop: 8,
      marginBottom: 10,
      textAlign: 'center',
      letterSpacing: -0.3,
    },
    welcomeSubtitle: {
      fontSize: 14,
      color: colors.textDim,
      textAlign: 'center',
      marginBottom: 28,
      lineHeight: 20,
      maxWidth: 300,
    },
    chipsWrap: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 8 },
    chip: {
      paddingHorizontal: 15,
      paddingVertical: 10,
      borderRadius: 20,
      backgroundColor: colors.surface + 'E6',
      borderWidth: 1,
      borderColor: colors.border,
    },
    chipText: { fontSize: 13, fontWeight: '500', color: colors.text },
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
    bubble: { maxWidth: '80%', borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10 },
    bubbleAssistant: {
      backgroundColor: colors.surface + 'F2',
      borderBottomLeftRadius: 5,
      borderWidth: 1,
      borderColor: colors.border + '99',
    },
    bubbleUser: { backgroundColor: colors.primary, borderBottomRightRadius: 5 },
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
    locationCard: {
      marginLeft: 34,
      padding: 12,
      borderRadius: 12,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      gap: 8,
      alignSelf: 'stretch',
    },
    locationHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    locationTitle: { fontSize: 14, fontWeight: '700', color: colors.text },
    locationOption: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 9,
      paddingHorizontal: 8,
      borderRadius: 10,
      backgroundColor: colors.surfaceLight,
    },
    locationPin: {
      width: 22,
      height: 22,
      borderRadius: 11,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.primary,
    },
    locationPinText: { fontSize: 12, fontWeight: '700', color: '#fff' },
    locationCopy: { flex: 1 },
    locationPrimary: { fontSize: 14, fontWeight: '700', color: colors.text },
    locationSecondary: { fontSize: 12, color: colors.textDim, lineHeight: 16 },
    locationRoute: { fontSize: 12, color: colors.primary, fontWeight: '600', marginTop: 3 },
    rideBanner: {
      marginHorizontal: 12,
      marginTop: 4,
      padding: 12,
      borderRadius: 12,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1,
      borderColor: colors.border,
      gap: 8,
    },
    rideBannerTextRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    rideBannerText: { flex: 1, fontSize: 13, fontWeight: '600', color: colors.text },
    rideBannerButtons: { flexDirection: 'row', gap: 14, paddingLeft: 24 },
    rideBannerButton: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    rideBannerButtonText: { fontSize: 13, fontWeight: '600', color: colors.primary },
    toolStatusRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      paddingHorizontal: 20,
      paddingVertical: 6,
    },
    toolStatusText: { fontSize: 13, color: colors.textDim, fontStyle: 'italic' },
    // Floating pill input — a single rounded surface holding the text field
    // and the gradient send button, lifted off the gradient with a shadow.
    inputRow: {
      paddingHorizontal: 14,
      paddingVertical: 10,
    },
    inputPill: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: 8,
      backgroundColor: colors.surface + 'F2',
      borderRadius: 26,
      paddingLeft: 18,
      paddingRight: 6,
      paddingVertical: 6,
      borderWidth: 1,
      borderColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.12,
      shadowRadius: 12,
      elevation: 4,
    },
    input: {
      flex: 1,
      fontSize: 15,
      color: colors.text,
      maxHeight: 100,
      paddingVertical: 8,
      paddingRight: 4,
    },
    sendButton: {
      width: 38,
      height: 38,
      borderRadius: 19,
      justifyContent: 'center',
      alignItems: 'center',
    },
    micButton: {
      width: 36,
      height: 36,
      borderRadius: 18,
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 4,
    },
    micButtonActive: {
      backgroundColor: '#EF4444',
    },
    stopButton: {
      width: 38,
      height: 38,
      borderRadius: 19,
      backgroundColor: colors.text,
      justifyContent: 'center',
      alignItems: 'center',
    },
    disclaimer: {
      fontSize: 11,
      color: colors.textDim,
      textAlign: 'center',
      paddingHorizontal: 20,
      paddingBottom: 8,
    },
  });
