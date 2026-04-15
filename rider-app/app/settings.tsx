import React, { useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '@shared/store/authStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function SettingsScreen() {
  const router = useRouter();
  const { user } = useAuthStore();
  const { colors, isDark, colorScheme, setTheme } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [pushEnabled, setPushEnabled] = useState(true);
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [smsEnabled, setSmsEnabled] = useState(true);
  const [language, setLanguage] = useState('English');
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const handleDarkModeToggle = (value: boolean) => {
    setTheme(value ? 'dark' : 'light');
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Settings</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {/* Notifications */}
        <Text style={styles.sectionTitle}>Notifications</Text>
        <View style={styles.card}>
          <SettingToggle icon="notifications" iconColor="#F59E0B" iconBg="#FEF3C7"
            title="Push Notifications" subtitle="Ride updates and alerts"
            value={pushEnabled} onToggle={setPushEnabled} />
          <SettingToggle icon="mail" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title="Email Notifications" subtitle="Receipts and promotions"
            value={emailEnabled} onToggle={setEmailEnabled} />
          <SettingToggle icon="chatbubble" iconColor="#10B981" iconBg="#ECFDF5"
            title="SMS Notifications" subtitle="OTP and ride confirmations"
            value={smsEnabled} onToggle={setSmsEnabled} />
        </View>

        {/* Appearance */}
        <Text style={styles.sectionTitle}>Appearance</Text>
        <View style={styles.card}>
          <SettingToggle icon="moon" iconColor="#6366F1" iconBg="#EEF2FF"
            title="Dark Mode" subtitle="Reduce eye strain at night"
            value={isDark} onToggle={handleDarkModeToggle} />
        </View>

        {/* Language */}
        <Text style={styles.sectionTitle}>Language & Region</Text>
        <View style={styles.card}>
          <TouchableOpacity style={styles.row} onPress={() => {
            setAlertState({
              visible: true,
              title: 'Language',
              message: 'Select your preferred language',
              variant: 'info',
              buttons: [
                { text: 'English', onPress: () => setLanguage('English') },
                { text: 'French', onPress: () => setLanguage('French') },
                { text: 'Cancel', style: 'cancel' },
              ],
            });
          }}>
            <View style={[styles.rowIcon, { backgroundColor: '#DBEAFE' }]}>
              <Ionicons name="globe" size={20} color="#3B82F6" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Language</Text>
              <Text style={styles.rowSub}>{language}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.row} onPress={() => router.push('/privacy-settings' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: colors.surfaceLight }]}>
              <Ionicons name="lock-closed" size={20} color={colors.textSecondary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Privacy & Data</Text>
              <Text style={styles.rowSub}>Permissions, data management</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        {/* Account */}
        <Text style={styles.sectionTitle}>Account</Text>
        <View style={styles.card}>
          <TouchableOpacity style={styles.row} onPress={() => router.push('/manage-cards' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: '#EDE9FE' }]}>
              <Ionicons name="card" size={20} color="#7C3AED" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Payment Methods</Text>
              <Text style={styles.rowSub}>Manage your cards</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.row} onPress={() => router.push('/saved-places' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: '#FEF3C7' }]}>
              <Ionicons name="bookmark" size={20} color="#F59E0B" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Saved Places</Text>
              <Text style={styles.rowSub}>Home, work, favourites</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        {/* About */}
        <Text style={styles.sectionTitle}>About</Text>
        <View style={styles.card}>
          <TouchableOpacity style={styles.row} onPress={() => router.push('/legal?type=tos' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: colors.surfaceLight }]}>
              <Ionicons name="document-text" size={20} color={colors.textSecondary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Terms of Service</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.row} onPress={() => router.push('/legal?type=privacy' as any)}>
            <View style={[styles.rowIcon, { backgroundColor: colors.surfaceLight }]}>
              <Ionicons name="eye" size={20} color={colors.textSecondary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Privacy Policy</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        <Text style={styles.version}>Spinr v1.0.2 · {user?.phone || ''}</Text>
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
      <Switch
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
  });
}
