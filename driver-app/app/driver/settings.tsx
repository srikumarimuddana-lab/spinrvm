import React, { useMemo, useState, useEffect } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    Modal,
    Pressable,
    ActivityIndicator,
    Alert,
    TextInput,
    Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { ScreenHeader } from '../../components/ScreenHeader';
import { useAuthStore } from '@shared/store/authStore';
import { useLanguageStore } from '../../store/languageStore';
import { useNavStore } from '../../store/navStore';
import { useAlertPrefsStore } from '../../store/alertPrefsStore';
import { languages } from '../../i18n';
import api, { getApiErrorMessage } from '@shared/api/client';
import {
    useNotificationPreferences,
    useUpdateNotificationPreferences,
    useDriverMe,
    useUpdateDriverMe,
} from '@shared/hooks/queries';
import { showToast } from '../../hooks/useToast';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function SettingsScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { logout } = useAuthStore();
    const { language, setLanguage, loadLanguage, t } = useLanguageStore();
    const { navApp, setNavApp, loadNavApp } = useNavStore();
    const { colors, colorScheme, setTheme } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [showLanguageModal, setShowLanguageModal] = useState(false);

    const [showDeleteStep2, setShowDeleteStep2] = useState(false);
    const [deleteInput, setDeleteInput] = useState('');
    const [exportingData, setExportingData] = useState(false);

    // Sound & haptics are device behaviours → device-local AsyncStorage
    // (alertPrefsStore), not notification_preferences on the server.
    const { soundEffects, vibration, setSoundEffects, setVibration, loadAlertPrefs } = useAlertPrefsStore();

    useEffect(() => {
        loadLanguage();
        loadNavApp();
        loadAlertPrefs();
        // All three are stable store actions; adding them doesn't change
        // this mount-only effect's firing.
    }, [loadLanguage, loadNavApp, loadAlertPrefs]);

    // Preference states (local)
    const [pushNotifications, setPushNotifications] = useState(true);
    const [rideAlerts, setRideAlerts] = useState(true);
    const [earningsSummary, setEarningsSummary] = useState(true);
    const [promotions, setPromotions] = useState(false);

    // Marketing consent (CASL): express opt-in → DEFAULTS OFF. Backed by
    // /marketing/preferences (separate from notification_preferences above:
    // those are operational push toggles; these are commercial-message consent).
    const [marketingEmails, setMarketingEmails] = useState(false);
    const [marketingSms, setMarketingSms] = useState(false);
    useEffect(() => {
        let active = true;
        api.get('/marketing/preferences')
            .then((res: any) => {
                const p = res?.data ?? res ?? {};
                if (!active) return;
                setMarketingEmails(Boolean(p.email_opt_in));
                setMarketingSms(Boolean(p.sms_opt_in));
            })
            .catch(() => { /* default off on failure */ });
        return () => { active = false; };
    }, []);
    const handleMarketingToggle = (
        field: 'email_opt_in' | 'sms_opt_in',
        setter: (v: boolean) => void,
    ) => async (value: boolean) => {
        setter(value);
        try {
            await api.put('/marketing/preferences', { [field]: value, source: 'driver_app' });
        } catch {
            setter(!value);
            showToast('error', t('settings.prefNotSavedTitle'), t('settings.prefNotSavedMsg'));
        }
    };

    // /notifications/preferences is owned by the useNotificationPreferences
    // hook. The persisted cache means revisiting Settings shows toggles
    // instantly with no spinner; the background refetch keeps them honest.
    const { data: prefsResponse } = useNotificationPreferences();
    const updatePreferences = useUpdateNotificationPreferences();

    // WAV capability — driver declares whether their vehicle is wheelchair-accessible.
    // SK Transportation Act requires WAV requests to be fulfilled when a WAV
    // driver is online in the service area.
    const { data: driverMeRaw } = useDriverMe();
    const driverMe = driverMeRaw as { is_wav?: boolean | null } | undefined;
    const updateDriverMe = useUpdateDriverMe();
    const [isWav, setIsWav] = useState(false);
    // Extracted so the effect's dep array below is a single statically
    // checkable primitive instead of an inline `driverMe?.is_wav` expression
    // (the "complex expression" the linter flagged) — same value, same
    // re-run trigger (primitive equality on this boolean/null), no behavior
    // change.
    const driverMeIsWav = driverMe?.is_wav;
    useEffect(() => {
        // Sync local WAV toggle from the cached/refetched driver row. Local
        // edits aren't fed back into driverMe, so this can't loop.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        if (driverMeIsWav != null) setIsWav(Boolean(driverMeIsWav));
    }, [driverMeIsWav]);
    useEffect(() => {
        // Server can return null when the row hasn't been created yet;
        // keep the defaults set above in that case. Sync local notification
        // toggles from the cached/refetched prefs response — local edits
        // aren't fed back into prefsResponse, so this can't loop.
        const prefs: any = prefsResponse;
        if (prefs == null) return;
        // eslint-disable-next-line react-hooks/set-state-in-effect
        if (prefs.push_enabled != null) setPushNotifications(Boolean(prefs.push_enabled));
        if (prefs.ride_updates != null) setRideAlerts(Boolean(prefs.ride_updates));
        if (prefs.earnings_summary != null) setEarningsSummary(Boolean(prefs.earnings_summary));
        if (prefs.promotions != null) setPromotions(Boolean(prefs.promotions));
    }, [prefsResponse]);

    const savePreference = (key: string, value: boolean, revert: () => void) => {
        updatePreferences.mutate({ [key]: value }, {
            onError: () => {
                revert();
                showToast('error', t('settings.prefNotSavedTitle'), t('settings.prefNotSavedMsg'));
            },
        });
    };

    const handleToggle = (key: string, setter: (v: boolean) => void) => (value: boolean) => {
        setter(value);
        savePreference(key, value, () => setter(!value));
    };

    const isDarkOn = colorScheme === 'dark';

    // Platform-aware navigation choices. The "default" option maps to each
    // platform's native maps app (Apple Maps on iOS, Google Maps on Android),
    // matching what the launcher in ActiveRidePanel actually opens. Google Maps
    // is only offered as a separate option on iOS (on Android it *is* default).
    const navOptions: { key: 'default' | 'google' | 'waze'; label: string; icon: string }[] =
        Platform.OS === 'ios'
            ? [
                  { key: 'default', label: t('settings.navApple'), icon: 'map' },
                  { key: 'google', label: t('settings.navGoogle'), icon: 'navigate' },
                  { key: 'waze', label: t('settings.navWaze'), icon: 'compass' },
              ]
            : [
                  { key: 'default', label: t('settings.navGoogle'), icon: 'navigate' },
                  { key: 'waze', label: t('settings.navWaze'), icon: 'compass' },
              ];

    const handleExportData = async () => {
        setExportingData(true);
        try {
            await api.post('/drivers/me/export-data');
            showToast('success', t('settings.exportRequestedTitle'), t('settings.exportRequestedMsg'));
        } catch {
            showToast('error', t('settings.exportFailedTitle'), t('settings.exportFailedMsg'));
        } finally {
            setExportingData(false);
        }
    };

    const handleDeleteAccount = () => {
        Alert.alert(
            t('settings.deleteAccount'),
            t('settings.deleteAccountConfirmMsg'),
            [
                { text: t('settings.cancel'), style: 'cancel' },
                { text: t('settings.deleteContinue'), style: 'destructive', onPress: () => { setDeleteInput(''); setShowDeleteStep2(true); } },
            ]
        );
    };

    const executeDelete = async () => {
        if (deleteInput.trim().toUpperCase() !== 'DELETE') {
            showToast('warning', t('settings.notConfirmedTitle'), t('settings.notConfirmedMsg'));
            return;
        }
        try {
            // PIPEDA-compliant 30-day grace window: DELETE /users/account
            // schedules the deletion rather than executing it immediately, so
            // drivers can recover the account by contacting support within 30
            // days. DELETE /users/profile (immediate, irreversible) is reserved
            // for internal admin tooling.
            await api.delete('/users/account');
            setShowDeleteStep2(false);
            await logout();
            router.replace('/login' as any);
        } catch (err: any) {
            setShowDeleteStep2(false);
            // Deletion rejections are actionable (active ride, unsettled
            // balance, pending payout) — show the backend's reason.
            showToast('error', t('settings.deleteFailedTitle'), getApiErrorMessage(err, t('settings.deleteFailedMsg')));
        }
    };

    const renderToggle = (
        label: string,
        description: string,
        value: boolean,
        onToggle: (val: boolean) => void,
        icon: string,
        iconColor: string = colors.primary,
    ) => (
        <View style={styles.settingRow}>
            <View style={[styles.settingIcon, { backgroundColor: `${iconColor}12` }]}>
                <Ionicons name={icon as any} size={18} color={iconColor} />
            </View>
            <View style={{ flex: 1 }}>
                <Text style={styles.settingLabel}>{label}</Text>
                <Text style={styles.settingDesc}>{description}</Text>
            </View>
            <TouchableOpacity
                activeOpacity={0.8}
                onPress={() => onToggle(!value)}
                style={[
                    styles.toggle,
                    { backgroundColor: value ? `${colors.primary}60` : colors.surfaceLight },
                ]}
            >
                <View
                    style={[
                        styles.toggleThumb,
                        { backgroundColor: value ? colors.primary : colors.textDim },
                        value ? { alignSelf: 'flex-end' } : { alignSelf: 'flex-start' },
                    ]}
                />
            </TouchableOpacity>
        </View>
    );

    return (
        <View style={styles.container}>
            <ScreenHeader title={t('settings.title')} />

            <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + 40 }} showsVerticalScrollIndicator={false}>
                {/* Notifications Section */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.notifications')}</Text>
                    <View style={styles.card}>
                        {renderToggle(t('settings.pushNotifications'), t('settings.pushNotificationsDesc'), pushNotifications, handleToggle('push_enabled', setPushNotifications), 'notifications', colors.primary)}
                        <View style={styles.cardDivider} />
                        {renderToggle(t('settings.rideAlerts'), t('settings.rideAlertsDesc'), rideAlerts, handleToggle('ride_updates', setRideAlerts), 'car', colors.orange)}
                        <View style={styles.cardDivider} />
                        {renderToggle(t('settings.earningsSummary'), t('settings.earningsSummaryDesc'), earningsSummary, handleToggle('earnings_summary', setEarningsSummary), 'wallet', colors.gold)}
                        <View style={styles.cardDivider} />
                        {renderToggle(t('settings.promotions'), t('settings.promotionsDesc'), promotions, handleToggle('promotions', setPromotions), 'gift', colors.primaryDark)}
                    </View>
                </View>

                {/* Marketing (CASL consent) */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionMarketing')}</Text>
                    <View style={styles.card}>
                        {renderToggle(t('settings.marketingEmails'), t('settings.marketingEmailsDesc'), marketingEmails, handleMarketingToggle('email_opt_in', setMarketingEmails), 'mail', colors.primary)}
                        <View style={styles.cardDivider} />
                        {renderToggle(t('settings.marketingSms'), t('settings.marketingSmsDesc'), marketingSms, handleMarketingToggle('sms_opt_in', setMarketingSms), 'chatbubble-ellipses', colors.primary)}
                    </View>
                </View>

                {/* Sound & Haptics */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionSound')}</Text>
                    <View style={styles.card}>
                        {renderToggle(t('settings.soundEffects'), t('settings.soundEffectsDesc'), soundEffects, setSoundEffects, 'volume-high', colors.primary)}
                        <View style={styles.cardDivider} />
                        {renderToggle(t('settings.vibration'), t('settings.vibrationDesc'), vibration, setVibration, 'phone-portrait', colors.orange)}
                    </View>
                </View>

                {/* Appearance */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionAppearance')}</Text>
                    <View style={styles.card}>
                        {renderToggle(t('settings.darkMode'), t('settings.darkModeDesc'), isDarkOn, (v) => setTheme(v ? 'dark' : 'light'), 'moon', '#6366F1')}
                    </View>
                </View>

                {/* Navigation */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionNavigation')}</Text>
                    <View style={styles.card}>
                        {navOptions.map((opt, i) => (
                            <React.Fragment key={opt.key}>
                                {i > 0 && <View style={styles.cardDivider} />}
                                <TouchableOpacity
                                    style={styles.navOption}
                                    onPress={() => { setNavApp(opt.key); }}
                                >
                                    <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                        <Ionicons name={opt.icon as any} size={18} color={colors.primary} />
                                    </View>
                                    <Text style={styles.settingLabel}>{opt.label}</Text>
                                    <View style={[styles.radio, navApp === opt.key && styles.radioActive]}>
                                        {navApp === opt.key && <View style={styles.radioInner} />}
                                    </View>
                                </TouchableOpacity>
                            </React.Fragment>
                        ))}
                    </View>
                </View>

                {/* Vehicle Accessibility */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionAccessibility')}</Text>
                    <View style={styles.card}>
                        {renderToggle(
                            t('settings.wavTitle'),
                            t('settings.wavDesc'),
                            isWav,
                            (value) => {
                                setIsWav(value);
                                updateDriverMe.mutate({ is_wav: value }, {
                                    onError: () => {
                                        setIsWav(!value);
                                        showToast('error', t('settings.wavNotSavedTitle'), t('settings.wavNotSavedMsg'));
                                    },
                                });
                            },
                            'accessibility',
                            '#0EA5E9',
                        )}
                    </View>
                    <Text style={styles.wavHint}>{t('settings.wavHint')}</Text>
                </View>

                {/* Account */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.account')}</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => setShowLanguageModal(true)}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="language" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.language')}</Text>
                            <Text style={styles.settingValue}>{languages.find((l) => l.code === language)?.nativeName ?? 'English'}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/legal' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="document-text" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.legal')}</Text>
                            <Text style={styles.settingValue}>{t('settings.termsAndPrivacy')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/policies' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="library" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.morePolicies')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/crc-consent' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="shield-checkmark" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.backgroundCheckConsent')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/appeal' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="hand-left" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.appealAccountStatus')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => router.push('/driver/tax-documents')}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="receipt" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.taxDocuments')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => router.push('/driver/destination-mode' as any)}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="navigate" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.destinationModeTitle')}</Text>
                            <Text style={styles.settingValue}>{t('settings.destinationModeDesc')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                    </View>
                </View>

                {/* Emergency Assistance */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionEmergency')}</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => Alert.alert(t('settings.emergencyServicesTitle'), t('settings.emergencyServicesMsg'), [{ text: t('settings.cancel'), style: 'cancel' }, { text: t('settings.call911'), style: 'destructive', onPress: () => Linking.openURL('tel:911') }])}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(239,68,68,0.1)' }]}>
                                <Ionicons name="call" size={18} color="#EF4444" />
                            </View>
                            <Text style={[styles.settingLabel, { color: '#EF4444' }]}>{t('settings.callEmergency')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/report-safety' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(245,158,11,0.1)' }]}>
                                <Ionicons name="warning" size={18} color="#F59E0B" />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.reportSafety')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/driver/emergency-contacts' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(34,197,94,0.1)' }]}>
                                <Ionicons name="people" size={18} color="#22C55E" />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.emergencyContacts')}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                    </View>
                </View>

                {/* Privacy */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>{t('settings.sectionPrivacy')}</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={handleExportData}
                            disabled={exportingData}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="mail-outline" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.downloadData')}</Text>
                            {exportingData ? (
                                <ActivityIndicator size="small" color={colors.primary} />
                            ) : (
                                <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                            )}
                        </TouchableOpacity>
                    </View>
                </View>

                {/* Danger Zone */}
                <View style={styles.section}>
                    <TouchableOpacity style={styles.deleteBtn} onPress={handleDeleteAccount}>
                        <Ionicons name="trash-outline" size={16} color={colors.error} />
                        <Text style={styles.deleteText}>{t('settings.deleteAccount')}</Text>
                    </TouchableOpacity>
                </View>

                {/* App Version */}
                <Text style={styles.version}>{t('settings.version')}</Text>
            </ScrollView>

            {/* Language Modal */}
            <Modal
                visible={showLanguageModal}
                transparent
                animationType="slide"
                onRequestClose={() => setShowLanguageModal(false)}
            >
                <Pressable style={styles.modalOverlay} onPress={() => setShowLanguageModal(false)}>
                    <Pressable style={[styles.modalContent, { paddingBottom: Math.max(insets.bottom + 12, 20) }]} onPress={(e) => e.stopPropagation()}>
                        <View style={styles.modalHeader}>
                            <Text style={styles.modalTitle}>{t('settings.language')}</Text>
                            <TouchableOpacity onPress={() => setShowLanguageModal(false)}>
                                <Ionicons name="close" size={24} color={colors.text} />
                            </TouchableOpacity>
                        </View>
                        <View style={styles.languageOptions}>
                            {languages.map((lang) => (
                                <TouchableOpacity
                                    key={lang.code}
                                    style={[
                                        styles.languageOption,
                                        language === lang.code && styles.languageOptionSelected,
                                    ]}
                                    onPress={() => {
                                        setLanguage(lang.code);
                                        setShowLanguageModal(false);
                                    }}
                                >
                                    <View>
                                        <Text style={[
                                            styles.languageName,
                                            language === lang.code && styles.languageNameSelected,
                                        ]}>
                                            {lang.nativeName}
                                        </Text>
                                        <Text style={styles.languageEnglish}>{lang.name}</Text>
                                    </View>
                                    {language === lang.code && (
                                        <Ionicons name="checkmark-circle" size={24} color={colors.primary} />
                                    )}
                                </TouchableOpacity>
                            ))}
                        </View>
                    </Pressable>
                </Pressable>
            </Modal>

            {/* Delete confirmation step 2 — needs text input */}
            <Modal
                visible={showDeleteStep2}
                transparent
                animationType="fade"
                onRequestClose={() => setShowDeleteStep2(false)}
            >
                <Pressable style={styles.deleteOverlay} onPress={() => setShowDeleteStep2(false)}>
                    <Pressable style={styles.deleteModal} onPress={e => e.stopPropagation()}>
                        <Ionicons name="alert-circle" size={40} color={colors.error} style={{ marginBottom: 12 }} />
                        <Text style={styles.deleteModalTitle}>{t('settings.confirmDeletionTitle')}</Text>
                        <Text style={styles.deleteModalMsg}>{t('settings.confirmDeletionMsg')}</Text>
                        <TextInput
                            style={styles.deleteInput}
                            placeholder={t('settings.typeDelete')}
                            placeholderTextColor={colors.textDim}
                            value={deleteInput}
                            onChangeText={setDeleteInput}
                            autoCapitalize="characters"
                        />
                        <View style={{ flexDirection: 'row', gap: 12, marginTop: 16 }}>
                            <TouchableOpacity style={[styles.deleteModalBtn, { backgroundColor: colors.surface }]} onPress={() => setShowDeleteStep2(false)}>
                                <Text style={[styles.deleteModalBtnText, { color: colors.text }]}>{t('settings.cancel')}</Text>
                            </TouchableOpacity>
                            <TouchableOpacity style={[styles.deleteModalBtn, { backgroundColor: colors.error }]} onPress={executeDelete}>
                                <Text style={[styles.deleteModalBtnText, { color: '#fff' }]}>{t('settings.deleteForever')}</Text>
                            </TouchableOpacity>
                        </View>
                    </Pressable>
                </Pressable>
            </Modal>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        section: { paddingHorizontal: 16, marginTop: 20 },
        sectionTitle: {
            color: colors.text,
            fontSize: 16,
            fontWeight: '700',
            marginBottom: 10,
        },
        card: {
            backgroundColor: colors.surface,
            borderRadius: 18,
            padding: 4,
            borderWidth: 1,
            borderColor: colors.border,
        },
        cardDivider: {
            height: 1,
            backgroundColor: colors.surfaceLight,
            marginHorizontal: 14,
        },
        settingRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            padding: 14,
        },
        settingIcon: {
            width: 36,
            height: 36,
            borderRadius: 10,
            justifyContent: 'center',
            alignItems: 'center',
        },
        settingLabel: { color: colors.text, fontSize: 14, fontWeight: '500' },
        settingDesc: { color: colors.textDim, fontSize: 11, marginTop: 1 },
        toggle: {
            width: 48,
            height: 28,
            borderRadius: 14,
            padding: 3,
            justifyContent: 'center',
        },
        toggleThumb: {
            width: 22,
            height: 22,
            borderRadius: 11,
        },
        settingValue: {
            color: colors.textDim,
            fontSize: 13,
            marginLeft: 'auto',
            marginRight: 6,
        },
        navOption: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            padding: 14,
        },
        radio: {
            width: 22,
            height: 22,
            borderRadius: 11,
            borderWidth: 2,
            borderColor: colors.border,
            justifyContent: 'center',
            alignItems: 'center',
            marginLeft: 'auto',
        },
        radioActive: { borderColor: colors.primary },
        radioInner: {
            width: 12,
            height: 12,
            borderRadius: 6,
            backgroundColor: colors.primary,
        },
        actionRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            padding: 14,
        },
        deleteBtn: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            padding: 16,
            backgroundColor: 'rgba(255,71,87,0.08)',
            borderRadius: 16,
            borderWidth: 1,
            borderColor: 'rgba(255,71,87,0.2)',
        },
        deleteText: { color: colors.error, fontSize: 14, fontWeight: '600' },
        wavHint: {
            color: colors.textDim,
            fontSize: 11,
            marginTop: 8,
            lineHeight: 16,
            paddingHorizontal: 4,
        },
        version: {
            color: colors.textDim,
            textAlign: 'center',
            fontSize: 12,
            marginTop: 20,
            marginBottom: 20,
        },
        modalOverlay: {
            flex: 1,
            backgroundColor: 'rgba(0,0,0,0.5)',
            justifyContent: 'flex-end',
        },
        modalContent: {
            backgroundColor: colors.background,
            borderTopLeftRadius: 20,
            borderTopRightRadius: 20,
        },
        modalHeader: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: 20,
            borderBottomWidth: 1,
            borderBottomColor: colors.border,
        },
        modalTitle: {
            fontSize: 18,
            fontWeight: '600',
            color: colors.text,
        },
        languageOptions: {
            padding: 20,
        },
        languageOption: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingVertical: 16,
            paddingHorizontal: 16,
            borderRadius: 12,
            marginBottom: 8,
            backgroundColor: colors.surface,
        },
        languageOptionSelected: {
            backgroundColor: `${colors.primary}1A`,
        },
        languageName: {
            fontSize: 16,
            fontWeight: '500',
            color: colors.text,
        },
        languageNameSelected: {
            color: colors.primary,
        },
        languageEnglish: {
            fontSize: 13,
            color: colors.textDim,
            marginTop: 2,
        },
        deleteOverlay: {
            flex: 1,
            backgroundColor: 'rgba(0,0,0,0.5)',
            justifyContent: 'center',
            alignItems: 'center' as const,
            padding: 24,
        },
        deleteModal: {
            backgroundColor: colors.surface,
            borderRadius: 20,
            padding: 24,
            width: '100%',
            alignItems: 'center' as const,
        },
        deleteModalTitle: {
            fontSize: 18,
            fontWeight: '700' as const,
            color: colors.text,
            marginBottom: 8,
        },
        deleteModalMsg: {
            fontSize: 14,
            color: colors.textDim,
            textAlign: 'center' as const,
            marginBottom: 16,
        },
        deleteInput: {
            width: '100%',
            backgroundColor: colors.background,
            borderRadius: 12,
            padding: 14,
            fontSize: 16,
            color: colors.text,
            borderWidth: 1,
            borderColor: colors.border,
            textAlign: 'center' as const,
        },
        deleteModalBtn: {
            flex: 1,
            paddingVertical: 14,
            borderRadius: 12,
            alignItems: 'center' as const,
        },
        deleteModalBtnText: {
            fontSize: 15,
            fontWeight: '600' as const,
        },
    });
}
