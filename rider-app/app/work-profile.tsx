import React, { useEffect, useMemo, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
} from 'react-native';
import CustomToggle from '../components/CustomToggle';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useWorkProfileStore } from '../store/workProfileStore';
import api from '@shared/api/client';

interface WorkRide {
  id: string;
  dropoff_address: string;
  total_fare: number;
  created_at: string;
  status: string;
  allowance_debit_amount: number | null;
  master_fallback_amount: number | null;
  source_type: string | null;
}

export default function WorkProfileScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const {
    profiles,
    activeCompanyId,
    workModeEnabled,
    balance,
    balanceLoading,
    isLoading,
    fetchProfiles,
    setActiveCompany,
    setWorkMode,
    fetchBalance,
  } = useWorkProfileStore();

  const [refreshing, setRefreshing] = useState(false);
  const [rides, setRides] = useState<WorkRide[]>([]);
  const [ridesLoading, setRidesLoading] = useState(false);

  const activeProfile = profiles.find(p => p.company.id === activeCompanyId);

  const loadAll = useCallback(async () => {
    await fetchProfiles();
    if (activeCompanyId) {
      await fetchBalance();
      setRidesLoading(true);
      try {
        const res = await api.get<WorkRide[]>(`/rider/work-profile/${activeCompanyId}/rides`);
        setRides((res.data || []).slice(0, 5));
      } catch {
        setRides([]);
      } finally {
        setRidesLoading(false);
      }
    }
  }, [activeCompanyId, fetchProfiles, fetchBalance]);

  useEffect(() => {
    // Mount-only load; deps are empty so the state loadAll sets can't
    // retrigger this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadAll();
  }, []);

  // TODO(C20): this effect re-fetches balance + the same rides list that
  // loadAll() above already fetches whenever activeCompanyId is set on
  // mount — the two run concurrently on first render (loadAll's fetch and
  // this effect's fetch to the same `/rider/work-profile/:id/rides`
  // endpoint), so whichever response resolves last silently wins. Not a
  // render loop (ridesLoading/rides aren't deps here), but it is a
  // redundant-fetch / minor race that looks unintentional rather than a
  // deliberate "load once, then re-load on company switch" split — the
  // duplication should probably be removed by having the mount effect only
  // call loadAll() and let *this* effect handle both company-switch AND
  // initial load, but confirming that's safe needs someone who knows why
  // both paths were written. Left unfixed — flagged in the C20 round 3
  // report as needing a human decision (rider-app/app/work-profile.tsx:76-85).
  useEffect(() => {
    if (activeCompanyId) {
      fetchBalance();
      setRidesLoading(true);
      api.get<WorkRide[]>(`/rider/work-profile/${activeCompanyId}/rides`)
        .then(r => setRides((r.data || []).slice(0, 5)))
        .catch(() => setRides([]))
        .finally(() => setRidesLoading(false));
    }
  }, [activeCompanyId]);

  const onRefresh = async () => {
    setRefreshing(true);
    await loadAll();
    setRefreshing(false);
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' });
  };

  const formatPeriod = (start: string | null, end: string | null) => {
    if (!start || !end) return null;
    const s = new Date(start).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
    const e = new Date(end).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
    return `${s} – ${e}`;
  };

  const renderBalance = () => {
    if (balanceLoading) {
      return (
        <View style={styles.balanceCard}>
          <ActivityIndicator color="#FFF" />
        </View>
      );
    }
    if (!balance) {
      return (
        <LinearGradient colors={['#6366F1', '#818CF8']} style={styles.balanceCard}>
          <Text style={styles.balanceCompany}>{activeProfile?.company.name ?? 'Work Profile'}</Text>
          <Text style={styles.balanceNoData}>No allowance configured</Text>
          <Text style={styles.balanceHint}>Contact your company admin to set up an allowance.</Text>
        </LinearGradient>
      );
    }

    const isUnlimited = balance.type === 'unlimited';
    const remaining = balance.remaining;
    const period = formatPeriod(balance.period_start, balance.period_end);

    return (
      <LinearGradient colors={['#1D4ED8', '#3B82F6']} style={styles.balanceCard}>
        <View style={styles.balanceHeader}>
          <Text style={styles.balanceCompany}>{balance.company_name ?? activeProfile?.company.name}</Text>
          {balance.status === 'active' && (
            <View style={styles.activeBadge}>
              <Text style={styles.activeBadgeText}>Active</Text>
            </View>
          )}
          {balance.status && balance.status !== 'active' && (
            <View style={[styles.activeBadge, { backgroundColor: 'rgba(255,255,255,0.25)' }]}>
              <Text style={styles.activeBadgeText}>{balance.status}</Text>
            </View>
          )}
        </View>

        <Text style={styles.balanceAmount}>
          {isUnlimited ? 'Unlimited' : remaining != null ? `$${remaining.toFixed(2)}` : '—'}
        </Text>
        <Text style={styles.balanceLabel}>
          {isUnlimited ? 'No spending cap' : 'Remaining allowance'}
        </Text>

        {!isUnlimited && balance.amount != null && (
          <View style={styles.balanceRow}>
            <Text style={styles.balanceSub}>Limit: ${balance.amount.toFixed(2)}</Text>
            {balance.used != null && balance.used > 0 && (
              <Text style={styles.balanceSub}>Used: ${balance.used.toFixed(2)}</Text>
            )}
          </View>
        )}

        {period && (
          <View style={styles.periodRow}>
            <Ionicons name="calendar-outline" size={13} color="rgba(255,255,255,0.8)" />
            <Text style={styles.periodText}>{period}</Text>
          </View>
        )}
      </LinearGradient>
    );
  };

  if (isLoading && profiles.length === 0) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Work Profile</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={styles.loadingCenter}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      </SafeAreaView>
    );
  }

  if (!isLoading && profiles.length === 0) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Work Profile</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={styles.emptyState}>
          <View style={styles.emptyIcon}>
            <Ionicons name="briefcase-outline" size={48} color={colors.textDim} />
          </View>
          <Text style={styles.emptyTitle}>No work profile yet</Text>
          <Text style={styles.emptyText}>
            Ask your company admin to send you an invite link, or check if your work email matches an allowed domain.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Work Profile</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        contentContainerStyle={{ paddingBottom: 40 }}
      >
        {/* Personal / Work toggle */}
        <View style={styles.modeSection}>
          <View style={styles.modeToggleRow}>
            <View style={styles.modeInfo}>
              <Ionicons
                name={workModeEnabled ? 'briefcase' : 'person'}
                size={20}
                color={workModeEnabled ? '#3B82F6' : colors.primary}
              />
              <View style={{ marginLeft: 12 }}>
                <Text style={styles.modeTitle}>
                  {workModeEnabled ? 'Work Mode' : 'Personal Mode'}
                </Text>
                <Text style={styles.modeSubtitle}>
                  {workModeEnabled
                    ? 'Rides billed to your company allowance'
                    : 'Rides billed to your personal payment method'}
                </Text>
              </View>
            </View>
            <CustomToggle
              value={workModeEnabled}
              onValueChange={v => setWorkMode(v)}
              trackColor={{ false: colors.border, true: '#3B82F6' }}
              thumbColor="#FFF"
            />
          </View>
        </View>

        {/* Company selector — shown if user has multiple profiles */}
        {profiles.length > 1 && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Company</Text>
            {profiles.map(p => {
              const isActive = p.company.id === activeCompanyId;
              return (
                <TouchableOpacity
                  key={p.company.id}
                  style={[styles.companyOption, isActive && styles.companyOptionActive]}
                  onPress={() => p.company.id && setActiveCompany(p.company.id)}
                >
                  <View style={[styles.companyIcon, isActive && { backgroundColor: '#DBEAFE' }]}>
                    <Ionicons name="business" size={18} color={isActive ? '#1D4ED8' : colors.textDim} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.companyName, isActive && { color: '#1D4ED8' }]}>
                      {p.company.name ?? 'Unknown company'}
                    </Text>
                    <Text style={styles.companyRole}>{p.membership.role}</Text>
                  </View>
                  {isActive && <Ionicons name="checkmark-circle" size={20} color="#1D4ED8" />}
                </TouchableOpacity>
              );
            })}
          </View>
        )}

        {/* Balance Card */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Allowance Balance</Text>
          {renderBalance()}

          {/* Request More Funds */}
          {balance && balance.type !== 'unlimited' && balance.status === 'active' && (
            <TouchableOpacity
              style={styles.requestBtn}
              onPress={() => router.push('/work-allowance-request' as any)}
            >
              <Ionicons name="add-circle-outline" size={20} color={colors.primary} />
              <Text style={styles.requestBtnText}>Request More Funds</Text>
              <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
            </TouchableOpacity>
          )}
        </View>

        {/* Recent Work Rides */}
        <View style={styles.section}>
          <View style={styles.sectionRow}>
            <Text style={styles.sectionTitle}>Recent Work Rides</Text>
            {activeCompanyId && (
              <TouchableOpacity
                onPress={() => router.push({ pathname: '/work-rides', params: { companyId: activeCompanyId } } as any)}
              >
                <Text style={styles.seeAll}>See all</Text>
              </TouchableOpacity>
            )}
          </View>

          {ridesLoading ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 12 }} />
          ) : rides.length === 0 ? (
            <View style={styles.emptyRides}>
              <Ionicons name="car-outline" size={32} color={colors.textDim} />
              <Text style={styles.emptyRidesText}>No work rides yet</Text>
            </View>
          ) : (
            rides.map(ride => (
              <View key={ride.id} style={styles.rideRow}>
                <View style={styles.rideIcon}>
                  <Ionicons name="checkmark-circle" size={18} color={ride.status === 'cancelled' ? colors.textDim : '#10B981'} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rideAddress} numberOfLines={1}>{ride.dropoff_address || 'Unknown'}</Text>
                  <Text style={styles.rideDate}>{formatDate(ride.created_at)}</Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={styles.rideFare}>
                    ${ride.status === 'cancelled' ? '0.00' : (ride.total_fare ?? 0).toFixed(2)}
                  </Text>
                  {ride.allowance_debit_amount != null && ride.allowance_debit_amount > 0 && (
                    <Text style={styles.rideAllowance}>
                      Allowance: ${ride.allowance_debit_amount.toFixed(2)}
                    </Text>
                  )}
                </View>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surfaceLight },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },

    loadingCenter: { flex: 1, justifyContent: 'center', alignItems: 'center' },

    modeSection: {
      backgroundColor: colors.surface, marginBottom: 12, padding: 16,
    },
    modeToggleRow: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    },
    modeInfo: { flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 12 },
    modeTitle: { fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    modeSubtitle: { fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2 },

    section: { backgroundColor: colors.surface, marginBottom: 12, paddingHorizontal: 16, paddingVertical: 16 },
    sectionRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
    sectionTitle: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_700Bold', color: colors.textDim,
      letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 12,
    },
    seeAll: { fontSize: 13, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.primary },

    companyOption: {
      flexDirection: 'row', alignItems: 'center', paddingVertical: 10,
      paddingHorizontal: 12, borderRadius: 12, marginBottom: 8,
      borderWidth: 1.5, borderColor: colors.border, backgroundColor: colors.surfaceLight,
    },
    companyOptionActive: { borderColor: '#93C5FD', backgroundColor: '#EFF6FF' },
    companyIcon: {
      width: 36, height: 36, borderRadius: 10, backgroundColor: colors.surfaceLight,
      alignItems: 'center', justifyContent: 'center', marginRight: 12,
    },
    companyName: { fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    companyRole: { fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 1 },

    balanceCard: {
      borderRadius: 16, padding: 20, marginBottom: 12,
    },
    balanceHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 },
    balanceCompany: { fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: 'rgba(255,255,255,0.9)' },
    activeBadge: {
      backgroundColor: 'rgba(255,255,255,0.2)', borderRadius: 10,
      paddingHorizontal: 10, paddingVertical: 3,
    },
    activeBadgeText: { fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold', color: '#FFF' },
    balanceAmount: {
      fontSize: 42, fontFamily: 'PlusJakartaSans_700Bold', color: '#FFF', marginBottom: 4,
    },
    balanceLabel: { fontSize: 13, fontFamily: 'PlusJakartaSans_400Regular', color: 'rgba(255,255,255,0.75)', marginBottom: 14 },
    balanceRow: { flexDirection: 'row', gap: 16 },
    balanceSub: { fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: 'rgba(255,255,255,0.8)' },
    balanceNoData: { fontSize: 26, fontFamily: 'PlusJakartaSans_700Bold', color: '#FFF', marginVertical: 8 },
    balanceHint: { fontSize: 13, color: 'rgba(255,255,255,0.75)' },
    periodRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10 },
    periodText: { fontSize: 12, fontFamily: 'PlusJakartaSans_500Medium', color: 'rgba(255,255,255,0.8)' },

    requestBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      paddingVertical: 14, borderTopWidth: 1, borderTopColor: colors.border,
    },
    requestBtnText: { flex: 1, fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.primary },

    rideRow: {
      flexDirection: 'row', alignItems: 'center', gap: 12,
      paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    rideIcon: {
      width: 36, height: 36, borderRadius: 18, backgroundColor: '#F0FDF4',
      alignItems: 'center', justifyContent: 'center',
    },
    rideAddress: { fontSize: 14, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    rideDate: { fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2 },
    rideFare: { fontSize: 15, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text },
    rideAllowance: { fontSize: 11, fontFamily: 'PlusJakartaSans_400Regular', color: '#3B82F6', marginTop: 2 },

    emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 40, paddingTop: 80 },
    emptyIcon: {
      width: 96, height: 96, borderRadius: 48, backgroundColor: colors.surfaceLight,
      alignItems: 'center', justifyContent: 'center', marginBottom: 20,
    },
    emptyTitle: { fontSize: 20, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text, marginBottom: 10 },
    emptyText: { fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, textAlign: 'center', lineHeight: 22 },

    emptyRides: { alignItems: 'center', paddingVertical: 24 },
    emptyRidesText: { fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 8 },
  });
}
