/**
 * aiChatStore tests.
 * Covers: optimistic send + token accumulation, tool status line, action
 * bubbles (booking card / support), error rollback (empty assistant bubble
 * removed), conversation id persistence + history rehydration, config gate,
 * stop/new-conversation. streamChat and the API client are mocked.
 */

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  ensureFreshToken: jest.fn(async () => undefined),
  getAuthHeader: jest.fn(async () => 'token'),
}));
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);
jest.mock('../../utils/aiChat', () => ({
  streamChat: jest.fn(),
}));
// aiChatStore imports expo-location directly (for location-aware assistant
// replies) — mocked to avoid pulling in the real native module, which
// crashes Jest's Expo Winter-runtime polyfill outside a real device/
// simulator context. Same pattern as __tests__/sosLocation.test.ts.
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ granted: false })),
  getLastKnownPositionAsync: jest.fn(() => Promise.resolve(null)),
}));

import AsyncStorage from '@react-native-async-storage/async-storage';
import api from '@shared/api/client';
import type { AiSseEvent } from '@shared/types/ai';
import { useAiChatStore } from '../aiChatStore';
import { streamChat } from '../../utils/aiChat';

const mockApi = api as jest.Mocked<typeof api>;
const mockStream = streamChat as jest.MockedFunction<typeof streamChat>;

const reset = () =>
  useAiChatStore.setState({
    messages: [],
    conversationId: null,
    isStreaming: false,
    toolStatus: null,
    enabled: false,
    disclaimer: '',
    abortController: null,
  });

/** streamChat stub that replays scripted SSE events. */
const scriptStream = (events: AiSseEvent[], fail?: string) =>
  mockStream.mockImplementation(async ({ onEvent }) => {
    if (fail) throw new Error(fail);
    events.forEach(onEvent);
  });

beforeEach(() => {
  jest.clearAllMocks();
  reset();
  (AsyncStorage.clear as jest.Mock)();
});

describe('sendMessage', () => {
  it('optimistically appends user bubble and grows the assistant bubble per token', async () => {
    scriptStream([
      { event: 'meta', data: { conversation_id: 'conv-9', user_message_id: 'u1' } },
      { event: 'token', data: { text: 'Your driver ' } },
      { event: 'token', data: { text: 'is close.' } },
      { event: 'done', data: { message_id: 'm1', usage: { input_tokens: 1, output_tokens: 2 }, stop_reason: 'end_turn' } },
    ]);

    await useAiChatStore.getState().sendMessage('where is my driver?');

    const { messages, conversationId, isStreaming } = useAiChatStore.getState();
    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({ role: 'user', content: 'where is my driver?' });
    expect(messages[1]).toMatchObject({ role: 'assistant', content: 'Your driver is close.' });
    expect(conversationId).toBe('conv-9');
    expect(isStreaming).toBe(false);
    expect(AsyncStorage.setItem).toHaveBeenCalledWith('spinr_ai_conversation_id', 'conv-9');
  });

  it('sends the stored conversation id for multi-turn context', async () => {
    useAiChatStore.setState({ conversationId: 'conv-9' });
    scriptStream([]);
    await useAiChatStore.getState().sendMessage('and my wallet?');
    expect(mockStream.mock.calls[0][0].conversationId).toBe('conv-9');
  });

  it('always requests the rider persona so dual-role accounts keep booking tools', async () => {
    scriptStream([]);
    await useAiChatStore.getState().sendMessage('book me a ride');
    expect(mockStream.mock.calls[0][0].audience).toBe('rider');
  });

  it('tracks tool status while a tool runs and clears it on tokens', async () => {
    const seen: (string | null)[] = [];
    mockStream.mockImplementation(async ({ onEvent }) => {
      onEvent({ event: 'tool', data: { name: 'get_active_ride', status: 'start' } });
      seen.push(useAiChatStore.getState().toolStatus);
      onEvent({ event: 'token', data: { text: 'Found it.' } });
      seen.push(useAiChatStore.getState().toolStatus);
    });
    await useAiChatStore.getState().sendMessage('where is my ride?');
    expect(seen).toEqual(['Checking your ride…', null]);
    expect(useAiChatStore.getState().toolStatus).toBeNull();
  });

  it('renders booking proposals as dedicated card bubbles', async () => {
    const proposal = {
      type: 'booking_proposal' as const,
      proposal: {
        pickup_lat: 1, pickup_lng: 2, pickup_address: '12 Elm St',
        dropoff_lat: 3, dropoff_lng: 4, dropoff_address: 'Airport',
      },
    };
    scriptStream([
      { event: 'action', data: proposal },
      { event: 'token', data: { text: 'Review the card below.' } },
    ]);
    await useAiChatStore.getState().sendMessage('book it');
    const cards = useAiChatStore.getState().messages.filter((m) => m.kind === 'booking_proposal');
    expect(cards).toHaveLength(1);
    expect(cards[0].action).toEqual(proposal);
  });

  it('renders location suggestions as dedicated action bubbles', async () => {
    const suggestions = {
      type: 'location_suggestions' as const,
      query: 'walmart',
      location_role: 'dropoff' as const,
      candidates: [
        {
          name: 'Walmart Supercentre',
          address: '4500 Gordon Rd, Regina, SK, Canada',
          lat: 50.4079,
          lng: -104.6501,
          in_service_area: true,
        },
      ],
    };
    scriptStream([{ event: 'action', data: suggestions }]);
    await useAiChatStore.getState().sendMessage('walmart');
    const cards = useAiChatStore.getState().messages.filter((m) => m.kind === 'location_suggestions');
    expect(cards).toHaveLength(1);
    expect(cards[0].action).toEqual(suggestions);
  });

  it('maps stream errors to friendly text and never leaves an empty bubble', async () => {
    scriptStream([], 'daily_cap');
    await useAiChatStore.getState().sendMessage('hello');
    const { messages, isStreaming } = useAiChatStore.getState();
    expect(isStreaming).toBe(false);
    expect(messages[1].content).toContain('limit');
    expect(messages.every((m) => m.kind !== 'text' || m.content.length > 0)).toBe(true);
  });

  it('drops the assistant bubble entirely when the stream dies before any output', async () => {
    mockStream.mockImplementation(async () => undefined); // no events at all
    await useAiChatStore.getState().sendMessage('hello');
    const { messages } = useAiChatStore.getState();
    expect(messages).toHaveLength(1); // just the user bubble
  });

  it('ignores sends while streaming', async () => {
    useAiChatStore.setState({ isStreaming: true });
    await useAiChatStore.getState().sendMessage('double-tap');
    expect(mockStream).not.toHaveBeenCalled();
  });
});

