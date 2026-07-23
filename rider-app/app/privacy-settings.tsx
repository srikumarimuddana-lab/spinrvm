import React, { useState, useMemo, useEffect } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
} from 'react-native';
import CustomToggle from '../components/CustomToggle';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import { useTheme } from '@shared/theme/ThemeContext';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import type { ThemeColors } from '@shared/theme/index';
import { useTranslation } from '../i18n';
import {
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from '@shared/hooks/queries';
import {
  checkNotificationPermission,
  requestNotificationPermission,
  openNotificationSettings,
} from '@shared/services/firebase';

export default function PrivacySettingsScreen() {
  const router = useRouter();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { logout } = useAuthStore();
  const { t } = useTranslation();
  // Push mirrors Settings → Notifications: same server-side preference
  // (notification_preferences.push_enabled), so the two screens agree.
  const [pushNotifications, setPushNotifications] = useState(true);
  const { data: notificationPrefs } = useNotificationPreferences();
  const updatePreferences = useUpdateNotificationPreferences();
  useEffect(() => {
    const prefs: any = notificationPrefs;
    if (prefs?.push_enabled != null) setPushNotifications(Boolean(prefs.push_enabled));
  }, [notificationPrefs]);
  const handlePushToggle = async (value: boolean) => {
    if (value) {
      const perm = await checkNotificationPermission();
      if (!perm.granted) {
        const granted = await requestNotificationPermission();
        if (!granted) {
          showToast('Permissions Required', 'Push notifications are disabled in device settings. Tap to open Settings.', 'warning');
          await openNotificationSettings();
          return;
        }
      }
    }
    setPushNotifications(value);
    updatePreferences.mutate({ push_enabled: value }, {
      onError: (err) => {
        setPushNotifications(!value);
        showToast('Update Failed', getApiErrorMessage(err, 'Could not save your preference. Please try again.'), 'danger');
      },
    });
  };
  // Marketing consent (CASL): express opt-in, so every channel DEFAULTS OFF.
  // Hydrated from the backend on mount; each toggle persists immediately.
  const [marketingEmails, setMarketingEmails] = useState(false);
  const [marketingSms, setMarketingSms] = useState(false);
  const [marketingPush, setMarketingPush] = useState(false);

  useEffect(() => {
    let active = true;
    api.get('/marketing/preferences')
      .then((res: any) => {
        const p = res?.data ?? res ?? {};
        if (!active) return;
        setMarketingEmails(!!p.email_opt_in);
        setMarketingSms(!!p.sms_opt_in);
        setMarketingPush(!!p.push_opt_in);
      })
      .catch(() => { /* default off on failure */ });
    return () => { active = false; };
  }, []);

  // Persist one marketing channel; optimistic with revert on failure.
  const saveMarketing = async (
    field: 'email_opt_in' | 'sms_opt_in' | 'push_opt_in',
    value: boolean,
    setter: (v: boolean) => void,
  ) => {
    setter(value);
    try {
      await api.put('/marketing/preferences', { [field]: value, source: 'rider_app' });
    } catch (err: any) {
      setter(!value);
      showToast('Update Failed', getApiErrorMessage(err, 'Could not save your preference. Please try again.'), 'danger');
    }
  };
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  // R-P1-6: PIPEDA — wire Download My Data to real backend endpoint
  const handleDownloadData = async () => {
    try {
      await api.post('/users/data-export');
      showToast(t('privacy.download_requested'), t('privacy.download_requested_msg'), 'success');
    } catch (err: any) {
      showToast('Export Failed', getApiErrorMessage(err, 'Could not request your data export. Please try again.'), 'danger');
    }
  };

  // R-P1-6: PIPEDA — wire Delete Account to real backend endpoint + logout
  const handleDeleteAccount = () => {
    setConfirmSheet({
      visible: true,
      title: t('privacy.delete_confirm_title'),
      message: t('privacy.delete_confirm_msg'),
      variant: 'danger',
      buttons: [
        {
          text: t('privacy.delete_my_account'),
          style: 'destructive',
          onPress: async () => {
            try {
              await api.delete('/users/account');
              await logout();
              router.replace('/login' as any);
            } catch (err: any) {
              showToast('Delete Failed', getApiErrorMessage(err, 'Could not delete account. Please contact support.'), 'danger');
            }
          },
        },
        { text: t('common.cancel'), style: 'cancel' },
      ],
    });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>{t('privacy.title')}</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {/* Notifications */}
        <Text style={styles.sectionTitle}>{t('privacy.notifications_section')}</Text>
        <View style={styles.card}>
          <SettingRow
            icon="notifications" iconColor="#F59E0B" iconBg="#FEF3C7"
            title={t('privacy.push_notifications')}
            subtitle={t('privacy.push_notifications_subtitle')}
            toggle value={pushNotifications} onToggle={handlePushToggle}
            colors={colors}
          />
          <SettingRow
            icon="mail" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title={t('privacy.marketing_emails')}
            subtitle={t('privacy.marketing_emails_subtitle')}
            toggle value={marketingEmails} onToggle={(v) => saveMarketing('email_opt_in', v, setMarketingEmails)}
            colors={colors}
          />
          <SettingRow
            icon="chatbubble-ellipses" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title={t('privacy.marketing_sms')}
            subtitle={t('privacy.marketing_sms_subtitle')}
            toggle value={marketingSms} onToggle={(v) => saveMarketing('sms_opt_in', v, setMarketingSms)}
            colors={colors}
          />
          <SettingRow
            icon="megaphone" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title={t('privacy.marketing_push')}
            subtitle={t('privacy.marketing_push_subtitle')}
            toggle value={marketingPush} onToggle={(v) => saveMarketing('push_opt_in', v, setMarketingPush)}
            colors={colors}
          />
        </View>

        {/* Data */}
        <Text style={styles.sectionTitle}>{t('privacy.data_section')}</Text>
        <View style={styles.card}>
          <SettingRow
            icon="shield-outline" iconColor="#6366F1" iconBg="#E0E7FF"
            title={t('privacy.privacy_policy')}
            subtitle={t('privacy.privacy_policy_subtitle')}
            onPress={() => router.push('/legal?type=privacy' as any)}
            colors={colors}
          />
          <SettingRow
            icon="document-text-outline" iconColor="#6366F1" iconBg="#E0E7FF"
            title={t('privacy.terms_of_service')}
            subtitle={t('privacy.terms_of_service_subtitle')}
            onPress={() => router.push('/legal?type=tos' as any)}
            colors={colors}
          />
          <SettingRow
            icon="download-outline" iconColor="#6B7280" iconBg="#F3F4F6"
            title={t('privacy.download_data')}
            subtitle={t('privacy.download_data_subtitle')}
            onPress={handleDownloadData}
            colors={colors}
          />
          <SettingRow
            icon="trash-outline" iconColor="#DC2626" iconBg="#FEE2E2"
            title={t('privacy.delete_account')}
            subtitle={t('privacy.delete_account_subtitle')}
            onPress={handleDeleteAccount}
            danger
            colors={colors}
          />
        </View>

        <Text style={styles.footerText}>{t('privacy.footer')}</Text>
      </ScrollView>
      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function SettingRow({ icon, iconColor, iconBg, title, subtitle, toggle, value, onToggle, onPress, danger, colors }: {
  icon: string; iconColor: string; iconBg: string;
  title: string; subtitle: string;
  toggle?: boolean; value?: boolean; onToggle?: (v: boolean) => void;
  onPress?: () => void; danger?: boolean; colors: ThemeColors;
}) {
  const Wrap = onPress ? TouchableOpacity : View;
  return (
    <Wrap style={[{ flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: colors.surfaceLight }]} onPress={onPress} activeOpacity={0.7}>
      <View style={[{ width: 40, height: 40, borderRadius: 12, justifyContent: 'center', alignItems: 'center', marginRight: 14 }, { backgroundColor: iconBg }]}>
        <Ionicons name={icon as any} size={20} color={iconColor} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={[{ fontSize: 15, fontWeight: '600', color: colors.text }, danger && { color: '#DC2626' }]}>{title}</Text>
        <Text style={{ fontSize: 12, color: colors.textDim, marginTop: 1 }}>{subtitle}</Text>
      </View>
      {toggle && onToggle ? (
        <CustomToggle
          value={value}
          onValueChange={onToggle}
          trackColor={{ false: colors.border, true: `${colors.primary}60` }}
          thumbColor={value ? colors.primary : '#FFF'}
        />
      ) : onPress ? (
        <Ionicons name="chevron-forward" size={18} color={colors.border} />
      ) : null}
    </Wrap>
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
    sectionTitle: { fontSize: 13, fontWeight: '700', color: colors.textDim, letterSpacing: 0.5, marginBottom: 8, marginTop: 16 },
    card: { backgroundColor: colors.surfaceLight, borderRadius: 16, paddingHorizontal: 14 },
    footerText: { fontSize: 12, color: colors.textDim, textAlign: 'center', marginTop: 24, lineHeight: 18 },
  });
}
