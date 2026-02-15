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
  StatusBar,
} from 'react-native';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;

export default function ProfileScreen() {
  const router = useRouter();
  const { user, driver: driverData, logout } = useAuthStore();

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
      const isFilled = i <= Math.round(rating);
      stars.push(
        <Ionicons
          key={i}
          name={isFilled ? 'star' : 'star-outline'}
          size={16}
          color={isFilled ? THEME.warning : '#ccc'} // Gold/Yellow for stars
        />
      );
    }
    return stars;
  };

  return (
    <View style={styles.container}>
      <StatusBar barStyle="dark-content" />
      <ScrollView contentContainerStyle={{ paddingBottom: 100 }} showsVerticalScrollIndicator={false}>
        {/* Header / Avatar - White Background */}
        <View style={styles.header}>
          <View style={styles.avatarContainer}>
            {user?.profile_image ? (
              <Image source={{ uri: user.profile_image }} style={styles.avatar} />
            ) : (
              <View style={styles.avatarPlaceholder}>
                <Ionicons name="person" size={40} color={THEME.textDim} />
              </View>
            )}
            <View style={styles.verifiedBadge}>
              <Ionicons
                name={driverData?.is_verified ? 'checkmark-circle' : 'time-outline'}
                size={20}
                color={driverData?.is_verified ? THEME.success : THEME.warning}
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
        </View>

        {driverData?.rejection_reason && !driverData.is_verified && (
          <View style={styles.rejectionBox}>
            <Ionicons name="alert-circle" size={20} color={THEME.error} />
            <Text style={styles.rejectionText}>
              Application Rejected: {driverData.rejection_reason}
            </Text>
          </View>
        )}

        {/* Vehicle Info */}
        <View style={styles.section}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <Text style={styles.sectionTitle}>Vehicle</Text>
            <TouchableOpacity onPress={() => router.push('/vehicle-info' as any)}>
              <Text style={{ color: THEME.primary, fontSize: 13, fontWeight: '600' }}>Edit</Text>
            </TouchableOpacity>
          </View>
          <TouchableOpacity style={styles.card} onPress={() => router.push('/vehicle-info' as any)}>
            <View style={styles.cardRow}>
              <View style={styles.iconBox}>
                <FontAwesome5 name="car" size={16} color={THEME.primary} />
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
                <MaterialCommunityIcons name="card-text" size={16} color={THEME.primary} />
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
                    <Ionicons name="calendar" size={16} color={THEME.primary} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.cardLabel}>Year</Text>
                    <Text style={styles.cardValue}>{driverData.vehicle_year}</Text>
                  </View>
                </View>
              </>
            )}
          </TouchableOpacity>
        </View>

        {/* Documents */}
        <View style={styles.section}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <Text style={styles.sectionTitle}>Documents</Text>
            <TouchableOpacity onPress={() => router.push('/documents' as any)}>
              <Text style={{ color: THEME.primary, fontSize: 13, fontWeight: '600' }}>Manage</Text>
            </TouchableOpacity>
          </View>
          <TouchableOpacity style={styles.card} onPress={() => router.push('/documents' as any)}>
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
                      <Ionicons name={doc.icon as any} size={16} color={THEME.primary} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.cardLabel}>{doc.label}</Text>
                      {expiry ? (
                        <Text
                          style={[
                            styles.cardValue,
                            isExpired
                              ? { color: THEME.error }
                              : expiresIn !== null && expiresIn < 30
                                ? { color: THEME.warning }
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
                        <Ionicons name="warning" size={18} color={THEME.error} />
                      </View>
                    )}
                  </View>
                </React.Fragment>
              );
            })}
          </TouchableOpacity>
        </View>

        {/* Quick Actions */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Quick Actions</Text>

          <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/(driver)/notifications' as any)}>
            <View style={[styles.iconBox, { backgroundColor: '#F9FAFB' }]}>
              <Ionicons name="help-circle" size={18} color={THEME.text} />
            </View>
            <Text style={styles.actionText}>Help Center</Text>
            <Ionicons name="chevron-forward" size={18} color={THEME.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.actionRow} onPress={() => router.push('/(driver)/settings' as any)}>
            <View style={[styles.iconBox, { backgroundColor: '#F9FAFB' }]}>
              <Ionicons name="settings" size={18} color={THEME.text} />
            </View>
            <Text style={styles.actionText}>Settings</Text>
            <Ionicons name="chevron-forward" size={18} color={THEME.textDim} />
          </TouchableOpacity>

          <TouchableOpacity style={[styles.actionRow, { borderBottomWidth: 0 }]} onPress={handleLogout}>
            <View style={[styles.iconBox, { backgroundColor: '#FEF2F2' }]}>
              <Ionicons name="log-out" size={18} color={THEME.error} />
            </View>
            <Text style={[styles.actionText, { color: THEME.error }]}>Sign Out</Text>
            <Ionicons name="chevron-forward" size={18} color={THEME.textDim} />
          </TouchableOpacity>
        </View>
      </ScrollView >
    </View >
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: THEME.background,
  },
  header: {
    paddingTop: Platform.OS === 'ios' ? 60 : 45,
    paddingBottom: 24,
    alignItems: 'center',
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#f0f0f0',
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
    borderColor: THEME.primary,
  },
  avatarPlaceholder: {
    width: 90,
    height: 90,
    borderRadius: 45,
    backgroundColor: THEME.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  verifiedBadge: {
    position: 'absolute',
    bottom: 0,
    right: 0,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: '#fff',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  name: {
    color: THEME.text,
    fontSize: 22,
    fontWeight: '800',
  },
  subtitle: {
    color: THEME.textDim,
    fontSize: 13,
    marginTop: 2,
  },
  ratingContainer: {
    alignItems: 'center',
    marginTop: 14,
    gap: 4,
  },
  ratingNumber: {
    color: THEME.text,
    fontSize: 24,
    fontWeight: '800',
  },
  starsRow: {
    flexDirection: 'row',
    gap: 3,
  },
  ratingCount: {
    color: THEME.textDim,
    fontSize: 12,
  },
  section: {
    paddingHorizontal: 16,
    marginTop: 20,
  },
  sectionTitle: {
    color: THEME.text,
    fontSize: 17,
    fontWeight: '700',
    marginBottom: 10,
  },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 4,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 2,
  },
  cardRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
  },
  cardDivider: {
    height: 1,
    backgroundColor: '#F3F4F6',
    marginHorizontal: 14,
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: '#FFF5F5', // Light red bg
    justifyContent: 'center',
    alignItems: 'center',
  },
  cardLabel: {
    color: THEME.textDim,
    fontSize: 11,
    letterSpacing: 0.3,
  },
  cardValue: {
    color: THEME.text,
    fontSize: 14,
    fontWeight: '600',
    marginTop: 1,
  },
  cardValueDim: {
    color: '#9CA3AF',
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
    borderBottomColor: '#F3F4F6',
  },
  actionText: {
    flex: 1,
    color: THEME.text,
    fontSize: 15,
    fontWeight: '500',
  },
  rejectionBox: {
    margin: 16,
    padding: 12,
    backgroundColor: '#FEF2F2',
    borderWidth: 1,
    borderColor: THEME.error,
    borderRadius: 8,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  rejectionText: {
    color: THEME.error,
    flex: 1,
    fontSize: 13,
  },
});
