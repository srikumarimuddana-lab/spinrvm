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
  request_map_pin: 'Setting up the map…',
  propose_ride_booking: 'Preparing your booking…',
  escalate_to_support: 'Preparing a support handoff…',
};

let nextId = 0;
const newId = () => `local-${Date.now()}-${nextId++}`;

/** Reject cached fixes older than this — a stale position would send "my
 * location" pickups to the wrong area; the backend then falls back to the
 * rider's last ride pickup instead. */
const LOCATION_MAX_AGE_MS = 5 * 60 * 1000;

/** Cap on waiting for a fresh GPS fix — a cold GPS must not stall the first
 * chat token; past this we fall back to the OS-cached position. */
const CURRENT_POSITION_TIMEOUT_MS = 4000;

/** Device position for the chat, only when permission is already granted —
 * the chat never triggers a permission prompt. Prefers a FRESH fix
 * (getCurrentPositionAsync, Balanced — the manual booking flow's accuracy)
 * over the OS's last-known cache: the cached fix can be a coarse wifi/cell
 * position from any app and drifting it between messages moved the
 * assistant's pickup pin blocks away from the rider. Falls back to the
 * cached fix on timeout/failure; null only when both fail.
 * Exported for tests. */
export async function deviceLocation(): Promise<{ lat: number; lng: number } | null> {
  try {
    const { granted } = await Location.getForegroundPermissionsAsync();
    if (!granted) return null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fresh = await Promise.race([
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }).catch(() => null),
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), CURRENT_POSITION_TIMEOUT_MS);
      }),
    ]);
    clearTimeout(timer);
    const pos = fresh ?? (await Location.getLastKnownPositionAsync({ maxAge: LOCATION_MAX_AGE_MS }));
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
  /** Pin confirmed while the previous turn was still streaming — sendMessage
   * refuses concurrent sends, so the pin waits here and flushes the moment
   * the stream ends (silently dropping it lost the rider's selection). */
  pendingMapPin: {
    role: 'pickup' | 'dropoff';
    pin: { lat: number; lng: number; address?: string | null };
  } | null;
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
  /** Return leg of the "Drop a pin" card: sends the confirmed map pin back
   * into the chat as a user message carrying exact [lat,lng] coordinates
   * (the bracketed format the model is instructed to pass through verbatim,
   * and the PII scrubber is taught to leave intact). */
  submitMapPin: (
    role: 'pickup' | 'dropoff',
    pin: { lat: number; lng: number; address?: string | null },
  ) => Promise<void>;
  stopStreaming: () => void;
  startNewConversation: () => Promise<void>;
}

export const useAiChatStore = create<AiChatState>((set, get) => ({
  messages: [],
  conversationId: null,
  isStreaming: false,
  toolStatus: null,
  pendingMapPin: null,
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
          const kindByAction: Partial<Record<AiAction['type'], AiChatMessage['kind']>> = {
            booking_proposal: 'booking_proposal',
            location_suggestions: 'location_suggestions',
            fare_quote: 'fare_quote',
            open_map_picker: 'map_picker',
          };
          set((state) => ({
            messages: [
              ...state.messages,
              {
                id: newId(),
                role: 'assistant',
                kind: kindByAction[action.type] ?? 'support_action',
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
      // Flush a pin confirmed while this turn was streaming — it queued
      // instead of being dropped by the isStreaming guard. Skip aborted
      // turns: stopStreaming flushes explicitly, and startNewConversation is
      // discarding this conversation (its clear may not have landed yet).
      const queued = get().pendingMapPin;
      if (queued && !abortController.signal.aborted) {
        set({ pendingMapPin: null });
        void get().submitMapPin(queued.role, queued.pin);
      }
    }
  },

  submitMapPin: async (role, pin) => {
    // Confirmed while the emitting turn is still streaming (rider beat the
    // model's follow-up text): queue it — sendMessage would silently refuse a
    // concurrent send, and the pick-on-map screen has already navigated back.
    // Latest confirmation wins if somehow queued twice.
    if (get().isStreaming) {
      set({ pendingMapPin: { role, pin } });
      return;
    }
    const place = pin.address ? `${pin.address} ` : '';
    await get().sendMessage(
      `I dropped a pin on the map for my ${role}: ${place}[${pin.lat.toFixed(5)},${pin.lng.toFixed(5)}]. Use these exact coordinates.`,
    );
  },

  stopStreaming: () => {
    get().abortController?.abort();
    set({ isStreaming: false, toolStatus: null, abortController: null });
    // The rider halted the turn deliberately — a pin they confirmed during it
    // is still theirs to send. Flush now rather than surprising them later.
    const queued = get().pendingMapPin;
    if (queued) {
      set({ pendingMapPin: null });
      void get().submitMapPin(queued.role, queued.pin);
    }
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
      // A queued pin answers a request from the abandoned conversation —
      // flushing it into the fresh one would be a context-free non sequitur.
      pendingMapPin: null,
    });
  },
}));
