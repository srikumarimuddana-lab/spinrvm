import React, { useEffect, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Alert,
} from 'react-native';
import SafeRefreshControl from '../../components/SafeRefreshControl';
import { LinearGradient } from 'expo-linear-gradient';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useQuestStore, MyQuestProgress } from '../../store/questStore';
import { getApiErrorMessage } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

/**
 * Quests & Bonuses.
 *
 * HARD LAYOUT RULES (regressions blanked this screen on-device before):
 *  1. The LinearGradient hero is a FIXED element ABOVE the ScrollView — never
 *     nested inside the scroll content (that blanked the whole screen).
 *  2. The ScrollView content container uses plain padding — NEVER `flexGrow: 1`
 *     (that collapsed the card list while the hero/counts still showed).
 *  3. Card spacing is `marginBottom`, never `gap`.
 * These render fine in tests but the failures were on-device only, so keep them.
 */

const QUEST_META: Record<string, { icon: string; label: string; tone: keyof ThemeColors }> = {
  ride_count: { icon: 'car', label: 'Complete rides', tone: 'primary' },
  rides_completed: { icon: 'car', label: 'Complete rides', tone: 'primary' },
  earnings_target: { icon: 'cash', label: 'Earnings target', tone: 'success' },
  online_hours: { icon: 'time', label: 'Online hours', tone: 'info' },
  peak_rides: { icon: 'flash', label: 'Peak-hour rides', tone: 'orange' },
  consecutive_days: { icon: 'calendar', label: 'Consecutive days', tone: 'warning' },
  rating_maintained: { icon: 'star', label: 'Maintain rating', tone: 'gold' },
};
const metaFor = (t?: string): { icon: string; label: string; tone: keyof ThemeColors } => {
  if (t && QUEST_META[t]) return QUEST_META[t];
  const label = (t || 'Bonus challenge').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return { icon: 'trophy', label, tone: 'primary' };
};

const num = (v: unknown, d = 0): number => {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? ''));
  return Number.isFinite(n) ? n : d;
};
const money = (v: unknown): string => {
  const n = num(v);
  return n % 1 === 0 ? `$${n}` : `$${n.toFixed(2)}`;
};
const pctOf = (cur: number, tgt: number): number =>
  tgt > 0 ? Math.max(0, Math.min(100, Math.round((cur / tgt) * 100))) : 0;
const fmtTarget = (t: string | undefined, tgt: number): string => {
  switch (t) {
    case 'earnings_target': return `$${tgt} earned`;
    case 'online_hours': return `${tgt} hours online`;
    case 'consecutive_days': return `${tgt} days in a row`;
    case 'rating_maintained': return `${tgt}★ rating`;
    case 'peak_rides': return `${tgt} peak rides`;
    default: return `${tgt} rides`;
  }
};
const timeLeft = (end?: string): { text: string; urgent: boolean; expired: boolean } => {
  if (!end) return { text: 'No deadline', urgent: false, expired: false };
  const t = new Date(end).getTime();
  if (!Number.isFinite(t)) return { text: 'No deadline', urgent: false, expired: false };
  const diff = t - Date.now();
  if (diff <= 0) return { text: 'Expired', urgent: false, expired: true };
  const d = Math.floor(diff / 86_400_000), h = Math.floor((diff % 86_400_000) / 3_600_000);
  if (d > 0) return { text: `${d}d ${h}h left`, urgent: d < 1, expired: false };
  return { text: `${h}h left`, urgent: true, expired: false };
};

