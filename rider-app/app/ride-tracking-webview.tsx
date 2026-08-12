import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, Share, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { WebView } from 'react-native-webview';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { TrackBaseUrlContext } from './_layout';

// Hosts that are allowed to load inside the in-app WebView.
// track.spinr.ca is the current Spinr-controlled live-tracking domain;
// spinr-track.app is kept for backward compatibility with links already
// sent out before the domain move. The backend's app_settings.track_base_url
// must resolve to one of these. Any other origin — including
// attacker-controlled deep-link injections — is rejected with an error
// state before the WebView loads.
const ALLOWED_TRACKING_HOSTS = new Set([
  'track.spinr.ca',
  'spinr-track.app',
  'www.spinr-track.app',
]);

function isAllowedTrackingUrl(raw: string): boolean {
  try {
    const u = new URL(raw);
    return u.protocol === 'https:' && ALLOWED_TRACKING_HOSTS.has(u.hostname);
  } catch {
    return false;
  }
}

export default function RideTrackingWebviewScreen() {
  const { trackingUrl, rideId } = useLocalSearchParams<{ trackingUrl?: string; rideId?: string }>();
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const webViewRef = useRef<WebView>(null);

  const trackBaseUrl = useContext(TrackBaseUrlContext);

  // Validate any caller-supplied trackingUrl before trusting it. Deep links
  // include broad path patterns (/ride, /promo, /join, spinr-track.app root)
  // so an attacker could craft spinr-user://ride-tracking-webview?trackingUrl=
  // https://evil.example/... and render a phishing page inside Spinr's chrome.
  const sanitizedInitialUrl =
    trackingUrl && isAllowedTrackingUrl(trackingUrl) ? trackingUrl : null;

  const [resolvedUrl, setResolvedUrl] = useState<string | null>(sanitizedInitialUrl);
  const [loading, setLoading] = useState(!sanitizedInitialUrl && !trackingUrl);
  const [webLoading, setWebLoading] = useState(true);
  const [error, setError] = useState<string | null>(
    trackingUrl && !sanitizedInitialUrl ? 'Invalid tracking link.' : null,
  );

  // Wrapped in useCallback (deps: [trackBaseUrl]) so the effect below can
  // safely depend on it without recreating (and refiring) on every render.
  const fetchTrackingUrl = useCallback(async (id: string) => {
    // Public tracking URL base is served by GET /settings → app_settings.track_base_url
    // so ops can rotate the domain without a mobile rebuild. Fail loud if it
    // isn't configured rather than fall back to a hardcoded spinr-track.app
    // that the admin may have intentionally moved away from.
    if (!trackBaseUrl) {
      setError('Live trip tracking is not set up yet. Please contact support.');
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const res = await api.get<{ share_token?: string }>(`/rides/${id}/share`);
      const token = res.data?.share_token ?? id;
      setResolvedUrl(`${trackBaseUrl}/${token}`);
    } catch {
      setError('Could not generate tracking link. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [trackBaseUrl]);

  useEffect(() => {
    if (!trackingUrl && rideId) {
      // trackingUrl is a route param, never set by fetchTrackingUrl (which
      // sets resolvedUrl/loading/error instead), so this guard can't
      // self-retrigger via this effect's own deps.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      fetchTrackingUrl(rideId);
    }
    // fetchTrackingUrl is now a useCallback keyed on [trackBaseUrl], the
    // same value already tracked here directly — equivalent firing.
  }, [rideId, trackingUrl, trackBaseUrl, fetchTrackingUrl]);

  const handleShare = async () => {
    if (!resolvedUrl) return;
    try {
      await Share.share({
        message: `Track my Spinr ride live: ${resolvedUrl}`,
        title: 'Track My Spinr Ride',
      });
    } catch {}
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <View style={styles.headerCenter}>
          <View style={styles.livePill}>
            <View style={styles.liveDot} />
            <Text style={styles.liveText}>LIVE</Text>
          </View>
          <Text style={styles.headerTitle}>Ride Tracking</Text>
        </View>
        <TouchableOpacity style={styles.shareBtn} onPress={handleShare} disabled={!resolvedUrl}>
          <Ionicons name="share-outline" size={22} color={resolvedUrl ? colors.primary : colors.textDim} />
        </TouchableOpacity>
      </View>

      {/* URL bar */}
      {resolvedUrl ? (
        <View style={styles.urlBar}>
          <Ionicons name="lock-closed" size={13} color="#10B981" />
          <Text style={styles.urlText} numberOfLines={1}>{resolvedUrl}</Text>
          {webLoading && <ActivityIndicator size="small" color={colors.primary} style={{ marginLeft: 8 }} />}
        </View>
      ) : null}

      {/* Content */}
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>Generating tracking link…</Text>
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Ionicons name="warning-outline" size={48} color={colors.error} />
          <Text style={styles.errorText}>{error}</Text>
          {rideId && (
            <TouchableOpacity style={styles.retryBtn} onPress={() => fetchTrackingUrl(rideId!)}>
              <Text style={styles.retryText}>Try Again</Text>
            </TouchableOpacity>
          )}
        </View>
      ) : resolvedUrl ? (
        <WebView
          ref={webViewRef}
          source={{ uri: resolvedUrl }}
          style={styles.webview}
          onLoadStart={() => setWebLoading(true)}
          onLoadEnd={() => setWebLoading(false)}
          onError={() => {
            setWebLoading(false);
            setError('Could not load tracking page.');
          }}
          allowsInlineMediaPlayback
          mediaPlaybackRequiresUserAction={false}
          javaScriptEnabled
          domStorageEnabled
        />
      ) : null}

      {/* Share footer */}
      {resolvedUrl && !loading && !error && (
        <View style={styles.footer}>
          <TouchableOpacity style={styles.footerShareBtn} onPress={handleShare}>
            <Ionicons name="share-social-outline" size={20} color="#FFF" />
            <Text style={styles.footerShareText}>Share Tracking Link</Text>
          </TouchableOpacity>
        </View>
      )}
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 10,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 40, height: 40, justifyContent: 'center', alignItems: 'center' },
    headerCenter: { flex: 1, alignItems: 'center', gap: 2 },
    headerTitle: { fontSize: 15, fontWeight: '700', color: colors.text },
    shareBtn: { width: 40, height: 40, justifyContent: 'center', alignItems: 'center' },
    livePill: {
      flexDirection: 'row', alignItems: 'center', gap: 5,
      backgroundColor: '#FEF2F2', paddingHorizontal: 10, paddingVertical: 3, borderRadius: 20,
    },
    liveDot: {
      width: 7, height: 7, borderRadius: 4, backgroundColor: colors.primary,
    },
    liveText: { fontSize: 11, fontWeight: '800', color: colors.primary, letterSpacing: 0.5 },
    urlBar: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      paddingHorizontal: 16, paddingVertical: 8,
      backgroundColor: colors.surfaceLight,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    urlText: {
      flex: 1, fontSize: 12, color: colors.textSecondary, fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
    },
    webview: { flex: 1 },
    center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24 },
    loadingText: { marginTop: 16, fontSize: 15, color: colors.textDim, textAlign: 'center' },
    errorText: { marginTop: 16, fontSize: 15, color: colors.error, textAlign: 'center' },
    retryBtn: {
      marginTop: 16, paddingHorizontal: 24, paddingVertical: 12,
      backgroundColor: colors.primary, borderRadius: 20,
    },
    retryText: { fontSize: 15, fontWeight: '600', color: '#FFF' },
    footer: {
      paddingHorizontal: 20, paddingVertical: 12,
      borderTopWidth: 1, borderTopColor: colors.border,
    },
    footerShareBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
      backgroundColor: colors.primary, paddingVertical: 14, borderRadius: 24,
    },
    footerShareText: { fontSize: 16, fontWeight: '700', color: '#FFF' },
  });
}
