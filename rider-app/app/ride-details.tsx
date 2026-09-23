import { cancellationPaymentMessages } from '../utils/cancellationPaymentMessages';
import React, { useCallback, useEffect, useState, useMemo } from 'react';
import {
  View, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Platform,
} from 'react-native';
import { Text } from '@shared/components/Text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { PROVIDER_GOOGLE, Polyline } from 'react-native-maps';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';
import api, { getApiErrorMessage, getAuthHeader } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT } from '@shared/utils/responsive';
import { useAuthStore } from '@shared/store/authStore';
import { useCompletedRouteRefresh } from '@shared/hooks/useCompletedRouteRefresh';
import { toReactNativeRouteSections, toReactNativeSegments } from '@shared/utils/routeSegments';
import SpinrConfig from '@shared/config/spinr.config';
import { showToast } from '../store/toastStore';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

// held_for_review is the pre-charge GPS-spoof gate (backend/routes/rides/
// payments.py::process_payment) — distinct from a normal not-yet-charged
// "Pending" so the rider isn't left thinking a retry on their end would help.
const paymentStatusLabel = (status: string | undefined | null): string => {
  if (status === 'paid') return 'Paid';
  if (status === 'failed') return 'Failed';
  if (status === 'refunded') return 'Refund recorded';
  if (status === 'partially_refunded') return 'Partial refund recorded';
  if (status === 'held_for_review') return 'Under review';
  return 'Pending';
};

const _num = (n: any): number => {
  const v = typeof n === 'number' ? n : parseFloat(String(n ?? 0));
  return Number.isFinite(v) ? v : 0;
};

