/**
 * AI assistant chat state (zustand).
 *
 * Owns the message list for the ai-assistant screen: optimistic user
 * bubbles, an assistant bubble that grows per `token` frame, transient tool
 * status ("Checking your ride…"), action bubbles (booking card / support
 * deep-link), and abort/error rollback. The server owns conversation
 * history; only the conversation id is persisted locally (AsyncStorage) so
 * reopening the screen rehydrates from GET /ai/conversations/{id}/messages.
 */
import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import api from '@shared/api/client';
import type { AiAction, AiChatMessage, AiSseEvent } from '@shared/types/ai';
import { streamChat } from '../utils/aiChat';

const CONVERSATION_KEY = 'spinr_ai_conversation_id';

/** Friendly status line per tool while it runs. */
const TOOL_STATUS: Record<string, string> = {
  get_active_ride: 'Checking your ride…',
  get_ride_history: 'Looking up your trips…',
  get_ride_details: 'Looking up that trip…',
  get_ride_receipt: 'Pulling up the receipt…',
  get_wallet_balance: 'Checking your wallet…',
  get_wallet_transactions: 'Checking your wallet…',
  get_available_promos: 'Checking your promos…',
  get_service_info: 'Checking service info…',
  explain_fare_rates: 'Looking up fare rates…',
  get_saved_places: 'Checking your saved places…',
  search_faqs: 'Searching the help centre…',
  get_company_info: 'Getting contact info…',
  find_place: 'Finding that place…',
  get_rider_location: 'Finding your location…',
  get_fare_quote: 'Getting exact prices…',
  propose_ride_booking: 'Preparing your booking…',
  escalate_to_support: 'Preparing a support handoff…',
};

let nextId = 0;
const newId = () => `local-${Date.now()}-${nextId++}`;

/** Reject cached fixes older than this — a stale position would send "my
 * location" pickups to the wrong area; the backend then falls back to the
 * rider's last ride pickup instead. */
const LOCATION_MAX_AGE_MS = 5 * 60 * 1000;

/** Last-known device position, only when permission is already granted —
 * the chat never triggers a permission prompt. Null on any failure or when
 * the only available fix is older than LOCATION_MAX_AGE_MS. */
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

const ERROR_MESSAGES: Record<string, string> = {
  ai_disabled: 'The AI assistant is currently unavailable.',
  daily_cap: "You've reached today's AI assistant limit — try again tomorrow.",
  not_authenticated: 'Please sign in again to use the AI assistant.',
  default: "I'm having trouble right now — please try again in a moment.",
};

interface AiChatState {
  messages: AiChatMessage[];
  conversationId: string | null;
  isStreaming: boolean;
  toolStatus: string | null;
  enabled: boolean;
  /** How to present the AI entry point while disabled: 'coming_soon' (show a
   * "coming soon" hint) or 'hidden' (don't render the icon at all). 'enabled'
   * while the assistant is on. */
  mode: 'enabled' | 'coming_soon' | 'hidden';
  disclaimer: string;
  abortController: AbortController | null;

  loadConfig: () => Promise<void>;
  loadHistory: () => Promise<void>;
  sendMessage: (text: string) => Promise<void>;
  stopStreaming: () => void;
  startNewConversation: () => Promise<void>;
}

