import React, { useEffect, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Modal, FlatList,
} from 'react-native';
import CustomToggle from '../components/CustomToggle';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '@shared/store/authStore';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import i18n, { useTranslation, useLanguageStore, LANGUAGES, type Language } from '../i18n';
import {
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from '@shared/hooks/queries';

export default function SettingsScreen() {
  const router = useRouter();
  const { user } = useAuthStore();
  const { colors, isDark, colorScheme, setTheme } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { language, setLanguage } = useLanguageStore();
  const { t } = useTranslation();

  const [pushEnabled, setPushEnabled] = useState(true);
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [smsEnabled, setSmsEnabled] = useState(true);
  const [showLangModal, setShowLangModal] = useState(false);
  const handleDarkModeToggle = (value: boolean) => {
    setTheme(value ? 'dark' : 'light');
  };

  // Real preference state lives server-side at /notifications/preferences —
  // mirrors the driver app's wiring (driver-app/app/driver/settings.tsx).
  const { data: notificationPrefs } = useNotificationPreferences();
  const updatePreferences = useUpdateNotificationPreferences();

  useEffect(() => {
    const prefs: any = notificationPrefs;
    if (prefs == null) return;
    if (prefs.push_enabled != null) setPushEnabled(Boolean(prefs.push_enabled));
    if (prefs.email_enabled != null) setEmailEnabled(Boolean(prefs.email_enabled));
    if (prefs.sms_enabled != null) setSmsEnabled(Boolean(prefs.sms_enabled));
  }, [notificationPrefs]);

  const handleNotificationToggle = (key: string, setter: (v: boolean) => void) => (value: boolean) => {
    setter(value);
    updatePreferences.mutate({ [key]: value }, {
      onError: () => {
        setter(!value);
        showToast(t('settings.prefNotSavedTitle'), t('settings.prefNotSavedMsg'), 'danger');
      },
    });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>{t('settings.title')}</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {/* Notifications */}
        <Text style={styles.sectionTitle}>{t('settings.notifications')}</Text>
        <View style={styles.card}>
          <SettingToggle icon="notifications" iconColor="#F59E0B" iconBg="#FEF3C7"
            title={t('settings.push_notifications')} subtitle={t('settings.push_notifications_subtitle')}
            value={pushEnabled} onToggle={handleNotificationToggle('push_enabled', setPushEnabled)} />
          <SettingToggle icon="mail" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title={t('settings.email_notifications')} subtitle={t('settings.email_notifications_subtitle')}
            value={emailEnabled} onToggle={handleNotificationToggle('email_enabled', setEmailEnabled)} />
          <SettingToggle icon="chatbubble" iconColor="#10B981" iconBg="#ECFDF5"
            title={t('settings.sms_notifications')} subtitle={t('settings.sms_notifications_subtitle')}
            value={smsEnabled} onToggle={handleNotificationToggle('sms_enabled', setSmsEnabled)} />
        </View>

        {/* Appearance */}
        <Text style={styles.sectionTitle}>{t('settings.appearance')}</Text>
        <View style={styles.card}>
          <SettingToggle icon="moon" iconColor="#6366F1" iconBg="#EEF2FF"
            title={t('settings.dark_mode')} subtitle={t('settings.dark_mode_subtitle')}
            value={isDark} onToggle={handleDarkModeToggle} />
        </View>

        {/* Language */}
        <Text style={styles.sectionTitle}>{t('settings.language_region')}</Text>
        <View style={styles.card}>
          <TouchableOpacity style={styles.row} onPress={() => setShowLangModal(true)}>
            <View style={[styles.rowIcon, { backgroundColor: '#DBEAFE' }]}>
              <Ionicons name="globe" size={20} color="#3B82F6" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{t('settings.language')}</Text>
              <Text style={styles.rowSub}>
                {LANGUAGES.find(l => l.code === language)?.flag}{' '}
                {LANGUAGES.find(l => l.code === language)?.nativeName ?? 'English'}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.row} onPress={() => router.push('/privacy-settings' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: colors.surfaceLight }]}>
              <Ionicons name="lock-closed" size={20} color={colors.textSecondary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{t('settings.privacy_data')}</Text>
              <Text style={styles.rowSub}>{t('settings.privacy_data_subtitle')}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        {/* Account */}
        <Text style={styles.sectionTitle}>{t('settings.account')}</Text>
        <View style={styles.card}>
          <TouchableOpacity style={styles.row} onPress={() => router.push('/manage-cards' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: '#EDE9FE' }]}>
              <Ionicons name="card" size={20} color="#7C3AED" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{t('settings.payment_methods')}</Text>
              <Text style={styles.rowSub}>{t('settings.payment_methods_subtitle')}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.row} onPress={() => router.push('/saved-places' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: '#FEF3C7' }]}>
              <Ionicons name="bookmark" size={20} color="#F59E0B" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{t('settings.saved_places')}</Text>
              <Text style={styles.rowSub}>{t('settings.saved_places_subtitle')}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        {/* About */}
        <Text style={styles.sectionTitle}>{t('settings.about')}</Text>
        <View style={styles.card}>
          <TouchableOpacity style={styles.row} onPress={() => router.push('/legal?type=tos' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: colors.surfaceLight }]}>
              <Ionicons name="document-text" size={20} color={colors.textSecondary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{t('settings.tos')}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.row} onPress={() => router.push('/legal?type=privacy' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: colors.surfaceLight }]}>
              <Ionicons name="eye" size={20} color={colors.textSecondary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{t('settings.privacy_policy')}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        <Text style={styles.version}>Spinr v1.0.2 · {user?.phone || ''}</Text>
      </ScrollView>
      {/* Language Picker Modal */}
      <Modal visible={showLangModal} animationType="slide" transparent onRequestClose={() => setShowLangModal(false)}>
        <TouchableOpacity style={styles.langOverlay} activeOpacity={1} onPress={() => setShowLangModal(false)}>
          <TouchableOpacity activeOpacity={1} style={styles.langSheet}>
            <View style={styles.langHandle} />
            <Text style={styles.langTitle}>Select Language</Text>
            {LANGUAGES.map((lang) => (
              <TouchableOpacity
                key={lang.code}
                style={[styles.langRow, language === lang.code && styles.langRowActive]}
                onPress={() => { setLanguage(lang.code as Language); setShowLangModal(false); }}
              >
                <Text style={styles.langFlag}>{lang.flag}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={styles.langNative}>{lang.nativeName}</Text>
                  <Text style={styles.langEnglish}>{lang.name}</Text>
                </View>
                {language === lang.code && (
                  <Ionicons name="checkmark-circle" size={22} color={colors.primary} />
                )}
              </TouchableOpacity>
            ))}
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

    </SafeAreaView>
  );
}

