/**
 * Embedded Stripe Connect onboarding (Option B).
 *
 * Renders Stripe's `account-onboarding` embedded component inside a WebView so
 * the driver never leaves the app — no browser redirect, no deep-link bounce.
 * Stripe still collects and *holds* the SIN itself; we only ever see the last-4
 * + verification status (mirrored back by the account.updated webhook).
 *
 * Auth: the backend's GET /stripe-embedded page is public (it carries only the
 * publishable key). The driver's bearer token is injected into the WebView at
 * runtime via injectedJavaScriptBeforeContentLoaded (window.__SPINR_TOKEN) and
 * used by the page's same-origin POST to /stripe-account-session — it is never
 * placed in the URL or written to logs.
 *
 * Diagnostics: this screen used to collapse every failure (token hang, page not
 * loading, connect.js blocked, COOP, slow fetchClientSecret) into one identical
 * infinite spinner. It now (a) logs the URL + each progress stage to the JS
 * console, (b) shows a live debug strip with the exact URL (long-press to copy
 * and open in a browser) + current stage, (c) trips a no-progress watchdog that
 * replaces the spinner with an actionable error naming the stall point, and
 * (d) surfaces HTTP / network / connect.js errors instead of swallowing them.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { showToast } from '../../hooks/useToast';
import { ensureFreshToken, getAuthHeader } from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import { useDriverMe } from '@shared/hooks/queries';
import { useTheme } from '@shared/theme/ThemeContext';

const EMBEDDED_URL = `${SpinrConfig.backendUrl}/api/v1/drivers/stripe-embedded`;
// Restrict top-level navigation to our API origin + Stripe (defence-in-depth
// alongside mediaCapturePermissionGrantType="grant" — Stripe renders inside
// iframes, which aren't subject to this list, so this stays tight).
const ORIGIN_WHITELIST = [SpinrConfig.backendUrl, 'https://*.stripe.com', 'https://connect-js.stripe.com'];
// If no terminal signal (mounted / exit / error) arrives within this window we
// stop spinning and show an actionable error naming the current stage.
const LOAD_TIMEOUT_MS = 30000;

export default function StripeOnboardingScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const { refetch } = useDriverMe();
    const webRef = useRef<WebView>(null);

    const [token, setToken] = useState<string | null>(null);
    const [tokenError, setTokenError] = useState(false);
    const [pageLoading, setPageLoading] = useState(true);
    const [stage, setStageState] = useState<string>('starting');
    const [fatalError, setFatalError] = useState<string | null>(null);
    const [reloadKey, setReloadKey] = useState(0);
    const exitedRef = useRef(false);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Single place to record progress: logs URL + stage to the JS console
    // (visible via Metro / `npx react-native log-android|log-ios`) and drives
    // the on-screen debug strip, so the exact stall point is observable.
    const log = (s: string) => {
         
        console.log(`[StripeOnboarding] stage=${s} url=${EMBEDDED_URL}`);
        setStageState(s);
    };

    const clearTimer = () => {
        if (timerRef.current) {
            clearTimeout(timerRef.current);
            timerRef.current = null;
        }
    };

    useEffect(() => {
        let active = true;
        (async () => {
            // Best-effort: hold the OS camera permission up front so Stripe's
            // in-page getUserMedia can capture the driver's government ID. If
            // denied, Stripe falls back to file upload — so never block on this.
            try {
                await ImagePicker.requestCameraPermissionsAsync();
            } catch {
                // ignore — onboarding continues with the file-upload fallback
            }
            try {
                log('requesting-token');
                await ensureFreshToken();
                const t = await getAuthHeader();
                if (!active) return;
                if (!t) {
                    log('token-missing');
                    setTokenError(true);
                    return;
                }
                log('token-ready');
                setToken(t);
            } catch {
                if (active) {
                    log('token-error');
                    setTokenError(true);
                }
            }
        })();
        return () => {
            active = false;
            clearTimer();
        };
    }, []);

    // Start the no-progress watchdog once the WebView has a token to load with
    // (and restart it on a manual retry). Cleared on the `mounted` signal.
    useEffect(() => {
        if (!token || fatalError) return;
        clearTimer();
        timerRef.current = setTimeout(() => {
            log('timeout');
            setFatalError('timeout');
        }, LOAD_TIMEOUT_MS);
        return clearTimer;
    }, [token, reloadKey, fatalError]);

    // Inject the bearer token into the page BEFORE its scripts run, so the
    // embedded component's fetchClientSecret can authenticate its POST.
    const injectedBeforeLoad = useMemo(
        () => `window.__SPINR_TOKEN = ${JSON.stringify(token ?? '')}; true;`,
        [token],
    );

    const finish = (opts: { refresh: boolean; message?: string; type?: 'success' | 'error' }) => {
        if (exitedRef.current) return;
        exitedRef.current = true;
        clearTimer();
        if (opts.message) showToast(opts.type ?? 'success', opts.type === 'error' ? 'Verification Failed' : 'Verification Complete', opts.message);
        if (opts.refresh) void refetch();
        router.back();
    };

    const onMessage = (e: WebViewMessageEvent) => {
        const data = e.nativeEvent.data || '';
        if (data === 'exit') {
            // User finished or closed Stripe's flow. Re-pull KYC status; the
            // account.updated webhook is the authoritative gate.
            finish({ refresh: true, message: 'Verification updated.', type: 'success' });
        } else if (data.startsWith('stage:')) {
            const s = data.slice(6);
            log(s);
            if (s === 'mounted') clearTimer(); // component is live; stop the watchdog
        } else if (data.startsWith('error:')) {
            // Show the specific failure (no-token / 401 / fetch-network /
            // connectjs-timeout / connectjs-load / init / <status>) instead of
            // an endless spinner.
            clearTimer();
            const code = data.slice(6);
            log(`error:${code}`);
            setFatalError(code);
        }
    };

    const retry = () => {
        exitedRef.current = false;
        setFatalError(null);
        setPageLoading(true);
        log('retry');
        setReloadKey((k) => k + 1); // remounts the WebView with a fresh load
    };

    const showSpinner =
        (!token && !tokenError && !fatalError) || (pageLoading && !tokenError && !fatalError);

    return (
        <View style={[styles.container, { backgroundColor: colors.background }]}>
            <LinearGradient
                colors={[colors.surface, colors.background]}
                style={[styles.header, { paddingTop: insets.top + 12 }]}
            >
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => finish({ refresh: true })} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={[styles.headerTitle, { color: colors.text }]}>Verify Identity</Text>
                    <View style={styles.backBtn} />
                </View>
            </LinearGradient>

            {tokenError ? (
                <View style={styles.center}>
                    <Ionicons name="alert-circle-outline" size={40} color={colors.textDim} />
                    <Text style={[styles.errText, { color: colors.textDim }]}>
                        Your session expired. Please go back and try again.
                    </Text>
                </View>
            ) : fatalError ? (
                <View style={styles.center}>
                    <Ionicons name="alert-circle-outline" size={40} color={colors.textDim} />
                    <Text style={[styles.errText, { color: colors.text, fontWeight: '700' }]}>
                        Verification couldn’t load.
                    </Text>
                    <Text style={[styles.errText, { color: colors.textDim }]}>
                        {fatalError === 'timeout'
                            ? `Timed out at stage "${stage}".`
                            : `Failure: ${fatalError} (last stage "${stage}").`}
                    </Text>
                    <Text style={[styles.debugLabel, { color: colors.textDim }]}>
                        Open this URL in a browser to test it directly:
                    </Text>
                    <Text selectable style={[styles.debugUrl, { color: colors.primary }]}>
                        {EMBEDDED_URL}
                    </Text>
                    <TouchableOpacity style={[styles.retryBtn, { backgroundColor: colors.primary }]} onPress={retry}>
                        <Text style={styles.retryText}>Retry</Text>
                    </TouchableOpacity>
                </View>
            ) : (
                <View style={{ flex: 1 }}>
                    {token && (
                        <WebView
                            key={reloadKey}
                            ref={webRef}
                            source={{ uri: EMBEDDED_URL }}
                            originWhitelist={ORIGIN_WHITELIST}
                            injectedJavaScriptBeforeContentLoaded={injectedBeforeLoad}
                            onMessage={onMessage}
                            onLoadStart={() => log('page-loading')}
                            onLoadEnd={() => {
                                setPageLoading(false);
                                log('page-loaded');
                            }}
                            onError={(e) => {
                                log(`webview-error:${e.nativeEvent?.description ?? ''}`);
                                clearTimer();
                                setFatalError('webview-error');
                            }}
                            onHttpError={(e) => log(`http-${e.nativeEvent?.statusCode ?? '?'}`)}
                            javaScriptEnabled
                            domStorageEnabled
                            // Stripe identity verification may capture a document photo.
                            mediaCapturePermissionGrantType="grant"
                            allowsInlineMediaPlayback
                            startInLoadingState={false}
                            setSupportMultipleWindows={false}
                            style={{ flex: 1, backgroundColor: colors.background }}
                        />
                    )}
                    {showSpinner && (
                        <View style={[styles.center, styles.overlay, { backgroundColor: colors.background }]}>
                            <ActivityIndicator size="large" color={colors.primary} />
                            <Text style={[styles.loadingText, { color: colors.textDim }]}>
                                Loading secure verification… ({stage})
                            </Text>
                        </View>
                    )}
                    {/* Always-on debug strip: the exact URL (long-press to copy
                        and open in a browser) + the live progress stage. */}
                    <View
                        style={[
                            styles.debugBar,
                            { borderTopColor: colors.border, paddingBottom: Math.max(insets.bottom, 6) },
                        ]}
                    >
                        <Text style={[styles.debugLabel, { color: colors.textDim }]}>stage: {stage}</Text>
                        <Text selectable style={[styles.debugUrl, { color: colors.textDim }]}>
                            {EMBEDDED_URL}
                        </Text>
                    </View>
                </View>
            )}
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1 },
    header: { paddingBottom: 12, paddingHorizontal: 16 },
    headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    headerTitle: { fontSize: 18, fontWeight: '700' },
    backBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
    overlay: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },
    loadingText: { marginTop: 12, fontSize: 14 },
    errText: { marginTop: 12, fontSize: 14, textAlign: 'center', lineHeight: 20 },
    debugBar: { borderTopWidth: 1, paddingHorizontal: 12, paddingTop: 6 },
    debugLabel: { fontSize: 11, textAlign: 'center', marginTop: 8 },
    debugUrl: { fontSize: 11, textAlign: 'center', marginTop: 2 },
    retryBtn: { marginTop: 18, paddingHorizontal: 32, paddingVertical: 12, borderRadius: 12 },
    retryText: { color: '#fff', fontSize: 15, fontWeight: '700' },
});
