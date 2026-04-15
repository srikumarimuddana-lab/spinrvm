import React, { useState, useEffect, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
  ActivityIndicator, RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useQuestStore, Quest, MyQuestProgress } from '../../store/questStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const QUEST_TYPE_ICONS: Record<string, string> = {
  ride_count: 'car',
  earnings_target: 'cash',
  online_hours: 'time',
  peak_rides: 'flash',
  consecutive_days: 'calendar',
  rating_maintained: 'star',
};

const QUEST_TYPE_LABELS: Record<string, string> = {
  ride_count: 'Complete rides',
  earnings_target: 'Earn target',
  online_hours: 'Online hours',
  peak_rides: 'Peak hour rides',
  consecutive_days: 'Consecutive days',
  rating_maintained: 'Maintain rating',
};

export default function QuestsScreen() {
  const router = useRouter();
  const {
    availableQuests, myQuests, isLoading,
    fetchAvailableQuests, fetchMyQuests, joinQuest, claimReward,
  } = useQuestStore();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [tab, setTab] = useState<'available' | 'active'>('available');
  const [refreshing, setRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    fetchAvailableQuests();
    fetchMyQuests();
  }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    await Promise.all([fetchAvailableQuests(), fetchMyQuests()]);
    setRefreshing(false);
  };

  const handleJoin = async (questId: string) => {
    setActionLoading(questId);
    try {
      await joinQuest(questId);
    } catch {}
    setActionLoading(null);
  };

  const handleClaim = async (progressId: string) => {
    setActionLoading(progressId);
    try {
      const result = await claimReward(progressId);
      // Could show a success modal here
    } catch {}
    setActionLoading(null);
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
  };

  const getTimeRemaining = (endDate: string) => {
    const end = new Date(endDate).getTime();
    const now = Date.now();
    const diff = end - now;
    if (diff <= 0) return 'Expired';
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    if (days > 0) return `${days}d ${hours}h left`;
    return `${hours}h left`;
  };

  const renderAvailableQuest = (quest: Quest) => {
    const icon = QUEST_TYPE_ICONS[quest.type] || 'trophy';
    const isJoined = quest.status !== 'available';
    const isJoining = actionLoading === quest.id;

    return (
      <View key={quest.id} style={styles.questCard}>
        <View style={styles.questHeader}>
          <View style={[styles.questIcon, { backgroundColor: colors.primary + '15' }]}>
            <Ionicons name={icon as any} size={24} color={colors.primary} />
          </View>
          <View style={styles.questMeta}>
            <Text style={styles.questTitle}>{quest.title}</Text>
            <Text style={styles.questType}>{QUEST_TYPE_LABELS[quest.type] || quest.type}</Text>
          </View>
          <View style={styles.rewardBadge}>
            <Text style={styles.rewardAmount}>${quest.reward_amount}</Text>
            <Text style={styles.rewardLabel}>reward</Text>
          </View>
        </View>

        <Text style={styles.questDesc}>{quest.description}</Text>

        <View style={styles.questFooter}>
          <View style={styles.questTimeline}>
            <Ionicons name="time-outline" size={14} color={colors.textSecondary} />
            <Text style={styles.timeText}>{getTimeRemaining(quest.end_date)}</Text>
          </View>
          <Text style={styles.targetText}>
            Target: {quest.target_value} {quest.type === 'earnings_target' ? 'CAD' : ''}
          </Text>
        </View>

        {isJoined ? (
          <View style={styles.joinedBadge}>
            <Ionicons name="checkmark-circle" size={16} color="#10B981" />
            <Text style={styles.joinedText}>Joined</Text>
          </View>
        ) : (
          <TouchableOpacity
            style={styles.joinButton}
            onPress={() => handleJoin(quest.id)}
            disabled={isJoining}
          >
            {isJoining ? (
              <ActivityIndicator color="#FFF" size="small" />
            ) : (
              <Text style={styles.joinButtonText}>Join Quest</Text>
            )}
          </TouchableOpacity>
        )}
      </View>
    );
  };

  const renderMyQuest = (item: MyQuestProgress) => {
    const quest = item.quest;
    const icon = QUEST_TYPE_ICONS[quest.type] || 'trophy';
    const isCompleted = item.status === 'completed';
    const isClaimed = item.status === 'claimed';
    const isClaiming = actionLoading === item.progress_id;

    return (
      <View key={item.progress_id} style={styles.questCard}>
        <View style={styles.questHeader}>
          <View style={[
            styles.questIcon,
            { backgroundColor: isCompleted ? '#10B981' + '15' : isClaimed ? '#F59E0B' + '15' : colors.primary + '15' },
          ]}>
            <Ionicons
              name={(isClaimed ? 'trophy' : icon) as any}
              size={24}
              color={isCompleted ? '#10B981' : isClaimed ? '#F59E0B' : colors.primary}
            />
          </View>
          <View style={styles.questMeta}>
            <Text style={styles.questTitle}>{quest.title}</Text>
            <Text style={styles.questType}>{QUEST_TYPE_LABELS[quest.type] || quest.type}</Text>
          </View>
          <View style={[styles.statusBadge, {
            backgroundColor: isCompleted ? '#10B981' + '15' : isClaimed ? '#F59E0B' + '15' : colors.textDim + '15',
          }]}>
            <Text style={[styles.statusText, {
              color: isCompleted ? '#10B981' : isClaimed ? '#F59E0B' : colors.textDim,
            }]}>
              {isClaimed ? 'Claimed' : isCompleted ? 'Complete!' : 'Active'}
            </Text>
          </View>
        </View>

        {/* Progress Bar */}
        <View style={styles.progressSection}>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, {
              width: `${Math.min(100, item.progress_pct)}%`,
              backgroundColor: isCompleted || isClaimed ? '#10B981' : colors.primary,
            }]} />
          </View>
          <View style={styles.progressLabels}>
            <Text style={styles.progressValue}>
              {item.current_value} / {quest.target_value}
            </Text>
            <Text style={styles.progressPct}>{item.progress_pct}%</Text>
          </View>
        </View>

        <View style={styles.questFooter}>
          <Text style={styles.rewardInline}>
            Reward: ${quest.reward_amount} {quest.reward_type === 'wallet_credit' ? 'wallet credit' : 'cash'}
          </Text>
          <Text style={styles.timeText}>{getTimeRemaining(quest.end_date)}</Text>
        </View>

        {isCompleted && !isClaimed && (
          <TouchableOpacity
            style={[styles.joinButton, { backgroundColor: '#10B981' }]}
            onPress={() => handleClaim(item.progress_id)}
            disabled={isClaiming}
          >
            {isClaiming ? (
              <ActivityIndicator color="#FFF" size="small" />
            ) : (
              <>
                <Ionicons name="gift" size={18} color="#FFF" />
                <Text style={styles.joinButtonText}> Claim ${quest.reward_amount} Reward</Text>
              </>
            )}
          </TouchableOpacity>
        )}
      </View>
    );
  };

  const activeQuests = myQuests.filter(q => q.status === 'active');
  const completedQuests = myQuests.filter(q => q.status === 'completed' || q.status === 'claimed');

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Quests & Bonuses</Text>
        <View style={{ width: 44 }} />
      </View>

      {/* Tabs */}
      <View style={styles.tabs}>
        <TouchableOpacity
          style={[styles.tab, tab === 'available' && styles.tabActive]}
          onPress={() => setTab('available')}
        >
          <Text style={[styles.tabText, tab === 'available' && styles.tabTextActive]}>
            Available ({availableQuests.filter(q => q.status === 'available').length})
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tab, tab === 'active' && styles.tabActive]}
          onPress={() => setTab('active')}
        >
          <Text style={[styles.tabText, tab === 'active' && styles.tabTextActive]}>
            My Quests ({myQuests.length})
          </Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        style={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        showsVerticalScrollIndicator={false}
      >
        {isLoading && !refreshing ? (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="large" color={colors.primary} />
          </View>
        ) : tab === 'available' ? (
          availableQuests.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Ionicons name="trophy-outline" size={48} color="#CCC" />
              <Text style={styles.emptyText}>No quests available right now</Text>
              <Text style={styles.emptySubtext}>Check back soon for new challenges!</Text>
            </View>
          ) : (
            availableQuests.map(renderAvailableQuest)
          )
        ) : (
          myQuests.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Ionicons name="flag-outline" size={48} color="#CCC" />
              <Text style={styles.emptyText}>No active quests</Text>
              <Text style={styles.emptySubtext}>Join a quest to start earning bonus rewards!</Text>
            </View>
          ) : (
            <>
              {activeQuests.length > 0 && (
                <>
                  <Text style={styles.sectionLabel}>Active</Text>
                  {activeQuests.map(renderMyQuest)}
                </>
              )}
              {completedQuests.length > 0 && (
                <>
                  <Text style={styles.sectionLabel}>Completed</Text>
                  {completedQuests.map(renderMyQuest)}
                </>
              )}
            </>
          )
        )}
        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surfaceLight },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12, backgroundColor: colors.surface,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backButton: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceLight,
      alignItems: 'center', justifyContent: 'center',
    },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },

    tabs: {
      flexDirection: 'row', backgroundColor: colors.surface,
      paddingHorizontal: 16, paddingBottom: 12, gap: 8,
    },
    tab: {
      flex: 1, paddingVertical: 10, borderRadius: 24,
      backgroundColor: colors.surfaceLight, alignItems: 'center',
    },
    tabActive: { backgroundColor: colors.primary },
    tabText: { fontSize: 14, fontWeight: '600', color: colors.textDim },
    tabTextActive: { color: '#FFF' },

    content: { flex: 1, padding: 16 },

    questCard: {
      backgroundColor: colors.surface, borderRadius: 16, padding: 18, marginBottom: 12,
      shadowColor: '#000', shadowOpacity: 0.04, shadowOffset: { width: 0, height: 2 }, shadowRadius: 8,
      elevation: 2,
    },
    questHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
    questIcon: {
      width: 48, height: 48, borderRadius: 24,
      alignItems: 'center', justifyContent: 'center', marginRight: 12,
    },
    questMeta: { flex: 1 },
    questTitle: { fontSize: 16, fontWeight: '700', color: colors.text },
    questType: { fontSize: 13, color: colors.textSecondary, marginTop: 2, textTransform: 'capitalize' },

    rewardBadge: {
      backgroundColor: '#F59E0B' + '15', paddingHorizontal: 12, paddingVertical: 6,
      borderRadius: 12, alignItems: 'center',
    },
    rewardAmount: { fontSize: 16, fontWeight: '800', color: '#F59E0B' },
    rewardLabel: { fontSize: 11, color: '#F59E0B', marginTop: 1 },

    questDesc: { fontSize: 14, color: colors.textDim, lineHeight: 20, marginBottom: 12 },

    questFooter: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    questTimeline: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    timeText: { fontSize: 13, color: colors.textSecondary },
    targetText: { fontSize: 13, color: colors.textDim, fontWeight: '500' },

    joinButton: {
      backgroundColor: colors.primary, borderRadius: 12, paddingVertical: 12,
      alignItems: 'center', justifyContent: 'center', marginTop: 14,
      flexDirection: 'row',
    },
    joinButtonText: { fontSize: 15, fontWeight: '700', color: '#FFF' },

    joinedBadge: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 6, marginTop: 12, paddingVertical: 8,
      backgroundColor: '#10B981' + '10', borderRadius: 10,
    },
    joinedText: { fontSize: 14, fontWeight: '600', color: '#10B981' },

    statusBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 10 },
    statusText: { fontSize: 12, fontWeight: '700' },

    progressSection: { marginBottom: 12 },
    progressBar: {
      height: 10, backgroundColor: colors.border, borderRadius: 5, overflow: 'hidden',
      marginBottom: 6,
    },
    progressFill: { height: '100%', borderRadius: 5 },
    progressLabels: { flexDirection: 'row', justifyContent: 'space-between' },
    progressValue: { fontSize: 13, fontWeight: '600', color: colors.text },
    progressPct: { fontSize: 13, fontWeight: '700', color: colors.primary },

    rewardInline: { fontSize: 13, color: '#F59E0B', fontWeight: '600' },

    sectionLabel: {
      fontSize: 14, fontWeight: '700', color: colors.textSecondary, textTransform: 'uppercase',
      letterSpacing: 0.5, marginBottom: 10, marginTop: 8,
    },

    loadingContainer: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 60 },
    emptyContainer: { alignItems: 'center', paddingTop: 60 },
    emptyText: { fontSize: 16, fontWeight: '600', color: colors.textSecondary, marginTop: 12 },
    emptySubtext: { fontSize: 14, color: colors.border, marginTop: 4 },
  });
}
