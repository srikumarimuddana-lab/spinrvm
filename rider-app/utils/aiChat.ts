/**
 * SSE client for POST /ai/chat.
 *
 * Transport: `expo/fetch` (WinterCG fetch with ReadableStream response
 * bodies — available since Expo SDK 52; the app is on SDK 55). The frame
 * parser is a pure class so jest can exercise chunk-split frames without
 * any transport, and the transport/auth are injectable for the same reason.
 * Fallback plan if a device misbehaves: swap the transport for
 * react-native-sse — the parser and event contract stay identical.
 */
// eslint-disable-next-line import/no-named-as-default -- keep the default
// import: __tests__/aiChat.test.ts (and other test files across the repo)
// jest.mock('@shared/config/spinr.config', () => ({ default: {...} }))
// without a matching named 'SpinrConfig' export, so a named import resolves
// to undefined under that mock and breaks SpinrConfig.backendUrl below.
import SpinrConfig from '@shared/config/spinr.config';
import { ensureFreshToken, getAuthHeader } from '@shared/api/client';
import type { AiSseEvent } from '@shared/types/ai';

/** Incremental SSE frame parser. Feed raw text chunks (any split points),
 * get complete `event:`/`data:` frames out. Comment lines (`: ping`) are
 * keep-alives and are dropped. */
export class SseFrameParser {
  private buffer = '';

  push(chunk: string): AiSseEvent[] {
    this.buffer += chunk;
    const events: AiSseEvent[] = [];
    let sep = this.buffer.indexOf('\n\n');
    while (sep !== -1) {
      const block = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + 2);
      const frame = this.parseBlock(block);
      if (frame) events.push(frame);
      sep = this.buffer.indexOf('\n\n');
    }
    return events;
  }

  private parseBlock(block: string): AiSseEvent | null {
    let event: string | null = null;
    let data: string | null = null;
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue; // comment / ping
      if (line.startsWith('event: ')) event = line.slice(7).trim();
      else if (line.startsWith('data: ')) data = line.slice(6);
    }
    if (!event || data == null) return null;
    try {
      return { event, data: JSON.parse(data) } as AiSseEvent;
    } catch {
      console.error('[aiChat] unparseable SSE frame dropped:', event);
      return null;
    }
  }
}

export interface StreamChatOptions {
  message: string;
  conversationId: string | null;
  /** Persona for this surface. The main-screen assistant always sends
   * 'rider' so dual-role (rider+driver) accounts still get the booking
   * tools; omitting it lets the backend infer from the user row. */
  audience?: 'rider' | 'driver';
  /** Rider's current device location (only when permission already granted) —
   * lets booking tools resolve "my location" without asking. */
  location?: { lat: number; lng: number } | null;
  onEvent: (event: AiSseEvent) => void;
  signal?: AbortSignal;
  /** Test seams — default to expo/fetch + the shared auth helpers. */
  fetchImpl?: (url: string, init: any) => Promise<any>;
  getToken?: () => Promise<string | null>;
}

async function defaultFetch(url: string, init: any): Promise<any> {
  // expo/fetch supports streaming response bodies on iOS and Android
  // (RN's built-in fetch does not). Lazy require (not a static import) is
  // deliberate: per the file header, fetchImpl is an injectable test seam —
  // tests that supply their own fetchImpl never call this function, so they
  // never pay for loading expo/fetch. A static import would load it eagerly
  // for every importer of this module, mocked or not.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { fetch: expoFetch } = require('expo/fetch');
  return expoFetch(url, init);
}

async function defaultGetToken(): Promise<string | null> {
  await ensureFreshToken();
  return getAuthHeader();
}

/**
 * Stream one chat turn. Resolves when the stream ends; every frame is
 * delivered through `onEvent` (including `error` frames — callers render
 * those as assistant-side error bubbles rather than throwing mid-stream).
 * Throws only for transport-level failures (no connection, non-2xx).
 */
export async function streamChat(options: StreamChatOptions): Promise<void> {
  const {
    message,
    conversationId,
    audience,
    location,
    onEvent,
    signal,
    fetchImpl = defaultFetch,
    getToken = defaultGetToken,
  } = options;

  const token = await getToken();
  if (!token) throw new Error('not_authenticated');

  const response = await fetchImpl(`${SpinrConfig.backendUrl}/api/v1/ai/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      stream: true,
      // UI features this build can render. "map_pin" = the chat's Drop-a-pin
      // card; the backend's request_map_pin tool only emits its action for
      // clients that declare it, so older installs are never promised a
      // button their build cannot draw.
      capabilities: ['map_pin'],
      ...(audience ? { audience } : {}),
      ...(location ? { location } : {}),
    }),
    signal,
  });

  if (!response.ok) {
    let detail = `http_${response.status}`;
    try {
      const body = await response.json();
      detail = body?.detail?.code ?? body?.detail ?? detail;
    } catch {
      // body not JSON — keep the status-based code
    }
    throw new Error(String(detail));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseFrameParser();
   
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const text = typeof value === 'string' ? value : decoder.decode(value, { stream: true });
    for (const event of parser.push(text)) onEvent(event);
  }
}
