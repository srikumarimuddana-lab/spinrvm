import React, { useEffect, useState, useMemo, useRef } from 'react';
import {
    View,
    Text,
    StyleSheet,
    ScrollView,
    Platform,
    ActivityIndicator,
    TouchableOpacity,
    Image,
} from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import api from '@shared/api/client';
import { ACTUAL_ROUTE_STROKE, PLANNED_ROUTE_STROKE, ROUTE_PIN_COLORS } from '@shared/constants/routeMapStyle';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useCompletedRouteRefresh } from '@shared/hooks/useCompletedRouteRefresh';
import { routeQualityLabel, toReactNativeSegments } from '@shared/utils/routeSegments';

export default function RideDetailScreen() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const mapRef = useRef<MapView>(null);
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    useEffect(() => {
        if (id) void loadRide(true);
    }, [id]);

    const loadRide = async (showLoading = false) => {
        if (showLoading) setLoading(true);
        try {
            const res = await api.get(`/rides/${id}`);
            setRide(res.data);
        } catch (err) {
            console.log('Failed to load ride detail:', err);
        }
        if (showLoading) setLoading(false);
    };

    useCompletedRouteRefresh(ride, loadRide);

    const hasPickup = ride?.pickup_lat && ride?.pickup_lng;
    const hasDropoff = ride?.dropoff_lat && ride?.dropoff_lng;
    // Drive to the road-snapped pickup when present (rider may have pinned a
    // spot inside a mall a car can't reach); fall back to the rider's pin.
    const navPickupLat = (ride as any)?.pickup_nav_lat ?? ride?.pickup_lat;
    const navPickupLng = (ride as any)?.pickup_nav_lng ?? ride?.pickup_lng;

    const mapRegion = useMemo(() => {
        if (!ride) return { latitude: 52.1332, longitude: -106.67, latitudeDelta: 0.03, longitudeDelta: 0.03 };
        if (hasPickup && hasDropoff) {
            const minLat = Math.min(ride.pickup_lat, ride.dropoff_lat);
            const maxLat = Math.max(ride.pickup_lat, ride.dropoff_lat);
            const minLng = Math.min(ride.pickup_lng, ride.dropoff_lng);
            const maxLng = Math.max(ride.pickup_lng, ride.dropoff_lng);
            const padLat = Math.max((maxLat - minLat) * 0.4, 0.01);
            const padLng = Math.max((maxLng - minLng) * 0.4, 0.01);
            return {
                latitude: (minLat + maxLat) / 2,
                longitude: (minLng + maxLng) / 2,
                latitudeDelta: (maxLat - minLat) + padLat,
                longitudeDelta: (maxLng - minLng) + padLng,
            };
        }
        return {
            latitude: ride.pickup_lat || 52.1332,
            longitude: ride.pickup_lng || -106.6700,
            latitudeDelta: 0.03,
            longitudeDelta: 0.03,
        };
    }, [ride, hasPickup, hasDropoff]);

    const actualSegments = useMemo(
        () => toReactNativeSegments(ride?.actual_route_segments),
        [ride?.actual_route_segments],
    );
    const plannedSegments = useMemo(
        () => toReactNativeSegments(ride?.planned_route_polyline ? [ride.planned_route_polyline] : []),
        [ride?.planned_route_polyline],
    );
    const isV2Route = Number(ride?.route_schema_version || 0) >= 2;
    const hasActualRoute = actualSegments.length > 0;
    const displaySegments = hasActualRoute ? actualSegments : isV2Route ? [] : plannedSegments;
    const mapCoordinates = useMemo(
        () => displaySegments.reduce<any[]>((all, segment) => all.concat(segment), []),
        [displaySegments],
    );
    const routeRevision = Number(ride?.route_revision || 0);
    const isActualSnapshot =
        isV2Route &&
        routeRevision > 0 &&
        Number(ride?.snapshot_revision || 0) === routeRevision;
    const routeSnapshotUrl = ride?.route_snapshot_url && isActualSnapshot ? ride.route_snapshot_url : '';
    const routeLabel = hasActualRoute ? 'Actual route' : isV2Route ? 'Actual route' : 'Planned route';
    const routeQuality = routeQualityLabel(ride?.route_quality);
    const routeIsProcessing =
        ride?.route_geometry_status === 'pending' || ride?.route_geometry_status === 'processing';
    const routeStatus = routeSnapshotUrl
        ? `Actual route · revision ${routeRevision}`
        : hasActualRoute
            ? routeQuality
            : isV2Route
                ? routeIsProcessing
                    ? 'Actual route processing'
                    : 'Actual route unavailable'
                : 'Planned route preview';

    if (loading) {
        return (
            <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
                <ActivityIndicator color={colors.primary} size="large" />
            </View>
        );
    }

    if (!ride) {
        return (
            <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
                <Ionicons name="alert-circle" size={48} color={colors.primary} />
                <Text style={styles.errorText}>Ride not found</Text>
                <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
                    <Text style={styles.backLinkText}>Go Back</Text>
                </TouchableOpacity>
            </View>
        );
    }

    const isCompleted = ride.status === 'completed';
    const statusColor = isCompleted ? colors.primary : colors.primary;
    const statusLabel = ride.status?.charAt(0).toUpperCase() + ride.status?.slice(1);

    const completedDate = ride.ride_completed_at || ride.cancelled_at || ride.created_at;
    const formattedDate = completedDate
        ? new Date(completedDate).toLocaleDateString('en', {
            weekday: 'long',
            month: 'long',
            day: 'numeric',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        })
        : '';

    return (
        <View style={styles.container}>
            <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: insets.bottom + 24 }}>
                {/* Route Map — actual GPS evidence is never replaced with directions */}
                {hasPickup && hasDropoff && (
                    <View style={styles.mapContainer}>
                        {routeSnapshotUrl ? (
                            <Image
                                source={{ uri: routeSnapshotUrl }}
                                style={styles.map}
                                resizeMode="cover"
                            />
                        ) : (
                            <MapView
                                ref={mapRef}
                                style={styles.map}
                                provider={MAP_PROVIDER}
                                initialRegion={mapRegion}
                                scrollEnabled={false}
                                zoomEnabled={false}
                                    rotateEnabled={false}
                                    onMapReady={() => {
                                        if (mapCoordinates.length >= 2) {
                                            mapRef.current?.fitToCoordinates(mapCoordinates, {
                                                edgePadding: { top: 40, right: 40, bottom: 40, left: 40 },
                                                animated: false,
                                            });
                                        }
                                    }}
                                >
                                    {actualSegments.map((coordinates, index) => (
                                        <Polyline
                                            key={`actual-segment-${index}`}
                                            coordinates={coordinates}
                                            {...ACTUAL_ROUTE_STROKE}
                                            lineCap="round"
                                            lineJoin="round"
                                        />
                                    ))}
                                    {!isV2Route && !hasActualRoute && plannedSegments.map((coordinates, index) => (
                                        <Polyline
                                            key={`planned-segment-${index}`}
                                            coordinates={coordinates}
                                            {...PLANNED_ROUTE_STROKE}
                                            lineCap="round"
                                            lineJoin="round"
                                        />
                                    ))}
                                <Marker coordinate={{ latitude: navPickupLat, longitude: navPickupLng }} anchor={{ x: 0.5, y: 0.5 }}>
                                    <View style={[styles.markerDot, { backgroundColor: ROUTE_PIN_COLORS.pickup }]}>
                                        <Ionicons name="location" size={14} color="#fff" />
                                    </View>
                                </Marker>
                                <Marker coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                                    <View style={[styles.markerDot, { backgroundColor: ROUTE_PIN_COLORS.dropoff }]}>
                                        <Ionicons name="flag" size={14} color="#fff" />
                                    </View>
                                </Marker>
                            </MapView>
                        )}

                        <TouchableOpacity style={[styles.backBtn, { top: insets.top + 12 }]} onPress={() => router.back()}>
                            <Ionicons name="arrow-back" size={22} color="#fff" />
                        </TouchableOpacity>
                        <View style={styles.routeStatusPill}>
                            <Ionicons name={hasActualRoute ? 'navigate-circle-outline' : 'map-outline'} size={14} color="#2563EB" />
                            <Text style={styles.routeStatusText} numberOfLines={1}>{routeLabel} · {routeStatus}</Text>
                        </View>
                    </View>
                )}

                {/* Status + Date */}
                <View style={styles.content}>
                    <View style={styles.statusRow}>
                        <View style={[styles.statusBadge, { backgroundColor: `${statusColor}15` }]}>
                            <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
                            <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
                        </View>
                        <Text style={styles.dateText}>{formattedDate}</Text>
                    </View>

                    {/* Route */}
                    <View style={styles.card}>
                        <View style={styles.routeRow}>
                            <View style={styles.routeDots}>
                                <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                                <View style={styles.dotLine} />
                                <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                            </View>
                            <View style={styles.routeTexts}>
                                <View>
                                    <Text style={styles.routeLabel}>PICKUP</Text>
                                    <Text style={styles.routeAddress}>{ride.pickup_address || 'Not available'}</Text>
                                </View>
                                <View>
                                    <Text style={styles.routeLabel}>DROPOFF</Text>
                                    <Text style={styles.routeAddress}>{ride.dropoff_address || 'Not available'}</Text>
                                </View>
                            </View>
                        </View>
                    </View>

                    {/* Trip Stats */}
                    <View style={styles.statsRow}>
                        <View style={styles.stat}>
                            <Ionicons name="speedometer" size={20} color={colors.primary} />
                            {/* GPS-measured first, billed/booking distance as legacy
                                fallback — same source order as the rider screens. */}
                            <Text style={styles.statValue}>{(ride.actual_distance_km ?? ride.distance_km ?? 0).toFixed(1)} km</Text>
                            <Text style={styles.statLabel}>Distance</Text>
                        </View>
                        <View style={styles.statDivider} />
                        <View style={styles.stat}>
                            <Ionicons name="time" size={20} color="#FF9500" />
                            <Text style={styles.statValue}>
                                {ride.ride_started_at && ride.ride_completed_at
                                    ? (() => {
                                        const m = Math.round((new Date(ride.ride_completed_at).getTime() - new Date(ride.ride_started_at).getTime()) / 60000);
                                        return isNaN(m) || m < 1 ? '< 1' : m;
                                    })()
                                    : (ride.duration_minutes || 0)} min
                            </Text>
                            <Text style={styles.statLabel}>Duration</Text>
                        </View>
                        <View style={styles.statDivider} />
                        <View style={styles.stat}>
                            <Ionicons name="star" size={20} color="#FFD700" />
                            <Text style={styles.statValue}>
                                {ride.rider_rating !== null && ride.rider_rating !== undefined
                                    ? ride.rider_rating
                                    : '—'}
                            </Text>
                            <Text style={styles.statLabel}>Rating</Text>
                        </View>
                    </View>

                    {/* Trip Earnings Breakdown */}
                    {isCompleted && (
                        <>
                            {/* Rider name (first name only — PIPEDA) */}
                            {ride.rider_name && (
                                <View style={styles.riderRow}>
                                    <Ionicons name="person-circle-outline" size={18} color={colors.textDim} />
                                    <Text style={styles.riderNameText}>
                                        {ride.rider_name.trim().split(' ')[0]}
                                    </Text>
                                </View>
                            )}

                            <View style={styles.card}>
                                <Text style={styles.cardTitle}>Your Earnings</Text>

                                {/* Ride Fare = exactly what the rider was charged for the trip
                                    (base + distance + time, all locked at booking). This single
                                    line matches the "Ride fare" the rider sees on their receipt
                                    so the driver can verify they received 100% of it. */}
                                {(() => {
                                    const rideFare = parseFloat(ride.base_fare || '0')
                                        + parseFloat(ride.distance_fare || '0')
                                        + parseFloat(ride.time_fare || '0');
                                    const distKm = parseFloat(ride.distance_km || '0').toFixed(1);
                                    const surge = parseFloat(ride.surge_multiplier || '1');
                                    return (
                                        <>
                                            <View style={styles.fareRow}>
                                                <Text style={styles.fareLabel}>
                                                    Ride Fare ({distKm} km){surge > 1 ? ` · ${surge.toFixed(1)}× surge` : ''}
                                                </Text>
                                                <Text style={styles.fareValue}>
                                                    ${rideFare.toFixed(2)}
                                                </Text>
                                            </View>
                                        </>
                                    );
                                })()}

                                {parseFloat(ride.tip_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#f59e0b' }]}>Tip</Text>
                                        <Text style={[styles.fareValue, { color: '#f59e0b' }]}>
                                            +${parseFloat(ride.tip_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {parseFloat(ride.incentive_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#8b5cf6' }]}>Area Boost</Text>
                                        <Text style={[styles.fareValue, { color: '#8b5cf6' }]}>
                                            +${parseFloat(ride.incentive_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                {parseFloat(ride.tax_amount || '0') > 0 && (
                                    <View style={styles.fareRow}>
                                        <Text style={[styles.fareLabel, { color: '#6366f1' }]}>Tax</Text>
                                        <Text style={[styles.fareValue, { color: '#6366f1' }]}>
                                            +${parseFloat(ride.tax_amount || '0').toFixed(2)}
                                        </Text>
                                    </View>
                                )}

                                <View style={styles.fareDivider} />

                                <View style={styles.fareRow}>
                                    <Text style={styles.earningsLabel}>Total Earned</Text>
                                    <Text style={styles.earningsValue}>
                                        ${parseFloat(ride.total_earned || ride.driver_earnings || '0').toFixed(2)}
                                    </Text>
                                </View>
                            </View>

                            {parseFloat(ride.tax_amount || '0') > 0 && (
                                <View style={styles.card}>
                                    <Text style={styles.cardTitle}>Tax Info</Text>

                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>GST (5%)</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.tax_breakdown?.GST?.amount ?? '0').toFixed(2)}
                                        </Text>
                                    </View>
                                    <View style={styles.fareRow}>
                                        <Text style={styles.fareLabel}>PST (6%)</Text>
                                        <Text style={styles.fareValue}>
                                            ${parseFloat(ride.tax_breakdown?.PST?.amount ?? '0').toFixed(2)}
                                        </Text>
                                    </View>

                                    <View style={styles.fareDivider} />
                                    <View style={styles.taxNoteBox}>
                                        <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
                                        <Text style={styles.taxNoteText}>
                                            Tax collected from the rider is included in your total earnings. You are responsible for filing and remitting this amount.
                                        </Text>
                                    </View>
                                </View>
                            )}
                        </>
                    )}

                    {/* Cancellation / No-show fee earned */}
                    {ride.status === 'cancelled' && parseFloat(ride.cancel_fee_earned || '0') > 0 && (
                        <View style={styles.card}>
                            <Text style={styles.cardTitle}>
                                {ride.cancellation_type === 'noshow' ? 'No-Show Fee' : 'Cancellation Fee'}
                            </Text>
                            <View style={styles.fareRow}>
                                <Text style={styles.fareLabel}>
                                    {ride.cancellation_type === 'noshow'
                                        ? 'Rider did not show up'
                                        : 'Rider cancelled after acceptance'}
                                </Text>
                                <Text style={[styles.fareValue, { color: '#f59e0b' }]}>
                                    +${parseFloat(ride.cancel_fee_earned || '0').toFixed(2)}
                                </Text>
                            </View>
                            <View style={styles.fareDivider} />
                            <View style={styles.taxNoteBox}>
                                <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
                                <Text style={styles.taxNoteText}>
                                    This fee has been credited to your wallet.
                                </Text>
                            </View>
                        </View>
                    )}

                    {/* Trip Timeline */}
                    <View style={styles.card}>
                        <Text style={styles.cardTitle}>Trip Timeline</Text>
                        {(() => {
                            const fmtTS = (t: string) => {
                                const d = new Date(t);
                                return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                            };
                            const elapsed = (a: string, b: string): string => {
                                const secs = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000);
                                if (isNaN(secs) || secs < 0) return '';
                                if (secs < 60) return `${secs}s`;
                                const m = Math.floor(secs / 60), s = secs % 60;
                                return s > 0 ? `${m}m ${s}s` : `${m} min`;
                            };
                            type TLStep = { label: string; sub: string; time: string; dot: string; isCancelled?: boolean };
                            const steps: TLStep[] = [
                                ride.created_at && { label: 'Ride Requested', sub: fmtTS(ride.created_at), time: ride.created_at, dot: '#6B7280' },
                                ride.driver_accepted_at && {
                                    label: 'You Accepted',
                                    sub: [fmtTS(ride.driver_accepted_at), ride.created_at && elapsed(ride.created_at, ride.driver_accepted_at) && `${elapsed(ride.created_at, ride.driver_accepted_at)} after request`].filter(Boolean).join('  ·  '),
                                    time: ride.driver_accepted_at, dot: '#3B82F6',
                                },
                                ride.driver_arrived_at && {
                                    label: 'Arrived at Pickup',
                                    sub: [fmtTS(ride.driver_arrived_at), ride.driver_accepted_at && elapsed(ride.driver_accepted_at, ride.driver_arrived_at) && `${elapsed(ride.driver_accepted_at, ride.driver_arrived_at)} drive to pickup`].filter(Boolean).join('  ·  '),
                                    time: ride.driver_arrived_at, dot: '#8B5CF6',
                                },
                                ride.ride_started_at && {
                                    label: 'Trip Started',
                                    sub: [fmtTS(ride.ride_started_at), ride.driver_arrived_at && elapsed(ride.driver_arrived_at, ride.ride_started_at) && `${elapsed(ride.driver_arrived_at, ride.ride_started_at)} wait`].filter(Boolean).join('  ·  '),
                                    time: ride.ride_started_at, dot: '#F59E0B',
                                },
                                ride.ride_completed_at && {
                                    label: 'Trip Completed',
                                    sub: [fmtTS(ride.ride_completed_at), ride.ride_started_at && elapsed(ride.ride_started_at, ride.ride_completed_at) && `${elapsed(ride.ride_started_at, ride.ride_completed_at)} trip`].filter(Boolean).join('  ·  '),
                                    time: ride.ride_completed_at, dot: '#10B981',
                                },
                                ride.cancelled_at && {
                                    label: 'Cancelled',
                                    sub: fmtTS(ride.cancelled_at),
                                    time: ride.cancelled_at, dot: '#EF4444', isCancelled: true,
                                },
                            ].filter(Boolean) as TLStep[];

                            return steps.map((step, i) => {
                                const isLast = i === steps.length - 1;
                                return (
                                <View key={i} style={styles.tlRow}>
                                    {/* Left spine */}
                                    <View style={styles.tlSpine}>
                                        <View style={[styles.tlDot, { backgroundColor: step.dot }]} />
                                        {!isLast && <View style={styles.tlLine} />}
                                    </View>
                                    {/* Content */}
                                    <View style={[styles.tlContent, isLast ? {} : { paddingBottom: 20 }]}>
                                        <Text style={[styles.tlLabel, step.isCancelled && { color: '#EF4444' }, isLast && !step.isCancelled && { color: '#10B981', fontWeight: '700' }]}>
                                            {step.label}
                                        </Text>
                                        <Text style={styles.tlSub}>{step.sub}</Text>
                                    </View>
                                </View>
                                );
                            });
                        })()}
                    </View>
                </View>
                {isCompleted && (
                    <TouchableOpacity
                        style={styles.foundItemBtn}
                        onPress={() => router.push({
                            pathname: '/driver/lost-and-found-chat',
                            params: { rideId: id },
                        } as any)}
                        activeOpacity={0.7}
                    >
                        <Ionicons name="bag-handle-outline" size={18} color="#F97316" />
                        <Text style={styles.foundItemText}>Found an item on this ride?</Text>
                        <Ionicons name="chevron-forward" size={16} color="#F97316" />
                    </TouchableOpacity>
                )}
            </ScrollView>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        foundItemBtn: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            margin: 16,
            padding: 14,
            backgroundColor: 'rgba(249, 115, 22, 0.08)',
            borderRadius: 14,
            borderWidth: 1,
            borderColor: 'rgba(249, 115, 22, 0.3)',
        },
        foundItemText: {
            flex: 1,
            fontSize: 14,
            fontFamily: 'PlusJakartaSans_600SemiBold',
            color: '#F97316',
        },
        mapContainer: { height: 220, position: 'relative' },
        map: { ...StyleSheet.absoluteFill },
        backBtn: {
            position: 'absolute',
            left: 16,
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: 'rgba(10, 14, 33, 0.85)',
            justifyContent: 'center',
            alignItems: 'center',
        },
            markerDot: {
            width: 28,
            height: 28,
            borderRadius: 14,
            justifyContent: 'center',
            alignItems: 'center',
            borderWidth: 2,
                borderColor: '#fff',
            },
            routeStatusPill: {
                position: 'absolute', left: 12, right: 12, bottom: 12,
                flexDirection: 'row', alignItems: 'center', gap: 5,
                backgroundColor: 'rgba(255,255,255,0.95)', borderRadius: 10, paddingHorizontal: 10, paddingVertical: 7,
            },
            routeStatusText: { flex: 1, color: '#2563EB', fontSize: 11, fontWeight: '600' },
        content: { padding: 16 },
        statusRow: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16,
        },
        statusBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            paddingHorizontal: 12,
            paddingVertical: 6,
            borderRadius: 14,
        },
        statusDot: { width: 7, height: 7, borderRadius: 4 },
        statusText: { fontSize: 13, fontWeight: '700' },
        dateText: { color: colors.textDim, fontSize: 12 },
        card: {
            backgroundColor: colors.surface,
            borderRadius: 18,
            padding: 16,
            marginBottom: 14,
            borderWidth: 1,
            borderColor: colors.border,
        },
        cardTitle: {
            color: colors.text,
            fontSize: 16,
            fontWeight: '700',
            marginBottom: 14,
        },
        routeRow: { flexDirection: 'row', gap: 12 },
        routeDots: { alignItems: 'center', width: 12, paddingTop: 6 },
        dot: { width: 10, height: 10, borderRadius: 5 },
        dotLine: {
            width: 2,
            height: 30,
            backgroundColor: colors.surfaceLight,
            marginVertical: 4,
        },
        routeTexts: { flex: 1, gap: 18 },
        routeLabel: {
            color: colors.textDim,
            fontSize: 10,
            letterSpacing: 1,
            fontWeight: '600',
            marginBottom: 2,
        },
        routeAddress: { color: colors.text, fontSize: 14, fontWeight: '500' },
        statsRow: {
            flexDirection: 'row',
            backgroundColor: colors.surface,
            borderRadius: 18,
            padding: 16,
            marginBottom: 14,
            borderWidth: 1,
            borderColor: colors.border,
        },
        stat: { flex: 1, alignItems: 'center', gap: 6 },
        statDivider: { width: 1, backgroundColor: colors.surfaceLight },
        statValue: { color: colors.text, fontSize: 18, fontWeight: '800' },
        statLabel: { color: colors.textDim, fontSize: 11 },
        fareRow: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            marginBottom: 8,
        },
        fareLabel: { color: colors.textDim, fontSize: 14 },
        fareValue: { color: colors.text, fontSize: 14, fontWeight: '600' },
        fareDivider: {
            height: 1,
            backgroundColor: colors.surfaceLight,
            marginVertical: 8,
        },
        earningsLabel: { color: colors.primary, fontSize: 16, fontWeight: '700' },
        earningsValue: { color: colors.primary, fontSize: 20, fontWeight: '800' },
        riderRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            marginBottom: 12,
        },
        riderNameText: {
            color: colors.textDim,
            fontSize: 14,
            fontWeight: '600',
        },
        taxNoteBox: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            gap: 8,
            backgroundColor: colors.surfaceLight,
            borderRadius: 10,
            padding: 12,
        },
        taxNoteText: {
            flex: 1,
            color: colors.textDim,
            fontSize: 12,
            lineHeight: 18,
        },
        tlRow: { flexDirection: 'row', gap: 12 },
        tlSpine: { alignItems: 'center', width: 14 },
        tlDot: { width: 14, height: 14, borderRadius: 7, marginTop: 3 },
        tlLine: { width: 2, flex: 1, backgroundColor: colors.border, marginTop: 4 },
        tlContent: { flex: 1 },
        tlLabel: { fontSize: 14, fontWeight: '600', color: colors.text },
        tlSub: { fontSize: 12, color: colors.textDim, marginTop: 2 },
        errorText: { color: colors.textDim, fontSize: 16, marginTop: 12 },
        backLink: { marginTop: 16, padding: 10 },
        backLinkText: { color: colors.primary, fontSize: 15, fontWeight: '600' },
    });
}
