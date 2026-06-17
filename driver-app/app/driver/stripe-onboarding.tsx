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

export default function StripeOnboardingScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const { refetch } = useDriverMe();
    const webRef = useRef<WebView>(null);

    const [token, setToken] = useState<string | null>(null);
    const [tokenError, setTokenError] = useState(false);
    const [pageLoading, setPageLoading] = useState(true);
    const exitedRef = useRef(false);

    useEffect(() => {
        let active = true;
        (async () => {
            // Best-effort: hold the OS camera permission up front so Stripe's
            // in-page getUserMedia can capture the driver's government ID. On
            // Android the WebView can only grant the page's camera request if
            // the app already holds CAMERA; iOS prompts on first use anyway. If
            // denied, Stripe falls back to file upload — so never block on this.
            try {
                await ImagePicker.requestCameraPermissionsAsync();
            } catch {
                // ignore — onboarding continues with the file-upload fallback
            }
            try {
                await ensureFreshToken();
                const t = await getAuthHeader();
                if (!active) return;
                if (!t) {
                    setTokenError(true);
                    return;
                }
                setToken(t);
            } catch {
                if (active) setTokenError(true);
            }
        })();
        return () => {
            active = false;
        };
    }, []);

    // Inject the bearer token into the page BEFORE its scripts run, so the
    // embedded component's fetchClientSecret can authenticate its POST.
    const injectedBeforeLoad = useMemo(
        () => `window.__SPINR_TOKEN = ${JSON.stringify(token ?? '')}; true;`,
        [token],
    );

    const finish = (opts: { refresh: boolean; message?: string; type?: 'success' | 'error' }) => {
        if (exitedRef.current) return;
        exitedRef.current = true;
        if (opts.message) showToast(opts.type ?? 'info', opts.type === 'error' ? 'Error' : 'Done', opts.message);
        if (opts.refresh) void refetch();
        router.back();
    };

    const onMessage = (e: WebViewMessageEvent) => {
        const data = e.nativeEvent.data || '';
        if (data === 'exit') {
            // User finished or closed Stripe's flow. Re-pull KYC status; the
            // account.updated webhook is the authoritative gate, so the payout
            // screen reflects "verified" once Stripe confirms.
            finish({ refresh: true, message: 'Verification updated.', type: 'success' });
        } else if (data.startsWith('error:')) {
            finish({
                refresh: false,
                message: 'Could not load verification. Please try again.',
                type: 'error',
            });
        }
    };

    const showSpinner = (!token && !tokenError) || (pageLoading && !tokenError);

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
            ) : (
                <View style={{ flex: 1 }}>
                    {token && (
                        <WebView
                            ref={webRef}
                            source={{ uri: EMBEDDED_URL }}
                            originWhitelist={['https://*']}
                            injectedJavaScriptBeforeContentLoaded={injectedBeforeLoad}
                            onMessage={onMessage}
                            onLoadEnd={() => setPageLoading(false)}
                            onError={() =>
                                finish({
                                    refresh: false,
                                    message: 'Network error loading verification.',
                                    type: 'error',
                                })
                            }
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
                                Loading secure verification…
                            </Text>
                        </View>
                    )}
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
});