export default function QuestsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const {
    availableQuests, myQuests, isLoadingAvailable, isLoadingMine, error,
    fetchAvailableQuests, fetchMyQuests, joinQuest, claimReward,
  } = useQuestStore();

  const [tab, setTab] = useState<'available' | 'active'>('available');
  const [busy, setBusy] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    fetchAvailableQuests();
    fetchMyQuests();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    try { await Promise.all([fetchAvailableQuests(), fetchMyQuests()]); }
    finally { setRefreshing(false); }
  };
  const handleJoin = async (id: string) => {
    setBusy(id);
    try { await joinQuest(id); setTab('active'); }
    catch (e: any) { Alert.alert('Could not join', getApiErrorMessage(e, 'Could not join the quest. Please try again.')); }
    finally { setBusy(null); }
  };
  const handleClaim = async (pid: string, reward: string) => {
    setBusy(pid);
    try {
      const r = await claimReward(pid);
      Alert.alert('Reward claimed! 🎉', `${money(r?.reward_amount ?? reward)} added to your wallet.`);
    } catch (e: any) {
      Alert.alert('Could not claim', getApiErrorMessage(e, 'Could not claim your reward. Please try again.'));
    } finally { setBusy(null); }
  };

  const summary = useMemo(() => {
    let active = 0, claim = 0, earned = 0;
    for (const q of myQuests) {
      if (!q) continue;
      if (q.status === 'completed') { active++; claim++; }
      else if (q.status === 'claimed') earned += num(q.quest?.reward_amount);
      else if (q.status !== 'expired') active++;
    }
    return { active, claim, earned };
  }, [myQuests]);

  return (
    <View style={styles.container}>
      {/* Fixed gradient hero (NEVER inside the ScrollView) */}
      <LinearGradient
        colors={[colors.primary, colors.primaryDark]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.hero, { paddingTop: insets.top + 10 }]}
      >
        <View style={styles.heroTop}>
          <TouchableOpacity style={styles.back} onPress={() => router.back()} activeOpacity={0.7}>
            <Ionicons name="arrow-back" size={22} color="#FFF" />
          </TouchableOpacity>
          <Text style={styles.heroTitle}>Quests & Bonuses</Text>
          <View style={{ width: 40 }} />
        </View>
        <View style={styles.statsRow}>
          <HeroStat value={String(summary.active)} label="Active" styles={styles} />
          <View style={styles.statSep} />
          <HeroStat value={String(summary.claim)} label="To claim" styles={styles} highlight={summary.claim > 0} />
          <View style={styles.statSep} />
          <HeroStat value={money(summary.earned)} label="Earned" styles={styles} />
        </View>
      </LinearGradient>

      {/* Floating segmented tabs */}
      <View style={styles.segment}>
        <Seg label={`Available${availableQuests.length ? ` · ${availableQuests.length}` : ''}`} active={tab === 'available'} onPress={() => setTab('available')} styles={styles} />
        <Seg label={`My Quests${myQuests.length ? ` · ${myQuests.length}` : ''}`} active={tab === 'active'} onPress={() => setTab('active')} styles={styles} />
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={<SafeRefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} colors={[colors.primary]} />}
      >
        {tab === 'available' ? (
          isLoadingAvailable && availableQuests.length === 0 ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 56 }} />
          ) : error && availableQuests.length === 0 ? (
            <Empty styles={styles} colors={colors} icon="cloud-offline-outline" title="Couldn’t load quests" sub="Pull down to retry." />
          ) : availableQuests.length === 0 ? (
            <Empty styles={styles} colors={colors} icon="trophy-outline" title="No quests right now" sub="New bonus challenges drop regularly — check back soon to start earning extra." />
          ) : (
            availableQuests.map((q, i) => {
              if (!q) return null;
              const meta = metaFor(q.type);
              const tone = colors[meta.tone] || colors.primary;
              const cur = num(q.current_value), tgt = num(q.target_value);
              const pct = pctOf(cur, tgt);
              const joined = q.status && q.status !== 'available';
              const rem = timeLeft(q.end_date);
              return (
                <View key={q.id || `a${i}`} style={styles.card}>
                  <View style={styles.cardTop}>
                    <View style={[styles.iconChip, { backgroundColor: tone + '1A' }]}>
                      <Ionicons name={meta.icon as any} size={22} color={tone} />
                    </View>
                    <View style={styles.titleWrap}>
                      <Text style={styles.cardTitle} numberOfLines={2}>{q.title || 'Quest'}</Text>
                      <Text style={styles.cardSub}>{meta.label}</Text>
                    </View>
                    <View style={styles.coin}>
                      <Text style={styles.coinAmt}>{money(q.reward_amount)}</Text>
                      <Text style={styles.coinLbl}>reward</Text>
                    </View>
                  </View>

                  {!!q.description && <Text style={styles.desc}>{q.description}</Text>}

                  {cur > 0 && (
                    <View style={styles.progWrap}>
                      <View style={styles.track}><View style={[styles.fill, { width: `${pct}%`, backgroundColor: tone }]} /></View>
                      <View style={styles.progRow}>
                        <Text style={styles.progVal}>{cur} / {tgt}</Text>
                        <Text style={[styles.progPct, { color: tone }]}>{pct}%</Text>
                      </View>
                    </View>
                  )}

                  <View style={styles.meta}>
                    <View style={styles.metaItem}>
                      <Ionicons name="flag-outline" size={14} color={colors.textSecondary} />
                      <Text style={styles.metaText}>{fmtTarget(q.type, tgt)}</Text>
                    </View>
                    <View style={styles.metaItem}>
                      <Ionicons name="time-outline" size={14} color={rem.urgent ? colors.warning : colors.textSecondary} />
                      <Text style={[styles.metaText, rem.urgent && { color: colors.warning, fontWeight: '700' }]}>{rem.text}</Text>
                    </View>
                  </View>

                  {joined ? (
                    <View style={[styles.pill, { backgroundColor: colors.successBg }]}>
                      <Ionicons name="checkmark-circle" size={16} color={colors.success} />
                      <Text style={[styles.pillText, { color: colors.success }]}>Joined — see My Quests</Text>
                    </View>
                  ) : (
                    <TouchableOpacity style={[styles.btn, rem.expired && styles.btnOff]} onPress={() => handleJoin(q.id)} disabled={busy === q.id || !!rem.expired} activeOpacity={0.85}>
                      {busy === q.id ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.btnText}>{rem.expired ? 'Quest ended' : 'Join Quest'}</Text>}
                    </TouchableOpacity>
                  )}
                </View>
              );
            })
          )
        ) : (
          isLoadingMine && myQuests.length === 0 ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 56 }} />
          ) : myQuests.length === 0 ? (
            <Empty styles={styles} colors={colors} icon="rocket-outline" title="No quests joined yet" sub="Join a quest from the Available tab to start tracking progress and earning bonuses." actionLabel="Browse quests" onAction={() => setTab('available')} />
          ) : (
            myQuests.map((m, i) => {
              if (!m) return null;
              const q = m.quest || ({} as MyQuestProgress['quest']);
              const meta = metaFor(q.type);
              const cur = num(m.current_value), tgt = num(q.target_value);
              const pct = tgt > 0 ? pctOf(cur, tgt) : pctOf(num(m.progress_pct), 100);
              const completed = m.status === 'completed', claimed = m.status === 'claimed', expired = m.status === 'expired';
              const tone = claimed ? colors.success : completed ? colors.success : expired ? colors.textSecondary : colors.primary;
              const rem = timeLeft(q.end_date);
              return (
                <View key={m.progress_id || `m${i}`} style={[styles.card, completed && { borderColor: colors.success, borderWidth: 1.5 }]}>
                  <View style={styles.cardTop}>
                    <View style={[styles.iconChip, { backgroundColor: tone + '1A' }]}>
                      <Ionicons name={(claimed ? 'trophy' : meta.icon) as any} size={22} color={tone} />
                    </View>
                    <View style={styles.titleWrap}>
                      <Text style={styles.cardTitle} numberOfLines={2}>{q.title || 'Quest'}</Text>
                      <Text style={styles.cardSub}>{meta.label}</Text>
                    </View>
                    <View style={[styles.statusBadge, { backgroundColor: tone + '1A' }]}>
                      <Text style={[styles.statusText, { color: tone }]}>{claimed ? 'Claimed' : completed ? 'Ready' : expired ? 'Expired' : 'Active'}</Text>
                    </View>
                  </View>

                  <View style={styles.progWrap}>
                    <View style={styles.track}><View style={[styles.fill, { width: `${pct}%`, backgroundColor: tone }]} /></View>
                    <View style={styles.progRow}>
                      <Text style={styles.progVal}>{cur} / {tgt}</Text>
                      <Text style={[styles.progPct, { color: tone }]}>{pct}%</Text>
                    </View>
                  </View>

                  <View style={styles.meta}>
                    <View style={styles.metaItem}>
                      <Ionicons name="gift-outline" size={14} color={colors.orange} />
                      <Text style={[styles.metaText, { color: colors.orange, fontWeight: '700' }]}>{money(q.reward_amount)} {q.reward_type === 'cash' ? 'cash' : 'wallet credit'}</Text>
                    </View>
                    {!claimed && !expired && (
                      <View style={styles.metaItem}>
                        <Ionicons name="time-outline" size={14} color={rem.urgent ? colors.warning : colors.textSecondary} />
                        <Text style={[styles.metaText, rem.urgent && { color: colors.warning, fontWeight: '700' }]}>{rem.text}</Text>
                      </View>
                    )}
                  </View>

                  {completed && (
                    <TouchableOpacity style={[styles.btn, { backgroundColor: colors.success }]} onPress={() => handleClaim(m.progress_id, money(q.reward_amount))} disabled={busy === m.progress_id} activeOpacity={0.85}>
                      {busy === m.progress_id ? <ActivityIndicator color="#fff" size="small" /> : (
                        <View style={styles.btnRow}>
                          <Ionicons name="gift" size={18} color="#fff" />
                          <Text style={styles.btnText}>Claim {money(q.reward_amount)} reward</Text>
                        </View>
                      )}
                    </TouchableOpacity>
                  )}
                  {claimed && (
                    <View style={[styles.pill, { backgroundColor: colors.successBg }]}>
                      <Ionicons name="checkmark-done-circle" size={16} color={colors.success} />
                      <Text style={[styles.pillText, { color: colors.success }]}>Reward added to your wallet</Text>
                    </View>
                  )}
                </View>
              );
            })
          )
        )}
      </ScrollView>
    </View>
  );
}