export const useAiChatStore = create<AiChatState>((set, get) => ({
  messages: [],
  conversationId: null,
  isStreaming: false,
  toolStatus: null,
  enabled: false,
  mode: 'coming_soon',
  disclaimer: '',
  abortController: null,

  loadConfig: async () => {
    try {
      const res = await api.get<{ enabled?: boolean; mode?: string; disclaimer?: string }>('/ai/config');
      const enabled = !!res.data?.enabled;
      const mode = (res.data?.mode as AiChatState['mode']) ?? (enabled ? 'enabled' : 'coming_soon');
      set({ enabled, mode, disclaimer: res.data?.disclaimer ?? '' });
    } catch {
      // Config failure hides the AI entry points (safe default).
      set({ enabled: false, mode: 'hidden' });
    }
  },

  loadHistory: async () => {
    try {
      const stored = await AsyncStorage.getItem(CONVERSATION_KEY);
      if (!stored) return;
      const res = await api.get<{
        messages?: { id: string; role: 'user' | 'assistant'; content: string; created_at: string }[];
      }>(`/ai/conversations/${stored}/messages`);
      const messages: AiChatMessage[] = (res.data?.messages ?? []).map(
        (m: { id: string; role: 'user' | 'assistant'; content: string; created_at: string }) => ({
          id: m.id,
          role: m.role,
          kind: 'text' as const,
          content: m.content,
          createdAt: Date.parse(m.created_at) || Date.now(),
        }),
      );
      set({ conversationId: stored, messages });
    } catch {
      // 404 = purged/foreign conversation — start fresh rather than error.
      await AsyncStorage.removeItem(CONVERSATION_KEY).catch(() => undefined);
      set({ conversationId: null, messages: [] });
    }
  },

  sendMessage: async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || get().isStreaming) return;

    const userMessage: AiChatMessage = {
      id: newId(),
      role: 'user',
      kind: 'text',
      content: trimmed,
      createdAt: Date.now(),
    };
    const assistantId = newId();
    const abortController = new AbortController();
    set((state) => ({
      messages: [
        ...state.messages,
        userMessage,
        { id: assistantId, role: 'assistant', kind: 'text', content: '', createdAt: Date.now() },
      ],
      isStreaming: true,
      toolStatus: null,
      abortController,
    }));

    const appendToAssistant = (text: string) =>
      set((state) => ({
        messages: state.messages.map((m) =>
          m.id === assistantId ? { ...m, content: m.content + text } : m,
        ),
      }));

    const onEvent = (event: AiSseEvent) => {
      switch (event.event) {
        case 'meta':
          set({ conversationId: event.data.conversation_id });
          AsyncStorage.setItem(CONVERSATION_KEY, event.data.conversation_id).catch(() => undefined);
          break;
        case 'token':
          appendToAssistant(event.data.text);
          set({ toolStatus: null });
          break;
        case 'tool':
          set({
            toolStatus:
              event.data.status === 'start'
                ? (TOOL_STATUS[event.data.name] ?? 'Working on it…')
                : null,
          });
          break;
        case 'action': {
          const action = event.data as AiAction;
          set((state) => ({
            messages: [
              ...state.messages,
              {
                id: newId(),
                role: 'assistant',
                kind:
                  action.type === 'booking_proposal'
                    ? 'booking_proposal'
                    : action.type === 'location_suggestions'
                      ? 'location_suggestions'
                      : action.type === 'fare_quote'
                        ? 'fare_quote'
                        : 'support_action',
                content: '',
                action,
                createdAt: Date.now(),
              },
            ],
          }));
          break;
        }
        case 'error':
          appendToAssistant(ERROR_MESSAGES[event.data.code] ?? event.data.message ?? ERROR_MESSAGES.default);
          break;
        case 'done':
          break;
      }
    };

    try {
      await streamChat({
        message: trimmed,
        conversationId: get().conversationId,
        // Main-screen assistant is always the rider persona — dual-role
        // accounts must keep booking tools here (help-centre chat stays on
        // the backend-inferred persona for driver grievances).
        audience: 'rider',
        location: await deviceLocation(),
        onEvent,
        signal: abortController.signal,
      });
    } catch (error: unknown) {
      // error.message is an internal ERROR_MESSAGES lookup key here, never
      // rendered to the user directly — safe to read raw.
      // eslint-disable-next-line no-restricted-syntax
      const code = error instanceof Error ? error.message : 'default';
      appendToAssistant(ERROR_MESSAGES[code] ?? ERROR_MESSAGES.default);
    } finally {
      // Drop the assistant bubble if nothing ever arrived for it.
      set((state) => ({
        isStreaming: false,
        toolStatus: null,
        abortController: null,
        messages: state.messages.filter((m) => !(m.id === assistantId && m.kind === 'text' && !m.content)),
      }));
    }
  },

  stopStreaming: () => {
    get().abortController?.abort();
    set({ isStreaming: false, toolStatus: null, abortController: null });
  },

  startNewConversation: async () => {
    get().abortController?.abort();
    await AsyncStorage.removeItem(CONVERSATION_KEY).catch(() => undefined);
    set({
      messages: [],
      conversationId: null,
      isStreaming: false,
      toolStatus: null,
      abortController: null,
    });
  },
}));
