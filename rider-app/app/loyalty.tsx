import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl, ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface LoyaltyData {
  points: number;
  lifetime_points: number;
  tier: string;
  multiplier: number;
  next_tier: { tier: string; points_needed: number } | null;
  redemption_rate: number;
}

interface LoyaltyHistoryItem {
  id: string;
  type: string;
  points: number;
  description: string;
  reference_id?: string;
  created_at: string;
}

const TIER_COLORS: Record<string, string> = {
  bronze: '#CD7F32',
  silver: '#C0C0C0',
  gold: '#FFB800',
  platinum: '#6B7280',
};

function getTierColor(tier: string): string {
  return TIER_COLORS[tier?.toLowerCase()] ?? '#CD7F32';
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch { return ''; }
}

function getHistoryIcon(type: string): { name: string; color: string } {
  switch (type) {
    case 'earn':
    case 'ride_earn':
      return { name: 'add-circle', color: '#10B981' };
    case 'redeem':
    case 'redemption':
      return { name: 'remove-circle', color: '#EF4444' };
    case 'bonus':
      return { name: 'star', color: '#F59E0B' };
    case 'promo':
    case 'promotion':
      return { name: 'gift', color: '#8B5CF6' };
    case 'expire':
    case 'expiry':
      return { name: 'time', color: '#9CA3AF' };
    default:
      return { name: 'ellipse', color: '#6B7280' };
  }
}