function HeroStat({ value, label, styles, highlight }: { value: string; label: string; styles: any; highlight?: boolean }) {
  return (
    <View style={styles.stat}>
      <Text style={[styles.statValue, highlight && styles.statValueHi]}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}
function Seg({ label, active, onPress, styles }: { label: string; active: boolean; onPress: () => void; styles: any }) {
  return (
    <TouchableOpacity style={[styles.seg, active && styles.segActive]} onPress={onPress} activeOpacity={0.8}>
      <Text style={[styles.segText, active && styles.segTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}
function Empty({ styles, colors, icon, title, sub, actionLabel, onAction }: {
  styles: any; colors: ThemeColors; icon: string; title: string; sub: string; actionLabel?: string; onAction?: () => void;
}) {
  return (
    <View style={styles.empty}>
      <View style={styles.emptyCircle}><Ionicons name={icon as any} size={38} color={colors.primary} /></View>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptySub}>{sub}</Text>
      {actionLabel && onAction && (
        <TouchableOpacity style={styles.emptyBtn} onPress={onAction} activeOpacity={0.85}><Text style={styles.emptyBtnText}>{actionLabel}</Text></TouchableOpacity>
      )}
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surfaceLight },

    hero: { paddingHorizontal: 16, paddingBottom: 22, borderBottomLeftRadius: 26, borderBottomRightRadius: 26 },
    heroTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 },
    back: { width: 40, height: 40, borderRadius: 20, backgroundColor: 'rgba(255,255,255,0.18)', alignItems: 'center', justifyContent: 'center' },
    heroTitle: { fontSize: 18, fontWeight: '800', color: '#FFF' },
    statsRow: { flexDirection: 'row', alignItems: 'center' },
    statSep: { width: 1, height: 30, backgroundColor: 'rgba(255,255,255,0.25)' },
    stat: { flex: 1, alignItems: 'center' },
    statValue: { fontSize: 21, fontWeight: '800', color: '#FFF' },
    statValueHi: { color: '#FFE08A' },
    statLabel: { fontSize: 12, color: 'rgba(255,255,255,0.85)', marginTop: 2, fontWeight: '600' },

    segment: { flexDirection: 'row', marginTop: 16, marginHorizontal: 16, backgroundColor: colors.surface, borderRadius: 14, padding: 4, borderWidth: 1, borderColor: colors.border, shadowColor: '#000', shadowOpacity: 0.05, shadowOffset: { width: 0, height: 2 }, shadowRadius: 6, elevation: 2 },
    seg: { flex: 1, paddingVertical: 10, borderRadius: 11, alignItems: 'center' },
    segActive: { backgroundColor: colors.primary },
    segText: { fontSize: 14, fontWeight: '700', color: colors.textDim },
    segTextActive: { color: '#FFF' },

    scroll: { flex: 1 },
    scrollContent: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 44 },

    empty: { alignItems: 'center', marginTop: 48, paddingHorizontal: 24 },
    emptyCircle: { width: 82, height: 82, borderRadius: 41, backgroundColor: colors.primary + '15', alignItems: 'center', justifyContent: 'center', marginBottom: 16 },
    emptyTitle: { fontSize: 18, fontWeight: '800', color: colors.text, textAlign: 'center' },
    emptySub: { fontSize: 14, color: colors.textDim, textAlign: 'center', marginTop: 6, lineHeight: 21 },
    emptyBtn: { marginTop: 18, backgroundColor: colors.primary, borderRadius: 13, paddingVertical: 12, paddingHorizontal: 24 },
    emptyBtnText: { color: '#FFF', fontSize: 15, fontWeight: '800' },

    card: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, marginBottom: 14, borderWidth: 1, borderColor: colors.border, shadowColor: '#000', shadowOpacity: 0.05, shadowOffset: { width: 0, height: 2 }, shadowRadius: 8, elevation: 2 },
    cardTop: { flexDirection: 'row', alignItems: 'center' },
    iconChip: { width: 46, height: 46, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginRight: 12 },
    titleWrap: { flex: 1 },
    cardTitle: { fontSize: 16, fontWeight: '800', color: colors.text },
    cardSub: { fontSize: 13, color: colors.textSecondary, marginTop: 2 },
    coin: { alignItems: 'center', backgroundColor: colors.orange + '18', borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, marginLeft: 8 },
    coinAmt: { fontSize: 16, fontWeight: '900', color: colors.orange },
    coinLbl: { fontSize: 10, fontWeight: '700', color: colors.orange, marginTop: 1, textTransform: 'uppercase', letterSpacing: 0.4 },

    desc: { fontSize: 14, color: colors.textDim, lineHeight: 20, marginTop: 12 },

    progWrap: { marginTop: 14 },
    track: { height: 9, backgroundColor: colors.surfaceLight, borderRadius: 5, overflow: 'hidden', borderWidth: 1, borderColor: colors.border },
    fill: { height: '100%', borderRadius: 5 },
    progRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
    progVal: { fontSize: 13, fontWeight: '700', color: colors.text },
    progPct: { fontSize: 13, fontWeight: '800' },

    meta: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, flexWrap: 'wrap' },
    metaItem: { flexDirection: 'row', alignItems: 'center', marginRight: 12 },
    metaText: { fontSize: 13, color: colors.textSecondary, fontWeight: '500', marginLeft: 5 },

    btn: { backgroundColor: colors.primary, borderRadius: 13, paddingVertical: 13, alignItems: 'center', justifyContent: 'center', marginTop: 16 },
    btnOff: { backgroundColor: colors.textSecondary, opacity: 0.6 },
    btnText: { color: '#FFF', fontSize: 15, fontWeight: '800' },
    btnRow: { flexDirection: 'row', alignItems: 'center' },

    pill: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', marginTop: 16, paddingVertical: 11, borderRadius: 12 },
    pillText: { fontSize: 13, fontWeight: '700', marginLeft: 6 },

    statusBadge: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 10, marginLeft: 8 },
    statusText: { fontSize: 11, fontWeight: '800' },
  });
}
