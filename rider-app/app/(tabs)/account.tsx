import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  StatusBar,
  Alert,
  Modal,
} from 'react-native';
import { Image } from 'expo-image';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useFocusEffect } from '@react-navigation/native';
import { useAuthStore, type User } from '@shared/store/authStore';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useWorkProfileStore } from '../../store/workProfileStore';

const BLURHASH_PLACEHOLDER = 'LGF5]+Yk^6#M@-5c,1J5@[or[Q6.';

export default function AccountScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, logout } = useAuthStore();
  const { profiles, workModeEnabled } = useWorkProfileStore();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  // Account screen is view-only for the avatar — editing the photo lives in
  // Edit Personal Info. Tapping the avatar opens a full-screen viewer.
  const [showPhotoView, setShowPhotoView] = useState(false);
  const [companyInfo, setCompanyInfo] = useState<{
    name?: string; address?: string; phone?: string; email?: string; website?: string;
  }>({});

  useEffect(() => {
    api.get('/company-info')
      .then(res => setCompanyInfo(res?.data || {}))
      .catch(() => {});
  }, []);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        setIsRefreshing(true);
        try {
          const userRes = await api.get<User>('/auth/me');
          if (!cancelled && userRes.data) useAuthStore.setState({ user: userRes.data });
        } catch {}
        finally { if (!cancelled) setIsRefreshing(false); }
      })();
      return () => { cancelled = true; };
    }, [])
  );

  const ratingElements = (rating: number) => {
    const stars = [];
    for (let i = 1; i <= 5; i++) {
      stars.push(
        <Ionicons
          key={i}
          name={i <= Math.round(rating) ? 'star' : 'star-outline'}
          size={14}
          color={i <= Math.round(rating) ? '#FFD700' : 'rgba(255,255,255,0.3)'}
        />,
      );
    }
    return stars;
  };

  const formatPhone = (phone: string) => {
    if (!phone) return 'N/A';
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.length === 11 && cleaned.startsWith('1')) {
      return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
    }
    return phone;
  };

  return (
    <View style={styles.container}>
      <StatusBar barStyle="light-content" />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: Math.max(insets.bottom, 16) + 24 }}
        showsVerticalScrollIndicator={false}
      >
        <LinearGradient
          colors={[colors.primary, colors.primaryDark]}
          style={[styles.headerHero, { paddingTop: insets.top + 20 }]}
        >
          {isRefreshing && (
            <View style={{ position: 'absolute', top: insets.top + 10, right: 20 }}>
              <ActivityIndicator size="small" color="#fff" />
            </View>
          )}

          {/* View-only: tap to see the photo full-screen. Editing the photo
              lives in Edit Personal Info (profile-setup). */}
          <TouchableOpacity
            style={styles.avatarContainer}
            onPress={() => { if (user?.profile_image) setShowPhotoView(true); }}
            activeOpacity={user?.profile_image ? 0.8 : 1}
          >
            {user?.profile_image ? (
              <Image
                source={{ uri: user.profile_image }}
                style={styles.avatar}
                placeholder={BLURHASH_PLACEHOLDER}
                contentFit="cover"
                transition={200}
              />
            ) : (
              <View style={[styles.avatarPlaceholder, { backgroundColor: 'rgba(255,255,255,0.2)' }]}>
                <Ionicons name="person" size={40} color="#fff" />
              </View>
            )}
            {workModeEnabled && (
              <View style={styles.verifiedBadge}>
                <Ionicons name="briefcase" size={16} color="#1D4ED8" />
              </View>
            )}
          </TouchableOpacity>

          <Text style={styles.name}>
            {user?.first_name ? `${user.first_name} ${user.last_name || ''}`.trim() : 'Rider'}
          </Text>
          <Text style={styles.subtitle}>
            {workModeEnabled ? 'Work mode active' : 'Spinr Rider'}
          </Text>

          <View style={styles.ratingHeroContainer}>
            <View style={styles.ratingBox}>
              <Text style={styles.ratingNumber}>{(user?.rating || 5.0).toFixed(1)}</Text>
              <View style={styles.starsRow}>{ratingElements(user?.rating || 5)}</View>
            </View>
            <View style={styles.ratingDivider} />
            <View style={styles.ratingBox}>
              <Text style={styles.ratingNumber}>{user?.total_rides || 0}</Text>
              <Text style={styles.ratingLabel}>Rides</Text>
            </View>
          </View>
        </LinearGradient>

        <View style={styles.contentBody}>
          <View style={styles.section}>
            <View style={styles.sectionHeaderRow}>
              <Text style={styles.sectionTitle}>Personal Info</Text>
              <TouchableOpacity onPress={() => router.push('/profile-setup' as any)} style={styles.editBtn}>
                <Text style={styles.editBtnText}>Edit</Text>
              </TouchableOpacity>
            </View>
            <View style={styles.card}>
              <View style={styles.cardRow}>
                <View style={[styles.iconBox, { backgroundColor: 'rgba(239, 68, 68, 0.1)' }]}>
                  <Ionicons name="call" size={16} color={colors.primary} />
                </View>
                <View style={styles.cardInfo}>
                  <Text style={styles.cardLabel}>Phone</Text>
                  <Text style={styles.cardValue}>{formatPhone(user?.phone || '')}</Text>
                </View>
              </View>
              <View style={styles.cardDivider} />
              <View style={styles.cardRow}>
                <View style={[styles.iconBox, { backgroundColor: 'rgba(56, 189, 248, 0.1)' }]}>
                  <Ionicons name="mail" size={16} color="#38BDF8" />
                </View>
                <View style={styles.cardInfo}>
                  <Text style={styles.cardLabel}>Email</Text>
                  <Text style={styles.cardValue}>{user?.email || 'N/A'}</Text>
                </View>
              </View>
              {user?.gender && (
                <>
                  <View style={styles.cardDivider} />
                  <View style={styles.cardRow}>
                    <View style={[styles.iconBox, { backgroundColor: 'rgba(245, 158, 11, 0.1)' }]}>
                      <Ionicons name="person" size={16} color="#F59E0B" />
                    </View>
                    <View style={styles.cardInfo}>
                      <Text style={styles.cardLabel}>Gender</Text>
                      <Text style={styles.cardValue}>{user.gender}</Text>
                    </View>
                  </View>
                </>
              )}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Wallet & Payment</Text>
            <View style={styles.card}>
              <MenuRow styles={styles} colors={colors} icon="wallet" iconColor="#8B5CF6" iconBg="rgba(139, 92, 246, 0.1)" label="Wallet" onPress={() => router.push('/wallet' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="card" iconColor="#7C3AED" iconBg="rgba(124, 58, 237, 0.1)" label="Payment Methods" onPress={() => router.push('/manage-cards' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="pricetag" iconColor="#10B981" iconBg="rgba(16, 185, 129, 0.1)" label="Promotions" onPress={() => router.push('/promotions' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="gift" iconColor="#8B5CF6" iconBg="rgba(139, 92, 246, 0.1)" label="Refer & Earn" onPress={() => router.push('/referral' as any)} />
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Rides & Places</Text>
            <View style={styles.card}>
              <MenuRow styles={styles} colors={colors} icon="calendar" iconColor="#3B82F6" iconBg="rgba(59, 130, 246, 0.1)" label="Scheduled Rides" onPress={() => router.push('/scheduled-rides' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="location" iconColor="#F59E0B" iconBg="rgba(245, 158, 11, 0.1)" label="Saved Places" onPress={() => router.push('/saved-places' as any)} />
            </View>
          </View>

          {profiles.length > 0 && (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Work</Text>
              <View style={styles.card}>
                <TouchableOpacity
                  style={styles.actionRow}
                  activeOpacity={0.7}
                  onPress={() => router.push('/work-profile' as any)}
                >
                  <View style={[styles.iconBox, { backgroundColor: 'rgba(29, 78, 216, 0.1)' }]}>
                    <Ionicons name="briefcase" size={18} color="#1D4ED8" />
                  </View>
                  <View style={styles.actionContent}>
                    <Text style={styles.actionText}>Work Profile</Text>
                    <Text style={styles.actionSubtitle}>
                      {workModeEnabled
                        ? 'Work mode active'
                        : `${profiles.length} company${profiles.length > 1 ? ' accounts' : ''}`}
                    </Text>
                  </View>
                  {workModeEnabled && (
                    <View style={styles.badge}>
                      <Text style={styles.badgeText}>Work</Text>
                    </View>
                  )}
                  <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
                </TouchableOpacity>
              </View>
            </View>
          )}

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Safety & Privacy</Text>
            <View style={styles.card}>
              <MenuRow styles={styles} colors={colors} icon="shield-checkmark" iconColor="#EF4444" iconBg="rgba(239, 68, 68, 0.05)" label="Emergency Contacts" onPress={() => router.push('/emergency-contacts' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="alert-circle" iconColor="#F59E0B" iconBg="rgba(245, 158, 11, 0.1)" label="Report a Safety Issue" onPress={() => router.push('/report-safety' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="lock-closed" iconColor={colors.textDim} iconBg={colors.surfaceLight} label="Privacy & Settings" onPress={() => router.push('/privacy-settings' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="notifications" iconColor="#6366F1" iconBg="rgba(99, 102, 241, 0.1)" label="Notifications" onPress={() => router.push('/notifications' as any)} />
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Support</Text>
            <View style={styles.card}>
              <MenuRow styles={styles} colors={colors} icon="bag-handle" iconColor="#F97316" iconBg="rgba(249, 115, 22, 0.1)" label="Lost & Found" onPress={() => router.push('/lost-and-found' as any)} />
              <View style={styles.cardDivider} />
              <MenuRow styles={styles} colors={colors} icon="help-circle" iconColor="#2563EB" iconBg="rgba(37, 99, 235, 0.1)" label="Help Center" onPress={() => router.push('/support' as any)} />
            </View>
          </View>

          {/* Sign Out — standalone, centered (Legal / Privacy / Data / Delete
              now live in Privacy & Settings). */}
          <TouchableOpacity
            style={styles.signOutButton}
            activeOpacity={0.7}
            onPress={() => {
              Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
                { text: 'Cancel', style: 'cancel' },
                {
                  text: 'Sign Out',
                  style: 'destructive',
                  onPress: async () => { await logout(); router.replace('/login' as any); },
                },
              ]);
            }}
          >
            <Ionicons name="log-out-outline" size={18} color="#EF4444" />
            <Text style={styles.signOutText}>Sign Out</Text>
          </TouchableOpacity>

          {(companyInfo.address || companyInfo.phone || companyInfo.email || companyInfo.website) && (
            <View style={styles.companySection}>
              <Text style={styles.companyName}>{companyInfo.name || 'Spinr'}</Text>
              {!!companyInfo.address && <Text style={styles.companyLine}>{companyInfo.address}</Text>}
              {!!companyInfo.phone && <Text style={styles.companyLine}>{companyInfo.phone}</Text>}
              {!!companyInfo.email && <Text style={styles.companyLine}>{companyInfo.email}</Text>}
              {!!companyInfo.website && <Text style={styles.companyLine}>{companyInfo.website}</Text>}
            </View>
          )}
        </View>
      </ScrollView>

      {/* Full-screen profile photo viewer (view-only). */}
      <Modal
        visible={showPhotoView}
        transparent
        animationType="fade"
        statusBarTranslucent
        onRequestClose={() => setShowPhotoView(false)}
      >
        <TouchableOpacity
          style={styles.photoViewerBackdrop}
          activeOpacity={1}
          onPress={() => setShowPhotoView(false)}
        >
          {user?.profile_image && (
            <Image
              source={{ uri: user.profile_image }}
              style={styles.photoViewerImage}
              contentFit="contain"
              transition={150}
            />
          )}
          <View style={styles.photoViewerClose}>
            <Ionicons name="close" size={28} color="#fff" />
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

function MenuRow({
  styles, colors, icon, iconColor, iconBg, label, onPress,
}: {
  styles: any; colors: ThemeColors;
  icon: string; iconColor: string; iconBg: string;
  label: string; onPress: () => void;
}) {
  return (
    <TouchableOpacity style={styles.actionRow} activeOpacity={0.7} onPress={onPress}>
      <View style={[styles.iconBox, { backgroundColor: iconBg }]}>
        <Ionicons name={icon as any} size={18} color={iconColor} />
      </View>
      <Text style={styles.actionText}>{label}</Text>
      <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
    </TouchableOpacity>
  );
}

function createStyles(colors: ThemeColors) { return StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.surfaceLight },
  headerHero: {
    paddingHorizontal: 20, paddingBottom: 40,
    borderBottomLeftRadius: 32, borderBottomRightRadius: 32,
    alignItems: 'center',
    shadowColor: colors.primaryDark,
    shadowOffset: { width: 0, height: 10 }, shadowOpacity: 0.2, shadowRadius: 20,
    elevation: 10, zIndex: 10,
  },
  avatarContainer: { position: 'relative', marginBottom: 16, marginTop: 10 },
  avatar: {
    width: 100, height: 100, borderRadius: 50,
    borderWidth: 4, borderColor: '#fff',
    backgroundColor: 'rgba(255,255,255,0.2)',
  },
  photoViewerBackdrop: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.92)',
    justifyContent: 'center', alignItems: 'center',
  },
  photoViewerImage: { width: '92%', height: '70%' },
  photoViewerClose: { position: 'absolute', top: 56, right: 20 },
  signOutButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    alignSelf: 'center', gap: 8, paddingVertical: 14, marginTop: 4,
  },
  signOutText: { fontSize: 15, fontWeight: '700', color: '#EF4444' },
  avatarPlaceholder: {
    width: 100, height: 100, borderRadius: 50,
    justifyContent: 'center', alignItems: 'center',
    borderWidth: 4, borderColor: 'rgba(255,255,255,0.3)',
  },
  cameraButton: {
    position: 'absolute', bottom: 0, left: 0,
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: colors.surface,
    justifyContent: 'center', alignItems: 'center',
    shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.15, shadowRadius: 8, elevation: 4,
  },
  verifiedBadge: {
    position: 'absolute', bottom: 0, right: 0,
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: colors.surface,
    justifyContent: 'center', alignItems: 'center',
    shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.15, shadowRadius: 8, elevation: 4,
  },
  name: { color: '#fff', fontSize: 26, fontWeight: '900', letterSpacing: 0.5 },
  subtitle: { color: 'rgba(255,255,255,0.8)', fontSize: 14, marginTop: 2, fontWeight: '500' },
  ratingHeroContainer: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.15)', borderRadius: 16,
    marginTop: 20, paddingVertical: 12, paddingHorizontal: 24,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.3)',
  },
  ratingBox: { alignItems: 'center', paddingHorizontal: 12 },
  ratingDivider: { width: 1, height: 30, backgroundColor: 'rgba(255,255,255,0.3)', marginHorizontal: 12 },
  ratingNumber: { color: '#fff', fontSize: 22, fontWeight: '900' },
  ratingLabel: { color: 'rgba(255,255,255,0.8)', fontSize: 11, fontWeight: '600', marginTop: 2, letterSpacing: 1 },
  starsRow: { flexDirection: 'row', marginTop: 4, gap: 2 },
  contentBody: { paddingTop: 10 },
  section: { paddingHorizontal: 16, marginTop: 20 },
  sectionHeaderRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginBottom: 12, paddingHorizontal: 4,
  },
  sectionTitle: {
    color: colors.text, fontSize: 18, fontWeight: '800', letterSpacing: 0.2,
    marginBottom: 12, paddingHorizontal: 4,
  },
  editBtn: {
    backgroundColor: colors.surface, paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 16, borderWidth: 1, borderColor: colors.border,
  },
  editBtnText: { color: colors.textDim, fontSize: 12, fontWeight: '700' },
  card: {
    backgroundColor: colors.surface, borderRadius: 24,
    paddingHorizontal: 16, paddingVertical: 8,
    borderWidth: 1, borderColor: 'rgba(0,0,0,0.02)',
    shadowColor: '#000', shadowOffset: { width: 0, height: 6 }, shadowOpacity: 0.04, shadowRadius: 16, elevation: 3,
  },
  cardRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 12, gap: 14 },
  cardDivider: { height: 1, backgroundColor: colors.surfaceLight, marginLeft: 50 },
  iconBox: { width: 40, height: 40, borderRadius: 20, justifyContent: 'center', alignItems: 'center' },
  cardInfo: { flex: 1 },
  cardLabel: { color: colors.textDim, fontSize: 11, fontWeight: '600', letterSpacing: 0.5, textTransform: 'uppercase' },
  cardValue: { color: colors.text, fontSize: 15, fontWeight: '700', marginTop: 2 },
  actionRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, gap: 14 },
  actionContent: { flex: 1 },
  actionText: { flex: 1, color: colors.text, fontSize: 15, fontWeight: '600' },
  actionSubtitle: { color: colors.textDim, fontSize: 12, fontWeight: '500', marginTop: 2 },
  badge: { backgroundColor: colors.primary, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2, marginRight: 8 },
  badgeText: { fontSize: 10, fontWeight: '700', color: colors.surface },
  companySection: {
    marginHorizontal: 16, marginTop: 24, marginBottom: 40, paddingTop: 16, alignItems: 'center',
  },
  companyName: { color: colors.textDim, fontSize: 13, fontWeight: '700', marginBottom: 6 },
  companyLine: { color: colors.textDim, fontSize: 11, marginTop: 2, textAlign: 'center' },
}); }