export default function RideDetailsScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [ride, setRide] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [emailSending, setEmailSending] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const mapRef = React.useRef<MapView>(null);
  const [routeMapReady, setRouteMapReady] = useState(false);

  const handleEmailReceipt = async () => {
    if (emailSending) return;
    setEmailSending(true);
    try {
      await api.post(`/rides/${rideId}/email-receipt`);
      const user = useAuthStore.getState().user;
      showToast('Receipt Sent', `Receipt emailed to ${user?.email || 'your registered email'}.`, 'success');
    } catch (e: any) {
      showToast('Email Not Sent', getApiErrorMessage(e, 'Could not send receipt email. Please try again.'), 'danger');
    } finally {
      setEmailSending(false);
    }
  };

  const handleDownloadInvoice = async () => {
    if (pdfBusy) return;
    setPdfBusy(true);
    // Native modules — dynamic import so an older build without them degrades
    // gracefully (caught below) instead of crashing at startup.
    let FS: typeof import('expo-file-system');
    let Sharing: typeof import('expo-sharing');
    try {
      FS = await import('expo-file-system');
      Sharing = await import('expo-sharing');
    } catch {
      showToast('PDF Unavailable', 'PDF export requires the latest app version. Please update the app and try again.', 'warning');
      setPdfBusy(false);
      return;
    }
    try {
      // Fetches the backend's one official receipt PDF (utils/receipt_pdf.py
      // via routes/rides/receipts.py::get_ride_receipt_pdf) instead of
      // rendering a second, independently-implemented HTML receipt on-device
      // (R9, docs/audit/ride-experience/ROADMAP.md).
      const token = await getAuthHeader();
      const file = await FS.File.downloadFileAsync(
        `${SpinrConfig.backendUrl}/api/v1/rides/${rideId}/receipt.pdf`,
        FS.Paths.cache,
        { headers: token ? { Authorization: `Bearer ${token}` } : {}, idempotent: true },
      );
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(file.uri, { mimeType: 'application/pdf', dialogTitle: 'Spinr ride receipt' });
      } else {
        showToast('Saved', 'Receipt PDF downloaded.', 'success');
      }
    } catch (e: any) {
      showToast('Download Failed', getApiErrorMessage(e, 'Could not download the receipt. Please check your connection and try again.'), 'danger');
    } finally {
      setPdfBusy(false);
    }
  };

  // Wrapped in useCallback([rideId]) so it's stable both for this file's
  // own effect below and for useCompletedRouteRefresh (which stores it in a
  // ref, so this wasn't required there — but keeping one definition stable
  // avoids two different staleness stories for the same function).
  const fetchRide = useCallback(async () => {
    try {
      const res = await api.get(`/rides/${rideId}`);
      setRide(res.data);
    } catch { }
    finally { setLoading(false); }
  }, [rideId]);

  useEffect(() => {
    // Refetches only when rideId changes (fetchRide is itself keyed on
    // [rideId], so depending on it is equivalent); the state fetchRide sets
    // isn't in this effect's own deps.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (rideId) fetchRide();
  }, [rideId, fetchRide]);

  useCompletedRouteRefresh(ride, fetchRide);

  const normalizedBreakdown = useMemo(() => {
    const raw: any[] = ride?.fare_breakdown || [];

    // Consolidate old separate fare lines into a single "Ride fare" line
    const fareLines = raw.filter((l: any) => l.type === 'fare');
    let lines: any[];
    if (fareLines.length <= 1) {
      lines = raw.map((l: any) => l.type === 'fare' ? { ...l, type: 'ride' } : l);
    } else {
      const rideTotal = fareLines.reduce((sum: number, l: any) => sum + parseFloat(String(l.amount || 0)), 0);
      const distKm = ride?.distance_km ? `${parseFloat(ride.distance_km).toFixed(1)} km` : '';
      const rideLine = { label: `Ride fare${distKm ? ` (${distKm})` : ''}`, amount: rideTotal.toFixed(2), type: 'ride' };
      lines = [rideLine, ...raw.filter((l: any) => l.type !== 'fare')];
    }

    // Inject promo discount if ride has one but breakdown doesn't
    const hasDiscount = lines.some((l: any) => l.type === 'discount');
    if (!hasDiscount && ride?.discount_amount && parseFloat(ride.discount_amount) > 0) {
      const promoLabel = ride.promo_code ? `Promo (${ride.promo_code})` : 'Promo discount';
      lines.push({ label: promoLabel, amount: -parseFloat(ride.discount_amount), type: 'discount' });
    }

    // Inject tip if ride has one but breakdown doesn't
    const hasTip = lines.some((l: any) => l.type === 'tip');
    if (!hasTip && ride?.tip_amount && parseFloat(ride.tip_amount) > 0) {
      lines.push({ label: 'Tip', amount: ride.tip_amount, type: 'tip' });
    }

    return lines;
  }, [ride]);

  // Cancelled rides are billed a flat cancellation fee, never the full trip
  // fare — the original fare_breakdown reflects the booking-time estimate,
  // not what was actually charged, so it must never be shown for a cancelled
  // ride (it reads as "you paid for a ride you never took").
  const cancellationFeeTotal = useMemo(
    () => _num(ride?.cancellation_fee_admin) + _num(ride?.cancellation_fee_driver),
    [ride]
  );

  const allSections = useMemo(() => toReactNativeRouteSections(ride?.actual_route_segments), [ride?.actual_route_segments]);
  // The trip (P3) line and the optional pickup leg (P2 — only arrives when
  // the server's rider_show_pickup_leg_enabled flag is on) render separately:
  // the pickup leg is dashed context, never joined into the trip gradient.
  const actualSections = useMemo(
    () => allSections.filter((s) => !s.phase || s.phase === 'trip_in_progress'),
    [allSections],
  );
  const pickupLegSections = useMemo(
    () => allSections.filter((s) => s.phase === 'navigating_to_pickup' || s.phase === 'arrived_at_pickup'),
    [allSections],
  );
  const plannedSegments = useMemo(() => {
    // Read the field once so the compiler's inferred dependency matches the
    // declared one exactly (react-hooks/preserve-manual-memoization): the
    // previous two-site read — one optional-chained (`ride?.…`), one not
    // (`ride.…`) — made the inferred dependency the coarser `ride` object
    // instead of the declared `.planned_route_polyline` field, so manual
    // memoization silently wasn't being preserved.
    const polyline = ride?.planned_route_polyline;
    return toReactNativeSegments(polyline ? [polyline] : []);
  }, [ride?.planned_route_polyline]);
  const isV2Route = _num(ride?.route_schema_version) >= 2;
  const hasActualRoute = actualSections.length > 0;
  // Booked-route dashed underlay: whenever the v2 pipeline could not deliver a
  // complete actual route (still processing, finalized incomplete, or no
  // drawable sections at all — e.g. GPS capture died mid-trip), the booked
  // route draws as dashed grey context so the map never shows a missing path.
  // Dashed = context, solid = actual GPS evidence; the pill stays honest.
  const showPlannedUnderlay =
    isV2Route && plannedSegments.length > 0 && (!hasActualRoute || ride?.route_geometry_status !== 'complete');
  const mapCoordinates = useMemo(
    () => [
      ...(hasActualRoute
        ? actualSections.reduce(
            (coordinates, section) => coordinates.concat(section.coordinates),
            [] as { latitude: number; longitude: number }[],
          )
        : []),
      // Legacy rides keep their planned solid line; v2 rides include booked
      // coords only while the dashed underlay is on screen, so the camera
      // frames it alongside any fragments.
      ...((showPlannedUnderlay || (!isV2Route && !hasActualRoute)) ? plannedSegments : []).reduce(
        (coordinates, segment) => coordinates.concat(segment),
        [] as { latitude: number; longitude: number }[],
      ),
    ],
    [actualSections, hasActualRoute, isV2Route, plannedSegments, showPlannedUnderlay],
  );

  useEffect(() => {
    if (!routeMapReady || mapCoordinates.length < 2) return;
    mapRef.current?.fitToCoordinates(mapCoordinates, {
      edgePadding: { top: 30, right: 30, bottom: 30, left: 30 },
      animated: false,
    });
  }, [routeMapReady, mapCoordinates]);

  const formatDate = (d: string) => {
    try {
      const date = new Date(d);
      return date.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
        + ' at ' + date.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' });
    } catch { return d; }
  };

  if (loading) {
    return (
      <SafeAreaView style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  if (!ride) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Ride Details</Text>
          <View style={{ width: 44 }} />
        </View>
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
          <Text style={{ color: colors.textDim, fontSize: FONT.bodyLg }}>Ride not found</Text>
        </View>
      </SafeAreaView>
    );
  }

  const isCompleted = ride.status === 'completed';
  const isCancelled = ride.status === 'cancelled';
  const cancellationMessages = cancellationPaymentMessages(ride);
  // Backend-computed, flag-gated (app_settings.legacy_ride_badge_enabled) —
  // never derive this from legacy_import_metadata directly, so the dark-ship
  // flag stays the single source of truth for this UI.
  const isImported = !!ride.show_legacy_badge;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Ride Details</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {/* Status Badge */}
        <View style={[styles.statusBadge, { backgroundColor: isCompleted ? colors.successBg : isCancelled ? colors.dangerBg : '#FEF3C7' }]}>
          <Ionicons
            name={isCompleted ? 'checkmark-circle' : isCancelled ? 'close-circle' : 'time'}
            size={18}
            color={isCompleted ? colors.success : isCancelled ? colors.error : colors.warning}
          />
          <Text style={[styles.statusText, { color: isCompleted ? '#065F46' : isCancelled ? '#991B1B' : '#92400E' }]}>
            {isCompleted ? 'Completed' : isCancelled ? 'Cancelled' : ride.status}
          </Text>
          <Text style={styles.statusDate}>{formatDate(ride.created_at)}</Text>
        </View>

        {/* Imported from the previous app (legacy migration) — honesty layer,
            mirrors admin-dashboard's ride-detail-modal.tsx badge. Never shown
            unless the backend flag is on AND this specific ride is a real
            legacy import. */}
        {isImported && (
          <View style={styles.importedBadge}>
            <Ionicons name="archive-outline" size={12} color={colors.textDim} />
            <Text style={styles.importedBadgeText}>Imported</Text>
          </View>
        )}

        {/* V2 actual geometry is segmented. Planned geometry is separately labelled. */}
        {ride.pickup_lat && ride.dropoff_lat && (
          <View style={styles.mapCard}>
            <MapView
              ref={mapRef}
              style={styles.map}
              provider={MAP_PROVIDER}
              scrollEnabled={false}
              zoomEnabled={false}
              rotateEnabled={false}
              userInterfaceStyle={isDark ? "dark" : "light"}
              initialRegion={{
                latitude: (ride.pickup_lat + ride.dropoff_lat) / 2,
                longitude: (ride.pickup_lng + ride.dropoff_lng) / 2,
                latitudeDelta: Math.abs(ride.pickup_lat - ride.dropoff_lat) * 2.5 + 0.01,
                longitudeDelta: Math.abs(ride.pickup_lng - ride.dropoff_lng) * 2.5 + 0.01,
              }}
              onMapReady={() => setRouteMapReady(true)}
            >
              {/* Booked-route dashed underlay (v2 only): drawn when the actual
                  route is missing or incomplete so the map never shows an
                  empty/fragmented path — dashed grey context, never a
                  substitute solid line. */}
              {showPlannedUnderlay && plannedSegments.map((segment, index) => (
                <Polyline
                  key={`planned-underlay-${index}`}
                  coordinates={segment}
                  strokeColor="#9CA3AF"
                  strokeWidth={3}
                  lineDashPattern={[6, 6]}
                  lineCap="round"
                />
              ))}
              {/* Pickup leg (P2) as dashed grey context UNDER the trip line —
                  present only when the server flag sends non-trip phases. */}
              {pickupLegSections.map((s) => (
                <Polyline
                  key={`pickup-leg-${s.id}`}
                  coordinates={s.coordinates}
                  strokeColor="#9CA3AF"
                  strokeWidth={3}
                  lineDashPattern={[6, 6]}
                  lineCap="round"
                />
              ))}
              {/* v2 sections passed SEPARATELY (paths) so a GPS gap is never
                  bridged by a false chord; legacy planned polyline is one path. */}
              {hasActualRoute ? (
                <RouteLine paths={actualSections.map((s) => s.coordinates)} />
              ) : isV2Route ? null : (
                <RouteLine path={mapCoordinates} />
              )}
              <RoutePins
                pickup={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }}
                dropoff={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }}
                completion={ride.actual_completion_point || null}
              />
            </MapView>
          </View>
        )}
        {isCompleted && isImported && (
          <Text style={styles.routeQualityText}>
            Imported from the previous app — no GPS was recorded for this ride
          </Text>
        )}

        {/* Route Details */}
        <View style={styles.routeCard}>
          <View style={styles.routeRow}>
            <View style={styles.routeDots}>
              <View style={[styles.dot, { backgroundColor: colors.success }]} />
              <View style={styles.routeLine} />
              <View style={[styles.dot, { backgroundColor: colors.primary }]} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.routeLabel}>PICKUP</Text>
              <Text style={styles.routeAddr} numberOfLines={2}>{ride.pickup_address}</Text>
              <View style={{ height: 16 }} />
              <Text style={styles.routeLabel}>DROPOFF</Text>
              <Text style={styles.routeAddr} numberOfLines={2}>{ride.dropoff_address}</Text>
            </View>
          </View>
        </View>

        {isCancelled && cancellationMessages.length > 0 && (
          <View style={styles.fareBreakdownCard}>
            <Text style={styles.fareBreakdownTitle}>Cancellation payment</Text>
            {cancellationMessages.map(message => <Text key={message} style={styles.paymentText}>{message}</Text>)}
          </View>
        )}

        {/* Fare breakdown — same layout as ride-options. Cancelled rides never
            took a trip, so they show the flat cancellation fee instead of the
            booking-time fare estimate. */}
        {isCancelled ? (
          cancellationFeeTotal > 0 && (
            <View style={styles.fareBreakdownCard}>
              <Text style={styles.fareBreakdownTitle}>Cancellation fee</Text>
              <View style={[styles.fareBreakdownRow, styles.fareBreakdownTotal]}>
                <Text style={styles.fareBreakdownTotalLabel}>Fee</Text>
                <Text style={styles.fareBreakdownTotalValue}>${cancellationFeeTotal.toFixed(2)}</Text>
              </View>
              <View style={styles.paymentRow}>
                <Ionicons
                  name={ride.payment_method === 'wallet' ? 'wallet' : ride.payment_method === 'company_allowance' ? 'business' : 'card'}
                  size={14}
                  color={ride.payment_status === 'paid' ? colors.success : colors.textDim}
                />
                <Text style={[styles.paymentText, ride.payment_status === 'paid' && { color: colors.success }]}>
                  {ride.payment_method === 'wallet'
                    ? 'Spinr Wallet'
                    : ride.payment_method === 'company_allowance'
                      ? 'Company Account'
                      : ride.card_last4 ? `Card •••• ${ride.card_last4}` : 'Card'}
                  {' · '}
                  {paymentStatusLabel(ride.payment_status)}
                </Text>
              </View>
            </View>
          )
        ) : (
          normalizedBreakdown.length > 0 && (
            <View style={styles.fareBreakdownCard}>
              <Text style={styles.fareBreakdownTitle}>Fare breakdown</Text>
              {normalizedBreakdown.map((line: any, i: number) => (
                line.amount != null ? (
                  <View key={i} style={[styles.fareBreakdownRow, line.type === 'ride' && { alignItems: 'flex-start' }]}>
                    {line.type === 'ride' ? (
                      <View style={{ flex: 1 }}>
                        <Text style={styles.fareBreakdownLabel}>{line.label}</Text>
                        <Text style={styles.fareBreakdownDriverBadge}>100% goes to your driver · ride local, support local</Text>
                      </View>
                    ) : line.type === 'discount' ? (
                      <Text style={[styles.fareBreakdownLabel, { color: colors.success }]}>{line.label}</Text>
                    ) : line.type === 'tip' ? (
                      <Text style={[styles.fareBreakdownLabel, { color: colors.info }]}>{line.label}</Text>
                    ) : line.type === 'tax' ? (
                      <Text style={[styles.fareBreakdownLabel, { color: colors.textSecondary }]}>{line.label}</Text>
                    ) : (
                      <Text style={styles.fareBreakdownLabel}>{line.label}</Text>
                    )}
                    <Text style={[
                      styles.fareBreakdownValue,
                      line.type === 'discount' && { color: colors.success },
                      line.type === 'tip' && { color: colors.info },
                    ]}>
                      {line.type === 'discount'
                        ? `-$${Math.abs(parseFloat(String(line.amount))).toFixed(2)}`
                        : `$${parseFloat(String(line.amount)).toFixed(2)}`}
                    </Text>
                  </View>
                ) : line.type === 'modifier' ? (
                  <View key={i} style={[styles.fareBreakdownRow, { gap: SPACING.xs }]}>
                    <Ionicons name="flash" size={12} color={colors.warning} />
                    <Text style={[styles.fareBreakdownLabel, { color: colors.warning }]}>{line.label}</Text>
                  </View>
                ) : null
              ))}
              <View style={[styles.fareBreakdownRow, styles.fareBreakdownTotal]}>
                <Text style={styles.fareBreakdownTotalLabel}>You paid</Text>
                <Text style={styles.fareBreakdownTotalValue}>${normalizedBreakdown.reduce((sum: number, l: any) => l.amount != null ? sum + parseFloat(String(l.amount)) : sum, 0).toFixed(2)}</Text>
              </View>
              <View style={styles.paymentRow}>
                <Ionicons
                  name={ride.payment_method === 'wallet' ? 'wallet' : ride.payment_method === 'company_allowance' ? 'business' : 'card'}
                  size={14}
                  color={ride.payment_status === 'paid' ? colors.success : colors.textDim}
                />
                <Text style={[styles.paymentText, ride.payment_status === 'paid' && { color: colors.success }]}>
                  {ride.payment_method === 'wallet'
                    ? 'Spinr Wallet'
                    : ride.payment_method === 'company_allowance'
                      ? 'Company Account'
                      : ride.card_last4 ? `Card •••• ${ride.card_last4}` : 'Card'}
                  {' · '}
                  {paymentStatusLabel(ride.payment_status)}
                </Text>
              </View>
            </View>
          )
        )}

        {/* Trip Stats — actual GPS-tracked values from the API, with the
            booking-time estimate as fallback for legacy rides that lack
            phase_durations data. */}
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <Ionicons name="speedometer-outline" size={22} color={colors.textDim} />
            <Text style={styles.statVal}>
              {(ride.actual_distance_km ?? ride.distance_km ?? 0).toFixed(1)} km
            </Text>
            {/* The tile shows the GPS-measured trip distance; the receipt line
                shows the billed distance. Label the measured one explicitly so
                the two can never read as a contradiction under fare-lock. */}
            <Text style={styles.statLabel}>{ride.actual_distance_km != null ? 'Distance (GPS)' : 'Distance'}</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="time-outline" size={22} color={colors.textDim} />
            <Text style={styles.statVal}>
              {ride.actual_duration_minutes ?? ride.duration_minutes ?? 0} min
            </Text>
            <Text style={styles.statLabel}>Duration</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="star" size={22} color="#FFB800" />
            <Text style={styles.statVal}>{ride.rider_rating || '—'}</Text>
            <Text style={styles.statLabel}>Your Rating</Text>
          </View>
        </View>

        {/* Receipt actions (completed rides only) */}
        {isCompleted && (
          <TouchableOpacity style={styles.actionBtn} onPress={handleEmailReceipt} disabled={emailSending}>
            <Ionicons name="mail-outline" size={20} color={colors.primary} />
            <Text style={styles.actionText}>{emailSending ? 'Sending…' : 'Email receipt'}</Text>
            {emailSending
              ? <ActivityIndicator size="small" color={colors.primary} />
              : <Ionicons name="chevron-forward" size={16} color={colors.border} />}
          </TouchableOpacity>
        )}
        {isCompleted && (
          <TouchableOpacity style={styles.actionBtn} onPress={handleDownloadInvoice} disabled={pdfBusy}>
            <Ionicons name="download-outline" size={20} color={colors.primary} />
            <Text style={styles.actionText}>{pdfBusy ? 'Preparing…' : 'Download invoice (PDF)'}</Text>
            {pdfBusy
              ? <ActivityIndicator size="small" color={colors.primary} />
              : <Ionicons name="chevron-forward" size={16} color={colors.border} />}
          </TouchableOpacity>
        )}

        {/* Help */}
        <TouchableOpacity style={styles.helpBtn} onPress={() => router.push('/support' as any)}>
          <Ionicons name="help-circle-outline" size={20} color={colors.primary} />
          <Text style={styles.helpText}>Get help with this ride</Text>
          <Ionicons name="chevron-forward" size={16} color={colors.border} />
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: SPACING.md, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    content: { padding: 20, paddingBottom: 40 },

    statusBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingHorizontal: SPACING.md, paddingVertical: 12, borderRadius: 14, marginBottom: SPACING.md,
    },
    statusText: { fontSize: FONT.bodyMd, fontWeight: '700' },
    statusDate: { flex: 1, fontSize: 12, color: colors.textDim, textAlign: 'right' },

    importedBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start',
      backgroundColor: colors.surfaceLight, paddingHorizontal: 10, paddingVertical: SPACING.xs,
      borderRadius: 10, marginBottom: SPACING.md,
    },
    importedBadgeText: { fontSize: FONT.label, fontWeight: '600', color: colors.textDim },

        mapCard: { height: 180, borderRadius: 18, overflow: 'hidden', marginBottom: SPACING.md, backgroundColor: colors.border },
        map: { flex: 1 },
        routeQualityText: { color: colors.textDim, fontSize: 12, marginTop: -10, marginBottom: SPACING.md },

    routeCard: { backgroundColor: colors.surfaceLight, borderRadius: 18, padding: SPACING.md, marginBottom: SPACING.md },
    routeRow: { flexDirection: 'row' },
    routeDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
    dot: { width: 10, height: 10, borderRadius: 5 },
    routeLine: { width: 2, flex: 1, backgroundColor: colors.border, marginVertical: 3 },
    routeLabel: { fontSize: 10, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5, marginBottom: 2 },
    routeAddr: { fontSize: 14, fontWeight: '500', color: colors.text },

    fareBreakdownCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14,
      marginBottom: SPACING.md, borderWidth: 1, borderColor: colors.border,
    },
    fareBreakdownTitle: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim, marginBottom: SPACING.sm,
      textTransform: 'uppercase', letterSpacing: 0.5,
    },
    fareBreakdownRow: {
      flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3,
    },
    fareBreakdownLabel: {
      fontSize: FONT.bodySm, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text,
    },
    fareBreakdownValue: {
      fontSize: FONT.bodySm, fontFamily: 'PlusJakartaSans_500Medium', color: colors.text,
    },
    fareBreakdownDriverBadge: {
      fontSize: 10, fontFamily: 'PlusJakartaSans_500Medium', color: colors.success, marginTop: 2,
    },
    fareBreakdownTotal: {
      borderTopWidth: 1, borderTopColor: colors.border, marginTop: 4, paddingTop: 6,
    },
    fareBreakdownTotalLabel: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    fareBreakdownTotalValue: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary,
    },
    paymentRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10 },
    paymentText: { fontSize: FONT.bodySm, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim },

    statsRow: { flexDirection: 'row', gap: 10, marginBottom: SPACING.md },
    statCard: { flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14, alignItems: 'center' },
    statVal: { fontSize: 18, fontWeight: '700', color: colors.text, marginTop: 6 },
    statLabel: { fontSize: FONT.label, color: colors.textDim, marginTop: 2 },

    helpBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: SPACING.md,
    },
    helpText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.primary },
    actionBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: SPACING.md, marginBottom: 12,
    },
    actionText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.text },
  });
}
