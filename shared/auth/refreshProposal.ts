/**
 * X8 lost-rotation recovery: a successor refresh token the client proposes
 * with POST /auth/refresh.
 *
 * The proposal is persisted before the request, bound to the refresh token it
 * is for. If the response is lost (app killed, network drop), the next attempt
 * replays the same pair and the server re-serves the successor it committed,
 * instead of treating the replay as token theft and signing out every device.
 * The server ignores it unless `refresh_successor_commitment_enabled` is on.
 *
 * Best-effort: any failure means "refresh without a proposal", which is the
 * behaviour before X8. Callers hold the session lock. No React/auth-store
 * imports: the driver app's headless background task uses this too.
 */
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { sessionKeychainOptions } from './sessionLock';

export const REFRESH_PROPOSAL_KEY = 'refresh_proposal';

// Same shape the server accepts: ^[A-Za-z0-9_-]{64}$ (384 bits).
const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const PROPOSAL_RE = /^[A-Za-z0-9_-]{64}$/;

function generateProposal(): string | null {
  // Loaded lazily so a missing native module degrades to "no proposal".
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const Crypto = require('expo-crypto') as typeof import('expo-crypto');
  // Platform CSPRNG (SecRandomCopyBytes / SecureRandom). 256 is a multiple of
  // 64, so `byte & 63` maps each byte to the alphabet without bias.
  const bytes = Crypto.getRandomBytes(64);
  if (!(bytes instanceof Uint8Array) || bytes.length !== 64) return null;
  // A no-op or broken RNG (all bytes equal) must never become a credential.
  if (bytes.every((byte) => byte === bytes[0])) return null;
  let proposal = '';
  for (const byte of bytes) proposal += ALPHABET[byte & 63];
  return PROPOSAL_RE.test(proposal) ? proposal : null;
}

/** The proposal to send with `parent`, or null to refresh without one. Never throws. */
export async function refreshProposalFor(parent: string): Promise<string | null> {
  if (Platform.OS === 'web' || !parent) return null;
  try {
    const stored = await SecureStore.getItemAsync(REFRESH_PROPOSAL_KEY);
    if (stored) {
      try {
        const pending = JSON.parse(stored) as { parent?: unknown; proposal?: unknown };
        if (pending.parent === parent && typeof pending.proposal === 'string' && PROPOSAL_RE.test(pending.proposal)) {
          return pending.proposal;
        }
      } catch {
        // Corrupt pending entry — fall through and generate a fresh proposal
        // instead of aborting via the outer catch (which would silently
        // disable X8 recovery until the entry is cleared some other way).
      }
    }
    const proposal = generateProposal();
    if (!proposal) return null;
    // Persist before sending: an unsaved proposal cannot be replayed after a lost response.
    await SecureStore.setItemAsync(REFRESH_PROPOSAL_KEY, JSON.stringify({ parent, proposal }), sessionKeychainOptions);
    return proposal;
  } catch {
    return null;
  }
}

/** Drop the pending proposal once its refresh has settled. Never throws. */
export async function clearRefreshProposal(): Promise<void> {
  if (Platform.OS === 'web') return;
  try {
    await SecureStore.deleteItemAsync(REFRESH_PROPOSAL_KEY);
  } catch {
    // A leftover proposal sits in the same keychain as the refresh token, is
    // bound to it, and is replaced on the next refresh.
  }
}
