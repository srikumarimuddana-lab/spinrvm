import React, { useEffect, useMemo, useRef } from 'react';
import {
    View, Text, TouchableOpacity, StyleSheet, ActivityIndicator,
    BackHandler, Animated, Easing,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface IncentiveItem {
    name: string;
    bonus_amount: number;
    incentive_type: string;
}

interface QuestHint {
    title: string;
    current_value: number;
    target_value: number;
    progress_pct: number;
    reward_amount: number;
}

interface IncomingRide {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    fare: string | number;
    distance_km?: number;
    duration_minutes?: number;
    rider_name?: string;
    rider_rating?: number;
    requires_wav?: boolean;
    surge_multiplier?: number;
    incentives?: IncentiveItem[];
    total_bonus?: number;
    quest_hint?: QuestHint | null;
    payment_method?: string;
}

interface RideOfferPanelProps {
    incomingRide: IncomingRide | null;
    countdownSeconds: number;
    maxCountdown?: number;
    pickupDistanceKm?: number | null;
    isLoading: boolean;
    onAccept: () => void;
    onDecline: () => void;
}

const COUNTDOWN_GREEN = '#34C759';
const COUNTDOWN_AMBER = '#FF9F0A';
const COUNTDOWN_RED = '#FF453A';
const MONEY_GREEN = '#34C759';
const SURGE_ORANGE = '#FF9F0A';
const BONUS_GOLD = '#FFD700';

function getCountdownColor(seconds: number): string {
    if (seconds > 10) return COUNTDOWN_GREEN;
    if (seconds > 5) return COUNTDOWN_AMBER;
    return COUNTDOWN_RED;
}

export const RideOfferPanel: React.FC<RideOfferPanelProps> = ({
    incomingRide,
    countdownSeconds,
    maxCountdown = 15,
    pickupDistanceKm,
    isLoading,
    onAccept,
    onDecline,
}) => {
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const slideAnim = useRef(new Animated.Value(600)).current;
    const pulseAnim = useRef(new Animated.Value(1)).current;
    const glowAnim = useRef(new Animated.Value(0.3)).current;

    useEffect(() => {
        const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
        return () => sub.remove();
    }, []);

    useEffect(() => {
        if (incomingRide) {
            Animated.spring(slideAnim, {
                toValue: 0,
                tension: 65,
                friction: 11,
                useNativeDriver: true,
            }).start();

            Animated.loop(
                Animated.sequence([
                    Animated.timing(pulseAnim, { toValue: 1.04, duration: 800, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
                    Animated.timing(pulseAnim, { toValue: 1, duration: 800, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
                ])
            ).start();

            Animated.loop(
                Animated.sequence([
                    Animated.timing(glowAnim, { toValue: 0.6, duration: 1200, useNativeDriver: true }),
                    Animated.timing(glowAnim, { toValue: 0.3, duration: 1200, useNativeDriver: true }),
                ])
            ).start();
        }
    }, [incomingRide]);

    if (!incomingRide) return null;

    const fare = typeof incomingRide.fare === 'string'
        ? parseFloat(incomingRide.fare) || 0
        : (incomingRide.fare || 0);
    const fareStr = `$${fare.toFixed(2)}`;
    const progress = Math.max(0, Math.min(1, countdownSeconds / maxCountdown));
    const timerColor = getCountdownColor(countdownSeconds);

    const perKm = incomingRide.distance_km && incomingRide.distance_km > 0
        ? (fare / incomingRide.distance_km).toFixed(2) : null;
    const perHr = incomingRide.duration_minutes && incomingRide.duration_minutes > 0
        ? ((fare / incomingRide.duration_minutes) * 60).toFixed(0) : null;

    const hasSurge = (incomingRide.surge_multiplier ?? 0) > 1.0;
    const hasIncentives = (incomingRide.incentives?.length ?? 0) > 0;
    const totalBonus = incomingRide.total_bonus ?? 0;
    const totalEarnings = fare + totalBonus;
    const quest = incomingRide.quest_hint;

    const pickupStr = pickupDistanceKm != null
        ? (pickupDistanceKm < 1
            ? `${Math.round(pickupDistanceKm * 1000)} m`
            : `${pickupDistanceKm.toFixed(1)} km`)
        : null;

    const panelLabel = [
        incomingRide.requires_wav && 'Wheelchair-accessible vehicle required.',
        `New ride offer. Earnings: ${fareStr}`,
        pickupStr && `${pickupStr} to pickup`,
        incomingRide.pickup_address && `Pickup: ${incomingRide.pickup_address}`,
    ].filter(Boolean).join('. ');

    return (
        <View style={styles.overlay} accessibilityViewIsModal accessibilityLabel={panelLabel}>
            <View style={styles.dimBackground} />

            <Animated.View style={[styles.sheet, { transform: [{ translateY: slideAnim }] }]}>
                {/* ── Timer Bar ── */}
                <View style={styles.timerBarTrack}>
                    <Animated.View
                        style={[
                            styles.timerBarFill,
                            { width: `${progress * 100}%`, backgroundColor: timerColor },
                        ]}
                    />
                </View>

                {/* ── Header: Countdown + Title + Fare ── */}
                <View style={styles.header}>
                    <View
                        style={[styles.countdownRing, { borderColor: timerColor }]}
                        accessibilityLabel={`${countdownSeconds} seconds remaining`}
                        accessibilityLiveRegion="polite"
                        accessible
                    >
                        <Text style={[styles.countdownNum, { color: timerColor }]} allowFontScaling={false}>
                            {countdownSeconds}
                        </Text>
                    </View>

                    <View style={styles.headerCenter}>
                        <Text style={styles.headerTitle}>New Ride Request</Text>
                        <View style={styles.headerMeta}>
                            {incomingRide.requires_wav && (
                                <View style={styles.wavChip}>
                                    <Ionicons name="accessibility" size={11} color="#fff" />
                                    <Text style={styles.wavChipText}>WAV</Text>
                                </View>
                            )}
                            {hasSurge && (
                                <View style={styles.surgeChip}>
                                    <Ionicons name="flame" size={11} color="#fff" />
                                    <Text style={styles.surgeChipText}>
                                        {incomingRide.surge_multiplier!.toFixed(1)}×
                                    </Text>
                                </View>
                            )}
                            {incomingRide.payment_method && (
                                <View style={[styles.metaChip, { backgroundColor: colors.surfaceLight }]}>
                                    <Ionicons
                                        name={incomingRide.payment_method === 'card' ? 'card-outline' : 'wallet-outline'}
                                        size={11}
                                        color={colors.textDim}
                                    />
                                    <Text style={[styles.metaChipText, { color: colors.textDim }]}>
                                        {incomingRide.payment_method === 'card' ? 'Card' : 'Wallet'}
                                    </Text>
                                </View>
                            )}
                        </View>
                    </View>

                    <View style={styles.headerFare}>
                        <Text style={styles.fareLabel}>EARN</Text>
                        <Text style={[styles.fareHero, { color: MONEY_GREEN }]} allowFontScaling={false}>
                            {fareStr}
                        </Text>
                    </View>
                </View>

                {/* ── Commission + Efficiency Row ── */}
                <View style={styles.valueRow}>
                    <View style={[styles.valuePill, { backgroundColor: MONEY_GREEN + '15', borderColor: MONEY_GREEN + '30' }]}>
                        <Ionicons name="checkmark-circle" size={13} color={MONEY_GREEN} />
                        <Text style={[styles.valuePillText, { color: MONEY_GREEN }]}>You keep 100%</Text>
                    </View>
                    {perKm && (
                        <View style={[styles.valuePill, { backgroundColor: colors.surfaceLight, borderColor: colors.border }]}>
                            <Text style={[styles.valuePillText, { color: colors.text }]}>${perKm}/km</Text>
                        </View>
                    )}
                    {perHr && (
                        <View style={[styles.valuePill, { backgroundColor: colors.surfaceLight, borderColor: colors.border }]}>
                            <Text style={[styles.valuePillText, { color: colors.text }]}>~${perHr}/hr</Text>
                        </View>
                    )}
                </View>

                {/* ── Incentive Badges ── */}
                {hasIncentives && (
                    <View style={styles.incentiveRow}>
                        {incomingRide.incentives!.map((inc, i) => (
                            <View key={i} style={styles.incentiveBadge}>
                                <Ionicons name="gift" size={13} color={BONUS_GOLD} />
                                <Text style={styles.incentiveName}>{inc.name}</Text>
                                <Text style={styles.incentiveAmount}>+${inc.bonus_amount.toFixed(0)}</Text>
                            </View>
                        ))}
                        {totalBonus > 0 && (
                            <Text style={styles.totalEarningsHint}>
                                Total with bonuses: ${totalEarnings.toFixed(2)}
                            </Text>
                        )}
                    </View>
                )}

                {/* ── Route Card ── */}
                <View style={styles.routeCard}>
                    <View style={styles.routeIcons}>
                        <View style={[styles.routeDot, { backgroundColor: MONEY_GREEN }]} />
                        <View style={styles.routeLine} />
                        <View style={[styles.routeDot, { backgroundColor: colors.error }]} />
                    </View>
                    <View style={styles.routeAddresses}>
                        <View style={styles.routeRow}>
                            <Text style={styles.routeLabel}>PICKUP</Text>
                            <Text style={styles.routeAddress} numberOfLines={1}>
                                {incomingRide.pickup_address || 'Pickup location'}
                            </Text>
                            {pickupStr && (
                                <Text style={styles.pickupDistance}>{pickupStr} away</Text>
                            )}
                        </View>
                        <View style={styles.routeDivider} />
                        <View style={styles.routeRow}>
                            <Text style={styles.routeLabel}>DROP-OFF</Text>
                            <Text style={styles.routeAddress} numberOfLines={1}>
                                {incomingRide.dropoff_address || 'Drop-off location'}
                            </Text>
                        </View>
                    </View>
                </View>

                {/* ── Trip Stats + Rider Row ── */}
                <View style={styles.infoRow}>
                    {incomingRide.distance_km != null && (
                        <View style={styles.infoPill}>
                            <Ionicons name="navigate-outline" size={14} color={colors.primary} />
                            <Text style={styles.infoPillText}>{incomingRide.distance_km.toFixed(1)} km</Text>
                        </View>
                    )}
                    {incomingRide.duration_minutes != null && (
                        <View style={styles.infoPill}>
                            <Ionicons name="time-outline" size={14} color={colors.primary} />
                            <Text style={styles.infoPillText}>{Math.round(incomingRide.duration_minutes)} min</Text>
                        </View>
                    )}
                    {incomingRide.rider_name && (
                        <View style={styles.infoPill}>
                            <Ionicons name="person-outline" size={14} color={colors.primary} />
                            <Text style={styles.infoPillText}>{incomingRide.rider_name}</Text>
                            {incomingRide.rider_rating != null && incomingRide.rider_rating > 0 && (
                                <>
                                    <Ionicons name="star" size={11} color={BONUS_GOLD} />
                                    <Text style={styles.infoPillText}>
                                        {incomingRide.rider_rating.toFixed(1)}
                                    </Text>
                                </>
                            )}
                        </View>
                    )}
                </View>

                {/* ── Quest Progress ── */}
                {quest && quest.target_value > 0 && (
                    <View style={styles.questCard}>
                        <View style={styles.questHeader}>
                            <Ionicons name="trophy" size={15} color={BONUS_GOLD} />
                            <Text style={styles.questTitle} numberOfLines={1}>
                                {quest.title}
                            </Text>
                            <Text style={styles.questReward}>${quest.reward_amount.toFixed(0)} bonus</Text>
                        </View>
                        <View style={styles.questBarTrack}>
                            <View
                                style={[
                                    styles.questBarFill,
                                    { width: `${Math.min(quest.progress_pct, 100)}%` },
                                ]}
                            />
                        </View>
                        <Text style={styles.questProgress}>
                            {Math.floor(quest.current_value)}/{Math.floor(quest.target_value)} —{' '}
                            {quest.progress_pct >= 100 ? 'Ready to claim!' : `${Math.round(quest.progress_pct)}% done`}
                        </Text>
                    </View>
                )}

                {/* ── Action Buttons ── */}
                <View style={styles.actions}>
                    <TouchableOpacity
                        style={styles.declineBtn}
                        onPress={onDecline}
                        activeOpacity={0.7}
                        accessibilityRole="button"
                        accessibilityLabel="Decline ride offer"
                    >
                        <Ionicons name="close-circle" size={22} color={colors.error} />
                        <Text style={[styles.declineText, { color: colors.error }]}>Decline</Text>
                    </TouchableOpacity>

                    <Animated.View style={[styles.acceptBtnWrap, { transform: [{ scale: pulseAnim }] }]}>
                        <TouchableOpacity
                            style={styles.acceptBtn}
                            onPress={onAccept}
                            disabled={isLoading}
                            activeOpacity={0.85}
                            accessibilityRole="button"
                            accessibilityLabel={`Accept ride for ${fareStr}`}
                            accessibilityState={{ disabled: isLoading, busy: isLoading }}
                        >
                            <LinearGradient
                                colors={[colors.primary, colors.primaryDark]}
                                start={{ x: 0, y: 0 }}
                                end={{ x: 1, y: 0 }}
                                style={styles.acceptGradient}
                            >
                                {isLoading ? (
                                    <ActivityIndicator color="#fff" />
                                ) : (
                                    <>
                                        <Ionicons name="checkmark-circle" size={22} color="#fff" />
                                        <Text style={styles.acceptText}>Accept</Text>
                                        <Text style={styles.acceptFare}>{fareStr}</Text>
                                    </>
                                )}
                            </LinearGradient>
                        </TouchableOpacity>
                    </Animated.View>
                </View>
            </Animated.View>
        </View>
    );
};

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        overlay: {
            position: 'absolute',
            top: 0, left: 0, right: 0, bottom: 0,
            zIndex: 20,
            justifyContent: 'flex-end',
        },
        dimBackground: {
            ...StyleSheet.absoluteFillObject as any,
            backgroundColor: 'rgba(0,0,0,0.35)',
        },
        sheet: {
            backgroundColor: colors.surface,
            borderTopLeftRadius: 28,
            borderTopRightRadius: 28,
            paddingBottom: 34,
            shadowColor: '#000',
            shadowOffset: { width: 0, height: -8 },
            shadowOpacity: 0.15,
            shadowRadius: 24,
            elevation: 24,
        },

        // Timer bar
        timerBarTrack: {
            height: 5,
            backgroundColor: colors.surfaceLight,
            borderTopLeftRadius: 28,
            borderTopRightRadius: 28,
            overflow: 'hidden',
        },
        timerBarFill: {
            height: '100%',
            borderRadius: 5,
        },

        // Header
        header: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 20,
            paddingTop: 18,
            paddingBottom: 12,
        },
        countdownRing: {
            width: 52,
            height: 52,
            borderRadius: 26,
            borderWidth: 3,
            justifyContent: 'center',
            alignItems: 'center',
            backgroundColor: colors.surfaceLight,
        },
        countdownNum: {
            fontSize: 22,
            fontWeight: '800',
        },
        headerCenter: {
            flex: 1,
            marginLeft: 14,
        },
        headerTitle: {
            fontSize: 17,
            fontWeight: '700',
            color: colors.text,
        },
        headerMeta: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            marginTop: 4,
        },
        wavChip: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 3,
            backgroundColor: '#3B82F6',
            paddingHorizontal: 7,
            paddingVertical: 2,
            borderRadius: 6,
        },
        wavChipText: {
            fontSize: 10,
            fontWeight: '700',
            color: '#fff',
        },
        surgeChip: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 3,
            backgroundColor: SURGE_ORANGE,
            paddingHorizontal: 7,
            paddingVertical: 2,
            borderRadius: 6,
        },
        surgeChipText: {
            fontSize: 10,
            fontWeight: '700',
            color: '#fff',
        },
        metaChip: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 3,
            paddingHorizontal: 7,
            paddingVertical: 2,
            borderRadius: 6,
        },
        metaChipText: {
            fontSize: 10,
            fontWeight: '600',
        },
        headerFare: {
            alignItems: 'flex-end',
        },
        fareLabel: {
            fontSize: 10,
            fontWeight: '600',
            color: colors.textDim,
            letterSpacing: 0.5,
        },
        fareHero: {
            fontSize: 32,
            fontWeight: '800',
            letterSpacing: -1,
        },

        // Value row (commission + efficiency)
        valueRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 6,
            paddingHorizontal: 20,
            marginBottom: 12,
        },
        valuePill: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 4,
            paddingHorizontal: 10,
            paddingVertical: 5,
            borderRadius: 10,
            borderWidth: 1,
        },
        valuePillText: {
            fontSize: 12,
            fontWeight: '600',
        },

        // Incentives
        incentiveRow: {
            paddingHorizontal: 20,
            marginBottom: 12,
        },
        incentiveBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            backgroundColor: BONUS_GOLD + '12',
            borderWidth: 1,
            borderColor: BONUS_GOLD + '30',
            borderRadius: 10,
            paddingHorizontal: 10,
            paddingVertical: 6,
            marginBottom: 4,
        },
        incentiveName: {
            fontSize: 12,
            fontWeight: '500',
            color: colors.text,
            flex: 1,
        },
        incentiveAmount: {
            fontSize: 13,
            fontWeight: '700',
            color: BONUS_GOLD,
        },
        totalEarningsHint: {
            fontSize: 11,
            fontWeight: '600',
            color: MONEY_GREEN,
            marginTop: 4,
        },

        // Route card
        routeCard: {
            flexDirection: 'row',
            marginHorizontal: 20,
            backgroundColor: colors.surfaceLight,
            borderRadius: 16,
            padding: 14,
            marginBottom: 10,
        },
        routeIcons: {
            alignItems: 'center',
            width: 20,
            paddingTop: 4,
        },
        routeDot: {
            width: 10,
            height: 10,
            borderRadius: 5,
        },
        routeLine: {
            width: 2,
            flex: 1,
            backgroundColor: colors.border,
            marginVertical: 4,
        },
        routeAddresses: {
            flex: 1,
            marginLeft: 12,
        },
        routeRow: {
            paddingVertical: 2,
        },
        routeLabel: {
            fontSize: 9,
            fontWeight: '700',
            color: colors.textDim,
            letterSpacing: 0.8,
            marginBottom: 1,
        },
        routeAddress: {
            fontSize: 14,
            fontWeight: '500',
            color: colors.text,
        },
        pickupDistance: {
            fontSize: 11,
            fontWeight: '600',
            color: colors.primary,
            marginTop: 2,
        },
        routeDivider: {
            height: 1,
            backgroundColor: colors.border,
            marginVertical: 8,
        },

        // Info pills (trip stats + rider)
        infoRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 8,
            paddingHorizontal: 20,
            marginBottom: 10,
        },
        infoPill: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 5,
            backgroundColor: colors.primary + '10',
            paddingHorizontal: 10,
            paddingVertical: 6,
            borderRadius: 10,
            borderWidth: 1,
            borderColor: colors.primary + '20',
        },
        infoPillText: {
            fontSize: 12,
            fontWeight: '600',
            color: colors.text,
        },

        // Quest card
        questCard: {
            marginHorizontal: 20,
            marginBottom: 14,
            backgroundColor: BONUS_GOLD + '10',
            borderWidth: 1,
            borderColor: BONUS_GOLD + '25',
            borderRadius: 12,
            padding: 12,
        },
        questHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            marginBottom: 8,
        },
        questTitle: {
            fontSize: 12,
            fontWeight: '600',
            color: colors.text,
            flex: 1,
        },
        questReward: {
            fontSize: 12,
            fontWeight: '700',
            color: BONUS_GOLD,
        },
        questBarTrack: {
            height: 6,
            backgroundColor: colors.surfaceLight,
            borderRadius: 3,
            overflow: 'hidden',
            marginBottom: 4,
        },
        questBarFill: {
            height: '100%',
            backgroundColor: BONUS_GOLD,
            borderRadius: 3,
        },
        questProgress: {
            fontSize: 11,
            fontWeight: '500',
            color: colors.textDim,
        },

        // Actions
        actions: {
            flexDirection: 'row',
            gap: 12,
            paddingHorizontal: 20,
        },
        declineBtn: {
            flex: 1,
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: colors.error + '10',
            borderRadius: 16,
            paddingVertical: 16,
            gap: 6,
            borderWidth: 1,
            borderColor: colors.error + '25',
        },
        declineText: {
            fontSize: 15,
            fontWeight: '600',
        },
        acceptBtnWrap: {
            flex: 2,
        },
        acceptBtn: {
            borderRadius: 16,
            overflow: 'hidden',
            shadowColor: colors.primary,
            shadowOffset: { width: 0, height: 4 },
            shadowOpacity: 0.35,
            shadowRadius: 10,
            elevation: 8,
        },
        acceptGradient: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            paddingVertical: 16,
            gap: 8,
        },
        acceptText: {
            fontSize: 16,
            fontWeight: '700',
            color: '#fff',
        },
        acceptFare: {
            fontSize: 16,
            fontWeight: '800',
            color: 'rgba(255,255,255,0.9)',
        },
    });
}

export default RideOfferPanel;