export default function LoyaltyScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [loyalty, setLoyalty] = useState<LoyaltyData | null>(null);
  const [history, setHistory] = useState<LoyaltyHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [loyaltyRes, historyRes] = await Promise.all([
        api.get('/loyalty'),
        api.get('/loyalty/history'),
      ]);
      setLoyalty(loyaltyRes.data);
      setHistory(historyRes.data || []);
    } catch {}
    finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleRefresh = () => {
    setRefreshing(true);
    loadData(true);
  };

  const tierColor = loyalty ? getTierColor(loyalty.tier) : '#CD7F32';
  const tierLabel = loyalty ? (loyalty.tier.charAt(0).toUpperCase() + loyalty.tier.slice(1).toLowerCase()) : '';

  // Progress bar calculation
  const progressPercent = useMemo(() => {
    if (!loyalty || !loyalty.next_tier) return 100;
    const needed = loyalty.next_tier.points_needed;
    if (needed <= 0) return 100;
    // Points already accumulated toward next tier = lifetime_points - what was needed to reach current tier
    // Since we only know next_tier.points_needed (points still needed), we compute progress as:
    // progress = 1 - (points_needed / (points_needed + current_points))
    // This is a reasonable approximation when we don't have the tier threshold
    const total = needed + loyalty.points;
    return Math.min(100, Math.max(0, Math.round((loyalty.points / total) * 100)));
  }, [loyalty]);

  const renderHistoryItem = ({ item }: { item: LoyaltyHistoryItem }) => {
    const iconInfo = getHistoryIcon(item.type);
    const isPositive = item.points >= 0;
    return (
      <View style={styles.historyCard}>
        <View style={[styles.historyIconWrap, { backgroundColor: `${iconInfo.color}18` }]}>
          <Ionicons name={iconInfo.name as any} size={22} color={iconInfo.color} />
        </View>
        <View style={styles.historyContent}>
          <Text style={styles.historyDesc} numberOfLines={2}>{item.description}</Text>
          <Text style={styles.historyDate}>{formatDate(item.created_at)}</Text>
        </View>
        <Text style={[styles.historyPoints, { color: isPositive ? '#10B981' : '#EF4444' }]}>
          {isPositive ? '+' : ''}{item.points.toLocaleString()} pts
        </Text>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Rewards</Text>
        <View style={{ width: 44 }} />
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <FlatList
          data={history}
          renderItem={renderHistoryItem}
          keyExtractor={(item) => item.id}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor={colors.primary}
              colors={[colors.primary]}
            />
          }
          ListHeaderComponent={
            loyalty ? (
              <View>
                {/* Tier Card */}
                <View style={[styles.tierCard, { borderTopColor: tierColor, borderTopWidth: 4 }]}>
                  {/* Tier badge + points */}
                  <View style={styles.tierTopRow}>
                    <View>
                      <View style={[styles.tierBadge, { backgroundColor: `${tierColor}22` }]}>
                        <Ionicons name="trophy" size={14} color={tierColor} />
                        <Text style={[styles.tierBadgeText, { color: tierColor }]}>{tierLabel}</Text>
                      </View>
                      <Text style={styles.tierPointsLabel}>Your Points</Text>
                      <Text style={[styles.tierPointsValue, { color: tierColor }]}>
                        {loyalty.points.toLocaleString()}
                      </Text>
                    </View>
                    <View style={styles.multiplierBox}>
                      <Text style={styles.multiplierValue}>{loyalty.multiplier}×</Text>
                      <Text style={styles.multiplierLabel}>Multiplier</Text>
                    </View>
                  </View>

                  {/* Progress bar */}
                  {loyalty.next_tier ? (
                    <View style={styles.progressSection}>
                      <View style={styles.progressLabelRow}>
                        <Text style={styles.progressLabel}>{tierLabel}</Text>
                        <Text style={styles.progressLabel}>
                          {loyalty.next_tier.tier.charAt(0).toUpperCase() + loyalty.next_tier.tier.slice(1).toLowerCase()}
                        </Text>
                      </View>
                      <View style={styles.progressTrack}>
                        <View style={[styles.progressFill, { width: `${progressPercent}%`, backgroundColor: tierColor }]} />
                      </View>
                      <Text style={styles.progressHint}>
                        {loyalty.next_tier.points_needed.toLocaleString()} pts needed to reach{' '}
                        {loyalty.next_tier.tier.charAt(0).toUpperCase() + loyalty.next_tier.tier.slice(1).toLowerCase()}
                      </Text>
                    </View>
                  ) : (
                    <View style={styles.progressSection}>
                      <Text style={[styles.progressHint, { color: tierColor, fontWeight: '600' }]}>
                        You've reached the highest tier!
                      </Text>
                    </View>
                  )}
                </View>

                {/* Points info row */}
                <View style={styles.infoRow}>
                  <View style={styles.infoCard}>
                    <Ionicons name="star" size={20} color="#F59E0B" />
                    <Text style={styles.infoValue}>{loyalty.points.toLocaleString()}</Text>
                    <Text style={styles.infoLabel}>Current Points</Text>
                  </View>
                  <View style={styles.infoSep} />
                  <View style={styles.infoCard}>
                    <Ionicons name="flame" size={20} color={colors.primary} />
                    <Text style={styles.infoValue}>{loyalty.lifetime_points.toLocaleString()}</Text>
                    <Text style={styles.infoLabel}>Lifetime Points</Text>
                  </View>
                  <View style={styles.infoSep} />
                  <View style={styles.infoCard}>
                    <Ionicons name="cash-outline" size={20} color="#10B981" />
                    <Text style={styles.infoValue}>
                      {loyalty.redemption_rate > 0
                        ? `${Math.round(1 / loyalty.redemption_rate)} pts`
                        : '100 pts'}
                    </Text>
                    <Text style={styles.infoLabel}>= $1 value</Text>
                  </View>
                </View>

                {/* History header */}
                <Text style={styles.sectionTitle}>Points History</Text>
              </View>
            ) : null
          }
          contentContainerStyle={styles.list}
          ListEmptyComponent={
            !loading ? (
              <View style={styles.empty}>
                <Ionicons name="receipt-outline" size={48} color="#DDD" />
                <Text style={styles.emptyTitle}>No points history yet</Text>
                <Text style={styles.emptySub}>Complete rides to start earning points</Text>
              </View>
            ) : null
          }
        />
      )}
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: 16, paddingBottom: 32 },

    // Tier card
    tierCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 20, marginBottom: 12,
    },
    tierTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 },
    tierBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 5,
      paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20, alignSelf: 'flex-start', marginBottom: 10,
    },
    tierBadgeText: { fontSize: 13, fontWeight: '700', letterSpacing: 0.3 },
    tierPointsLabel: { fontSize: 12, color: colors.textDim, marginBottom: 2 },
    tierPointsValue: { fontSize: 36, fontWeight: '800', letterSpacing: -1 },
    multiplierBox: {
      alignItems: 'center',
      backgroundColor: colors.surface, borderRadius: 14, paddingHorizontal: 16, paddingVertical: 10,
    },
    multiplierValue: { fontSize: 22, fontWeight: '800', color: colors.text },
    multiplierLabel: { fontSize: 11, color: colors.textDim, marginTop: 2 },

    progressSection: { marginTop: 4 },
    progressLabelRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
    progressLabel: { fontSize: 11, fontWeight: '600', color: colors.textDim },
    progressTrack: {
      height: 8, backgroundColor: colors.border, borderRadius: 4, overflow: 'hidden', marginBottom: 6,
    },
    progressFill: { height: '100%', borderRadius: 4 },
    progressHint: { fontSize: 12, color: colors.textDim, textAlign: 'center' },

    // Info row
    infoRow: {
      flexDirection: 'row', backgroundColor: colors.surfaceLight,
      borderRadius: 16, marginBottom: 20, overflow: 'hidden',
    },
    infoCard: { flex: 1, alignItems: 'center', paddingVertical: 14, gap: 4 },
    infoSep: { width: 1, backgroundColor: colors.border, marginVertical: 12 },
    infoValue: { fontSize: 15, fontWeight: '800', color: colors.text },
    infoLabel: { fontSize: 10, color: colors.textDim, textAlign: 'center' },

    sectionTitle: {
      fontSize: 13, fontWeight: '700', color: colors.textDim,
      letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 10,
    },

    // History
    historyCard: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 14, marginBottom: 10,
    },
    historyIconWrap: {
      width: 44, height: 44, borderRadius: 13, justifyContent: 'center', alignItems: 'center', marginRight: 12,
    },
    historyContent: { flex: 1 },
    historyDesc: { fontSize: 14, fontWeight: '500', color: colors.text, marginBottom: 2 },
    historyDate: { fontSize: 11, color: colors.textDim },
    historyPoints: { fontSize: 14, fontWeight: '700', marginLeft: 8 },

    empty: { alignItems: 'center', paddingVertical: 40 },
    emptyTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 12 },
    emptySub: { fontSize: 13, color: colors.textDim, marginTop: 4, textAlign: 'center' },
  });
}
