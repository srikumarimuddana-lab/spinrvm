import React, { useEffect, useMemo } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator, BackHandler } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface IncomingRide {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    fare: number;
    distance_km?: number;
    duration_minutes?: number;
    rider_name?: string;
    rider_rating?: number;
    requires_wav?: boolean;
}

interface RideOfferPanelProps {
    incomingRide: IncomingRide | null;
    countdownSeconds: number;
    isLoading: boolean;
    onAccept: () => void;
    onDecline: () => void;
}

export const RideOfferPanel: React.FC<RideOfferPanelProps> = ({
    incomingRide,
    countdownSeconds,
    isLoading,
    onAccept,
    onDecline,
}) => {
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    useEffect(() => {
        const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
        return () => sub.remove();
    }, []);

    if (!incomingRide) return null;

    const progress = countdownSeconds / 15;

    const fareStr = `$${(incomingRide.fare || 0).toFixed(2)}`;
    const distStr = incomingRide.distance_km != null ? `${incomingRide.distance_km.toFixed(1)} km` : '';
    const panelLabel = [
        incomingRide.requires_wav && 'Wheelchair-accessible vehicle required.',
        `New ride offer. Earnings: ${fareStr}`,
        distStr && `${distStr} to pickup`,
        incomingRide.pickup_address && `Pickup: ${incomingRide.pickup_address}`,
    ].filter(Boolean).join('. ');

    return (
        <View
            style={styles.rideOfferOverlay}
            accessibilityViewIsModal
            accessibilityLabel={panelLabel}
        >
            <LinearGradient colors={['rgba(10,14,33,0.95)', colors.primary]} style={styles.rideOfferGradient}>
                {/* Countdown Ring */}
                <View style={styles.countdownContainer}>
                    <View
                        style={styles.countdownCircle}
                        accessibilityLabel={`${countdownSeconds} seconds remaining to accept`}
                        accessibilityLiveRegion="polite"
                        accessible
                    >
                        <Text style={styles.countdownText} allowFontScaling={false}>{countdownSeconds}</Text>
                    </View>
                    <View style={[styles.countdownBar, { width: `${progress * 100}%` }]} />
                </View>

                {/* Ride Info */}
                <View style={styles.rideOfferInfo}>
                    {incomingRide.requires_wav && (
                        <View
                            style={styles.wavBanner}
                            accessibilityRole="text"
                            accessibilityLabel="Wheelchair-accessible vehicle required for this ride"
                        >
                            <Ionicons name="accessibility" size={18} color="#fff" />
                            <Text style={styles.wavBannerText}>WAV REQUIRED</Text>
                        </View>
                    )}

                    <View style={styles.fareHighlight} accessibilityRole="text" accessibilityLabel={`Your earnings: ${fareStr}`}>
                        <Text style={styles.fareLabel}>YOUR EARNINGS</Text>
                        <Text
                            style={styles.fareAmount}
                            allowFontScaling={false}
                            accessibilityLabel={`Earnings: ${fareStr}`}
                        >
                            {fareStr}
                        </Text>
                    </View>

                    {(incomingRide.distance_km != null || incomingRide.duration_minutes != null) && (
                        <View style={styles.tripStatsRow}>
                            {incomingRide.distance_km != null && (
                                <View style={styles.tripStat}>
                                    <Ionicons name="navigate-outline" size={18} color="#fff" />
                                    <Text style={styles.tripStatValue} allowFontScaling={false}>
                                        {incomingRide.distance_km.toFixed(1)} km
                                    </Text>
                                    <Text style={styles.tripStatLabel}>trip distance</Text>
                                </View>
                            )}
                            {incomingRide.duration_minutes != null && (
                                <View style={styles.tripStat}>
                                    <Ionicons name="time-outline" size={18} color="#fff" />
                                    <Text style={styles.tripStatValue} allowFontScaling={false}>
                                        {Math.round(incomingRide.duration_minutes)} min
                                    </Text>
                                    <Text style={styles.tripStatLabel}>estimated</Text>
                                </View>
                            )}
                        </View>
                    )}

                    <View style={styles.addressRow}>
                        <View style={styles.addressDot}>
                            <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                            <View style={styles.dottedLine} />
                            <View style={[styles.dot, { backgroundColor: colors.error }]} />
                        </View>
                        <View style={styles.addresses}>
                            <Text style={styles.addressText} numberOfLines={1} accessibilityRole="text" accessibilityLabel={`Pickup: ${incomingRide.pickup_address}`}>
                                {incomingRide.pickup_address}
                            </Text>
                            <View style={styles.addressDivider} />
                            <Text style={styles.addressText} numberOfLines={1} accessibilityRole="text" accessibilityLabel={`Dropoff: ${incomingRide.dropoff_address}`}>
                                {incomingRide.dropoff_address}
                            </Text>
                        </View>
                    </View>

                    {incomingRide.rider_name && (
                        <View style={styles.riderInfo}>
                            <View style={styles.riderAvatar}>
                                <Ionicons name="person" size={20} color={colors.textDim} />
                            </View>
                            <Text style={styles.riderName}>{incomingRide.rider_name}</Text>
                            {incomingRide.rider_rating && (
                                <View style={styles.ratingBadge}>
                                    <Ionicons name="star" size={12} color={colors.gold} />
                                    <Text style={styles.ratingText} allowFontScaling={false}>{incomingRide.rider_rating.toFixed(1)}</Text>
                                </View>
                            )}
                        </View>
                    )}
                </View>

                {/* Action Buttons */}
                <View style={styles.offerActions}>
                    <TouchableOpacity
                        style={styles.declineBtn}
                        onPress={onDecline}
                        accessibilityRole="button"
                        accessibilityLabel="Decline ride offer"
                        accessibilityHint="Double-tap to decline this ride"
                    >
                        <Ionicons name="close" size={28} color={colors.error} />
                        <Text style={styles.declineText}>Decline</Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                        style={styles.acceptBtn}
                        onPress={onAccept}
                        disabled={isLoading}
                        accessibilityRole="button"
                        accessibilityLabel={`Accept ride — ${fareStr}`}
                        accessibilityHint={incomingRide.requires_wav ? 'WAV required — double-tap to accept this wheelchair-accessible ride' : 'Double-tap to accept this ride'}
                        accessibilityState={{ disabled: isLoading, busy: isLoading }}
                    >
                        <LinearGradient
                            colors={[colors.primary, colors.primaryDark]}
                            style={styles.acceptGradient}
                        >
                            {isLoading ? (
                                <ActivityIndicator color="#fff" />
                            ) : (
                                <>
                                    <Ionicons name="checkmark" size={28} color="#fff" />
                                    <Text style={styles.acceptText}>Accept</Text>
                                </>
                            )}
                        </LinearGradient>
                    </TouchableOpacity>
                </View>
            </LinearGradient>
        </View>
    );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    rideOfferOverlay: {
        ...StyleSheet.absoluteFill as any,
        zIndex: 20,
    },
    rideOfferGradient: {
        flex: 1,
        justifyContent: 'space-between',
        paddingTop: 60,
        paddingBottom: 40,
        paddingHorizontal: 20,
    },
    countdownContainer: {
        alignItems: 'center',
        marginBottom: 20,
    },
    countdownCircle: {
        width: 80,
        height: 80,
        borderRadius: 40,
        backgroundColor: 'rgba(255,255,255,0.1)',
        justifyContent: 'center',
        alignItems: 'center',
        marginBottom: 12,
    },
    countdownText: {
        fontSize: 36,
        fontWeight: '700',
        color: '#fff',
    },
    countdownBar: {
        height: 4,
        backgroundColor: colors.primary,
        borderRadius: 2,
        maxWidth: 200,
    },
    rideOfferInfo: {
        backgroundColor: 'rgba(255,255,255,0.1)',
        borderRadius: 16,
        padding: 20,
    },
    wavBanner: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: colors.info,
        borderRadius: 8,
        paddingVertical: 8,
        paddingHorizontal: 12,
        marginBottom: 16,
        gap: 8,
    },
    wavBannerText: {
        fontSize: 13,
        fontWeight: '700',
        color: '#fff',
        letterSpacing: 1,
    },
    fareHighlight: {
        alignItems: 'center',
        marginBottom: 20,
    },
    fareLabel: {
        fontSize: 12,
        color: 'rgba(255,255,255,0.7)',
        letterSpacing: 1,
    },
    fareAmount: {
        fontSize: 42,
        fontWeight: '700',
        color: '#fff',
        marginTop: 4,
    },
    tripStatsRow: {
        flexDirection: 'row',
        justifyContent: 'space-around',
        backgroundColor: 'rgba(255,255,255,0.08)',
        borderRadius: 12,
        paddingVertical: 10,
        marginBottom: 16,
    },
    tripStat: {
        alignItems: 'center',
        flex: 1,
    },
    tripStatValue: {
        fontSize: 16,
        fontWeight: '700',
        color: '#fff',
        marginTop: 4,
    },
    tripStatLabel: {
        fontSize: 10,
        color: 'rgba(255,255,255,0.7)',
        letterSpacing: 0.5,
        marginTop: 2,
    },
    addressRow: {
        flexDirection: 'row',
        marginBottom: 16,
    },
    addressDot: {
        width: 24,
        alignItems: 'center',
        marginRight: 12,
    },
    dot: {
        width: 12,
        height: 12,
        borderRadius: 6,
    },
    dottedLine: {
        width: 2,
        flex: 1,
        backgroundColor: 'rgba(255,255,255,0.3)',
        marginVertical: 4,
    },
    addresses: {
        flex: 1,
    },
    addressText: {
        fontSize: 15,
        color: '#fff',
        paddingVertical: 8,
    },
    addressDivider: {
        height: 1,
        backgroundColor: 'rgba(255,255,255,0.2)',
    },
    riderInfo: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingTop: 12,
        borderTopWidth: 1,
        borderTopColor: 'rgba(255,255,255,0.1)',
    },
    riderAvatar: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: 'rgba(255,255,255,0.2)',
        justifyContent: 'center',
        alignItems: 'center',
        marginRight: 10,
    },
    riderName: {
        fontSize: 16,
        fontWeight: '600',
        color: '#fff',
        flex: 1,
    },
    ratingBadge: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: 'rgba(255,255,255,0.2)',
        paddingHorizontal: 8,
        paddingVertical: 4,
        borderRadius: 12,
    },
    ratingText: {
        fontSize: 12,
        fontWeight: '600',
        color: '#fff',
        marginLeft: 4,
    },
    offerActions: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        gap: 16,
    },
    declineBtn: {
        flex: 1,
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(255,255,255,0.15)',
        borderRadius: 16,
        paddingVertical: 16,
        gap: 8,
    },
    declineText: {
        fontSize: 16,
        fontWeight: '600',
        color: colors.error,
    },
    acceptBtn: {
        flex: 2,
        borderRadius: 16,
        overflow: 'hidden',
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
  });
}

export default RideOfferPanel;
