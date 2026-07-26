/**
 * SSE client for /ai/chat.
 *
 * Pins: the frame parser survives arbitrary chunk splits (a frame broken
 * mid-`data:` line must not be lost or double-emitted), ping comments are
 * dropped, unparseable frames are skipped without killing the stream, and
 * streamChat wires auth + body + abort and surfaces non-2xx as typed errors.
 */
import { SseFrameParser, streamChat } from '../aiChat';
import type { AiSseEvent } from '@shared/types/ai';

jest.mock('@shared/api/client', () => ({
  ensureFreshToken: jest.fn(async () => undefined),
  getAuthHeader: jest.fn(async () => 'test-token'),
}));
jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://api.test' },
}));

const FRAME =
  'event: token\ndata: {"text":"Hello"}\n\nevent: done\ndata: {"message_id":"m1","usage":{"input_tokens":1,"output_tokens":2},"stop_reason":"end_turn"}\n\n';

describe('SseFrameParser', () => {
  it('parses complete frames', () => {
    const parser = new SseFrameParser();
    const events = parser.push(FRAME);
    expect(events.map((e) => e.event)).toEqual(['token', 'done']);
    expect((events[0] as any).data.text).toBe('Hello');
  });

  it('survives chunk splits at every byte boundary', () => {
    // The same frames fed one character at a time must yield identical events.
    const parser = new SseFrameParser();
    const events: AiSseEvent[] = [];
    for (const char of FRAME) events.push(...parser.push(char));
    expect(events.map((e) => e.event)).toEqual(['token', 'done']);
    expect((events[1] as any).data.message_id).toBe('m1');
  });

  it('drops ping comments without emitting', () => {
    const parser = new SseFrameParser();
    expect(parser.push(': ping\n\n')).toEqual([]);
    // and a ping between frames does not corrupt parsing
    const events = parser.push('event: token\ndata: {"text":"a"}\n\n: ping\n\nevent: token\ndata: {"text":"b"}\n\n');
    expect(events.map((e: any) => e.data.text)).toEqual(['a', 'b']);
  });

  it('skips unparseable frames and keeps going', () => {
    const parser = new SseFrameParser();
    const events = parser.push('event: token\ndata: {broken\n\nevent: token\ndata: {"text":"ok"}\n\n');
    expect(events).toHaveLength(1);
    expect((events[0] as any).data.text).toBe('ok');
  });

  it('holds incomplete frames in the buffer', () => {
    const parser = new SseFrameParser();
    expect(parser.push('event: token\ndata: {"text":"hi"}')).toEqual([]);
    expect(parser.push('\n\n')).toHaveLength(1);
  });
});

function streamResponse(chunks: string[], init: { ok?: boolean; status?: number; json?: any } = {}) {
  let index = 0;
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => init.json,
    body: {
      getReader: () => ({
        read: async () =>
          index < chunks.length ? { done: false, value: chunks[index++] } : { done: true, value: undefined },
      }),
    },
  };
}

describe('streamChat', () => {
  it('POSTs with auth + stream body and delivers events in order', async () => {
    const fetchImpl = jest.fn(async (_url: string, _init: any) => streamResponse([FRAME]));
    const events: AiSseEvent[] = [];
    await streamChat({
      message: 'where is my driver?',
      conversationId: 'conv-1',
      audience: 'rider',
      onEvent: (e) => events.push(e),
      fetchImpl,
    });

    expect(events.map((e) => e.event)).toEqual(['token', 'done']);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/ai/chat');
    expect(init.headers.Authorization).toBe('Bearer test-token');
    expect(init.headers.Accept).toBe('text/event-stream');
    expect(JSON.parse(init.body)).toEqual({
      message: 'where is my driver?',
      conversation_id: 'conv-1',
      stream: true,
      audience: 'rider',
      // This build renders the Drop-a-pin card; the backend gates
      // request_map_pin's action on the declaration.
      capabilities: ['map_pin'],
    });
  });

  it('omits audience from the body when not provided', async () => {
    const fetchImpl = jest.fn(async (_url: string, _init: any) => streamResponse([FRAME]));
    await streamChat({ message: 'hi', conversationId: null, onEvent: jest.fn(), fetchImpl });
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({
      message: 'hi',
      conversation_id: null,
      stream: true,
      capabilities: ['map_pin'],
    });
  });

  it('surfaces backend error codes from non-2xx responses', async () => {
    const fetchImpl = jest.fn(async () =>
      streamResponse([], { ok: false, status: 429, json: { detail: { code: 'daily_cap', message: 'x' } } }),
    );
    await expect(
      streamChat({ message: 'hi', conversationId: null, onEvent: jest.fn(), fetchImpl }),
    ).rejects.toThrow('daily_cap');
  });

  it('throws when no auth token is available', async () => {
    await expect(
      streamChat({
        message: 'hi',
        conversationId: null,
        onEvent: jest.fn(),
        fetchImpl: jest.fn(),
        getToken: async () => null,
      }),
    ).rejects.toThrow('not_authenticated');
  });

  it('passes the abort signal to the transport', async () => {
    const controller = new AbortController();
    const fetchImpl = jest.fn(async (_url: string, _init: any) => streamResponse([]));
    await streamChat({
      message: 'hi',
      conversationId: null,
      onEvent: jest.fn(),
      signal: controller.signal,
      fetchImpl,
    });
    expect(fetchImpl.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
