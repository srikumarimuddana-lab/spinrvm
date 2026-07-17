import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl, ScrollView, Modal, TextInput,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface RewardTier {
  id: string;
  name: string;
  points_required: number;
  reward_value: number;
  description: string;
}

const REWARD_TIERS: RewardTier[] = [
  { id: 'r100',  name: '$1 Ride Credit',  points_required: 100,  reward_value: 1,  description: '100 pts → $1 off your next ride' },
  { id: 'r500',  name: '$5 Ride Credit',  points_required: 500,  reward_value: 5,  description: '500 pts → $5 off your next ride' },
  { id: 'r1000', name: '$10 Ride Credit', points_required: 1000, reward_value: 10, description: '1,000 pts → $10 off your next ride' },
  { id: 'r2500', name: '$25 Ride Credit', points_required: 2500, reward_value: 25, description: '2,500 pts → $25 off (best value!)' },
];

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
  const [showRedeemModal, setShowRedeemModal] = useState(false);
  const [selectedReward, setSelectedReward] = useState<RewardTier | null>(null);
  const [redeeming, setRedeeming] = useState(false);
  const [redeemAlert, setRedeemAlert] = useState<{ visible: boolean; title: string; message: string; variant: 'info' | 'warning' | 'danger' | 'success' }>({ visible: false, title: '', message: '', variant: 'info' });

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [loyaltyRes, historyRes] = await Promise.all([
        api.get<LoyaltyData>('/loyalty'),
        api.get<LoyaltyHistoryItem[]>('/loyalty/history'),
      ]);
      setLoyalty(loyaltyRes.data);
      setHistory((historyRes.data as LoyaltyHistoryItem[]) || []);
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

  const handleRedeem = async () => {
    if (!selectedReward || !loyalty) return;
    if (loyalty.points < selectedReward.points_required) {
      setRedeemAlert({ visible: true, title: 'Not enough points', message: `You need ${selectedReward.points_required.toLocaleString()} pts but have ${loyalty.points.toLocaleString()}.`, variant: 'warning' });
      return;
    }
    setRedeeming(true);
    try {
      await api.post('/loyalty/redeem', {
        reward_id: selectedReward.id,
        points: selectedReward.points_required,
        reward_value: selectedReward.reward_value,
      });
      setShowRedeemModal(false);
      setSelectedReward(null);
      setRedeemAlert({ visible: true, title: 'Redeemed!', message: `${selectedReward.points_required.toLocaleString()} pts redeemed for $${selectedReward.reward_value} ride credit. It will apply on your next booking.`, variant: 'success' });
      loadData(true);
    } catch (err: unknown) {
      setRedeemAlert({ visible: true, title: 'Error', message: getApiErrorMessage(err, 'Redemption failed. Please try again.'), variant: 'danger' });
    } finally {
      setRedeeming(false);
    }
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
        <TouchableOpacity
          style={styles.redeemHeaderBtn}
          onPress={() => setShowRedeemModal(true)}
          disabled={!loyalty}
        >
          <Ionicons name="gift" size={20} color={loyalty ? colors.primary : colors.textDim} />
        </TouchableOpacity>
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

                {/* Redeem CTA */}
                <TouchableOpacity style={styles.redeemCta} onPress={() => setShowRedeemModal(true)}>
                  <View style={styles.redeemCtaIcon}>
                    <Ionicons name="gift" size={22} color="#FFF" />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.redeemCtaTitle}>Redeem Points</Text>
                    <Text style={styles.redeemCtaSub}>Turn your points into ride credits</Text>
                  </View>
                  <Ionicons name="chevron-forward" size={18} color={colors.primary} />
                </TouchableOpacity>

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
      {/* Redeem Modal */}
      <Modal visible={showRedeemModal} animationType="slide" transparent onRequestClose={() => setShowRedeemModal(false)}>
        <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={() => setShowRedeemModal(false)}>
          <TouchableOpacity activeOpacity={1} style={styles.redeemSheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.redeemSheetTitle}>Redeem Points</Text>
            <Text style={styles.redeemSheetSub}>
              You have <Text style={{ fontWeight: '800', color: colors.primary }}>{(loyalty?.points ?? 0).toLocaleString()} pts</Text>
            </Text>

            {REWARD_TIERS.map(tier => {
              const canAfford = (loyalty?.points ?? 0) >= tier.points_required;
              const isSelected = selectedReward?.id === tier.id;
              return (
                <TouchableOpacity
                  key={tier.id}
                  style={[
                    styles.rewardRow,
                    isSelected && styles.rewardRowSelected,
                    !canAfford && styles.rewardRowDisabled,
                  ]}
                  onPress={() => canAfford && setSelectedReward(tier)}
                  disabled={!canAfford}
                >
                  <View style={styles.rewardIcon}>
                    <Ionicons name="ticket" size={20} color={canAfford ? colors.primary : colors.textDim} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.rewardName, !canAfford && { color: colors.textDim }]}>{tier.name}</Text>
                    <Text style={styles.rewardDesc}>{tier.description}</Text>
                  </View>
                  <View style={styles.rewardCost}>
                    <Text style={[styles.rewardCostText, !canAfford && { color: colors.textDim }]}>
                      {tier.points_required.toLocaleString()} pts
                    </Text>
                    {!canAfford && (
                      <Text style={styles.rewardNeedMore}>
                        Need {(tier.points_required - (loyalty?.points ?? 0)).toLocaleString()} more
                      </Text>
                    )}
                  </View>
                  {isSelected && <Ionicons name="checkmark-circle" size={22} color={colors.primary} style={{ marginLeft: 6 }} />}
                </TouchableOpacity>
              );
            })}

            <TouchableOpacity
              style={[styles.redeemConfirmBtn, (!selectedReward || redeeming) && styles.redeemConfirmBtnDisabled]}
              onPress={handleRedeem}
              disabled={!selectedReward || redeeming}
            >
              {redeeming ? <ActivityIndicator color="#FFF" /> : (
                <Text style={styles.redeemConfirmText}>
                  {selectedReward ? `Redeem ${selectedReward.points_required.toLocaleString()} pts` : 'Select a reward'}
                </Text>
              )}
            </TouchableOpacity>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      {/* Redeem Alert */}
      {redeemAlert.visible && (
        <Modal transparent animationType="fade" visible>
          <View style={styles.modalOverlay}>
            <View style={{ backgroundColor: colors.surface, borderRadius: 20, padding: 24, margin: 24 }}>
              <Text style={{ fontSize: 17, fontWeight: '700', color: colors.text, marginBottom: 8 }}>{redeemAlert.title}</Text>
              <Text style={{ fontSize: 15, color: colors.textSecondary, marginBottom: 20 }}>{redeemAlert.message}</Text>
              <TouchableOpacity
                style={{ backgroundColor: colors.primary, borderRadius: 12, paddingVertical: 14, alignItems: 'center' }}
                onPress={() => setRedeemAlert(prev => ({ ...prev, visible: false }))}
              >
                <Text style={{ fontSize: 16, fontWeight: '600', color: '#FFF' }}>OK</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
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

    // Header redeem button
    redeemHeaderBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },

    // Redeem CTA in list header
    redeemCta: {
      flexDirection: 'row', alignItems: 'center', gap: 14,
      backgroundColor: `${colors.primary}12`, borderRadius: 16, padding: 16, marginBottom: 20,
      borderWidth: 1, borderColor: `${colors.primary}30`,
    },
    redeemCtaIcon: {
      width: 44, height: 44, borderRadius: 12, backgroundColor: colors.primary,
      justifyContent: 'center', alignItems: 'center',
    },
    redeemCtaTitle: { fontSize: 16, fontWeight: '700', color: colors.text },
    redeemCtaSub: { fontSize: 12, color: colors.textDim, marginTop: 2 },

    // Modal
    modalOverlay: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.4)' },
    redeemSheet: {
      backgroundColor: colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24,
      paddingHorizontal: 20, paddingBottom: 40, paddingTop: 12,
    },
    sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border, alignSelf: 'center', marginBottom: 16 },
    redeemSheetTitle: { fontSize: 20, fontWeight: '800', color: colors.text, marginBottom: 4 },
    redeemSheetSub: { fontSize: 14, color: colors.textDim, marginBottom: 16 },

    rewardRow: {
      flexDirection: 'row', alignItems: 'center', gap: 12,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14, marginBottom: 8,
      borderWidth: 1.5, borderColor: 'transparent',
    },
    rewardRowSelected: { borderColor: colors.primary, backgroundColor: `${colors.primary}10` },
    rewardRowDisabled: { opacity: 0.5 },
    rewardIcon: {
      width: 40, height: 40, borderRadius: 10,
      backgroundColor: colors.surface, justifyContent: 'center', alignItems: 'center',
    },
    rewardName: { fontSize: 15, fontWeight: '700', color: colors.text },
    rewardDesc: { fontSize: 12, color: colors.textDim, marginTop: 1 },
    rewardCost: { alignItems: 'flex-end' },
    rewardCostText: { fontSize: 13, fontWeight: '700', color: colors.primary },
    rewardNeedMore: { fontSize: 10, color: colors.textDim, marginTop: 2 },

    redeemConfirmBtn: {
      marginTop: 16, backgroundColor: colors.primary, borderRadius: 24,
      paddingVertical: 16, alignItems: 'center',
    },
    redeemConfirmBtnDisabled: { opacity: 0.5 },
    redeemConfirmText: { fontSize: 16, fontWeight: '700', color: '#FFF' },
  });
}
