// JS side of the local RideOfferTone Expo module (Android only).
//
// Plays the bundled ride-offer tone as a navigation prompt so Android Auto
// routes it to the car speakers and ducks music. The native module is absent
// on iOS, web, Expo Go and any binary built before this module existed, so
// every call here degrades to 'unsupported' / no-op instead of throwing.
// lib/androidAuto/carOfferRing.ts is the only caller.

export type ToneResult =
  | 'playing'
  | 'playing_unfocused'
  | 'blocked_call'
  // A later start()/stop() superseded this start while it was still waiting
  // out a navigation prompt. Not a failure.
  | 'cancelled'
  | 'unsupported'
  | 'error';

type NativeRideOfferTone = {
  isSupported(): boolean;
  start(maxMs: number, gapMs: number): Promise<string> | string;
  stop(): void;
  // Expo native modules are event emitters (expo-modules-core).
  addListener?(event: string, listener: (e: { reason?: string }) => void): { remove(): void };
};

const RESULTS: readonly ToneResult[] = [
  'playing',
  'playing_unfocused',
  'blocked_call',
  'cancelled',
  'unsupported',
  'error',
];

let nativeCache: NativeRideOfferTone | null | undefined;
let supportedCache: boolean | undefined;

function nativeModule(): NativeRideOfferTone | null {
  if (nativeCache !== undefined) return nativeCache;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { requireOptionalNativeModule } = require('expo-modules-core');
    nativeCache = (requireOptionalNativeModule('RideOfferTone') as NativeRideOfferTone | null) ?? null;
  } catch (e) {
    console.error('[ride-offer-tone] native module lookup failed:', e);
    nativeCache = null;
  }
  return nativeCache;
}

/** True only on an Android 8+ build that has the module and the raw sound. */
export function isRideOfferToneSupported(): boolean {
  if (supportedCache !== undefined) return supportedCache;
  const mod = nativeModule();
  if (!mod) {
    supportedCache = false;
    return false;
  }
  try {
    supportedCache = mod.isSupported() === true;
  } catch (e) {
    console.error('[ride-offer-tone] isSupported failed:', e);
    supportedCache = false;
  }
  return supportedCache;
}

/**
 * Start (or restart) the looping tone. Never rejects. `maxMs` is clamped to
 * 1000..60000 and `gapMs` to 0..10000 natively as well.
 */
export async function startRideOfferTone(maxMs: number, gapMs: number): Promise<ToneResult> {
  const mod = nativeModule();
  if (!mod) return 'unsupported';
  try {
    const raw = await mod.start(Math.round(maxMs), Math.round(gapMs));
    return (RESULTS as readonly string[]).includes(raw) ? (raw as ToneResult) : 'error';
  } catch (e) {
    console.error('[ride-offer-tone] start failed:', e);
    return 'error';
  }
}

/** Stop the tone and release audio focus. Idempotent; safe when unsupported. */
export function stopRideOfferTone(): void {
  const mod = nativeModule();
  if (!mod) return;
  try {
    mod.stop();
  } catch (e) {
    console.error('[ride-offer-tone] stop failed:', e);
  }
}

/**
 * Called when a tone that already started as 'playing' / 'playing_unfocused'
 * ends early natively: permanent audio-focus loss (the driver started music
 * elsewhere) or a playback error. Not called for stop() or the tone reaching
 * its own maxMs. Returns an unsubscribe function; a no-op when unsupported.
 */
export function subscribeRideOfferToneEnded(listener: (reason: string) => void): () => void {
  const mod = nativeModule();
  if (!mod || typeof mod.addListener !== 'function') return () => {};
  try {
    const sub = mod.addListener('onToneEnded', (e) => listener(String(e?.reason ?? 'unknown')));
    return () => {
      try {
        sub.remove();
      } catch (e) {
        console.error('[ride-offer-tone] listener remove failed:', e);
      }
    };
  } catch (e) {
    console.error('[ride-offer-tone] addListener failed:', e);
    return () => {};
  }
}