describe('config + history', () => {
  it('loadConfig gates the entry points', async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { enabled: true, mode: 'enabled', disclaimer: 'AI can be wrong.' },
      status: 200,
    });
    await useAiChatStore.getState().loadConfig();
    expect(useAiChatStore.getState().enabled).toBe(true);
    expect(useAiChatStore.getState().mode).toBe('enabled');
    expect(useAiChatStore.getState().disclaimer).toBe('AI can be wrong.');

    mockApi.get.mockRejectedValueOnce(new Error('network'));
    await useAiChatStore.getState().loadConfig();
    expect(useAiChatStore.getState().enabled).toBe(false);
    // config failure hides the entry point (safe default)
    expect(useAiChatStore.getState().mode).toBe('hidden');
  });

  it('loadConfig passes through the disabled mode', async () => {
    mockApi.get.mockResolvedValueOnce({ data: { enabled: false, mode: 'hidden' }, status: 200 });
    await useAiChatStore.getState().loadConfig();
    expect(useAiChatStore.getState().enabled).toBe(false);
    expect(useAiChatStore.getState().mode).toBe('hidden');
  });

  it('loadHistory rehydrates the stored conversation', async () => {
    await AsyncStorage.setItem('spinr_ai_conversation_id', 'conv-9');
    mockApi.get.mockResolvedValueOnce({
      data: {
        messages: [
          { id: 'm1', role: 'user', content: 'hi', created_at: '2026-06-01T00:00:00Z' },
          { id: 'm2', role: 'assistant', content: 'hello!', created_at: '2026-06-01T00:00:01Z' },
        ],
      },
      status: 200,
    });
    await useAiChatStore.getState().loadHistory();
    const { messages, conversationId } = useAiChatStore.getState();
    expect(conversationId).toBe('conv-9');
    expect(messages.map((m) => m.content)).toEqual(['hi', 'hello!']);
    expect(mockApi.get).toHaveBeenCalledWith('/ai/conversations/conv-9/messages');
  });

  it('loadHistory starts fresh when the conversation is gone (404/purged)', async () => {
    await AsyncStorage.setItem('spinr_ai_conversation_id', 'conv-old');
    mockApi.get.mockRejectedValueOnce(new Error('404'));
    await useAiChatStore.getState().loadHistory();
    expect(useAiChatStore.getState().conversationId).toBeNull();
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith('spinr_ai_conversation_id');
  });
});

describe('controls', () => {
  it('stopStreaming aborts the in-flight request', async () => {
    const controller = new AbortController();
    useAiChatStore.setState({ isStreaming: true, abortController: controller });
    useAiChatStore.getState().stopStreaming();
    expect(controller.signal.aborted).toBe(true);
    expect(useAiChatStore.getState().isStreaming).toBe(false);
  });

  it('startNewConversation clears state and storage', async () => {
    useAiChatStore.setState({
      conversationId: 'conv-9',
      messages: [{ id: 'x', role: 'user', kind: 'text', content: 'hi', createdAt: 1 }],
    });
    await useAiChatStore.getState().startNewConversation();
    expect(useAiChatStore.getState().messages).toEqual([]);
    expect(useAiChatStore.getState().conversationId).toBeNull();
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith('spinr_ai_conversation_id');
  });
});
