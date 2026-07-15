/**
 * Plays the ride-offer alert tone in an Uber-style loop while a ride
 * offer is on screen. Matches Lyft/Uber driver-app behaviour: the alert
 * fires even with the device on silent (so a driver who set their phone
 * down across the room doesn't miss the offer), and mixes with other
 * audio (turn-by-turn nav voice prompts aren't cut off).
 *
 * Sources, in order of preference:
 *   1. Admin-uploaded URL from /drivers/config → setOfferSoundUrl(url)
 *   2. Bundled placeholder at assets/sounds/ride_offer.mp3
 *      (underscore name: the same file is copied into Android res/raw by
 *      plugins/withRideOfferSound, and Android resource names forbid '-')
 *
 * The bundled file is `require()`d at module load so Metro bundles it.
 * If the remote URL fails to load (network down, 404), we silently keep
 * the previously-working player rather than going mute.
 *
 * Lifecycle:
 *   - `play()`  — starts the tone immediately, then re-plays every
 *                 ~2.5 s on an interval. Idempotent.
 *   - `stop()`  — clears the interval and pauses the player.
 *   - `setOfferSoundUrl(url)` — module-level setter the dashboard calls
 *                 after /drivers/config resolves. Pass null/empty to
 *                 revert to the bundled placeholder.
 */
import { useEffect, useRef, useCallback } from 'react';
import {
    createAudioPlayer,
    setAudioModeAsync,
    type AudioPlayer,
} from 'expo-audio';
import { useAlertPrefsStore } from '../store/alertPrefsStore';

const REPLAY_INTERVAL_MS = 2500;

let _player: AudioPlayer | null = null;
let _currentUrl: string | null = null;
let _audioModeConfigured = false;

function _createBundledPlayer(): AudioPlayer | null {
    try {
        const src = require('../assets/sounds/ride_offer.mp3');
        return createAudioPlayer(src);
    } catch (e) {
        if (__DEV__) {
            console.warn(
                '[useRideOfferSound] ride_offer.mp3 not found at ' +
                'driver-app/assets/sounds/ride_offer.mp3 — alert will be silent. ' +
                'Drop a ~1.5s mp3 there to enable the audio cue.',
                e,
            );
        }
        return null;
    }
}

function _getOrCreatePlayer(): AudioPlayer | null {
    if (_player) return _player;
    _player = _createBundledPlayer();
    return _player;
}

/**
 * Swap the active player to a remote URL (admin-uploaded sound).
 * Re-call with null or empty string to revert to the bundled placeholder.
 * Safe to call repeatedly — same-URL re-init is a no-op.
 */
export function setOfferSoundUrl(url: string | null | undefined): void {
    const next = url && url.trim() ? url.trim() : null;
    if (next === _currentUrl) return; // unchanged
    _currentUrl = next;

    // Drop the prior player. expo-audio reclaims resources via the JS GC
    // but pause() ensures any in-flight playback stops immediately.
    try {
        _player?.pause();
    } catch {
        // pause() may throw if the player is in a bad state — ignore.
    }
    _player = null;

    if (!next) {
        // Revert to bundled. _getOrCreatePlayer() will re-create on demand.
        return;
    }
    try {
        _player = createAudioPlayer({ uri: next });
    } catch (e) {
        if (__DEV__) console.warn('[useRideOfferSound] remote createAudioPlayer failed, falling back to bundled:', e);
        _player = _createBundledPlayer();
    }
}

async function _configureAudioMode(): Promise<void> {
    if (_audioModeConfigured) return;
    try {
        await setAudioModeAsync({
            playsInSilentMode: true,
            interruptionMode: 'mixWithOthers',
        });
        _audioModeConfigured = true;
    } catch (e) {
        if (__DEV__) console.warn('[useRideOfferSound] setAudioModeAsync failed:', e);
    }
}

export interface RideOfferSoundControls {
    play: () => void;
    stop: () => void;
}

export function useRideOfferSound(): RideOfferSoundControls {
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const playOnce = useCallback(async () => {
        // Settings → Sound & Haptics → Sound Effects. Checked per replay (not
        // just at play()) so muting mid-offer silences the loop immediately.
        if (!useAlertPrefsStore.getState().soundEffects) return;
        const player = _getOrCreatePlayer();
        if (!player) return;
        try {
            // Seek to start before each replay so a still-playing tone restarts
            // cleanly instead of stacking with itself. seekTo() is async
            // (Promise<void>) — it MUST be awaited before play(). Previously
            // play() fired before the seek landed, so once the first tone had
            // finished the player was still parked at its end position and
            // play() was a no-op: the alert sounded only once on iOS, while
            // Android's faster seek happened to win the race and repeat.
            await player.seekTo(0);
            player.play();
        } catch (e) {
            if (__DEV__) console.warn('[useRideOfferSound] play failed:', e);
        }
    }, []);

    const play = useCallback(() => {
        if (intervalRef.current) return; // idempotent
        void _configureAudioMode();
        void playOnce();
        intervalRef.current = setInterval(() => { void playOnce(); }, REPLAY_INTERVAL_MS);
    }, [playOnce]);

    const stop = useCallback(() => {
        if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
        }
        try {
            _player?.pause();
        } catch {
            // pause on a never-loaded player throws; ignore.
        }
    }, []);

    useEffect(() => {
        return () => {
            if (intervalRef.current) {
                clearInterval(intervalRef.current);
                intervalRef.current = null;
            }
        };
    }, []);

    return { play, stop };
}
