import React, { useEffect, useMemo, useRef } from 'react';
import {
    View, Text, TouchableOpacity, StyleSheet, ActivityIndicator,
    BackHandler, Animated, Easing, Dimensions, Platform, Vibration
} from 'react-native';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAlertPrefsStore } from '../../store/alertPrefsStore';
import { showAlert } from '../AlertDialog';

// Machine-readable code for the one flaggable decline reason surfaced here.
// Trust & safety queries backend audit_logs / logs for this exact string —
// keep it in sync with backend/routes/drivers/ride_flow.py.
export const DECLINE_REASON_SERVICE_ANIMAL = 'service_animal';

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
    rider_profile_image?: string;
    requires_wav?: boolean;
    quiet_mode?: boolean;
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
    /** Optional reason (e.g. DECLINE_REASON_SERVICE_ANIMAL) from the long-press flag flow. */
    onDecline: (reason?: string) => void;
}

const ACCENT = '#10B981';
const ACCENT_DARK = '#059669';
const SURGE_ORANGE = '#F97316';
const GOLD = '#F59E0B';
const QUEST_PURPLE = '#8B5CF6';

export const RideOfferPanel: React.FC<RideOfferPanelProps> = ({
    incomingRide,
    countdownSeconds,
    maxCountdown = 15,
    pickupDistanceKm,
    isLoading,
    onAccept,
    onDecline,
}) => {
    const { colors, isDark } = useTheme();
    const styles = useMemo(() => createStyles(colors, isDark), [colors, isDark]);
    const screenHeight = Dimensions.get('window').height;

    const slideAnim = useRef(new Animated.Value(screenHeight)).current;
    const progressAnim = useRef(new Animated.Value(1)).current;

    useEffect(() => {
        const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
        return () => sub.remove();
    }, []);

    useEffect(() => {
        if (incomingRide) {
            if (Platform.OS !== 'web' && useAlertPrefsStore.getState().vibration) {
                Vibration.vibrate([0, 80, 60, 80]);
            }
            Animated.spring(slideAnim, {
                toValue: 0,
                tension: 65,
                friction: 12,
                useNativeDriver: true,
            }).start();
        }
    }, [incomingRide]);

    useEffect(() => {
        const fraction = Math.max(0, Math.min(1, countdownSeconds / maxCountdown));
        Animated.timing(progressAnim, {
            toValue: fraction,
            duration: 950,
            easing: Easing.linear,
            useNativeDriver: false,
        }).start();
    }, [countdownSeconds, maxCountdown]);

    if (!incomingRide) return null;

    const baseFare = typeof incomingRide.fare === 'string'
        ? parseFloat(incomingRide.fare) || 0
        : (incomingRide.fare || 0);
    const totalBonus = incomingRide.total_bonus ?? 0;
    const totalEarnings = baseFare + totalBonus;
    const hasSurge = (incomingRide.surge_multiplier ?? 0) > 1.0;
    const hasIncentives = (incomingRide.incentives?.length ?? 0) > 0;
    const hasBonus = totalBonus > 0;
    const quest = incomingRide.quest_hint;

    const vibrateForActionTap = () => {
        if (Platform.OS !== 'web' && useAlertPrefsStore.getState().vibration) {
            Vibration.vibrate(35);
        }
    };

    const handleDecline = (reason?: string) => {
        vibrateForActionTap();
        onDecline(reason);
    };

    // Long-press on Decline surfaces a single, optional flag for the one
    // decline reason trust & safety needs to be able to detect today: a
    // driver refusing to accommodate a service animal (CLAUDE.md
    // Accessibility: "mandatory; drivers cannot refuse"). The plain tap
    // (the fast, common path on a ~15s countdown) is completely unchanged —
    // this is additive only, reachable but not in the way.
    const handleDeclineLongPress = () => {
        showAlert(
            'Report a reason?',
            "If you're declining because you can't accommodate a service animal, let us know. Service animal accommodation is mandatory and refusals are reviewed.",
            [
                { text: 'Cancel', style: 'cancel' },
                {
                    text: 'Service animal — report & decline',
                    style: 'destructive',
                    onPress: () => handleDecline(DECLINE_REASON_SERVICE_ANIMAL),
                },
            ],
        );
    };

    const handleAccept = () => {
        vibrateForActionTap();
        onAccept();
    };

    const timerPct = countdownSeconds / maxCountdown;
    const timerColor = timerPct > 0.6 ? ACCENT : timerPct > 0.3 ? '#F59E0B' : '#EF4444';

    const pickupStr = pickupDistanceKm != null
        ? (pickupDistanceKm < 1
            ? `${Math.round(pickupDistanceKm * 1000)}m`
            : `${pickupDistanceKm.toFixed(1)}km`)
        : null;

    return (
        <View style={styles.overlay} accessibilityViewIsModal>
            <Animated.View
                style={[
                    styles.dim,
                    { opacity: slideAnim.interpolate({ inputRange: [0, screenHeight], outputRange: [1, 0] }) }
                ]}
            />

            <Animated.View style={[styles.container, { transform: [{ translateY: slideAnim }] }]}>
                <View style={styles.card}>

                    {/* Timer bar */}
                    <View style={styles.timerTrack}>
                        <Animated.View style={[
                            styles.timerFill,
                            {
                                backgroundColor: timerColor,
                                width: progressAnim.interpolate({
                                    inputRange: [0, 1],
                                    outputRange: ['0%', '100%']
                                })
                            }
                        ]} />
                    </View>

                    {/* Header: countdown + label */}
                    <View style={styles.header}>
                        <View style={styles.headerLeft}>
                            <View style={styles.liveIndicator}>
                                <View style={[styles.liveDot, { backgroundColor: ACCENT }]} />
                                <Text style={styles.liveText}>NEW RIDE</Text>
                            </View>
                            {incomingRide.rider_name ? (
                                <View style={styles.riderInfo}>
                                    {incomingRide.rider_profile_image ? (
                                        <Image
                                            source={{ uri: incomingRide.rider_profile_image }}
                                            style={styles.riderPhoto}
                                            contentFit="cover"
                                        />
                                    ) : (
                                        <View style={styles.riderAvatar}>
                                            <Text style={styles.riderInitial}>
                                                {incomingRide.rider_name.charAt(0).toUpperCase()}
                                            </Text>
                                        </View>
                                    )}
                                    <Text style={styles.riderName} numberOfLines={1}>{incomingRide.rider_name}</Text>
                                    {incomingRide.rider_rating ? (
                                        <>
                                            <Ionicons name="star" size={12} color={GOLD} />
                                            <Text style={styles.riderRating}>{incomingRide.rider_rating.toFixed(1)}</Text>
                                        </>
                                    ) : null}
                                </View>
                            ) : null}
                        </View>
                        <View style={[styles.timerCircle, { borderColor: timerColor }]}>
                            <Text style={[styles.timerText, { color: timerColor }]}>{countdownSeconds}</Text>
                            <Text style={[styles.timerUnit, { color: timerColor }]}>s</Text>
                        </View>
                    </View>

                    {/* Earnings hero + trip metrics side by side */}
                    <View style={styles.earningsSection}>
                        <Text style={styles.earningsLabel}>YOUR EARNINGS</Text>
                        <View style={styles.earningsHero}>
                            <View style={styles.earningsLeft}>
                                <View style={styles.earningsRow}>
                                    <Text style={styles.earningsDollar}>$</Text>
                                    <Text style={styles.earningsAmount} allowFontScaling={false}>
                                        {totalEarnings.toFixed(2)}
                                    </Text>
                                </View>
                                {hasBonus && (
                                    <Text style={styles.earningsBreakdown}>
                                        ${baseFare.toFixed(2)} fare + ${totalBonus.toFixed(2)} bonus
                                    </Text>
                                )}
                            </View>
                            <View style={styles.metricsStack}>
                                <View style={styles.metricItem}>
                                    <Text style={styles.metricValue}>{incomingRide.distance_km?.toFixed(1) || '--'}</Text>
                                    <Text style={styles.metricUnit}>km</Text>
                                </View>
                                <View style={styles.metricItemDivider} />
                                <View style={styles.metricItem}>
                                    <Text style={styles.metricValue}>{Math.round(incomingRide.duration_minutes || 0)}</Text>
                                    <Text style={styles.metricUnit}>min</Text>
                                </View>
                                {(incomingRide.distance_km ?? 0) > 0 && (
                                    <>
                                        <View style={styles.metricItemDivider} />
                                        <View style={styles.metricItem}>
                                            <Text style={[styles.metricValue, { color: hasBonus ? GOLD : ACCENT_DARK }]}>
                                                ${((baseFare + totalBonus) / incomingRide.distance_km!).toFixed(2)}
                                            </Text>
                                            <Text style={[styles.metricUnit, { color: hasBonus ? GOLD : ACCENT_DARK }]}>/km</Text>
                                        </View>
                                    </>
                                )}
                            </View>
                        </View>
                        <View style={styles.keepBadge}>
                            <Ionicons name="shield-checkmark" size={12} color={ACCENT} />
                            <Text style={styles.keepText}>100% yours — $0 commission</Text>
                        </View>
                    </View>

                    {/* Badges row: surge, wav, quiet, cash, payment */}
                    {(hasSurge || incomingRide.requires_wav || incomingRide.quiet_mode || incomingRide.payment_method === 'cash') && (
                        <View style={styles.badgesRow}>
                            {hasSurge && (
                                <View style={[styles.badge, { backgroundColor: SURGE_ORANGE + '20' }]}>
                                    <Ionicons name="flame" size={13} color={SURGE_ORANGE} />
                                    <Text style={[styles.badgeText, { color: SURGE_ORANGE }]}>
                                        {incomingRide.surge_multiplier!.toFixed(1)}x Surge
                                    </Text>
                                </View>
                            )}
                            {incomingRide.requires_wav && (
                                <View style={[styles.badge, { backgroundColor: '#3B82F620' }]}>
                                    <Ionicons name="accessibility" size={13} color="#3B82F6" />
                                    <Text style={[styles.badgeText, { color: '#3B82F6' }]}>WAV</Text>
                                </View>
                            )}
                            {incomingRide.quiet_mode && (
                                <View style={[styles.badge, { backgroundColor: '#8B5CF620' }]}>
                                    <Ionicons name="volume-mute" size={13} color="#8B5CF6" />
                                    <Text style={[styles.badgeText, { color: '#8B5CF6' }]}>Quiet ride</Text>
                                </View>
                            )}
                            {incomingRide.payment_method === 'cash' && (
                                <View style={[styles.badge, { backgroundColor: '#6B728020' }]}>
                                    <Ionicons name="cash" size={13} color={colors.textDim} />
                                    <Text style={[styles.badgeText, { color: colors.textDim }]}>Cash</Text>
                                </View>
                            )}
                        </View>
                    )}

                    {/* Incentives */}
                    {hasIncentives && (
                        <View style={styles.incentivesRow}>
                            {incomingRide.incentives!.map((inc, idx) => (
                                <View key={idx} style={styles.incentiveChip}>
                                    <Ionicons name="gift" size={12} color={GOLD} />
                                    <Text style={styles.incentiveText} numberOfLines={1}>
                                        {inc.name}
                                    </Text>
                                    <Text style={styles.incentiveAmount}>+${inc.bonus_amount.toFixed(2)}</Text>
                                </View>
                            ))}
                        </View>
                    )}

                    {/* Quest progress */}
                    {quest && quest.target_value > 0 && (
                        <View style={styles.questRow}>
                            <Ionicons name="trophy" size={14} color={QUEST_PURPLE} />
                            <Text style={styles.questText} numberOfLines={1}>
                                {quest.title} — {Math.floor(quest.current_value)}/{Math.floor(quest.target_value)}
                            </Text>
                            <Text style={styles.questReward}>${quest.reward_amount.toFixed(0)}</Text>
                        </View>
                    )}

                    {/* Route */}
                    <View style={styles.routeSection}>
                        <View style={styles.routeStop}>
                            <View style={[styles.routeDot, { backgroundColor: ACCENT }]} />
                            <View style={styles.routeContent}>
                                <View style={styles.routeLabelRow}>
                                    <Text style={styles.routeLabel}>PICKUP</Text>
                                    {pickupStr && (
                                        <Text style={styles.routeDistance}>{pickupStr} away</Text>
                                    )}
                                </View>
                                <Text style={styles.routeAddress} numberOfLines={1}>
                                    {incomingRide.pickup_address || 'Pickup location'}
                                </Text>
                            </View>
                        </View>
                        <View style={styles.routeLine} />
                        <View style={styles.routeStop}>
                            <View style={[styles.routeDot, { backgroundColor: '#EF4444' }]} />
                            <View style={styles.routeContent}>
                                <Text style={styles.routeLabel}>DROP-OFF</Text>
                                <Text style={styles.routeAddress} numberOfLines={1}>
                                    {incomingRide.dropoff_address || 'Drop-off location'}
                                </Text>
                            </View>
                        </View>
                    </View>


                    {/* Action buttons — Decline left, Accept right (reversed from typical so Accept is away from next screen's Cancel) */}
                    <View style={styles.actionBar}>
                        <TouchableOpacity
                            style={styles.declineBtn}
                            onPress={() => handleDecline()}
                            onLongPress={handleDeclineLongPress}
                            activeOpacity={0.7}
                            accessibilityLabel="Decline ride"
                            disabled={isLoading}
                        >
                            <Text style={styles.declineBtnText}>Decline</Text>
                        </TouchableOpacity>

                        <TouchableOpacity
                            style={styles.acceptBtn}
                            onPress={handleAccept}
                            disabled={isLoading}
                            activeOpacity={0.85}
                            accessibilityLabel="Accept ride"
                        >
                            <LinearGradient
                                colors={[ACCENT, ACCENT_DARK]}
                                style={styles.acceptGradient}
                                start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }}
                            >
                                {isLoading ? (
                                    <ActivityIndicator color="#fff" size="small" />
                                ) : (
                                    <>
                                        <Text style={styles.acceptBtnText}>Accept</Text>
                                        <Text style={styles.acceptBtnFare}>${totalEarnings.toFixed(2)}</Text>
                                    </>
                                )}
                            </LinearGradient>
                        </TouchableOpacity>
                    </View>
                </View>
            </Animated.View>
        </View>
    );
};

