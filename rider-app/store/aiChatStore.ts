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
import { useAuthStore, registerLogoutCallback } from '@shared/store/authStore';
import type { AiAction, AiChatMessage, AiSseEvent } from '@shared/types/ai';
import { streamChat } from '../utils/aiChat';

/**
 * Conversation-pointer key, namespaced per authenticated user (F03).
 *
 * It used to be one unscoped key, `spinr_ai_conversation_id`. On an
 * account switch the new account's screen read the previous account's
 * pointer and issued GET /ai/conversations/{that id}/messages. The backend
 * correctly refuses a foreign conversation, but the rejection is
 * asynchronous — the screen renders the existing store first — so account B
 * could see account A's messages while that request was in flight.
 *
 * Namespacing removes the cross-account read entirely rather than relying on
 * the server's rejection arriving before the first paint. The legacy
 * unscoped key is deleted on the first clear so it cannot be resurrected.
 */
const LEGACY_CONVERSATION_KEY = 'spinr_ai_conversation_id';
const conversationKeyFor = (userId: string | null | undefined) =>
  userId ? `spinr_ai_conversation_id:${userId}` : LEGACY_CONVERSATION_KEY;

const currentUserId = (): string | null => useAuthStore.getState().user?.id ?? null;

/**
 * Session generation. Incremented on every logout/account switch; every
 * async callback captures the value current when it started and drops its
 * result if the generation has moved on.
 *
 * An AbortController alone is not enough: `streamChat`'s already-queued
 * `onEvent` callbacks and the `finally` block can still run after abort, and
 * they call `set(...)` — which would repopulate a store that logout just
 * cleared, with the previous account's tokens.
 */
let sessionGeneration = 0;

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

// AI17/F3: every `code` the backend's streamChat 'error' event can carry
// (see backend/ai/orchestrator.py's `yield "error", {"code": ...}` sites)
// must have an entry here — the lookup below never falls back to the raw
// `event.data.message`, so an unmapped future code gets `default` instead
// of a possibly-technical backend string leaking to the rider.
const ERROR_MESSAGES: Record<string, string> = {
  ai_disabled: 'The AI assistant is currently unavailable.',
  daily_cap: "You've reached today's AI assistant limit — try again tomorrow.",
  not_authenticated: 'Please sign in again to use the AI assistant.',
  // orchestrator.py: another reply for this conversation is already
  // in-flight (e.g. a double send) — rider should just wait, not retry hard.
  conversation_busy: "Still working on your last message — give it a moment before sending another.",
  // orchestrator.py: the conversation id no longer resolves (deleted,
  // expired, or not this rider's) — nothing to recover, only a fresh one.
  not_found: "This conversation isn't available anymore — start a new one to keep chatting.",
  // Matches orchestrator.py's GENERIC_ERROR_MESSAGE wording verbatim so this
  // fix doesn't change what the rider already sees for these two codes.
  ai_misconfigured: 'Something went wrong on our side — please try again in a moment.',
  provider_error: 'Something went wrong on our side — please try again in a moment.',
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
  sendMessage: (text: string, displayText?: string) => Promise<void>;
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
  /** Wipe every per-session field and disarm in-flight callbacks. Called
   * from the auth store's logout callback; see F03. */
  clearForSession: () => void;
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
    const generation = sessionGeneration;
    // Captured once: if the session ends mid-request, currentUserId() becomes
    // null and the catch branch below would otherwise clear the LEGACY key
    // instead of this user's.
    const storageKey = conversationKeyFor(currentUserId());
    try {
      const stored = await AsyncStorage.getItem(storageKey);
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
      // Drop the result if the session ended while this request was in
      // flight — otherwise the previous account's history lands in the new
      // account's store (F03).
      if (generation !== sessionGeneration) return;
      set({ conversationId: stored, messages });
    } catch {
      // 404 = purged/foreign conversation — start fresh rather than error.
      await AsyncStorage.removeItem(storageKey).catch(() => undefined);
      if (generation !== sessionGeneration) return;
      set({ conversationId: null, messages: [] });
    }
  },

  sendMessage: async (text: string, displayText?: string) => {
    const trimmed = text.trim();
    if (!trimmed || get().isStreaming) return;

    // AI17/F2: `content` stays the full text the model needs (e.g. a
    // quote-tap's "(vehicle id <uuid>)") — only `displayContent`, if given,
    // changes what the bubble renders. streamChat below still sends `trimmed`.
    const userMessage: AiChatMessage = {
      id: newId(),
      role: 'user',
      kind: 'text',
      content: trimmed,
      displayContent: displayText?.trim() || undefined,
      createdAt: Date.now(),
    };
    const assistantId = newId();
    const abortController = new AbortController();
    // F03: every callback below is gated on this. streamChat's queued
    // onEvent calls and the finally block can outlive an abort, and they
    // write to the store — which would repopulate state logout just cleared.
    const generation = sessionGeneration;
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
      if (generation !== sessionGeneration) return;
      switch (event.event) {
        case 'meta':
          set({ conversationId: event.data.conversation_id });
          AsyncStorage.setItem(
            conversationKeyFor(currentUserId()),
            event.data.conversation_id,
          ).catch(() => undefined);
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
          // Never fall back to the raw event.data.message — an unmapped code
          // must resolve to a known, rider-safe string, not backend text.
          appendToAssistant(ERROR_MESSAGES[event.data.code] ?? ERROR_MESSAGES.default);
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
      if (generation === sessionGeneration) {
        appendToAssistant(ERROR_MESSAGES[code] ?? ERROR_MESSAGES.default);
      }
    } finally {
      // Guarded rather than an early `return`: a control-flow statement in a
      // finally block would discard any in-flight exception (and trips
      // eslint's no-unsafe-finally). A logout during the turn already cleared
      // the store, and re-running this cleanup would resurrect
      // isStreaming/messages for the next user.
      if (generation === sessionGeneration) {
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
    await AsyncStorage.removeItem(conversationKeyFor(currentUserId())).catch(() => undefined);
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

  clearForSession: () => {
    // F03. Deliberately NOT stopStreaming(): that flushes a queued map pin
    // into a fresh submitMapPin, which on logout would fire an authenticated
    // request after sign-out — and, if an account switch is in progress,
    // send the previous rider's confirmed coordinates as the NEW account's
    // message. The pin is dropped here instead.
    //
    // Bumping the generation first is what makes this safe: every in-flight
    // onEvent/finally callback from the outgoing session is disarmed before
    // the state is cleared, so none of them can write back into the store
    // afterwards. abort() alone does not guarantee that — callbacks already
    // queued on the microtask queue still run.
    sessionGeneration += 1;
    get().abortController?.abort();
    set({
      messages: [],
      conversationId: null,
      isStreaming: false,
      toolStatus: null,
      abortController: null,
      pendingMapPin: null,
      // enabled/mode/disclaimer are global feature config, not per-session
      // data, and are refreshed by loadConfig on the next launch. Clearing
      // them here would flash the "coming soon" placeholder at the next
      // rider before their config call returns.
    });
  },
}));

// Wipe AI chat state whenever a session ends, so the next account on this
// device never renders the previous rider's conversation. The AI store was
// the one per-session store that never registered here (F03) — every other
// one (rideStore, driverStore) already did.
registerLogoutCallback(() => {
  useAiChatStore.getState().clearForSession();
  // The pointer is per-user now, but a pre-F03 install still has the old
  // unscoped key on disk holding the previous account's conversation id.
  // Delete it on the first sign-out after upgrade so it can never be read.
  void AsyncStorage.removeItem(LEGACY_CONVERSATION_KEY).catch(() => undefined);
});
