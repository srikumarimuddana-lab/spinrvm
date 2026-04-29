import React, { useMemo, useState, useEffect } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    Switch,
    Modal,
    Pressable,
    ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useLanguageStore } from '../../store/languageStore';
import { languages, Language } from '../../i18n';
import api from '@shared/api/client';
import {
    useNotificationPreferences,
    useUpdateNotificationPreferences,
    useDriverMe,
    useUpdateDriverMe,
} from '@shared/hooks/queries';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function SettingsScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { logout } = useAuthStore();
    const { language, setLanguage, loadLanguage, t } = useLanguageStore();
    const { colors, colorScheme, setTheme } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [showLanguageModal, setShowLanguageModal] = useState(false);

    // Custom alert states
    const [showDeleteStep1, setShowDeleteStep1] = useState(false);
    const [showDeleteStep2, setShowDeleteStep2] = useState(false);
    const [deleteInput, setDeleteInput] = useState('');
    const [showEmergencyAlert, setShowEmergencyAlert] = useState(false);
    const [exportingData, setExportingData] = useState(false);
    const [feedbackAlert, setFeedbackAlert] = useState<{
        visible: boolean; title: string; message?: string;
        variant: 'info' | 'success' | 'danger' | 'warning';
    }>({ visible: false, title: '', variant: 'info' });

    useEffect(() => {
        loadLanguage();
    }, []);

    // Preference states (local)
    const [pushNotifications, setPushNotifications] = useState(true);
    const [rideAlerts, setRideAlerts] = useState(true);
    const [earningsSummary, setEarningsSummary] = useState(true);
    const [promotions, setPromotions] = useState(false);
    const [soundEffects, setSoundEffects] = useState(true);
    const [vibration, setVibration] = useState(true);

    // /notifications/preferences is owned by the useNotificationPreferences
    // hook. The persisted cache means revisiting Settings shows toggles
    // instantly with no spinner; the background refetch keeps them honest.
    const { data: prefsResponse } = useNotificationPreferences();
    const updatePreferences = useUpdateNotificationPreferences();

    // WAV capability — driver declares whether their vehicle is wheelchair-accessible.
    // SK Transportation Act requires WAV requests to be fulfilled when a WAV
    // driver is online in the service area.
    const { data: driverMe } = useDriverMe();
    const updateDriverMe = useUpdateDriverMe();
    const [isWav, setIsWav] = useState(false);
    useEffect(() => {
        if (driverMe?.is_wav != null) setIsWav(Boolean(driverMe.is_wav));
    }, [driverMe?.is_wav]);
    useEffect(() => {
        // Server can return null when the row hasn't been created yet;
        // keep the defaults set above in that case.
        const prefs: any = prefsResponse;
        if (prefs == null) return;
        if (prefs.push_notifications != null) setPushNotifications(Boolean(prefs.push_notifications));
        if (prefs.ride_alerts != null) setRideAlerts(Boolean(prefs.ride_alerts));
        if (prefs.earnings_summary != null) setEarningsSummary(Boolean(prefs.earnings_summary));
        if (prefs.promotions != null) setPromotions(Boolean(prefs.promotions));
        if (prefs.sound_effects != null) setSoundEffects(Boolean(prefs.sound_effects));
        if (prefs.vibration != null) setVibration(Boolean(prefs.vibration));
    }, [prefsResponse]);

    const savePreference = (key: string, value: boolean, revert: () => void) => {
        updatePreferences.mutate({ [key]: value }, {
            onError: () => {
                revert();
                setFeedbackAlert({
                    visible: true,
                    title: 'Preference not saved',
                    message: 'Could not reach the server. Your setting has been reverted — please try again.',
                    variant: 'danger',
                });
            },
        });
    };

    const handleToggle = (key: string, setter: (v: boolean) => void) => (value: boolean) => {
        setter(value);
        savePreference(key, value, () => setter(!value));
    };
    const [navApp, setNavApp] = useState<'default' | 'google' | 'waze'>('default');

    const isDarkOn = colorScheme === 'dark';

    const handleExportData = async () => {
        setExportingData(true);
        try {
            await api.post('/drivers/me/export-data');
            setFeedbackAlert({
                visible: true,
                title: 'Export Requested',
                message: 'Your data export is on its way — check your email.',
                variant: 'success',
            });
        } catch {
            setFeedbackAlert({
                visible: true,
                title: 'Error',
                message: 'Failed to request data export. Please try again.',
                variant: 'danger',
            });
        } finally {
            setExportingData(false);
        }
    };

    const handleDeleteAccount = () => {
        setShowDeleteStep1(true);
    };

    const handleDeleteStep2 = () => {
        setShowDeleteStep1(false);
        setDeleteInput('');
        setShowDeleteStep2(true);
    };

    const executeDelete = async () => {
        if (deleteInput.trim().toUpperCase() !== 'DELETE') {
            setFeedbackAlert({
                visible: true,
                title: 'Not Confirmed',
                message: 'You must type DELETE to confirm account deletion.',
                variant: 'warning',
            });
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
            setFeedbackAlert({
                visible: true,
                title: 'Error',
                message: err.message || 'Failed to delete account. Please contact support.',
                variant: 'danger',
            });
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
            <Switch
                value={value}
                onValueChange={onToggle}
                trackColor={{ false: colors.surfaceLight, true: `${colors.primary}60` }}
                thumbColor={value ? colors.primary : colors.textDim}
            />
        </View>
    );

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[colors.surface, colors.background]} style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Settings</Text>
                    <View style={{ width: 40 }} />
                </View>
            </LinearGradient>

            <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + 40 }} showsVerticalScrollIndicator={false}>
                {/* Notifications Section */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Notifications</Text>
                    <View style={styles.card}>
                        {renderToggle('Push Notifications', 'Receive real-time notifications', pushNotifications, handleToggle('push_notifications', setPushNotifications), 'notifications', colors.primary)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Ride Alerts', 'Sound and vibration for new rides', rideAlerts, handleToggle('ride_alerts', setRideAlerts), 'car', colors.orange)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Daily Earnings Summary', 'Get notified about daily earnings', earningsSummary, handleToggle('earnings_summary', setEarningsSummary), 'wallet', colors.gold)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Promotions & Offers', 'Special offers and promotions', promotions, handleToggle('promotions', setPromotions), 'gift', colors.primaryDark)}
                    </View>
                </View>

                {/* Sound & Haptics */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Sound & Haptics</Text>
                    <View style={styles.card}>
                        {renderToggle('Sound Effects', 'Play sounds for ride events', soundEffects, handleToggle('sound_effects', setSoundEffects), 'volume-high', colors.primary)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Vibration', 'Haptic feedback for new rides', vibration, handleToggle('vibration', setVibration), 'phone-portrait', colors.orange)}
                    </View>
                </View>

                {/* Appearance */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Appearance</Text>
                    <View style={styles.card}>
                        {renderToggle('Dark Mode', 'Reduce eye strain at night', isDarkOn, (v) => setTheme(v ? 'dark' : 'light'), 'moon', '#6366F1')}
                    </View>
                </View>

                {/* Navigation */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Navigation</Text>
                    <View style={styles.card}>
                        {(['default', 'google', 'waze'] as const).map((app, i) => (
                            <React.Fragment key={app}>
                                {i > 0 && <View style={styles.cardDivider} />}
                                <TouchableOpacity
                                    style={styles.navOption}
                                    onPress={() => setNavApp(app)}
                                >
                                    <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                        <Ionicons
                                            name={app === 'default' ? 'map' : app === 'google' ? 'navigate' : 'compass'}
                                            size={18}
                                            color={colors.primary}
                                        />
                                    </View>
                                    <Text style={styles.settingLabel}>
                                        {app === 'default' ? 'Default Maps' : app === 'google' ? 'Google Maps' : 'Waze'}
                                    </Text>
                                    <View style={[styles.radio, navApp === app && styles.radioActive]}>
                                        {navApp === app && <View style={styles.radioInner} />}
                                    </View>
                                </TouchableOpacity>
                            </React.Fragment>
                        ))}
                    </View>
                </View>

                {/* Vehicle Accessibility */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Vehicle Accessibility</Text>
                    <View style={styles.card}>
                        {renderToggle(
                            'Wheelchair-Accessible Vehicle (WAV)',
                            'Enables you to receive ride requests from riders who need an accessible vehicle (SK Transportation Act)',
                            isWav,
                            (value) => {
                                setIsWav(value);
                                updateDriverMe.mutate({ is_wav: value }, {
                                    onError: () => {
                                        setIsWav(!value);
                                        setFeedbackAlert({
                                            visible: true,
                                            title: 'Could not save',
                                            message: 'WAV setting could not be updated. Please try again.',
                                            variant: 'danger',
                                        });
                                    },
                                });
                            },
                            'accessibility',
                            '#0EA5E9',
                        )}
                    </View>
                    <Text style={styles.wavHint}>
                        When enabled, you will appear in searches for wheelchair-accessible rides.
                        Ensure your vehicle meets WAV requirements before enabling.
                    </Text>
                </View>

                {/* Account */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Account</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => setShowLanguageModal(true)}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="language" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>{t('settings.language')}</Text>
                            <Text style={styles.settingValue}>{language === 'en' ? 'English' : 'Français'}</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => router.push('/driver/faq' as any)}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="help-circle" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>Help Center & FAQ</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/legal' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="document-text" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>Legal</Text>
                            <Text style={styles.settingValue}>Terms & Privacy</Text>
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
                            <Text style={styles.settingLabel}>Tax Documents (T4A)</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                    </View>
                </View>

                {/* Emergency Assistance */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Emergency Assistance</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={() => setShowEmergencyAlert(true)}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(239,68,68,0.1)' }]}>
                                <Ionicons name="call" size={18} color="#EF4444" />
                            </View>
                            <Text style={[styles.settingLabel, { color: '#EF4444' }]}>Call Emergency (911)</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/report-safety' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(245,158,11,0.1)' }]}>
                                <Ionicons name="warning" size={18} color="#F59E0B" />
                            </View>
                            <Text style={styles.settingLabel}>Report Safety Issue</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/driver/emergency-contacts' as any)}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(34,197,94,0.1)' }]}>
                                <Ionicons name="people" size={18} color="#22C55E" />
                            </View>
                            <Text style={styles.settingLabel}>Emergency Contacts</Text>
                            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
                        </TouchableOpacity>
                    </View>
                </View>

                {/* Privacy */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Privacy</Text>
                    <View style={styles.card}>
                        <TouchableOpacity
                            style={styles.actionRow}
                            onPress={handleExportData}
                            disabled={exportingData}
                        >
                            <View style={[styles.settingIcon, { backgroundColor: `${colors.primary}12` }]}>
                                <Ionicons name="download-outline" size={18} color={colors.primary} />
                            </View>
                            <Text style={styles.settingLabel}>Download My Data</Text>
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
                        <Text style={styles.deleteText}>Delete Account</Text>
                    </TouchableOpacity>
                </View>

                {/* App Version */}
                <Text style={styles.version}>Spinr Driver v1.0.0</Text>
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

            {/* Custom Alert Modals */}
            <CustomAlert
                visible={showDeleteStep1}
                title="Delete Account"
                message={'Your account will be scheduled for deletion with a 30-day recovery window.\n\nYou can reactivate by contacting support within 30 days; after that, all your data, earnings history, and ride records are permanently deleted.'}
                variant="danger"
                icon="trash-outline"
                buttons={[
                    { text: 'Cancel', style: 'cancel' },
                    { text: 'Yes, Continue', style: 'destructive', onPress: handleDeleteStep2 },
                ]}
                onClose={() => setShowDeleteStep1(false)}
            />

            <CustomAlert
                visible={showDeleteStep2}
                title="Confirm Deletion"
                message='Type "DELETE" below to permanently delete your account.'
                variant="danger"
                icon="alert-circle"
                showInput
                inputPlaceholder="Type DELETE"
                inputValue={deleteInput}
                onInputChange={setDeleteInput}
                buttons={[
                    { text: 'Cancel', style: 'cancel' },
                    { text: 'Delete Forever', style: 'destructive', onPress: executeDelete },
                ]}
                onClose={() => setShowDeleteStep2(false)}
            />

            <CustomAlert
                visible={showEmergencyAlert}
                title="Emergency Services"
                message="Call 911 for immediate emergency assistance."
                variant="danger"
                icon="call"
                buttons={[
                    { text: 'Cancel', style: 'cancel' },
                    { text: 'Call 911', style: 'destructive' },
                ]}
                onClose={() => setShowEmergencyAlert(false)}
            />

            <CustomAlert
                visible={feedbackAlert.visible}
                title={feedbackAlert.title}
                message={feedbackAlert.message}
                variant={feedbackAlert.variant}
                buttons={[{ text: 'OK', style: 'default' }]}
                onClose={() => setFeedbackAlert(prev => ({ ...prev, visible: false }))}
            />
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        header: {
            paddingBottom: 12,
            paddingHorizontal: 16,
        },
        headerRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
        },
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },
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
    });
}