function SettingToggle({ icon, iconColor, iconBg, title, subtitle, value, onToggle }: {
  icon: string; iconColor: string; iconBg: string;
  title: string; subtitle: string; value: boolean; onToggle: (v: boolean) => void;
}) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  return (
    <View style={styles.row}>
      <View style={[styles.rowIcon, { backgroundColor: iconBg }]}>
        <Ionicons name={icon as any} size={20} color={iconColor} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.rowSub}>{subtitle}</Text>
      </View>
      <CustomToggle
        value={value}
        onValueChange={onToggle}
        trackColor={{ false: colors.border, true: `${colors.primary}60` }}
        thumbColor={value ? colors.primary : colors.surface}
      />
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    content: { padding: 20, paddingBottom: 40 },
    sectionTitle: { fontSize: 13, fontWeight: '700', color: colors.textDim, letterSpacing: 0.5, marginBottom: 8, marginTop: 20 },
    card: { backgroundColor: colors.surfaceLight, borderRadius: 16, paddingHorizontal: 14 },
    row: {
      flexDirection: 'row', alignItems: 'center', paddingVertical: 14,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    rowIcon: { width: 40, height: 40, borderRadius: 12, justifyContent: 'center', alignItems: 'center', marginRight: 14 },
    rowTitle: { fontSize: 15, fontWeight: '600', color: colors.text },
    rowSub: { fontSize: 12, color: colors.textDim, marginTop: 1 },
    version: { fontSize: 12, color: colors.textDim, textAlign: 'center', marginTop: 24 },

    // Language modal
    langOverlay: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.4)' },
    langSheet: {
      backgroundColor: colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24,
      paddingHorizontal: 20, paddingBottom: 40, paddingTop: 12,
    },
    langHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border, alignSelf: 'center', marginBottom: 16 },
    langTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginBottom: 16 },
    langRow: {
      flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 12,
      borderRadius: 14, marginBottom: 8, backgroundColor: colors.surfaceLight,
      borderWidth: 1.5, borderColor: 'transparent',
    },
    langRowActive: { borderColor: colors.primary, backgroundColor: `${colors.primary}10` },
    langFlag: { fontSize: 28 },
    langNative: { fontSize: 16, fontWeight: '600', color: colors.text },
    langEnglish: { fontSize: 12, color: colors.textDim, marginTop: 1 },
  });
}
