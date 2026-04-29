/**
 * Offline request queue — queues failed API requests when offline and
 * replays them when connectivity is restored.
 *
 * Usage in app _layout.tsx:
 *   import { initOfflineQueue } from '@shared/api/offlineQueue';
 *   useEffect(() => { initOfflineQueue(); }, []);
 *
 * The queue automatically:
 * 1. Detects network state changes via NetInfo
 * 2. Queues POST/PUT/PATCH requests that fail due to network errors
 * 3. Replays them in FIFO order when connectivity returns
 * 4. Persists the queue to AsyncStorage so it survives app restarts
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo, { NetInfoState } from '@react-native-community/netinfo';

const QUEUE_KEY = '@spinr_offline_queue';
const MAX_QUEUE_SIZE = 50;

export interface QueuedRequest {
  id: string;
  method: string;
  url: string;
  body?: unknown;
  createdAt: string;
  retries: number;
}

let _queue: QueuedRequest[] = [];
let _isOnline = true;
let _isProcessing = false;
let _initialized = false;

// ── Error callback ──
// Screens register here to receive a notification when a queued request
// fails with a 4xx error after replay, so they can show a toast.
type QueueErrorFn = (request: QueuedRequest, error: unknown) => void;
let _onQueueError: QueueErrorFn | null = null;

export function setQueueErrorCallback(fn: QueueErrorFn): void {
  _onQueueError = fn;
}

// ── Public API ──

export function isOnline(): boolean {
  return _isOnline;
}

export function getQueueLength(): number {
  return _queue.length;
}

export function getQueue(): QueuedRequest[] {
  return [..._queue];
}

export async function enqueueRequest(method: string, url: string, body?: unknown): Promise<void> {
  if (_queue.length >= MAX_QUEUE_SIZE) {
    _queue.shift(); // Drop oldest
  }

  const request: QueuedRequest = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    method,
    url,
    body,
    createdAt: new Date().toISOString(),
    retries: 0,
  };

  _queue.push(request);
  await _persist();
  console.log(`[OfflineQueue] Enqueued ${method} ${url} (${_queue.length} in queue)`);
}

export async function clearQueue(): Promise<void> {
  _queue = [];
  await _persist();
}

export async function initOfflineQueue(): Promise<void> {
  if (_initialized) return;
  _initialized = true;

  // Load persisted queue
  try {
    const stored = await AsyncStorage.getItem(QUEUE_KEY);
    if (stored) {
      _queue = JSON.parse(stored);
      console.log(`[OfflineQueue] Loaded ${_queue.length} queued requests`);
    }
  } catch {
    _queue = [];
  }

  // Listen for network changes
  NetInfo.addEventListener((state: NetInfoState) => {
    const wasOffline = !_isOnline;
    _isOnline = !!state.isConnected && !!state.isInternetReachable;

    if (wasOffline && _isOnline && _queue.length > 0) {
      console.log(`[OfflineQueue] Back online — replaying ${_queue.length} requests`);
      processQueue();
    }
  });

  // Check initial state
  const state = await NetInfo.fetch();
  _isOnline = !!state.isConnected && !!state.isInternetReachable;

  // Process any pending items if online
  if (_isOnline && _queue.length > 0) {
    processQueue();
  }
}

// ── Internal ──

/** Narrows an unknown caught value to an object with optional name/message fields. */
function isErrorLike(e: unknown): e is { name?: string; message?: string } {
  return typeof e === 'object' && e !== null;
}

/** Narrows an unknown caught value to an Axios-style response error with a numeric status. */
function isResponseError(e: unknown): e is { response: { status: number } } {
  return (
    typeof e === 'object' &&
    e !== null &&
    'response' in e &&
    typeof (e as { response: unknown }).response === 'object' &&
    (e as { response: unknown }).response !== null &&
    'status' in (e as { response: { status: unknown } }).response &&
    typeof (e as { response: { status: unknown } }).response.status === 'number'
  );
}

async function processQueue(): Promise<void> {
  if (_isProcessing || _queue.length === 0 || !_isOnline) return;
  _isProcessing = true;

  try {
    // Import api client lazily to avoid circular dependency
    const { default: api } = await import('./client');

    while (_queue.length > 0 && _isOnline) {
      const request = _queue[0];

      try {
        if (request.method === 'POST') {
          await api.post(request.url, request.body);
        } else if (request.method === 'PUT') {
          await api.put(request.url, request.body);
        } else if (request.method === 'PATCH') {
          await api.patch(request.url, request.body);
        }

        // Success — remove from queue
        _queue.shift();
        await _persist();
        console.log(`[OfflineQueue] Replayed ${request.method} ${request.url} — ${_queue.length} remaining`);
      } catch (error: unknown) {
        // If it's still a network error, stop processing
        const errLike = isErrorLike(error);
        if (errLike && (error.name === 'TimeoutError' || error.message?.includes('Network request failed'))) {
          console.log('[OfflineQueue] Still offline — pausing replay');
          break;
        }
        // 4xx errors — the request is logically invalid; drop and surface to UI
        const status: number | undefined = isResponseError(error) ? error.response.status : undefined;
        const is4xx = status !== undefined && status >= 400 && status < 500;
        if (is4xx) {
          console.log(`[OfflineQueue] 4xx (${status}) on ${request.method} ${request.url} — dropping and notifying`);
          _queue.shift();
          await _persist();
          _onQueueError?.(request, error);
          continue;
        }
        // 5xx / other — retry up to 3 times then drop silently
        request.retries += 1;
        if (request.retries >= 3) {
          console.log(`[OfflineQueue] Dropping ${request.method} ${request.url} after 3 retries`);
          _queue.shift();
          await _persist();
        } else {
          break; // Retry later
        }
      }
    }
  } finally {
    _isProcessing = false;
  }
}

async function _persist(): Promise<void> {
  try {
    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(_queue));
  } catch {}
}
