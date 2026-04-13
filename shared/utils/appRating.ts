/**
 * App Rating Prompt — shows a native app store review prompt after
 * positive ride experiences (rating >= 4 stars, 3+ completed rides).
 *
 * Uses expo-store-review for native iOS/Android in-app review dialogs.
 * Tracks when the prompt was last shown to avoid over-asking (max once per 30 days).
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

const LAST_PROMPT_KEY = '@spinr_last_rating_prompt';
const RIDE_COUNT_KEY = '@spinr_completed_ride_count';
const MIN_RIDES_BEFORE_PROMPT = 3;
const MIN_DAYS_BETWEEN_PROMPTS = 30;

export async function trackCompletedRide(): Promise<void> {
  try {
    const stored = await AsyncStorage.getItem(RIDE_COUNT_KEY);
    const count = stored ? parseInt(stored, 10) : 0;
    await AsyncStorage.setItem(RIDE_COUNT_KEY, String(count + 1));
  } catch {}
}

export async function shouldShowRatingPrompt(riderRating: number): Promise<boolean> {
  // Only prompt after good experiences (4+ stars)
  if (riderRating < 4) return false;

  try {
    // Check minimum ride count
    const countStr = await AsyncStorage.getItem(RIDE_COUNT_KEY);
    const count = countStr ? parseInt(countStr, 10) : 0;
    if (count < MIN_RIDES_BEFORE_PROMPT) return false;

    // Check cooldown period
    const lastPrompt = await AsyncStorage.getItem(LAST_PROMPT_KEY);
    if (lastPrompt) {
      const lastDate = new Date(lastPrompt);
      const daysSince = (Date.now() - lastDate.getTime()) / (1000 * 60 * 60 * 24);
      if (daysSince < MIN_DAYS_BETWEEN_PROMPTS) return false;
    }

    return true;
  } catch {
    return false;
  }
}

export async function showAppRatingPrompt(): Promise<boolean> {
  try {
    // Use expo-store-review for native in-app review
    const StoreReview = require('expo-store-review');

    const isAvailable = await StoreReview.isAvailableAsync();
    if (!isAvailable) {
      console.log('[AppRating] Store review not available on this device');
      return false;
    }

    await StoreReview.requestReview();
    await AsyncStorage.setItem(LAST_PROMPT_KEY, new Date().toISOString());
    console.log('[AppRating] Review prompt shown');
    return true;
  } catch (error) {
    console.log('[AppRating] Failed to show review prompt:', error);
    return false;
  }
}

/**
 * Call this after the rider submits their rating on the ride-completed screen.
 * It will automatically show the app store review prompt if conditions are met.
 */
export async function onRideRated(riderRating: number): Promise<void> {
  await trackCompletedRide();

  if (await shouldShowRatingPrompt(riderRating)) {
    // Small delay so the rating submission animation finishes first
    setTimeout(async () => {
      await showAppRatingPrompt();
    }, 1500);
  }
}