const { width: SCREEN_W } = Dimensions.get('window');

function createStyles(colors: ThemeColors, isDark: boolean) {
    const bg = isDark ? '#1C1C1E' : '#FFFFFF';
    const surfaceBg = isDark ? '#2C2C2E' : '#F5F5F7';
    const borderClr = isDark ? '#3A3A3C' : '#E5E5EA';

    return StyleSheet.create({
        overlay: {
            position: 'absolute',
            top: 0, left: 0, right: 0, bottom: 0,
            zIndex: 100,
            justifyContent: 'flex-end',
        },
        dim: {
            position: 'absolute',
            top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: 'rgba(0,0,0,0.55)',
        },
        container: {
            paddingHorizontal: 12,
            paddingBottom: Platform.OS === 'ios' ? 28 : 16,
        },
        card: {
            backgroundColor: bg,
            borderRadius: 24,
            overflow: 'hidden',
            shadowColor: '#000',
            shadowOffset: { width: 0, height: -4 },
            shadowOpacity: 0.2,
            shadowRadius: 16,
            elevation: 20,
        },

        // Timer
        timerTrack: {
            height: 3,
            backgroundColor: borderClr,
        },
        timerFill: {
            height: '100%',
        },

        // Header
        header: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingHorizontal: 20,
            paddingTop: 16,
            paddingBottom: 8,
        },
        headerLeft: {
            flex: 1,
            marginRight: 12,
        },
        liveIndicator: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            marginBottom: 6,
        },
        liveDot: {
            width: 6, height: 6,
            borderRadius: 3,
        },
        liveText: {
            fontSize: 11,
            fontWeight: '800',
            color: ACCENT,
            letterSpacing: 1,
        },
        riderInfo: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
        },
        riderPhoto: {
            width: 30, height: 30,
            borderRadius: 15,
        },
        riderAvatar: {
            width: 30, height: 30,
            borderRadius: 15,
            backgroundColor: colors.primary,
            justifyContent: 'center',
            alignItems: 'center',
        },
        riderInitial: {
            color: '#FFF',
            fontSize: 14,
            fontWeight: '700',
        },
        riderName: {
            color: colors.text,
            fontSize: 14,
            fontWeight: '600',
            maxWidth: 120,
        },
        riderRating: {
            color: colors.textDim,
            fontSize: 12,
            fontWeight: '600',
        },
        timerCircle: {
            width: 52, height: 52,
            borderRadius: 26,
            borderWidth: 3,
            justifyContent: 'center',
            alignItems: 'center',
            backgroundColor: surfaceBg,
        },
        timerText: {
            fontSize: 18,
            fontWeight: '900',
            lineHeight: 20,
        },
        timerUnit: {
            fontSize: 9,
            fontWeight: '700',
            marginTop: -2,
        },

        // Earnings
        earningsSection: {
            alignItems: 'center',
            paddingVertical: 12,
            paddingHorizontal: 20,
        },
        earningsLabel: {
            fontSize: 10,
            fontWeight: '800',
            color: colors.textDim,
            letterSpacing: 1.2,
            marginBottom: 4,
        },
        earningsHero: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 16,
        },
        earningsLeft: {
            alignItems: 'center',
        },
        earningsRow: {
            flexDirection: 'row',
            alignItems: 'flex-start',
        },
        earningsDollar: {
            fontSize: 24,
            fontWeight: '700',
            color: colors.text,
            marginTop: 8,
            marginRight: 1,
        },
        earningsAmount: {
            fontSize: 52,
            fontWeight: '900',
            color: colors.text,
            letterSpacing: -2,
            lineHeight: 56,
        },
        earningsBreakdown: {
            fontSize: 12,
            fontWeight: '600',
            color: ACCENT_DARK,
            marginTop: 2,
        },
        metricsStack: {
            backgroundColor: surfaceBg,
            borderRadius: 12,
            paddingHorizontal: 14,
            paddingVertical: 10,
            gap: 6,
        },
        metricItem: {
            flexDirection: 'row',
            alignItems: 'baseline',
            gap: 3,
        },
        metricItemDivider: {
            height: 1,
            backgroundColor: borderClr,
        },
        metricValue: {
            fontSize: 18,
            fontWeight: '800',
            color: colors.text,
        },
        metricUnit: {
            fontSize: 12,
            fontWeight: '600',
            color: colors.textDim,
        },
        keepBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 4,
            backgroundColor: ACCENT + '15',
            paddingHorizontal: 10,
            paddingVertical: 4,
            borderRadius: 8,
            marginTop: 8,
        },
        keepText: {
            fontSize: 11,
            fontWeight: '700',
            color: ACCENT_DARK,
        },

        // Badges
        badgesRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 6,
            paddingHorizontal: 20,
            marginBottom: 8,
            justifyContent: 'center',
        },
        badge: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 10,
            paddingVertical: 5,
            borderRadius: 8,
            gap: 4,
        },
        badgeText: {
            fontSize: 12,
            fontWeight: '700',
        },

        // Incentives
        incentivesRow: {
            paddingHorizontal: 20,
            marginBottom: 8,
            gap: 4,
        },
        incentiveChip: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: GOLD + '12',
            borderRadius: 8,
            paddingHorizontal: 10,
            paddingVertical: 6,
            gap: 6,
        },
        incentiveText: {
            flex: 1,
            fontSize: 12,
            fontWeight: '600',
            color: colors.text,
        },
        incentiveAmount: {
            fontSize: 13,
            fontWeight: '800',
            color: GOLD,
        },

        // Quest
        questRow: {
            flexDirection: 'row',
            alignItems: 'center',
            marginHorizontal: 20,
            marginBottom: 8,
            backgroundColor: QUEST_PURPLE + '12',
            borderRadius: 8,
            paddingHorizontal: 10,
            paddingVertical: 6,
            gap: 6,
        },
        questText: {
            flex: 1,
            fontSize: 12,
            fontWeight: '600',
            color: colors.text,
        },
        questReward: {
            fontSize: 13,
            fontWeight: '800',
            color: QUEST_PURPLE,
        },

        // Route
        routeSection: {
            marginHorizontal: 20,
            marginBottom: 10,
            backgroundColor: surfaceBg,
            borderRadius: 14,
            padding: 14,
        },
        routeStop: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            gap: 10,
        },
        routeDot: {
            width: 10, height: 10,
            borderRadius: 5,
            marginTop: 4,
        },
        routeLine: {
            width: 2,
            height: 8,
            backgroundColor: borderClr,
            marginLeft: 4,
            marginVertical: 2,
        },
        routeContent: {
            flex: 1,
        },
        routeLabelRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
        },
        routeLabel: {
            fontSize: 9,
            fontWeight: '800',
            color: colors.textDim,
            letterSpacing: 0.8,
        },
        routeDistance: {
            fontSize: 10,
            fontWeight: '700',
            color: ACCENT_DARK,
        },
        routeAddress: {
            fontSize: 14,
            fontWeight: '600',
            color: colors.text,
            marginTop: 1,
        },

        // Action buttons
        actionBar: {
            flexDirection: 'row',
            gap: 10,
            paddingHorizontal: 20,
            paddingTop: 4,
            paddingBottom: 20,
        },
        declineBtn: {
            flex: 1,
            height: 54,
            borderRadius: 14,
            backgroundColor: surfaceBg,
            justifyContent: 'center',
            alignItems: 'center',
            borderWidth: 1,
            borderColor: borderClr,
        },
        declineBtnText: {
            fontSize: 16,
            fontWeight: '700',
            color: colors.textDim,
        },
        acceptBtn: {
            flex: 1,
            height: 54,
            borderRadius: 14,
            overflow: 'hidden',
        },
        acceptGradient: {
            flex: 1,
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
        },
        acceptBtnText: {
            color: '#FFF',
            fontSize: 17,
            fontWeight: '800',
        },
        acceptBtnFare: {
            color: 'rgba(255,255,255,0.9)',
            fontSize: 15,
            fontWeight: '700',
        },
    });
}

export default RideOfferPanel;
