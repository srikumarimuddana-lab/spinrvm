import React, { useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Switch, Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;

export default function PrivacySettingsScreen() {
  const router = useRouter();
  const [locationAlways, setLocationAlways] = useState(false);
  const [shareRideData, setShareRideData] = useState(true);
  const [marketingEmails, setMarketingEmails] = useState(true);
  const [pushNotifications, setPushNotifications] = useState(true);

  const handleDeleteAccount = () => {
    Alert.alert(
      'Delete Account',
      'This will permanently delete your account and all data. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Delete My Account', style: 'destructive', onPress: () => Alert.alert('Submitted', 'Your account deletion request has been submitted. You will receive a confirmation email.') },
      ]
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
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
          />
          <SettingRow
            icon="navigate" iconColor="#10B981" iconBg="#ECFDF5"
            title="Share Live Location"
            subtitle="Share location with driver during ride"
            toggle value={shareRideData} onToggle={setShareRideData}
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
          />
          <SettingRow
            icon="mail" iconColor="#8B5CF6" iconBg="#EDE9FE"
            title="Marketing Emails"
            subtitle="Offers, news, and updates"
            toggle value={marketingEmails} onToggle={setMarketingEmails}
          />
        </View>

        {/* Data */}
        <Text style={styles.sectionTitle}>Data & Privacy</Text>
        <View style={styles.card}>
          <SettingRow
            icon="download-outline" iconColor="#6B7280" iconBg="#F3F4F6"
            title="Download My Data"
            subtitle="Request a copy of your personal data"
            onPress={() => Alert.alert('Requested', 'Your data export has been requested. You will receive an email with a download link.')}
          />
          <SettingRow
            icon="trash-outline" iconColor="#DC2626" iconBg="#FEE2E2"
            title="Delete Account"
            subtitle="Permanently delete account and data"
            onPress={handleDeleteAccount}
            danger
          />
        </View>

        <Text style={styles.footerText}>
          Your data is handled in accordance with our Privacy Policy. Spinr never sells your personal information.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function SettingRow({ icon, iconColor, iconBg, title, subtitle, toggle, value, onToggle, onPress, danger }: {
  icon: string; iconColor: string; iconBg: string;
  title: string; subtitle: string;
  toggle?: boolean; value?: boolean; onToggle?: (v: boolean) => void;
  onPress?: () => void; danger?: boolean;
}) {
  const Wrap = onPress ? TouchableOpacity : View;
  return (
    <Wrap style={sStyles.row} onPress={onPress} activeOpacity={0.7}>
      <View style={[sStyles.icon, { backgroundColor: iconBg }]}>
        <Ionicons name={icon as any} size={20} color={iconColor} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={[sStyles.title, danger && { color: '#DC2626' }]}>{title}</Text>
        <Text style={sStyles.subtitle}>{subtitle}</Text>
      </View>
      {toggle && onToggle ? (
        <Switch
          value={value}
          onValueChange={onToggle}
          trackColor={{ false: '#E5E5E5', true: `${COLORS.primary}60` }}
          thumbColor={value ? COLORS.primary : '#FFF'}
        />
      ) : onPress ? (
        <Ionicons name="chevron-forward" size={18} color="#CCC" />
      ) : null}
    </Wrap>
  );
}

const sStyles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: '#F5F5F5' },
  icon: { width: 40, height: 40, borderRadius: 12, justifyContent: 'center', alignItems: 'center', marginRight: 14 },
  title: { fontSize: 15, fontWeight: '600', color: '#1A1A1A' },
  subtitle: { fontSize: 12, color: '#999', marginTop: 1 },
});

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F0F0F0',
  },
  backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
  headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
  content: { padding: 20, paddingBottom: 40 },
  sectionTitle: { fontSize: 13, fontWeight: '700', color: '#999', letterSpacing: 0.5, marginBottom: 8, marginTop: 16 },
  card: { backgroundColor: '#F9F9F9', borderRadius: 16, paddingHorizontal: 14 },
  footerText: { fontSize: 12, color: '#BBB', textAlign: 'center', marginTop: 24, lineHeight: 18 },
});
