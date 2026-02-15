import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Platform,
  Image,
  Alert,
} from 'react-native';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useAuthStore } from '../../store/authStore';

const COLORS = {
  primary: '#FF3B30', // Vibrant Red
  accent: '#FF3B30',
  accentDim: '#D32F2F',
  surface: '#FFFFFF',
  surfaceLight: '#F5F5F5',
  text: '#1A1A1A',
  textDim: '#666666',
  success: '#34C759',
  gold: '#FFD700',
  orange: '#FF9500',
  danger: '#DC2626',
  border: '#E5E7EB',
};

export default function ProfileScreen() {
  const router = useRouter();
  const { user, driver: driverData, logout, toggleDriverMode } = useAuthStore();

  const handleSwitchToRider = () => {
    toggleDriverMode();
    router.replace('/(tabs)');
  };

  const handleLogout = () => {
    Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: () => {
          logout();
          router.replace('/');
        },
      },
    ]);
  };

  const ratingStars = (rating: number) => {
    const stars = [];
    for (let i = 1; i <= 5; i++) {
      stars.push(
        <Ionicons
          key={i}
          name={i <= Math.round(rating) ? 'star' : 'star-outline'}
          size={16}
          color={i <= Math.round(rating) ? COLORS.gold : COLORS.surfaceLight}
        />
      );
    }
    return stars;
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={{ paddingBottom: 100 }} showsVerticalScrollIndicator={false}>
        {/* Header / Avatar */}
        <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
          <View style={styles.avatarContainer}>
            {user?.profile_image ? (
              <Image source={{ uri: user.profile_image }} style={styles.avatar} />
            ) : (
              <View style={styles.avatarPlaceholder}>
                <Ionicons name="person" size={40} color={COLORS.textDim} />
              </View>
            )}
            <View style={styles.verifiedBadge}>
              <Ionicons
                name={driverData?.is_verified ? 'checkmark-circle' : 'time-outline'}
                size={20}
                color={driverData?.is_verified ? COLORS.accent : COLORS.orange}
              />
            </View>
          </View>

          <Text style={styles.name}>{driverData?.name || user?.first_name || 'Driver'}</Text>
          <Text style={styles.subtitle}>
            {driverData?.is_verified ? 'Verified Driver' : 'Pending Verification'}
          </Text>

          {/* Rating */}
          <View style={styles.ratingContainer}>
            <Text style={styles.ratingNumber}>{(driverData?.rating || 5.0).toFixed(1)}</Text>
            <View style={styles.starsRow}>{ratingStars(driverData?.rating || 5)}</View>
            <Text style={styles.ratingCount}>
              {driverData?.total_rides || 0} ride{(driverData?.total_rides || 0) !== 1 ? 's' : ''}
            </Text>
          </View>
        </LinearGradient>

        {/* Vehicle Info */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Vehicle</Text>
          <View style={styles.card}>
            <View style={styles.cardRow}>
              <View style={styles.iconBox}>
                <FontAwesome5 name="car" size={16} color={COLORS.accent} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.cardLabel}>Vehicle</Text>
                <Text style={styles.cardValue}>
                  {driverData?.vehicle_color} {driverData?.vehicle_make} {driverData?.vehicle_model}
                </Text>
              </View>
            </View>
            <View style={styles.cardDivider} />
            <View style={styles.cardRow}>
              <View style={styles.iconBox}>
                <MaterialCommunityIcons name="card-text" size={16} color={COLORS.accent} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.cardLabel}>License Plate</Text>
                <Text style={styles.cardValue}>{driverData?.license_plate || 'N/A'}</Text>
              </View>
            </View>
            {driverData?.vehicle_year && (
              <>
                <View style={styles.cardDivider} />
                <View style={styles.cardRow}>
                  <View style={styles.iconBox}>
                    <Ionicons name="calendar" size={16} color={COLORS.accent} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.cardLabel}>Year</Text>
                    <Text style={styles.cardValue}>{driverData.vehicle_year}</Text>
                  </View>
                </View>
              </>
            )}
          </View>
        </View>

        {/* Documents */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Documents</Text>
          <View style={styles.card}>
            {[
              { icon: 'id-card-outline', label: 'Driver\'s License', key: 'license_expiry_date' },
              { icon: 'shield-checkmark-outline', label: 'Insurance', key: 'insurance_expiry_date' },
              { icon: 'document-text-outline', label: 'Background Check', key: 'background_check_expiry_date' },
              { icon: 'car-sport-outline', label: 'Vehicle Inspection', key: 'vehicle_inspection_expiry_date' },
            ].map((doc, i) => {
              const expiry = driverData?.[doc.key];
              const isExpired = expiry ? new Date(expiry) < new Date() : false;
              const expiresIn = expiry
                ? Math.ceil((new Date(expiry).getTime() - Date.now()) / (1000 * 60 * 60 * 24))
                : null;

              return (
                <React.Fragment key={doc.key}>
                  {i > 0 && <View style={styles.cardDivider} />}
                  <View style={styles.cardRow}>
                    <View style={styles.iconBox}>
                      <Ionicons name={doc.icon as any} size={16} color={COLORS.accent} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.cardLabel}>{doc.label}</Text>
                      {expiry ? (
                        <Text
                          style={[
                            styles.cardValue,
                            isExpired
                              ? { color: COLORS.danger }
                              : expiresIn !== null && expiresIn < 30
                                ? { color: COLORS.orange }
                                : {},
                          ]}
                        >
                          {isExpired
                            ? 'EXPIRED'
                            : expiresIn !== null && expiresIn < 30
                              ? `Expires in ${expiresIn} days`
                              : new Date(expiry).toLocaleDateString()}
                        </Text>
                      ) : (
                        <Text style={styles.cardValueDim}>Not submitted</Text>
                      )}
                    </View>
                    {isExpired && (
                      <View style={styles.warningIcon}>
                        <Ionicons name="warning" size={18} color={COLORS.danger} />
                      </View>
                    )}
                  </View>
                </React.Fragment>
              );
            })}
          </View>
        </View>

        {/* Quick Actions */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Quick Actions</Text>

          <TouchableOpacity style={styles.actionRow} onPress={handleSwitchToRider}>
            <View style={[styles.iconBox, { backgroundColor: 'rgba(0, 212, 170, 0.1)' }]}>
              <Ionicons name="swap-horizontal" size={18} color={COLORS.accent} />
            </View>
            <Text style={styles.actionText}>Switch to Rider Mode</Text>
            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/(driver)/notifications' as any)}>
            <View style={[styles.iconBox, { backgroundColor: 'rgba(255, 215, 0, 0.1)' }]}>
              <Ionicons name="help-circle" size={18} color={COLORS.gold} />
            </View>
            <Text style={styles.actionText}>Help Center</Text>
            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/(driver)/settings' as any)}>
            <View style={[styles.iconBox, { backgroundColor: 'rgba(255, 140, 66, 0.1)' }]}>
              <Ionicons name="settings" size={18} color={COLORS.orange} />
            </View>
            <Text style={styles.actionText}>Settings</Text>
            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={[styles.actionRow, { borderBottomWidth: 0 }]} onPress={handleLogout}>
            <View style={[styles.iconBox, { backgroundColor: 'rgba(255, 71, 87, 0.1)' }]}>
              <Ionicons name="log-out" size={18} color={COLORS.danger} />
            </View>
            <Text style={[styles.actionText, { color: COLORS.danger }]}>Sign Out</Text>
            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
          </TouchableOpacity>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.primary,
  },
  header: {
    paddingTop: Platform.OS === 'ios' ? 60 : 45,
    paddingBottom: 24,
    alignItems: 'center',
  },
  avatarContainer: {
    position: 'relative',
    marginBottom: 12,
  },
  avatar: {
    width: 90,
    height: 90,
    borderRadius: 45,
    borderWidth: 3,
    borderColor: COLORS.accent,
  },
  avatarPlaceholder: {
    width: 90,
    height: 90,
    borderRadius: 45,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 3,
    borderColor: COLORS.surfaceLight,
  },
  verifiedBadge: {
    position: 'absolute',
    bottom: 0,
    right: 0,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  name: {
    color: COLORS.text,
    fontSize: 22,
    fontWeight: '800',
  },
  subtitle: {
    color: COLORS.textDim,
    fontSize: 13,
    marginTop: 2,
  },
  ratingContainer: {
    alignItems: 'center',
    marginTop: 14,
    gap: 4,
  },
  ratingNumber: {
    color: COLORS.gold,
    fontSize: 28,
    fontWeight: '800',
  },
  starsRow: {
    flexDirection: 'row',
    gap: 3,
  },
  ratingCount: {
    color: COLORS.textDim,
    fontSize: 12,
  },
  section: {
    paddingHorizontal: 16,
    marginTop: 20,
  },
  sectionTitle: {
    color: COLORS.text,
    fontSize: 17,
    fontWeight: '700',
    marginBottom: 10,
  },
  card: {
    backgroundColor: COLORS.surface,
    borderRadius: 18,
    padding: 4,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.04)',
  },
  cardRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
  },
  cardDivider: {
    height: 1,
    backgroundColor: COLORS.surfaceLight,
    marginHorizontal: 14,
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: 'rgba(0, 212, 170, 0.08)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  cardLabel: {
    color: COLORS.textDim,
    fontSize: 11,
    letterSpacing: 0.3,
  },
  cardValue: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
    marginTop: 1,
  },
  cardValueDim: {
    color: COLORS.surfaceLight,
    fontSize: 13,
    marginTop: 1,
  },
  warningIcon: {
    marginLeft: 8,
  },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.04)',
  },
  actionText: {
    flex: 1,
    color: COLORS.text,
    fontSize: 15,
    fontWeight: '500',
  },
});
