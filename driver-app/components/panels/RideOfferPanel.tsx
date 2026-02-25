import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;

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
    if (!incomingRide) return null;

    const progress = countdownSeconds / 15;

    return (
        <View style={styles.rideOfferOverlay}>
            <LinearGradient colors={['rgba(10,14,33,0.95)', COLORS.primary]} style={styles.rideOfferGradient}>
                {/* Countdown Ring */}
                <View style={styles.countdownContainer}>
                    <View style={styles.countdownCircle}>
                        <Text style={styles.countdownText}>{countdownSeconds}</Text>
                    </View>
                    <View style={[styles.countdownBar, { width: `${progress * 100}%` }]} />
                </View>

                {/* Ride Info */}
                <View style={styles.rideOfferInfo}>
                    <View style={styles.fareHighlight}>
                        <Text style={styles.fareLabel}>ESTIMATED FARE</Text>
                        <Text style={styles.fareAmount}>${(incomingRide.fare || 0).toFixed(2)}</Text>
                    </View>

                    <View style={styles.addressRow}>
                        <View style={styles.addressDot}>
                            <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
                            <View style={styles.dottedLine} />
                            <View style={[styles.dot, { backgroundColor: COLORS.danger }]} />
                        </View>
                        <View style={styles.addresses}>
                            <Text style={styles.addressText} numberOfLines={1}>
                                {incomingRide.pickup_address}
                            </Text>
                            <View style={styles.addressDivider} />
                            <Text style={styles.addressText} numberOfLines={1}>
                                {incomingRide.dropoff_address}
                            </Text>
                        </View>
                    </View>

                    {incomingRide.rider_name && (
                        <View style={styles.riderInfo}>
                            <View style={styles.riderAvatar}>
                                <Ionicons name="person" size={20} color={COLORS.textDim} />
                            </View>
                            <Text style={styles.riderName}>{incomingRide.rider_name}</Text>
                            {incomingRide.rider_rating && (
                                <View style={styles.ratingBadge}>
                                    <Ionicons name="star" size={12} color={COLORS.gold} />
                                    <Text style={styles.ratingText}>{incomingRide.rider_rating.toFixed(1)}</Text>
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
                    >
                        <Ionicons name="close" size={28} color={COLORS.danger} />
                        <Text style={styles.declineText}>Decline</Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                        style={styles.acceptBtn}
                        onPress={onAccept}
                        disabled={isLoading}
                    >
                        <LinearGradient
                            colors={[COLORS.accent, COLORS.accentDim]}
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

const styles = StyleSheet.create({
    rideOfferOverlay: {
        ...StyleSheet.absoluteFillObject,
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
        backgroundColor: COLORS.accent,
        borderRadius: 2,
        maxWidth: 200,
    },
    rideOfferInfo: {
        backgroundColor: 'rgba(255,255,255,0.1)',
        borderRadius: 16,
        padding: 20,
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
        color: COLORS.danger,
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

export default RideOfferPanel;
