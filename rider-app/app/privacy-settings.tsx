import React, { useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function PrivacySettingsScreen() {
  const router = useRouter();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
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

  const handleDeleteAccount = () => {
    setAlertState({
      visible: true,
      title: 'Delete Account',
      message: 'This will permanently delete your account and all data. This cannot be undone.',
      variant: 'danger',
      buttons: [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete My Account',
          style: 'destructive',
          onPress: () => setAlertState({
            visible: true,
            title: 'Submitted',
            message: 'Your account deletion request has been submitted. You will receive a confirmation email.',
            variant: 'success',
          }),
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
        <Text style={styles.headerTitle}>Privacy & Settings</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {/* Location */}
        <Text style={styles.sectionTitle}>Location</Text>
        <View style={styles.card}>
          <SettingRow
            icon="location" iconColor="#3B82F6" iconBg="#DBEAFE"
            title="Background Location"
            subtitle="Allow location access when app is closed"
            toggle value={locationAlways} onToggle={setLocationAlways}
            colors={colors}
          />
          <SettingRow
            icon="navigate" iconColor="#10B981" iconBg="#ECFDF5"
            title="Share Live Location"
            subtitle="Share location with driver during ride"
            toggle value={shareRideData} onToggle={setShareRideData}
            colors={colors}
          />
        </View>

        {/* Notifications */}
        <Text style={styles.sectionTitle}>Notifications</Text>
        <View style={styles.card}>
          <SettingRow
            icon="notifications" iconColor="#F59E0B" iconBg="#FEF3C7"
            title="Push Notifications"
            subtitle="Ride updates, promotions, alerts"
            toggle value={pushNotifications} onToggle={setPushNotifications}
            colors={colors}
          />
          <SettingRow
            icon="mail" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title="Marketing Emails"
            subtitle="Offers, news, and updates"
            toggle value={marketingEmails} onToggle={setMarketingEmails}
            colors={colors}
          />
        </View>

        {/* Data */}
        <Text style={styles.sectionTitle}>Data & Privacy</Text>
        <View style={styles.card}>
          <SettingRow
            icon="download-outline" iconColor="#6B7280" iconBg="#F3F4F6"
            title="Download My Data"
            subtitle="Request a copy of your personal data"
            onPress={() => setAlertState({ visible: true, title: 'Requested', message: 'Your data export has been requested. You will receive an email with a download link.', variant: 'success' })}
            colors={colors}
          />
          <SettingRow
            icon="trash-outline" iconColor="#DC2626" iconBg="#FEE2E2"
            title="Delete Account"
            subtitle="Permanently delete account and data"
            onPress={handleDeleteAccount}
            danger
            colors={colors}
          />
        </View>

        <Text style={styles.footerText}>
          Your data is handled in accordance with our Privacy Policy. Spinr never sells your personal information.
        </Text>
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
        <Switch
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
