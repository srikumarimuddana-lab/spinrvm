import React, { useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
  Image, ActivityIndicator, Linking, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';

const COLORS = SpinrConfig.theme.colors;

export default function AccountScreen() {
  const router = useRouter();
  const { user, logout, updateProfileImage } = useAuthStore();
  const [uploading, setUploading] = useState(false);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const handleLogout = () => {
    setAlertState({
      visible: true,
      title: 'Logout',
      message: 'Are you sure you want to logout?',
      variant: 'warning',
      buttons: [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Logout', style: 'destructive', onPress: async () => { await logout(); router.replace('/login'); } },
      ],
    });
  };

  const takePhoto = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      setAlertState({ visible: true, title: 'Permission needed', message: 'Allow camera access.', variant: 'warning' });
      return;
    }
    const result = await ImagePicker.launchCameraAsync({ allowsEditing: true, aspect: [1, 1], quality: 0.8 });
    if (!result.canceled && result.assets[0]) {
      setUploading(true);
      try { await updateProfileImage(result.assets[0].uri); }
      catch { setAlertState({ visible: true, title: 'Error', message: 'Upload failed.', variant: 'danger' }); }
      finally { setUploading(false); }
    }
  };

  const pickFromLibrary = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      setAlertState({ visible: true, title: 'Permission needed', message: 'Allow photo library access.', variant: 'warning' });
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 0.8 });
    if (!result.canceled && result.assets[0]) {
      setUploading(true);
      try { await updateProfileImage(result.assets[0].uri); }
      catch { setAlertState({ visible: true, title: 'Error', message: 'Upload failed.', variant: 'danger' }); }
      finally { setUploading(false); }
    }
  };

  const handlePickImage = async () => {
    setAlertState({
      visible: true,
      title: 'Profile Picture',
      message: 'Choose an option',
      variant: 'info',
      buttons: [
        { text: 'Take Photo', onPress: takePhoto },
        { text: 'Choose from Library', onPress: pickFromLibrary },
        { text: 'Cancel', style: 'cancel' },
      ],
    });
  };

  const formatPhone = (phone: string) => {
    if (!phone) return '';
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.length === 11 && cleaned.startsWith('1')) {
      return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
    }
    return phone;
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>

        {/* Profile Header */}
        <View style={styles.profileSection}>
          <View style={styles.avatarWrap}>
            <View style={styles.avatar}>
              {user?.profile_image ? (
                <Image source={{ uri: user.profile_image }} style={styles.avatarImg} />
              ) : (
                <Ionicons name="person" size={40} color="#999" />
              )}
              {uploading && (
                <View style={styles.uploadOverlay}>
                  <ActivityIndicator size="small" color="#FFF" />
                </View>
              )}
            </View>
            <TouchableOpacity style={styles.cameraBtn} onPress={handlePickImage} disabled={uploading}>
              <Ionicons name="camera" size={14} color="#FFF" />
            </TouchableOpacity>
          </View>

          <Text style={styles.userName}>{user?.first_name} {user?.last_name}</Text>

          <View style={styles.metaRow}>
            <View style={styles.metaChip}>
              <Ionicons name="star" size={12} color="#FFB800" />
              <Text style={styles.metaText}>{user?.rating ? user.rating.toFixed(1) : '5.0'}</Text>
            </View>
            <View style={styles.metaChip}>
              <Ionicons name="call" size={12} color={COLORS.primary} />
              <Text style={styles.metaText}>{formatPhone(user?.phone || '')}</Text>
            </View>
            {user?.email && (
              <View style={styles.metaChip}>
                <Ionicons name="mail" size={12} color={COLORS.primary} />
                <Text style={styles.metaText} numberOfLines={1}>{user.email}</Text>
              </View>
            )}
          </View>
        </View>

        {/* Quick Actions */}
        <View style={styles.quickRow}>
          <TouchableOpacity style={styles.quickCard} onPress={() => router.push('/manage-cards' as any)}>
            <View style={[styles.quickIcon, { backgroundColor: '#EDE9FE' }]}>
              <Ionicons name="card" size={22} color="#7C3AED" />
            </View>
            <Text style={styles.quickLabel}>Payment</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.quickCard} onPress={() => router.push('/emergency-contacts' as any)}>
            <View style={[styles.quickIcon, { backgroundColor: '#FEE2E2' }]}>
              <Ionicons name="shield-checkmark" size={22} color="#DC2626" />
            </View>
            <Text style={styles.quickLabel}>Safety</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.quickCard} onPress={() => router.push('/support' as any)}>
            <View style={[styles.quickIcon, { backgroundColor: '#DBEAFE' }]}>
              <Ionicons name="headset" size={22} color="#2563EB" />
            </View>
            <Text style={styles.quickLabel}>Support</Text>
          </TouchableOpacity>
        </View>

        {/* Account Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Account</Text>

          <MenuItem
            icon="person-outline" iconColor={COLORS.primary} iconBg="#FEF2F2"
            title="Edit Profile" subtitle="Name, email, phone"
            onPress={() => router.push('/profile-setup' as any)}
          />
          <MenuItem
            icon="card-outline" iconColor="#7C3AED" iconBg="#EDE9FE"
            title="Payment Methods" subtitle="Add or manage your cards"
            onPress={() => router.push('/manage-cards' as any)}
          />
          <MenuItem
            icon="location-outline" iconColor="#F59E0B" iconBg="#FEF3C7"
            title="Saved Places" subtitle="Home, work, favourites"
            onPress={() => router.push('/saved-places' as any)}
          />
          <MenuItem
            icon="pricetag-outline" iconColor="#10B981" iconBg="#ECFDF5"
            title="Promotions" subtitle="Promo codes & rewards"
            onPress={() => router.push('/promotions' as any)}
          />
        </View>

        {/* Safety & Privacy */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Safety & Privacy</Text>

          <MenuItem
            icon="shield-outline" iconColor="#DC2626" iconBg="#FEE2E2"
            title="Emergency Contacts" subtitle="Trusted contacts for safety alerts"
            onPress={() => router.push('/emergency-contacts' as any)}
          />
          <MenuItem
            icon="alert-circle-outline" iconColor="#F59E0B" iconBg="#FEF3C7"
            title="Report a Safety Issue" subtitle="Report an incident from a ride"
            onPress={() => router.push('/report-safety' as any)}
          />
          <MenuItem
            icon="lock-closed-outline" iconColor="#6B7280" iconBg="#F3F4F6"
            title="Privacy & Settings" subtitle="Data, notifications, permissions"
            onPress={() => router.push('/privacy-settings' as any)}
          />
        </View>

        {/* Legal & Help */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Legal & Help</Text>

          <MenuItem
            icon="help-circle-outline" iconColor="#2563EB" iconBg="#DBEAFE"
            title="Help Center" subtitle="FAQ, contact us"
            onPress={() => router.push('/support' as any)}
          />
          <MenuItem
            icon="document-text-outline" iconColor="#6B7280" iconBg="#F3F4F6"
            title="Legal" subtitle="Terms of service, privacy policy"
            onPress={() => router.push('/legal?type=tos' as any)}
          />
        </View>

        {/* Logout */}
        <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
          <Ionicons name="log-out-outline" size={20} color={COLORS.primary} />
          <Text style={styles.logoutText}>Log Out</Text>
        </TouchableOpacity>

        {/* Version */}
        <Text style={styles.version}>Spinr v1.0.2 · Saskatchewan, Canada</Text>

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

// Reusable menu item component
function MenuItem({ icon, iconColor, iconBg, title, subtitle, onPress, badge }: {
  icon: string; iconColor: string; iconBg: string;
  title: string; subtitle: string; onPress: () => void; badge?: string;
}) {
  return (
    <TouchableOpacity style={miStyles.row} onPress={onPress} activeOpacity={0.7}>
      <View style={[miStyles.icon, { backgroundColor: iconBg }]}>
        <Ionicons name={icon as any} size={20} color={iconColor} />
      </View>
      <View style={miStyles.content}>
        <Text style={miStyles.title}>{title}</Text>
        <Text style={miStyles.subtitle}>{subtitle}</Text>
      </View>
      {badge && (
        <View style={miStyles.badge}>
          <Text style={miStyles.badgeText}>{badge}</Text>
        </View>
      )}
      <Ionicons name="chevron-forward" size={18} color="#CCC" />
    </TouchableOpacity>
  );
}

const miStyles = StyleSheet.create({
  row: {
    flexDirection: 'row', alignItems: 'center', paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: '#F5F5F5',
  },
  icon: {
    width: 40, height: 40, borderRadius: 12,
    justifyContent: 'center', alignItems: 'center', marginRight: 14,
  },
  content: { flex: 1 },
  title: { fontSize: 15, fontWeight: '600', color: '#1A1A1A' },
  subtitle: { fontSize: 12, color: '#999', marginTop: 1 },
  badge: {
    backgroundColor: COLORS.primary, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2, marginRight: 8,
  },
  badgeText: { fontSize: 10, fontWeight: '700', color: '#FFF' },
});

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },

  // Profile
  profileSection: { alignItems: 'center', paddingTop: 20, paddingBottom: 24 },
  avatarWrap: { position: 'relative', marginBottom: 14 },
  avatar: {
    width: 90, height: 90, borderRadius: 45, backgroundColor: '#F0F0F0',
    justifyContent: 'center', alignItems: 'center', overflow: 'hidden',
  },
  avatarImg: { width: 90, height: 90, borderRadius: 45 },
  uploadOverlay: {
    ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.4)',
    borderRadius: 45, justifyContent: 'center', alignItems: 'center',
  },
  cameraBtn: {
    position: 'absolute', bottom: 0, right: -2,
    width: 30, height: 30, borderRadius: 15, backgroundColor: COLORS.primary,
    justifyContent: 'center', alignItems: 'center', borderWidth: 3, borderColor: '#FFF',
  },
  userName: { fontSize: 22, fontWeight: '800', color: '#1A1A1A', marginBottom: 8 },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 8 },
  metaChip: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: '#F5F5F5', paddingHorizontal: 10, paddingVertical: 5, borderRadius: 16,
  },
  metaText: { fontSize: 12, fontWeight: '500', color: '#666' },

  // Quick Actions
  quickRow: { flexDirection: 'row', paddingHorizontal: 20, gap: 12, marginBottom: 8 },
  quickCard: {
    flex: 1, alignItems: 'center', paddingVertical: 16,
    backgroundColor: '#F9F9F9', borderRadius: 16,
  },
  quickIcon: {
    width: 48, height: 48, borderRadius: 14, justifyContent: 'center', alignItems: 'center', marginBottom: 8,
  },
  quickLabel: { fontSize: 12, fontWeight: '600', color: '#1A1A1A' },

  // Sections
  section: { paddingHorizontal: 20, marginTop: 16 },
  sectionTitle: {
    fontSize: 13, fontWeight: '700', color: '#999', letterSpacing: 0.5,
    textTransform: 'uppercase', marginBottom: 8,
  },

  // Logout
  logoutBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    marginHorizontal: 20, marginTop: 20, paddingVertical: 14,
    backgroundColor: '#FEF2F2', borderRadius: 14,
  },
  logoutText: { fontSize: 15, fontWeight: '600', color: COLORS.primary },

  version: { fontSize: 12, color: '#BBB', textAlign: 'center', marginTop: 20 },
});
