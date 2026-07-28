/**
 * Device integrity attestation using platform-native APIs.
 *
 * - Android: Google Play Integrity API (requires Google Cloud project setup)
 * - iOS: Apple App Attest / DeviceCheck (requires Apple Developer config)
 *
 * Flow:
 *   1. Client requests a nonce from backend (prevents replay)
 *   2. Client passes nonce to platform API → gets signed integrity token
 *   3. Client sends token to backend
 *   4. Backend verifies token with Google/Apple servers
 *   5. Backend returns trust verdict
 *
 * Setup required:
 *   - Android: Enable Play Integrity API in Google Cloud Console,
 *     link your app's package name, add google-services.json
 *   - iOS: Enable App Attest capability in Xcode,
 *     configure in Apple Developer portal
 *
 * Until native modules are configured, falls back to device signal
 * heuristics (emulator detection, brand/model checks).
 */

import { Platform, NativeModules } from 'react-native';
import * as Device from 'expo-device';
import * as Application from 'expo-application';
import api from '@shared/api/client';
import { captureException } from '@shared/services/errorReporting';

export type IntegrityResult = {
  attested: boolean;
  reason?: string;
  riskTier?: 'trusted' | 'elevated' | 'untrusted';
};

/**
 * Attempt to get a Play Integrity token via native module.
 * Returns null if the native module isn't linked (dev builds without the native SDK).
 */
async function getPlayIntegrityToken(nonce: string): Promise<string | null> {
  try {
    // This native module must be implemented in Android native code.
    // See: driver-app/android/app/src/main/java/com/spinr/driver/PlayIntegrityModule.java
    const { SpinrPlayIntegrity } = NativeModules;
    if (!SpinrPlayIntegrity?.requestIntegrityToken) {
      return null;
    }
    const token = await SpinrPlayIntegrity.requestIntegrityToken(nonce);
    return token || null;
  } catch (e) {
    console.warn('[PlayIntegrity] Native module call failed:', e);
    return null;
  }
}

/**
 * Attempt to get an App Attest assertion via native module.
 * Returns null if the native module isn't linked (dev builds without the native SDK).
 */
async function getAppAttestToken(nonce: string): Promise<string | null> {
  try {
    // This native module must be implemented in iOS native code.
    // See: driver-app/ios/SpinrDriver/AppAttestModule.swift
    const { SpinrAppAttest } = NativeModules;
    if (!SpinrAppAttest?.requestAttestation) {
      return null;
    }
    const token = await SpinrAppAttest.requestAttestation(nonce);
    return token || null;
  } catch (e) {
    console.warn('[AppAttest] Native module call failed:', e);
    return null;
  }
}

export async function attestDeviceIntegrity(): Promise<IntegrityResult> {
  try {
    // Emulator check — immediate reject (no need for network call)
    if (!Device.isDevice) {
      // Still report to backend for record-keeping
      await api.post('/drivers/attest-device', {
        platform: Platform.OS,
        isDevice: false,
        reason: 'emulator',
      }).catch(() => {});
      return { attested: false, reason: 'emulator_detected', riskTier: 'untrusted' };
    }

    // Step 1: Request a nonce from backend
    let nonce: string | null = null;
    try {
      const nonceResp = await api.post<{ nonce: string }>('/drivers/attest-nonce', {});
      nonce = nonceResp.data.nonce;
    } catch {
      // Backend doesn't support nonce yet — fall through to heuristic path
    }

    // Step 2: Attempt platform-native attestation
    let integrityToken: string | null = null;
    let attestMethod: 'play_integrity' | 'app_attest' | 'heuristic' = 'heuristic';

    if (nonce) {
      if (Platform.OS === 'android') {
        integrityToken = await getPlayIntegrityToken(nonce);
        if (integrityToken) attestMethod = 'play_integrity';
      } else if (Platform.OS === 'ios') {
        integrityToken = await getAppAttestToken(nonce);
        if (integrityToken) attestMethod = 'app_attest';
      }
    }

    // Step 3: Send to backend for verification
    const payload = {
      platform: Platform.OS,
      brand: Device.brand,
      modelName: Device.modelName,
      osVersion: Device.osVersion,
      isDevice: Device.isDevice,
      deviceType: Device.deviceType,
      applicationId: Application.applicationId,
      nativeApplicationVersion: Application.nativeApplicationVersion,
      // Platform attestation token (null if native module not available)
      integrityToken,
      attestMethod,
      nonce,
    };

    const response = await api.post<{
      trusted: boolean;
      risk_tier: string;
      reason?: string;
    }>('/drivers/attest-device', payload);

    if (!response.data.trusted) {
      return {
        attested: false,
        reason: response.data.reason || 'server_rejected',
        riskTier: response.data.risk_tier as IntegrityResult['riskTier'],
      };
    }

    return {
      attested: true,
      riskTier: response.data.risk_tier as IntegrityResult['riskTier'],
    };
  } catch (error) {
    console.warn('[DeviceIntegrity] Attestation failed, allowing with flag:', error);
    captureException(
      error instanceof Error ? error : new Error(String(error)),
      { domain: 'safety', surface: 'driver-app', attestation: 'failed_with_allow' },
    );
    return { attested: true, reason: 'attestation_unavailable' };
  }
}
