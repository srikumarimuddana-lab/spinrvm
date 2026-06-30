import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Platform, Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAuthStore } from '@shared/store/authStore';
import { showToast } from '../store/toastStore';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

function decodePolyline(encoded: string): { latitude: number; longitude: number }[] {
  const points: { latitude: number; longitude: number }[] = [];
  let index = 0, lat = 0, lng = 0;
  while (index < encoded.length) {
    let b: number, shift = 0, result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lng += (result & 1) ? ~(result >> 1) : (result >> 1);
    points.push({ latitude: lat / 1e5, longitude: lng / 1e5 });
  }
  return points;
}

const _num = (n: any): number => {
  const v = typeof n === 'number' ? n : parseFloat(String(n ?? 0));
  return Number.isFinite(v) ? v : 0;
};
const _money = (n: any): string => _num(n).toFixed(2);

// Branded HTML receipt for the rider's own ride, rendered to PDF via expo-print.
// PIPEDA: rider's own data only; driver block is name + vehicle (NO phone/plate).
function buildReceiptHtml(ride: any): string {
  const code = ride?.ride_code || String(ride?.id || '').slice(0, 8).toUpperCase() || '—';
  const dateRaw = ride?.ride_completed_at || ride?.created_at;
  const date = dateRaw ? new Date(dateRaw).toLocaleString() : '';
  const driverName =
    ride?.driver_name ||
    `${ride?.driver?.first_name || ''} ${ride?.driver?.last_name || ''}`.trim();
  const vehicle = ride?.driver_vehicle || '';
  const driverCode = ride?.driver_code || ride?.driver?.driver_code || '';
  const driverSub = [driverCode, vehicle].filter(Boolean).join(' · ') || 'Your driver';

  const line = (l: string, a: string) =>
    `<tr><td style="color:#666;padding:4px 0">${l}</td><td style="text-align:right;color:#1a1a1a">${a}</td></tr>`;
  const rows: string[] = [
    line('Base fare', '$' + _money(ride?.base_fare)),
    line(`Distance (${_num(ride?.distance_km).toFixed(1)} km)`, '$' + _money(ride?.distance_fare)),
    line(`Time (${_num(ride?.duration_minutes).toFixed(0)} min)`, '$' + _money(ride?.time_fare)),
  ];
  if (_num(ride?.booking_fee) > 0) rows.push(line('Booking fee', '$' + _money(ride?.booking_fee)));
  rows.push('<tr><td colspan="2" style="border-top:1px dashed #eee"></td></tr>');
  rows.push(line('Subtotal', '$' + _money(ride?.total_fare)));

  // GST/PST as separate line items from the persisted breakdown (SK regulatory);
  // fall back to the grand_total gap so the lines reconcile to what was charged.
  const tb = ride?.tax_breakdown && typeof ride.tax_breakdown === 'object' ? ride.tax_breakdown : {};
  let hadTax = false;
  for (const [label, payload] of Object.entries(tb)) {
    const amt = _num((payload as any)?.amount);
    const rate = _num((payload as any)?.rate);
    if (amt === 0) continue;
    hadTax = true;
    rows.push(line(`${label}${rate ? ` (${rate.toFixed(0)}%)` : ''}`, '$' + _money(amt)));
  }
  if (!hadTax) {
    const gap = _num(ride?.grand_total) - _num(ride?.total_fare);
    if (gap > 0.005) rows.push(line('Tax', '$' + _money(gap)));
  }
  if (_num(ride?.tip_amount) > 0) rows.push(line('Tip', '$' + _money(ride?.tip_amount)));

  const grand = _num(ride?.grand_total) + _num(ride?.tip_amount);
  const driverBlock = driverName
    ? `<tr><td style="padding:0 24px 16px"><table width="100%" style="background:#f9f9f9;border-radius:12px"><tr><td style="padding:12px 14px">
       <p style="margin:0;font-size:13px;font-weight:600;color:#1a1a1a">${driverName}</p>
       <p style="margin:2px 0 0;font-size:12px;color:#999">${driverSub}</p></td></tr></table></td></tr>`
    : '';

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
  <body style="margin:0;background:#f5f5f5;font-family:-apple-system,Roboto,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:0 auto;background:#fff">
    <tr><td style="background:#ee2b2b;padding:24px;text-align:center">
      <h1 style="color:#fff;margin:0;font-size:26px;font-weight:800">Spinr</h1>
      <p style="color:rgba(255,255,255,.85);margin:4px 0 0;font-size:13px">Ride Receipt</p></td></tr>
    <tr><td style="padding:20px 24px 0;text-align:center">
      <p style="color:#ee2b2b;font-size:34px;font-weight:800;margin:0">$${_money(grand)} CAD</p>
      <p style="color:#999;font-size:12px;margin:4px 0 0">${date}</p>
      <p style="color:#999;font-size:11px;margin:6px 0 0">Ride <strong style="color:#1a1a1a">${code}</strong></p></td></tr>
    <tr><td style="padding:16px 24px"><table width="100%" style="background:#f9f9f9;border-radius:12px"><tr><td style="padding:14px">
      <p style="color:#999;font-size:10px;margin:0;text-transform:uppercase">Pickup</p>
      <p style="color:#1a1a1a;font-size:13px;margin:2px 0 12px">${ride?.pickup_address || '—'}</p>
      <p style="color:#999;font-size:10px;margin:0;text-transform:uppercase">Dropoff</p>
      <p style="color:#1a1a1a;font-size:13px;margin:2px 0 0">${ride?.dropoff_address || '—'}</p></td></tr></table></td></tr>
    <tr><td style="padding:0 24px 12px"><table width="100%" style="font-size:13px">${rows.join('')}
      <tr><td colspan="2" style="border-top:1px solid #eee"></td></tr>
      <tr><td style="padding:8px 0;font-weight:700;font-size:15px">Total</td>
      <td style="text-align:right;color:#ee2b2b;font-weight:800;font-size:17px">$${_money(grand)}</td></tr></table></td></tr>
    ${driverBlock}
    <tr><td style="padding:12px 24px 24px;text-align:center;border-top:1px solid #f0f0f0">
      <p style="color:#bbb;font-size:11px;margin:0">Spinr Technologies Inc. · Saskatoon, SK</p>
      <p style="color:#bbb;font-size:11px;margin:3px 0 0">support@spinr.ca · www.spinr.ca</p></td></tr>
  </table></body></html>`;
}

export default function RideDetailsScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [ride, setRide] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [fallbackCoords, setFallbackCoords] = useState<any[]>([]);
  const [emailSending, setEmailSending] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const mapRef = React.useRef<MapView>(null);

  const handleEmailReceipt = async () => {
    if (emailSending) return;
    setEmailSending(true);
    try {
      await api.post(`/rides/${rideId}/email-receipt`);
      const user = useAuthStore.getState().user;
      showToast('Receipt Sent', `Receipt emailed to ${user?.email || 'your registered email'}.`, 'success');
    } catch (e: any) {
      showToast('Email Not Sent', 'Could not send receipt email. Please try again.', 'danger');
    } finally {
      setEmailSending(false);
    }
  };

  const handleDownloadInvoice = async () => {
    if (pdfBusy) return;
    setPdfBusy(true);
    try {
      // Native modules — dynamic import so an older build without them degrades
      // gracefully (caught below) instead of crashing at startup.
      const Print = await import('expo-print');
      const Sharing = await import('expo-sharing');
      const { uri } = await Print.printToFileAsync({ html: buildReceiptHtml(ride) });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, { mimeType: 'application/pdf', dialogTitle: 'Spinr ride receipt' });
      } else {
        showToast('Saved', 'Receipt PDF generated.', 'success');
      }
    } catch (e: any) {
      showToast('PDF Unavailable', 'PDF export requires the latest app version. Please update the app and try again.', 'warning');
    } finally {
      setPdfBusy(false);
    }
  };

  useEffect(() => {
    if (rideId) fetchRide();
  }, [rideId]);

  const fetchRide = async () => {
    try {
      const res = await api.get(`/rides/${rideId}`);
      setRide(res.data);
    } catch { }
    finally { setLoading(false); }
  };

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

  const savedPolyline = useMemo(() => {
    if (!ride) return [];
    const raw = (Array.isArray(ride.route_polyline) && ride.route_polyline.length >= 2)
      ? ride.route_polyline
      : ride.planned_route_polyline;
    if (!Array.isArray(raw) || raw.length < 2) return [];
    return raw
      .filter((p: any) => Array.isArray(p) && p.length >= 2)
      .map((p: any) => ({ latitude: p[0], longitude: p[1] }));
  }, [ride]);

  // Fetch Google Directions route when no stored polyline is available and the
  // ride has no pre-rendered snapshot (snapshot branch renders an Image — the
  // MapView never mounts so fetching here would be a wasted paid API call).
  // Placed after savedPolyline so the dep array can reference it without TDZ.
  useEffect(() => {
    if (
      savedPolyline.length >= 2 ||
      !ride?.pickup_lat || !ride?.dropoff_lat ||
      ride?.route_snapshot_url ||
      !GOOGLE_MAPS_API_KEY
    ) return;
    let cancelled = false;
    const fetchFallbackRoute = async () => {
      try {
        const origin = `${ride.pickup_lat},${ride.pickup_lng}`;
        const dest = `${ride.dropoff_lat},${ride.dropoff_lng}`;
        const url = `https://maps.googleapis.com/maps/api/directions/json?origin=${origin}&destination=${dest}&key=${GOOGLE_MAPS_API_KEY}`;
        const res = await fetch(url);
        const data = await res.json();
        if (data.status !== 'OK') return;
        const encoded = data?.routes?.[0]?.overview_polyline?.points;
        if (encoded && !cancelled) {
          const coords = decodePolyline(encoded);
          setFallbackCoords(coords);
          mapRef.current?.fitToCoordinates(coords, {
            edgePadding: { top: 30, right: 30, bottom: 30, left: 30 }, animated: false,
          });
        }
      } catch { }
    };
    fetchFallbackRoute();
    return () => { cancelled = true; };
  }, [savedPolyline.length, ride?.pickup_lat, ride?.pickup_lng, ride?.dropoff_lat, ride?.dropoff_lng, ride?.route_snapshot_url]);

  const polylineToRender = savedPolyline.length >= 2 ? savedPolyline : fallbackCoords;

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
          <Text style={{ color: colors.textDim, fontSize: 16 }}>Ride not found</Text>
        </View>
      </SafeAreaView>
    );
  }

  const isCompleted = ride.status === 'completed';
  const isCancelled = ride.status === 'cancelled';

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
        <View style={[styles.statusBadge, { backgroundColor: isCompleted ? '#ECFDF5' : isCancelled ? '#FEF2F2' : '#FEF3C7' }]}>
          <Ionicons
            name={isCompleted ? 'checkmark-circle' : isCancelled ? 'close-circle' : 'time'}
            size={18}
            color={isCompleted ? '#10B981' : isCancelled ? '#EF4444' : '#F59E0B'}
          />
          <Text style={[styles.statusText, { color: isCompleted ? '#065F46' : isCancelled ? '#991B1B' : '#92400E' }]}>
            {isCompleted ? 'Completed' : isCancelled ? 'Cancelled' : ride.status}
          </Text>
          <Text style={styles.statusDate}>{formatDate(ride.created_at)}</Text>
        </View>

        {/* Route Map — snapshot image first, live MapView as fallback */}
        {ride.pickup_lat && ride.dropoff_lat && (
          <View style={styles.mapCard}>
            {ride.route_snapshot_url ? (
              <Image
                source={{ uri: ride.route_snapshot_url }}
                style={styles.map}
                resizeMode="cover"
              />
            ) : (
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
                onMapReady={() => {
                  if (savedPolyline.length >= 2) {
                    mapRef.current?.fitToCoordinates(savedPolyline, {
                      edgePadding: { top: 30, right: 30, bottom: 30, left: 30 }, animated: false,
                    });
                  }
                }}
              >
                {polylineToRender.length > 1 && (() => {
                  const total = polylineToRender.length;
                  const SEGS = 15;
                  const chunk = Math.max(1, Math.floor(total / SEGS));
                  const segments: { coords: any[]; color: string }[] = [];
                  for (let i = 0; i < total - 1; i += chunk) {
                    const end = Math.min(i + chunk + 1, total);
                    const t = i / Math.max(total - 1, 1);
                    const r = Math.round(255 + (238 - 255) * t);
                    const g = Math.round(149 + (43 - 149) * t);
                    const b = Math.round(0 + (43 - 0) * t);
                    segments.push({ coords: polylineToRender.slice(i, end), color: `rgb(${r},${g},${b})` });
                  }
                  return segments.map((seg, idx) => (
                    <Polyline
                      key={`rd-seg-${idx}`}
                      coordinates={seg.coords}
                      strokeWidth={4}
                      strokeColor={seg.color}
                      lineCap="round"
                      lineJoin="round"
                    />
                  ));
                })()}
                <Marker coordinate={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                  <View style={[styles.pin, { backgroundColor: '#10B981' }]}>
                    <Ionicons name="location" size={14} color="#FFF" />
                  </View>
                </Marker>
                <Marker coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                  <View style={[styles.pin, { backgroundColor: '#EF4444' }]}>
                    <Ionicons name="flag" size={14} color="#FFF" />
                  </View>
                </Marker>
              </MapView>
            )}
          </View>
        )}

        {/* Route Details */}
        <View style={styles.routeCard}>
          <View style={styles.routeRow}>
            <View style={styles.routeDots}>
              <View style={[styles.dot, { backgroundColor: '#10B981' }]} />
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

        {/* Fare breakdown — same layout as ride-options. Cancelled rides never
            took a trip, so they show the flat cancellation fee instead of the
            booking-time fare estimate. */}
        {isCancelled ? (
          cancellationFeeTotal > 0 && (
            <View style={styles.fareBreakdownCard}>
              <Text style={styles.fareBreakdownTitle}>Cancellation fee</Text>
              <View style={[styles.fareBreakdownRow, styles.fareBreakdownTotal]}>
                <Text style={styles.fareBreakdownTotalLabel}>You paid</Text>
                <Text style={styles.fareBreakdownTotalValue}>${cancellationFeeTotal.toFixed(2)}</Text>
              </View>
              <View style={styles.paymentRow}>
                <Ionicons
                  name={ride.payment_method === 'wallet' ? 'wallet' : ride.payment_method === 'company_allowance' ? 'business' : 'card'}
                  size={14}
                  color={ride.payment_status === 'paid' ? '#10B981' : colors.textDim}
                />
                <Text style={[styles.paymentText, ride.payment_status === 'paid' && { color: '#10B981' }]}>
                  {ride.payment_method === 'wallet'
                    ? 'Spinr Wallet'
                    : ride.payment_method === 'company_allowance'
                      ? 'Company Account'
                      : ride.card_last4 ? `Card •••• ${ride.card_last4}` : 'Card'}
                  {' · '}
                  {ride.payment_status === 'paid' ? 'Paid' : ride.payment_status === 'failed' ? 'Failed' : 'Pending'}
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
                      <Text style={[styles.fareBreakdownLabel, { color: '#10B981' }]}>{line.label}</Text>
                    ) : line.type === 'tip' ? (
                      <Text style={[styles.fareBreakdownLabel, { color: '#3B82F6' }]}>{line.label}</Text>
                    ) : line.type === 'tax' ? (
                      <Text style={[styles.fareBreakdownLabel, { color: '#6B7280' }]}>{line.label}</Text>
                    ) : (
                      <Text style={styles.fareBreakdownLabel}>{line.label}</Text>
                    )}
                    <Text style={[
                      styles.fareBreakdownValue,
                      line.type === 'discount' && { color: '#10B981' },
                      line.type === 'tip' && { color: '#3B82F6' },
                    ]}>
                      {line.type === 'discount'
                        ? `-$${Math.abs(parseFloat(String(line.amount))).toFixed(2)}`
                        : `$${parseFloat(String(line.amount)).toFixed(2)}`}
                    </Text>
                  </View>
                ) : line.type === 'modifier' ? (
                  <View key={i} style={[styles.fareBreakdownRow, { gap: 4 }]}>
                    <Ionicons name="flash" size={12} color="#F59E0B" />
                    <Text style={[styles.fareBreakdownLabel, { color: '#F59E0B' }]}>{line.label}</Text>
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
                  color={ride.payment_status === 'paid' ? '#10B981' : colors.textDim}
                />
                <Text style={[styles.paymentText, ride.payment_status === 'paid' && { color: '#10B981' }]}>
                  {ride.payment_method === 'wallet'
                    ? 'Spinr Wallet'
                    : ride.payment_method === 'company_allowance'
                      ? 'Company Account'
                      : ride.card_last4 ? `Card •••• ${ride.card_last4}` : 'Card'}
                  {' · '}
                  {ride.payment_status === 'paid' ? 'Paid' : ride.payment_status === 'failed' ? 'Failed' : 'Pending'}
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
            <Text style={styles.statLabel}>Distance</Text>
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
      paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    content: { padding: 20, paddingBottom: 40 },

    statusBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingHorizontal: 16, paddingVertical: 12, borderRadius: 14, marginBottom: 16,
    },
    statusText: { fontSize: 15, fontWeight: '700' },
    statusDate: { flex: 1, fontSize: 12, color: colors.textDim, textAlign: 'right' },

    mapCard: { height: 180, borderRadius: 18, overflow: 'hidden', marginBottom: 16, backgroundColor: colors.border },
    map: { flex: 1 },
    pin: {
      width: 28, height: 28, borderRadius: 14, justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: '#FFF', elevation: 3,
    },

    routeCard: { backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 16, marginBottom: 16 },
    routeRow: { flexDirection: 'row' },
    routeDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
    dot: { width: 10, height: 10, borderRadius: 5 },
    routeLine: { width: 2, flex: 1, backgroundColor: colors.border, marginVertical: 3 },
    routeLabel: { fontSize: 10, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5, marginBottom: 2 },
    routeAddr: { fontSize: 14, fontWeight: '500', color: colors.text },

    fareBreakdownCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14,
      marginBottom: 16, borderWidth: 1, borderColor: colors.border,
    },
    fareBreakdownTitle: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim, marginBottom: 8,
      textTransform: 'uppercase', letterSpacing: 0.5,
    },
    fareBreakdownRow: {
      flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3,
    },
    fareBreakdownLabel: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text,
    },
    fareBreakdownValue: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: colors.text,
    },
    fareBreakdownDriverBadge: {
      fontSize: 10, fontFamily: 'PlusJakartaSans_500Medium', color: '#10B981', marginTop: 2,
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
    paymentText: { fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim },

    statsRow: { flexDirection: 'row', gap: 10, marginBottom: 16 },
    statCard: { flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14, alignItems: 'center' },
    statVal: { fontSize: 18, fontWeight: '700', color: colors.text, marginTop: 6 },
    statLabel: { fontSize: 11, color: colors.textDim, marginTop: 2 },

    helpBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 16,
    },
    helpText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.primary },
    actionBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 16, marginBottom: 12,
    },
    actionText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.text },
  });
}
