/**
 * Plays the ride-offer alert tone in an Uber-style loop while a ride
 * offer is on screen. Matches Lyft/Uber driver-app behaviour: the alert
 * fires even with the device on silent (so a driver who set their phone
 * down across the room doesn't miss the offer), and mixes with other
 * audio (turn-by-turn nav voice prompts aren't cut off).
 *
 * Lifecycle:
 *   - `play()`  — starts the tone immediately, then re-plays every
 *                 ~2.5 s on an interval. Idempotent — calling while
 *                 already playing is a no-op.
 *   - `stop()`  — clears the interval and pauses the player.
 *
 * The audio asset (`assets/sounds/ride-offer.mp3`) is intentionally
 * `require()`d at module load so Metro bundles it into the app binary
 * (offers arrive before the driver opens the app from a cold start, so
 * a network fetch wouldn't be timely). If the asset is missing the
 * hook degrades to a logged no-op rather than crashing.
 */
import { useEffect, useRef, useCallback } from 'react';
import {
    createAudioPlayer,
    setAudioModeAsync,
    type AudioPlayer,
} from 'expo-audio';

const REPLAY_INTERVAL_MS = 2500;

let _player: AudioPlayer | null = null;
let _audioModeConfigured = false;

function _getOrCreatePlayer(): AudioPlayer | null {
    if (_player) return _player;
    try {
        // Static require so Metro bundles the asset. If the file is
        // missing this throws synchronously and we fall through to null.
        const src = require('../assets/sounds/ride-offer.mp3');
        _player = createAudioPlayer(src);
        return _player;
    } catch (e) {
        if (__DEV__) {
            console.warn(
                '[useRideOfferSound] ride-offer.mp3 not found at ' +
                'driver-app/assets/sounds/ride-offer.mp3 — alert will be silent. ' +
                'Drop a ~1.5s mp3 there to enable the audio cue.',
                e,
            );
        }
        return null;
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

    const playOnce = useCallback(() => {
        const player = _getOrCreatePlayer();
        if (!player) return;
        try {
            // Seek to start before each replay so a still-playing tone
            // restarts cleanly instead of stacking with itself.
            player.seekTo(0);
            player.play();
        } catch (e) {
            if (__DEV__) console.warn('[useRideOfferSound] play failed:', e);
        }
    }, []);

    const play = useCallback(() => {
        if (intervalRef.current) return; // idempotent
        void _configureAudioMode();
        playOnce();
        intervalRef.current = setInterval(playOnce, REPLAY_INTERVAL_MS);
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
