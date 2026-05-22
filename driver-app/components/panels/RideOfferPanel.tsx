import React, { useEffect, useMemo, useRef } from 'react';
import {
    View, Text, TouchableOpacity, StyleSheet, ActivityIndicator,
    BackHandler, Animated, Easing, Dimensions, ScrollView, Platform, Vibration
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

// Color palette — bold, premium, money-positive
const GOLD = '#FFD60A';
const GOLD_DARK = '#B8860B';
const MONEY_GREEN = '#00D26A';
const MONEY_GREEN_DARK = '#00A050';
const SURGE_ORANGE = '#FF6B00';
const SURGE_RED = '#FF3B30';
const QUEST_PURPLE = '#A855F7';
const QUEST_PURPLE_DARK = '#7C3AED';
const COUNTDOWN_GREEN = '#34C759';
const COUNTDOWN_AMBER = '#FF9500';
const COUNTDOWN_RED = '#FF453A';

function getCountdownColor(seconds: number): string {
    if (seconds > 10) return COUNTDOWN_GREEN;
    if (seconds > 5) return COUNTDOWN_AMBER;
    return COUNTDOWN_RED;
}

function getIncentiveIcon(type: string): keyof typeof Ionicons.glyphMap {
    switch (type) {
        case 'peak_hours': return 'flame';
        case 'time_limited': return 'timer';
        case 'min_distance': return 'speedometer';
        case 'area_boost': return 'location';
        default: return 'gift';
    }
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
    const { colors, isDark } = useTheme();
    const styles = useMemo(() => createStyles(colors, isDark), [colors, isDark]);
    const screenHeight = Dimensions.get('window').height;

    const slideAnim = useRef(new Animated.Value(screenHeight)).current;
    const pulseAnim = useRef(new Animated.Value(1)).current;
    const glowAnim = useRef(new Animated.Value(0)).current;
    const progressAnim = useRef(new Animated.Value(1)).current;
    const heroScale = useRef(new Animated.Value(0.5)).current;
    const badgeShimmer = useRef(new Animated.Value(0)).current;

    useEffect(() => {
        const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
        return () => sub.remove();
    }, []);

    useEffect(() => {
        if (incomingRide) {
            // Haptic feedback on appear
            if (Platform.OS !== 'web') {
                Vibration.vibrate([0, 80, 60, 80]);
            }

            // Slide up entrance
            Animated.spring(slideAnim, {
                toValue: 0,
                tension: 60,
                friction: 11,
                useNativeDriver: true,
            }).start();

            // Hero scale pop-in
            Animated.spring(heroScale, {
                toValue: 1,
                tension: 80,
                friction: 7,
                useNativeDriver: true,
            }).start();

            // Continuous accept button pulse
            Animated.loop(
                Animated.sequence([
                    Animated.timing(pulseAnim, { toValue: 1.04, duration: 800, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
                    Animated.timing(pulseAnim, { toValue: 1, duration: 800, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
                ])
            ).start();

            // Hero glow breathing animation
            Animated.loop(
                Animated.sequence([
                    Animated.timing(glowAnim, { toValue: 1, duration: 1500, easing: Easing.inOut(Easing.ease), useNativeDriver: false }),
                    Animated.timing(glowAnim, { toValue: 0, duration: 1500, easing: Easing.inOut(Easing.ease), useNativeDriver: false }),
                ])
            ).start();

            // Shimmer across bonus badges
            Animated.loop(
                Animated.timing(badgeShimmer, {
                    toValue: 1,
                    duration: 2200,
                    easing: Easing.linear,
                    useNativeDriver: true,
                })
            ).start();
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

    const timerColor = getCountdownColor(countdownSeconds);

    const perHr = incomingRide.duration_minutes && incomingRide.duration_minutes > 0
        ? ((totalEarnings / incomingRide.duration_minutes) * 60).toFixed(0) : null;
    const perKm = incomingRide.distance_km && incomingRide.distance_km > 0
        ? (totalEarnings / incomingRide.distance_km).toFixed(2) : null;

    const hasSurge = (incomingRide.surge_multiplier ?? 0) > 1.0;
    const hasIncentives = (incomingRide.incentives?.length ?? 0) > 0;
    const hasBonus = totalBonus > 0;
    const quest = incomingRide.quest_hint;

    const pickupStr = pickupDistanceKm != null
        ? (pickupDistanceKm < 1
            ? `${Math.round(pickupDistanceKm * 1000)} m`
            : `${pickupDistanceKm.toFixed(1)} km`)
        : null;

    // Hero gradient — gets richer when bonus/surge present
    const heroGradient: [string, string, ...string[]] = hasBonus || hasSurge
        ? ['#00D26A', '#00A050', '#008040']
        : ['#34C759', '#28A745'];

    const glowOpacity = glowAnim.interpolate({
        inputRange: [0, 1],
        outputRange: [0.3, 0.85],
    });

    const shimmerTranslate = badgeShimmer.interpolate({
        inputRange: [0, 1],
        outputRange: [-200, 200],
    });

    return (
        <View style={styles.overlay} accessibilityViewIsModal>
            {/* Dim background */}
            <Animated.View
                style={[
                    styles.dimBackground,
                    { opacity: slideAnim.interpolate({ inputRange: [0, screenHeight], outputRange: [1, 0] }) }
                ]}
            />

            <Animated.View style={[styles.cardContainer, { transform: [{ translateY: slideAnim }] }]}>
                <View style={styles.card}>

                    {/* Countdown progress bar at top */}
                    <View style={styles.progressTrack}>
                        <Animated.View style={[
                            styles.progressFill,
                            {
                                backgroundColor: timerColor,
                                width: progressAnim.interpolate({
                                    inputRange: [0, 1],
                                    outputRange: ['0%', '100%']
                                })
                            }
                        ]} />
                    </View>

                    <ScrollView
                        showsVerticalScrollIndicator={false}
                        bounces={false}
                        contentContainerStyle={styles.scrollContent}
                    >
                        {/* Header row: New Request title + Countdown ring */}
                        <View style={styles.headerRow}>
                            <View style={styles.headerLeft}>
                                <View style={styles.newRequestBadge}>
                                    <View style={[styles.liveDot, { backgroundColor: MONEY_GREEN }]} />
                                    <Text style={styles.newRequestText}>NEW REQUEST</Text>
                                </View>
                                {incomingRide.rider_name && (
                                    <View style={styles.riderRow}>
                                        <View style={styles.riderAvatar}>
                                            <Text style={styles.riderInitial}>
                                                {incomingRide.rider_name.charAt(0).toUpperCase()}
                                            </Text>
                                        </View>
                                        <View>
                                            <Text style={styles.riderName} numberOfLines={1}>
                                                {incomingRide.rider_name}
                                            </Text>
                                            {incomingRide.rider_rating ? (
                                                <View style={styles.ratingRow}>
                                                    <Ionicons name="star" size={11} color={GOLD} />
                                                    <Text style={styles.ratingText}>
                                                        {incomingRide.rider_rating.toFixed(1)}
                                                    </Text>
                                                </View>
                                            ) : null}
                                        </View>
                                    </View>
                                )}
                            </View>

                            {/* Countdown ring */}
                            <View style={styles.countdownWrap}>
                                <View style={[styles.countdownOuterRing, { borderColor: timerColor + '30' }]}>
                                    <View style={[styles.countdownInnerRing, { borderColor: timerColor }]}>
                                        <Text style={[styles.countdownText, { color: timerColor }]}>
                                            {countdownSeconds}
                                        </Text>
                                        <Text style={[styles.countdownUnit, { color: timerColor }]}>SEC</Text>
                                    </View>
                                </View>
                            </View>
                        </View>

                        {/* HERO EARNINGS — the showpiece */}
                        <Animated.View style={[styles.heroContainer, { transform: [{ scale: heroScale }] }]}>
                            {/* Glow effect behind */}
                            <Animated.View style={[styles.heroGlow, { opacity: glowOpacity }]} />

                            <LinearGradient
                                colors={heroGradient}
                                start={{ x: 0, y: 0 }}
                                end={{ x: 1, y: 1 }}
                                style={styles.heroGradientBg}
                            >
                                {/* Decorative corner sparkles */}
                                <View style={styles.sparkleTopLeft}>
                                    <Ionicons name="sparkles" size={18} color="rgba(255,255,255,0.4)" />
                                </View>
                                <View style={styles.sparkleBottomRight}>
                                    <Ionicons name="sparkles" size={14} color="rgba(255,255,255,0.3)" />
                                </View>

                                <Text style={styles.heroLabel}>YOU EARN</Text>
                                <View style={styles.heroFareRow}>
                                    <Text style={styles.heroDollar} allowFontScaling={false}>$</Text>
                                    <Text style={styles.heroFare} allowFontScaling={false}>
                                        {totalEarnings.toFixed(2)}
                                    </Text>
                                </View>

                                {/* 100% commission badge with shimmer */}
                                <View style={styles.commissionBadge}>
                                    <Ionicons name="shield-checkmark" size={13} color="#FFF" />
                                    <Text style={styles.commissionText}>YOU KEEP 100% — $0 COMMISSION</Text>
                                    <Animated.View
                                        style={[
                                            styles.shimmer,
                                            { transform: [{ translateX: shimmerTranslate }] }
                                        ]}
                                    />
                                </View>

                                {/* Earnings breakdown line */}
                                {hasBonus && (
                                    <View style={styles.breakdownRow}>
                                        <Text style={styles.breakdownText}>
                                            ${baseFare.toFixed(2)} fare
                                        </Text>
                                        <Ionicons name="add" size={14} color="rgba(255,255,255,0.85)" />
                                        <Text style={[styles.breakdownText, styles.breakdownBonus]}>
                                            ${totalBonus.toFixed(2)} bonus
                                        </Text>
                                    </View>
                                )}
                            </LinearGradient>
                        </Animated.View>

                        {/* Top badges row: Surge / WAV / Payment */}
                        {(hasSurge || incomingRide.requires_wav || incomingRide.payment_method === 'cash') && (
                            <View style={styles.badgesRow}>
                                {hasSurge && (
                                    <LinearGradient
                                        colors={[SURGE_ORANGE, SURGE_RED]}
                                        start={{ x: 0, y: 0 }}
                                        end={{ x: 1, y: 1 }}
                                        style={styles.chipGradient}
                                    >
                                        <Ionicons name="flame" size={14} color="#FFF" />
                                        <Text style={styles.chipText}>
                                            {incomingRide.surge_multiplier!.toFixed(1)}× SURGE
                                        </Text>
                                    </LinearGradient>
                                )}
                                {incomingRide.requires_wav && (
                                    <View style={[styles.chip, { backgroundColor: '#0A84FF' }]}>
                                        <Ionicons name="accessibility" size={14} color="#FFF" />
                                        <Text style={styles.chipText}>WAV</Text>
                                    </View>
                                )}
                                {incomingRide.payment_method === 'cash' && (
                                    <View style={[styles.chip, { backgroundColor: '#3A3A3C' }]}>
                                        <Ionicons name="cash" size={14} color="#FFF" />
                                        <Text style={styles.chipText}>CASH</Text>
                                    </View>
                                )}
                            </View>
                        )}

                        {/* INCENTIVES — vibrant gold cards */}
                        {hasIncentives && (
                            <View style={styles.incentivesContainer}>
                                <View style={styles.sectionHeader}>
                                    <Ionicons name="gift" size={16} color={GOLD_DARK} />
                                    <Text style={styles.sectionTitle}>BONUS BOOSTS</Text>
                                    <View style={styles.totalBonusPill}>
                                        <Text style={styles.totalBonusText}>
                                            +${totalBonus.toFixed(2)}
                                        </Text>
                                    </View>
                                </View>
                                {incomingRide.incentives!.map((inc, idx) => (
                                    <View key={idx} style={styles.incentiveCard}>
                                        <LinearGradient
                                            colors={['#FFD60A', '#FFAB00']}
                                            start={{ x: 0, y: 0 }}
                                            end={{ x: 1, y: 1 }}
                                            style={styles.incentiveIconWrap}
                                        >
                                            <Ionicons
                                                name={getIncentiveIcon(inc.incentive_type)}
                                                size={20}
                                                color="#1C1C1E"
                                            />
                                        </LinearGradient>
                                        <View style={styles.incentiveTextWrap}>
                                            <Text style={styles.incentiveName} numberOfLines={1}>
                                                {inc.name}
                                            </Text>
                                            <Text style={styles.incentiveType}>
                                                {inc.incentive_type.replace(/_/g, ' ').toUpperCase()}
                                            </Text>
                                        </View>
                                        <Text style={styles.incentiveAmount}>
                                            +${inc.bonus_amount.toFixed(2)}
                                        </Text>
                                    </View>
                                ))}
                            </View>
                        )}

                        {/* QUEST PROGRESS — purple premium card */}
                        {quest && quest.target_value > 0 && (
                            <LinearGradient
                                colors={['#A855F7', '#7C3AED']}
                                start={{ x: 0, y: 0 }}
                                end={{ x: 1, y: 1 }}
                                style={styles.questCard}
                            >
                                <View style={styles.questHeader}>
                                    <View style={styles.questIconCircle}>
                                        <Ionicons name="trophy" size={20} color={GOLD} />
                                    </View>
                                    <View style={styles.questTextWrap}>
                                        <Text style={styles.questTitle} numberOfLines={1}>
                                            {quest.title}
                                        </Text>
                                        <Text style={styles.questSubtitle}>
                                            Ride {Math.floor(quest.current_value) + 1} of {Math.floor(quest.target_value)} • Reward ${quest.reward_amount.toFixed(0)}
                                        </Text>
                                    </View>
                                </View>
                                <View style={styles.questProgressTrack}>
                                    <View style={[
                                        styles.questProgressFill,
                                        { width: `${Math.min(100, ((quest.current_value + 1) / quest.target_value) * 100)}%` }
                                    ]} />
                                </View>
                            </LinearGradient>
                        )}

                        {/* TRIP METRICS — premium cards */}
                        <View style={styles.metricsRow}>
                            <View style={styles.metricCard}>
                                <Ionicons name="navigate-circle" size={22} color={colors.primary} />
                                <Text style={styles.metricValue}>
                                    {incomingRide.distance_km?.toFixed(1) || '--'}
                                </Text>
                                <Text style={styles.metricUnit}>KM TRIP</Text>
                            </View>
                            <View style={styles.metricCard}>
                                <Ionicons name="time" size={22} color={colors.primary} />
                                <Text style={styles.metricValue}>
                                    {Math.round(incomingRide.duration_minutes || 0)}
                                </Text>
                                <Text style={styles.metricUnit}>MIN</Text>
                            </View>
                            {perKm && (
                                <View style={[styles.metricCard, styles.metricCardHighlight]}>
                                    <Ionicons name="cash" size={22} color={MONEY_GREEN_DARK} />
                                    <Text style={[styles.metricValue, { color: MONEY_GREEN_DARK }]}>
                                        ${perKm}
                                    </Text>
                                    <Text style={[styles.metricUnit, { color: MONEY_GREEN_DARK }]}>PER KM</Text>
                                </View>
                            )}
                            {perHr && (
                                <View style={[styles.metricCard, styles.metricCardHighlight]}>
                                    <Ionicons name="trending-up" size={22} color={MONEY_GREEN_DARK} />
                                    <Text style={[styles.metricValue, { color: MONEY_GREEN_DARK }]}>
                                        ${perHr}
                                    </Text>
                                    <Text style={[styles.metricUnit, { color: MONEY_GREEN_DARK }]}>HR RATE</Text>
                                </View>
                            )}
                        </View>

                        {/* ROUTE TIMELINE — clean two-stop visualization */}
                        <View style={styles.routeCard}>
                            <View style={styles.routeColumn}>
                                <LinearGradient
                                    colors={[MONEY_GREEN, MONEY_GREEN_DARK]}
                                    style={styles.routeDot}
                                />
                                <View style={styles.routeLine} />
                                <View style={[styles.routeDot, { backgroundColor: SURGE_RED }]} />
                            </View>
                            <View style={styles.routeContent}>
                                <View style={styles.routeStop}>
                                    <View style={styles.routeStopHeader}>
                                        <Text style={styles.routeLabel}>PICKUP</Text>
                                        {pickupStr && (
                                            <View style={styles.distanceChip}>
                                                <Ionicons name="navigate" size={10} color={colors.primary} />
                                                <Text style={styles.distanceChipText}>{pickupStr} away</Text>
                                            </View>
                                        )}
                                    </View>
                                    <Text style={styles.routeAddress} numberOfLines={2}>
                                        {incomingRide.pickup_address || 'Pickup location'}
                                    </Text>
                                </View>
                                <View style={styles.routeStopDivider} />
                                <View style={styles.routeStop}>
                                    <Text style={styles.routeLabel}>DROP-OFF</Text>
                                    <Text style={styles.routeAddress} numberOfLines={2}>
                                        {incomingRide.dropoff_address || 'Drop-off location'}
                                    </Text>
                                </View>
                            </View>
                        </View>
                    </ScrollView>

                    {/* Action buttons — fixed at bottom */}
                    <View style={styles.actionBar}>
                        <TouchableOpacity
                            style={styles.declineBtn}
                            onPress={onDecline}
                            activeOpacity={0.7}
                            accessibilityLabel="Decline"
                            disabled={isLoading}
                        >
                            <Ionicons name="close" size={28} color={colors.textDim} />
                        </TouchableOpacity>

                        <Animated.View style={[styles.acceptBtnWrapper, { transform: [{ scale: pulseAnim }] }]}>
                            <TouchableOpacity
                                style={styles.acceptBtn}
                                onPress={onAccept}
                                disabled={isLoading}
                                activeOpacity={0.85}
                                accessibilityLabel="Accept Ride"
                            >
                                <LinearGradient
                                    colors={[MONEY_GREEN, MONEY_GREEN_DARK, '#008040']}
                                    style={styles.acceptGradient}
                                    start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                                >
                                    {isLoading ? (
                                        <ActivityIndicator color="#fff" size="small" />
                                    ) : (
                                        <>
                                            <View style={styles.acceptTextWrap}>
                                                <Text style={styles.acceptBtnText}>ACCEPT</Text>
                                                <Text style={styles.acceptBtnSub}>
                                                    ${totalEarnings.toFixed(2)}
                                                </Text>
                                            </View>
                                            <View style={styles.acceptArrow}>
                                                <Ionicons name="arrow-forward" size={22} color="#FFF" />
                                            </View>
                                        </>
                                    )}
                                </LinearGradient>
                            </TouchableOpacity>
                        </Animated.View>
                    </View>
                </View>
            </Animated.View>
        </View>
    );
};

function createStyles(colors: ThemeColors, isDark: boolean) {
    return StyleSheet.create({
        overlay: {
            position: 'absolute',
            top: 0, left: 0, right: 0, bottom: 0,
            zIndex: 100,
            justifyContent: 'flex-end',
        },
        dimBackground: {
            ...StyleSheet.absoluteFill as any,
            backgroundColor: 'rgba(0,0,0,0.6)',
        },
        cardContainer: {
            paddingHorizontal: 8,
            paddingBottom: Platform.OS === 'ios' ? 24 : 12,
        },
        card: {
            backgroundColor: isDark ? '#1C1C1E' : '#FFFFFF',
            borderRadius: 32,
            overflow: 'hidden',
            shadowColor: '#000',
            shadowOffset: { width: 0, height: -8 },
            shadowOpacity: 0.4,
            shadowRadius: 24,
            elevation: 24,
            maxHeight: Dimensions.get('window').height * 0.92,
        },
        progressTrack: {
            height: 4,
            backgroundColor: isDark ? '#2C2C2E' : '#F2F2F7',
        },
        progressFill: {
            height: '100%',
        },
        scrollContent: {
            padding: 18,
            paddingBottom: 4,
        },

        // HEADER
        headerRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 14,
        },
        headerLeft: {
            flex: 1,
            marginRight: 12,
        },
        newRequestBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: isDark ? '#0F2F1F' : '#E8FAF0',
            paddingHorizontal: 10,
            paddingVertical: 5,
            borderRadius: 8,
            alignSelf: 'flex-start',
            gap: 6,
            marginBottom: 10,
        },
        liveDot: {
            width: 7,
            height: 7,
            borderRadius: 4,
        },
        newRequestText: {
            color: MONEY_GREEN,
            fontSize: 11,
            fontWeight: '900',
            letterSpacing: 0.8,
        },
        riderRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
        },
        riderAvatar: {
            width: 36, height: 36,
            borderRadius: 18,
            backgroundColor: colors.primary,
            justifyContent: 'center',
            alignItems: 'center',
        },
        riderInitial: {
            color: '#FFF',
            fontSize: 16,
            fontWeight: '800',
        },
        riderName: {
            color: colors.text,
            fontSize: 15,
            fontWeight: '700',
            maxWidth: 140,
        },
        ratingRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 3,
            marginTop: 2,
        },
        ratingText: {
            color: colors.textDim,
            fontSize: 12,
            fontWeight: '600',
        },

        // COUNTDOWN RING
        countdownWrap: {
            position: 'relative',
        },
        countdownOuterRing: {
            width: 64, height: 64,
            borderRadius: 32,
            borderWidth: 3,
            justifyContent: 'center',
            alignItems: 'center',
        },
        countdownInnerRing: {
            width: 54, height: 54,
            borderRadius: 27,
            borderWidth: 3,
            justifyContent: 'center',
            alignItems: 'center',
            backgroundColor: isDark ? '#2C2C2E' : '#FAFAFA',
        },
        countdownText: {
            fontSize: 20,
            fontWeight: '900',
            lineHeight: 22,
        },
        countdownUnit: {
            fontSize: 8,
            fontWeight: '800',
            letterSpacing: 0.5,
            marginTop: -2,
        },

        // HERO
        heroContainer: {
            marginBottom: 14,
            position: 'relative',
        },
        heroGlow: {
            position: 'absolute',
            top: -10, left: -10, right: -10, bottom: -10,
            backgroundColor: MONEY_GREEN,
            borderRadius: 32,
            opacity: 0.5,
        },
        heroGradientBg: {
            borderRadius: 24,
            padding: 22,
            paddingTop: 18,
            alignItems: 'center',
            overflow: 'hidden',
            position: 'relative',
        },
        sparkleTopLeft: {
            position: 'absolute',
            top: 12, left: 14,
        },
        sparkleBottomRight: {
            position: 'absolute',
            bottom: 14, right: 16,
        },
        heroLabel: {
            color: 'rgba(255,255,255,0.85)',
            fontSize: 11,
            fontWeight: '800',
            letterSpacing: 1.5,
            marginBottom: 4,
        },
        heroFareRow: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            justifyContent: 'center',
        },
        heroDollar: {
            color: '#FFF',
            fontSize: 32,
            fontWeight: '800',
            marginTop: 12,
            marginRight: 2,
        },
        heroFare: {
            color: '#FFF',
            fontSize: 64,
            fontWeight: '900',
            letterSpacing: -3,
            includeFontPadding: false,
            lineHeight: 68,
        },
        commissionBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: 'rgba(0,0,0,0.25)',
            paddingHorizontal: 12,
            paddingVertical: 6,
            borderRadius: 14,
            gap: 6,
            marginTop: 6,
            overflow: 'hidden',
            borderWidth: 1,
            borderColor: 'rgba(255,255,255,0.2)',
        },
        commissionText: {
            color: '#FFF',
            fontSize: 11,
            fontWeight: '900',
            letterSpacing: 0.5,
        },
        shimmer: {
            position: 'absolute',
            top: 0, bottom: 0,
            width: 60,
            backgroundColor: 'rgba(255,255,255,0.3)',
            transform: [{ skewX: '-20deg' }],
        },
        breakdownRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            marginTop: 10,
        },
        breakdownText: {
            color: 'rgba(255,255,255,0.9)',
            fontSize: 13,
            fontWeight: '700',
        },
        breakdownBonus: {
            color: GOLD,
        },

        // BADGES
        badgesRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 8,
            marginBottom: 14,
        },
        chip: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 12,
            paddingVertical: 6,
            borderRadius: 14,
            gap: 6,
        },
        chipGradient: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 12,
            paddingVertical: 6,
            borderRadius: 14,
            gap: 6,
        },
        chipText: {
            color: '#FFF',
            fontSize: 12,
            fontWeight: '800',
            letterSpacing: 0.5,
        },

        // SECTION HEADER (shared)
        sectionHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            marginBottom: 8,
        },
        sectionTitle: {
            color: colors.textDim,
            fontSize: 11,
            fontWeight: '900',
            letterSpacing: 1,
            flex: 1,
        },

        // INCENTIVES
        incentivesContainer: {
            backgroundColor: isDark ? '#2A2515' : '#FFFBEB',
            borderRadius: 18,
            padding: 12,
            marginBottom: 14,
            borderWidth: 1,
            borderColor: GOLD + '40',
        },
        totalBonusPill: {
            backgroundColor: GOLD,
            paddingHorizontal: 10,
            paddingVertical: 3,
            borderRadius: 10,
        },
        totalBonusText: {
            color: '#1C1C1E',
            fontSize: 12,
            fontWeight: '900',
            letterSpacing: 0.3,
        },
        incentiveCard: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: isDark ? '#1C1C1E' : '#FFFFFF',
            borderRadius: 12,
            padding: 10,
            marginTop: 6,
            shadowColor: GOLD,
            shadowOffset: { width: 0, height: 2 },
            shadowOpacity: 0.15,
            shadowRadius: 4,
            elevation: 2,
        },
        incentiveIconWrap: {
            width: 40, height: 40,
            borderRadius: 20,
            justifyContent: 'center',
            alignItems: 'center',
            marginRight: 12,
        },
        incentiveTextWrap: {
            flex: 1,
        },
        incentiveName: {
            color: colors.text,
            fontSize: 14,
            fontWeight: '700',
        },
        incentiveType: {
            color: colors.textDim,
            fontSize: 10,
            fontWeight: '700',
            letterSpacing: 0.5,
            marginTop: 2,
        },
        incentiveAmount: {
            color: GOLD_DARK,
            fontSize: 17,
            fontWeight: '900',
            letterSpacing: -0.3,
        },

        // QUEST
        questCard: {
            borderRadius: 18,
            padding: 14,
            marginBottom: 14,
        },
        questHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            marginBottom: 10,
        },
        questIconCircle: {
            width: 38, height: 38,
            borderRadius: 19,
            backgroundColor: 'rgba(0,0,0,0.25)',
            justifyContent: 'center',
            alignItems: 'center',
            marginRight: 12,
        },
        questTextWrap: {
            flex: 1,
        },
        questTitle: {
            color: '#FFF',
            fontSize: 15,
            fontWeight: '800',
        },
        questSubtitle: {
            color: 'rgba(255,255,255,0.85)',
            fontSize: 12,
            fontWeight: '600',
            marginTop: 2,
        },
        questProgressTrack: {
            height: 8,
            backgroundColor: 'rgba(0,0,0,0.3)',
            borderRadius: 4,
            overflow: 'hidden',
        },
        questProgressFill: {
            height: '100%',
            backgroundColor: GOLD,
            borderRadius: 4,
        },

        // METRICS
        metricsRow: {
            flexDirection: 'row',
            gap: 8,
            marginBottom: 14,
        },
        metricCard: {
            flex: 1,
            backgroundColor: isDark ? '#2C2C2E' : '#F5F5F7',
            borderRadius: 14,
            paddingVertical: 12,
            paddingHorizontal: 6,
            alignItems: 'center',
        },
        metricCardHighlight: {
            backgroundColor: isDark ? '#0F2F1F' : '#E8FAF0',
            borderWidth: 1,
            borderColor: MONEY_GREEN + '40',
        },
        metricValue: {
            color: colors.text,
            fontSize: 19,
            fontWeight: '900',
            marginTop: 4,
            letterSpacing: -0.5,
        },
        metricUnit: {
            color: colors.textDim,
            fontSize: 9,
            fontWeight: '800',
            letterSpacing: 0.8,
            marginTop: 2,
        },

        // ROUTE
        routeCard: {
            flexDirection: 'row',
            backgroundColor: isDark ? '#2C2C2E' : '#F8F8FA',
            borderRadius: 18,
            padding: 14,
            marginBottom: 10,
        },
        routeColumn: {
            alignItems: 'center',
            marginRight: 12,
            paddingVertical: 4,
        },
        routeDot: {
            width: 12, height: 12,
            borderRadius: 6,
        },
        routeLine: {
            width: 2,
            flex: 1,
            backgroundColor: isDark ? '#3A3A3C' : '#D1D1D6',
            marginVertical: 4,
        },
        routeContent: {
            flex: 1,
        },
        routeStop: {
            paddingVertical: 2,
        },
        routeStopDivider: {
            height: 8,
        },
        routeStopHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 2,
        },
        routeLabel: {
            color: colors.textDim,
            fontSize: 10,
            fontWeight: '800',
            letterSpacing: 0.8,
        },
        distanceChip: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 3,
            backgroundColor: colors.primary + '15',
            paddingHorizontal: 7,
            paddingVertical: 2,
            borderRadius: 8,
        },
        distanceChipText: {
            color: colors.primary,
            fontSize: 10,
            fontWeight: '800',
        },
        routeAddress: {
            color: colors.text,
            fontSize: 14,
            fontWeight: '600',
            lineHeight: 19,
        },

        // ACTION BAR
        actionBar: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            padding: 14,
            paddingTop: 6,
            borderTopWidth: 1,
            borderTopColor: isDark ? '#2C2C2E' : '#F2F2F7',
        },
        declineBtn: {
            width: 64, height: 64,
            borderRadius: 32,
            backgroundColor: isDark ? '#2C2C2E' : '#F2F2F7',
            justifyContent: 'center',
            alignItems: 'center',
            borderWidth: 1,
            borderColor: isDark ? '#3A3A3C' : '#E5E5EA',
        },
        acceptBtnWrapper: {
            flex: 1,
        },
        acceptBtn: {
            height: 64,
            borderRadius: 32,
            overflow: 'hidden',
            shadowColor: MONEY_GREEN,
            shadowOffset: { width: 0, height: 8 },
            shadowOpacity: 0.5,
            shadowRadius: 18,
            elevation: 14,
        },
        acceptGradient: {
            flex: 1,
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingHorizontal: 22,
        },
        acceptTextWrap: {
            flexDirection: 'column',
        },
        acceptBtnText: {
            color: '#fff',
            fontSize: 19,
            fontWeight: '900',
            letterSpacing: 1.2,
        },
        acceptBtnSub: {
            color: 'rgba(255,255,255,0.95)',
            fontSize: 14,
            fontWeight: '700',
            marginTop: -1,
        },
        acceptArrow: {
            width: 36, height: 36,
            borderRadius: 18,
            backgroundColor: 'rgba(255,255,255,0.25)',
            justifyContent: 'center',
            alignItems: 'center',
        },
    });
}

export default RideOfferPanel;
