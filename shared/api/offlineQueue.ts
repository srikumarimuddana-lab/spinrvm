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
  body?: any;
  createdAt: string;
  retries: number;
}

let _queue: QueuedRequest[] = [];
let _isOnline = true;
let _isProcessing = false;
let _initialized = false;

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

export async function enqueueRequest(method: string, url: string, body?: any): Promise<void> {
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
      } catch (error: any) {
        // If it's still a network error, stop processing
        if (error?.name === 'TimeoutError' || error?.message?.includes('Network request failed')) {
          console.log('[OfflineQueue] Still offline — pausing replay');
          break;
        }
        // Other errors (4xx, 5xx) — drop the request to avoid infinite retry
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
