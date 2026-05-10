import React, { useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
} from 'react-native';
import CustomToggle from '../components/CustomToggle';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import type { ThemeColors } from '@shared/theme/index';
import { useTranslation } from '../i18n';

export default function PrivacySettingsScreen() {
  const router = useRouter();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { logout } = useAuthStore();
  const { t } = useTranslation();
  const [locationAlways, setLocationAlways] = useState(false);
  const [shareRideData, setShareRideData] = useState(true);
  const [marketingEmails, setMarketingEmails] = useState(true);
  const [pushNotifications, setPushNotifications] = useState(true);
  const [alertState, setAlertState] = useState<{
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
      setAlertState({
        visible: true,
        title: t('privacy.download_requested'),
        message: t('privacy.download_requested_msg'),
        variant: 'success',
      });
    } catch (err: any) {
      setAlertState({
        visible: true,
        title: 'Error',
        message: err?.response?.data?.detail || 'Failed to request data export. Please try again.',
        variant: 'danger',
      });
    }
  };

  // R-P1-6: PIPEDA — wire Delete Account to real backend endpoint + logout
  const handleDeleteAccount = () => {
    setAlertState({
      visible: true,
      title: t('privacy.delete_confirm_title'),
      message: t('privacy.delete_confirm_msg'),
      variant: 'danger',
      buttons: [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('privacy.delete_my_account'),
          style: 'destructive',
          onPress: async () => {
            try {
              await api.delete('/users/account');
              await logout();
              router.replace('/login' as any);
            } catch (err: any) {
              setAlertState({
                visible: true,
                title: 'Error',
                message: err?.response?.data?.detail || 'Failed to delete account. Please contact support.',
                variant: 'danger',
              });
            }
          },
        },
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
        {/* Location */}
        <Text style={styles.sectionTitle}>{t('privacy.location_section')}</Text>
        <View style={styles.card}>
          <SettingRow
            icon="location" iconColor="#3B82F6" iconBg="#DBEAFE"
            title={t('privacy.background_location')}
            subtitle={t('privacy.background_location_subtitle')}
            toggle value={locationAlways} onToggle={setLocationAlways}
            colors={colors}
          />
          <SettingRow
            icon="navigate" iconColor="#10B981" iconBg="#ECFDF5"
            title={t('privacy.share_live')}
            subtitle={t('privacy.share_live_subtitle')}
            toggle value={shareRideData} onToggle={setShareRideData}
            colors={colors}
          />
        </View>

        {/* Notifications */}
        <Text style={styles.sectionTitle}>{t('privacy.notifications_section')}</Text>
        <View style={styles.card}>
          <SettingRow
            icon="notifications" iconColor="#F59E0B" iconBg="#FEF3C7"
            title={t('privacy.push_notifications')}
            subtitle={t('privacy.push_notifications_subtitle')}
            toggle value={pushNotifications} onToggle={setPushNotifications}
            colors={colors}
          />
          <SettingRow
            icon="mail" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title={t('privacy.marketing_emails')}
            subtitle={t('privacy.marketing_emails_subtitle')}
            toggle value={marketingEmails} onToggle={setMarketingEmails}
            colors={colors}
          />
        </View>

        {/* Data */}
        <Text style={styles.sectionTitle}>{t('privacy.data_section')}</Text>
        <View style={styles.card}>
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
      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
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
