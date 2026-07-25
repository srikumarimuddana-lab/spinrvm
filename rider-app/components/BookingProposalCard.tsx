/**
 * In-chat booking confirmation card.
 *
 * Renders a booking_proposal action from the AI: loads the authoritative
 * estimate via rideStore.fetchEstimates() (surge shown before booking,
 * estimate_token captured), refreshes the quote every QUOTE_REFRESH_SECONDS,
 * and books ONLY when the rider taps Confirm — through the unmodified
 * rideStore.createRide() path (idempotency key, active-ride guard, 402
 * handling). Anything needing more input deep-links to the standard flow.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { BookingProposal } from '@shared/types/ai';
import { useRideStore } from '../store/rideStore';
import {
  QUOTE_REFRESH_SECONDS,
  displayFare,
  displayFareWithPromo,
  mapBookingError,
  paymentMethodForProposal,
  pickEstimate,
  postBookingRoute,
  priceChangeNotice,
  promoForProposal,
  scheduledDateForProposal,
  type BookingErrorDescriptor,
} from './bookingProposal';
import { grandTotalOf, promoDiscountForEstimate, ridePortionOf } from '../utils/promoDiscount';

type Phase = 'quoting' | 'ready' | 'booking' | 'booked' | 'error';

interface Props {
  proposal: BookingProposal;
}

export default function BookingProposalCard({ proposal }: Props) {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = createStyles(colors);

  const setPickup = useRideStore((s) => s.setPickup);
  const setDropoff = useRideStore((s) => s.setDropoff);
  const clearStops = useRideStore((s) => s.clearStops);
  const fetchEstimates = useRideStore((s) => s.fetchEstimates);
  const selectVehicle = useRideStore((s) => s.selectVehicle);
  const createRide = useRideStore((s) => s.createRide);
  const applyPromo = useRideStore((s) => s.applyPromo);
  const setScheduledTime = useRideStore((s) => s.setScheduledTime);
  const estimates = useRideStore((s) => s.estimates);
  const appliedPromo = useRideStore((s) => s.appliedPromo);
  const fetchAvailablePromos = useRideStore((s) => s.fetchAvailablePromos);

  const [phase, setPhase] = useState<Phase>('quoting');
  const [errorInfo, setErrorInfo] = useState<BookingErrorDescriptor | null>(null);
  const bookedRef = useRef(false);
  const paymentMethod = useMemo(() => paymentMethodForProposal(proposal), [proposal]);
  const scheduledDate = useMemo(() => scheduledDateForProposal(proposal), [proposal]);
  const proposedPromo = useMemo(() => promoForProposal(proposal), [proposal]);

  const loadQuote = useCallback(() => {
    // A leftover stop from an earlier manual search would silently price into
    // this chat booking — the proposal is always a two-point trip.
    clearStops();
    setPickup({ address: proposal.pickup_address, lat: proposal.pickup_lat, lng: proposal.pickup_lng });
    setDropoff({ address: proposal.dropoff_address, lat: proposal.dropoff_lat, lng: proposal.dropoff_lng });
    applyPromo(proposedPromo);
    setScheduledTime(scheduledDate);
    fetchEstimates();
  }, [proposal, clearStops, setPickup, setDropoff, applyPromo, proposedPromo, setScheduledTime, scheduledDate, fetchEstimates]);

  useEffect(() => {
    loadQuote();
  }, [loadQuote]);

  // Keep the quote (and its surge-locking estimate_token) fresh while the
  // card waits for a decision.
  useEffect(() => {
    if (phase === 'booked' || phase === 'booking') return undefined;
    const id = setInterval(() => fetchEstimates(), QUOTE_REFRESH_SECONDS * 1000);
    return () => clearInterval(id);
  }, [phase, fetchEstimates]);

  const estimate = pickEstimate(estimates, proposal);

  useEffect(() => {
    if (phase === 'quoting' && estimate) setPhase('ready');
  }, [phase, estimate]);

  // Load the REAL promo objects once an estimate is priced: the proposal only
  // carries a promo code, and fetchAvailablePromos re-validates it against
  // /promo/available (replacing the zero-discount placeholder applyPromo set
  // in loadQuote) so the card can display the post-promo total the quote
  // card promised. Re-fetched only when the vehicle or total changes.
  const lastPromoKey = useRef<string | null>(null);
  const [promosReady, setPromosReady] = useState(false);
  useEffect(() => {
    if (!estimate) return;
    const key = `${estimate.vehicle_type.id}:${grandTotalOf(estimate)}`;
    if (lastPromoKey.current === key) return;
    lastPromoKey.current = key;
    fetchAvailablePromos(grandTotalOf(estimate), ridePortionOf(estimate)).finally(() =>
      setPromosReady(true),
    );
  }, [estimate, fetchAvailablePromos]);

  // The proposed promo no longer applies (expired, wrong area, below the
  // fare minimum) — say so instead of showing a chip beside an undiscounted
  // total.
  const promoMissing = !!proposal.promo_code && appliedPromo?.code !== proposal.promo_code;
  const promoDiscount = estimate ? promoDiscountForEstimate(appliedPromo, estimate) : 0;

  // Never re-price silently: compare each displayed total against what the
  // rider last saw — the accepted quote's total first, then the previously
  // displayed total across the 60 s auto-refreshes. Comparison waits for the
  // promo fetch so a not-yet-applied discount doesn't fire a false notice.
  const displayedTotal = estimate ? displayFareWithPromo(estimate, appliedPromo) : null;
  const priceRef = useRef<string | null>(proposal.quoted_total ?? null);
  const [priceNotice, setPriceNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!displayedTotal || !promosReady) return;
    const notice = priceChangeNotice(priceRef.current, displayedTotal);
    if (notice) setPriceNotice(notice);
    priceRef.current = displayedTotal;
  }, [displayedTotal, promosReady]);

  const handleConfirm = useCallback(async () => {
    if (!estimate || bookedRef.current) return;
    // Card rides need an explicit card selection that can't be made from chat,
    // and the backend rejects a card ride with no pinned card. Hand off to the
    // standard booking screen (the quote is already loaded into the store) so
    // the rider can pick a card and book there. Wallet proposals book inline.
    if (paymentMethod === 'card') {
      selectVehicle(estimate.vehicle_type as never);
      applyPromo(proposedPromo);
      setScheduledTime(scheduledDate);
      router.push('/ride-options' as never);
      return;
    }
    setPhase('booking');
    try {
      selectVehicle(estimate.vehicle_type as never);
      applyPromo(proposedPromo);
      setScheduledTime(scheduledDate);
      const ride = await createRide(paymentMethod, undefined, undefined, undefined, {
        // The assistant only stamps this after the rider explicitly confirmed
        // a same-place trip in chat — don't make them confirm twice.
        allowSamePlace: !!proposal.same_location_confirmed,
      });
      if ('requires_action' in ride) {
        // A card needing on-device 3DS returns RideRequiresAction, which this
        // in-chat card can't complete — deep-link to the standard flow per the
        // contract in this file's header.
        setErrorInfo({
          message: 'This card needs extra authentication — finish booking on the payment screen.',
          link: { label: 'Open booking', href: '/(tabs)' },
        });
        setPhase('error');
        return;
      }
      bookedRef.current = true;
      setPhase('booked');
      const route = postBookingRoute(ride.id, scheduledDate);
      if (route) router.replace(route as never);
    } catch (error: unknown) {
      setErrorInfo(mapBookingError(error));
      setPhase('error');
    }
  }, [estimate, selectVehicle, applyPromo, proposedPromo, setScheduledTime, scheduledDate, createRide, paymentMethod, proposal, router]);

  return (
    <View style={styles.card}>
      <View style={styles.headerRow}>
        <Ionicons name="car-outline" size={18} color={colors.primary} />
        <Text style={styles.headerText}>Confirm your ride</Text>
      </View>

      <View style={styles.routeRow}>
        <Ionicons name="radio-button-on" size={13} color={colors.primary} />
        <Text style={styles.routeText} numberOfLines={1}>
          {proposal.pickup_address}
        </Text>
      </View>
      <View style={styles.routeRow}>
        <Ionicons name="location" size={13} color={colors.text} />
        <Text style={styles.routeText} numberOfLines={1}>
          {proposal.dropoff_address}
        </Text>
      </View>

      {phase === 'quoting' && (
        <View style={styles.quoteRow}>
          <ActivityIndicator size="small" color={colors.primary} />
          <Text style={styles.quoteHint}>Getting the exact fare…</Text>
        </View>
      )}

      {(phase === 'ready' || phase === 'booking') && estimate && (
        <>
          <View style={styles.fareRow}>
            <Text style={styles.vehicleName}>{estimate.vehicle_type.name}</Text>
            <View style={styles.fareAmounts}>
              {promoDiscount > 0 && <Text style={styles.fareStruck}>${displayFare(estimate)}</Text>}
              <Text style={styles.fareText}>${displayFareWithPromo(estimate, appliedPromo)}</Text>
            </View>
          </View>
          <View style={styles.preferenceGrid}>
            <View style={styles.preferencePill}>
              <Ionicons name="time-outline" size={12} color={colors.textDim} />
              <Text style={styles.preferenceText}>
                {scheduledDate
                  ? scheduledDate.toLocaleString('en-CA', {
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : 'Now'}
              </Text>
            </View>
            <View style={styles.preferencePill}>
              <Ionicons name={paymentMethod === 'wallet' ? 'wallet-outline' : 'card-outline'} size={12} color={colors.textDim} />
              <Text style={styles.preferenceText}>{paymentMethod === 'wallet' ? 'Wallet' : 'Card'}</Text>
            </View>
            {appliedPromo?.code && !promoMissing ? (
              <View style={styles.preferencePill}>
                <Ionicons name="pricetag-outline" size={12} color={colors.success} />
                <Text style={[styles.preferenceText, { color: colors.success }]}>Promo {appliedPromo.code}</Text>
              </View>
            ) : null}
          </View>
          {promoMissing && (
            <View style={styles.surgeBadge}>
              <Ionicons name="alert-circle-outline" size={12} color={colors.warning} />
              <Text style={styles.surgeText}>
                Promo {proposal.promo_code} is no longer available — total shown without it.
              </Text>
            </View>
          )}
          {(estimate.surge_multiplier ?? 1) > 1 && (
            <View style={styles.surgeBadge}>
              <Ionicons name="trending-up" size={12} color={colors.warning} />
              <Text style={styles.surgeText}>{estimate.surge_multiplier}x surge applies</Text>
            </View>
          )}
          {priceNotice && (
            <View style={styles.priceNoticeRow}>
              <Ionicons name="alert-circle-outline" size={13} color={colors.warning} />
              <Text style={styles.priceNoticeText}>{priceNotice}</Text>
            </View>
          )}
          <TouchableOpacity
            style={[styles.confirmButton, phase === 'booking' && styles.confirmDisabled]}
            onPress={handleConfirm}
            disabled={phase === 'booking'}
            accessibilityRole="button"
            accessibilityLabel="Confirm booking"
          >
            {phase === 'booking' ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <Text style={styles.confirmText}>Confirm — book this ride</Text>
            )}
          </TouchableOpacity>
          <Text style={styles.fineprint}>Total incl. fees & taxes. Quote refreshes automatically.</Text>
          {proposal.promo_code ? (
            <Text style={styles.fineprint}>Promo eligibility is confirmed when the ride is created.</Text>
          ) : null}
        </>
      )}

      {phase === 'quoting' && !estimate && null}

      {phase === 'ready' && !estimate && (
        <Text style={styles.quoteHint}>No vehicles available right now — try again shortly.</Text>
      )}

      {phase === 'booked' && (
        <View style={styles.bookedRow}>
          <Ionicons name="checkmark-circle" size={18} color={colors.success} />
          <Text style={styles.bookedText}>
            {scheduledDate ? 'Booked! Your ride is scheduled.' : 'Booked! Searching for your driver…'}
          </Text>
        </View>
      )}

      {phase === 'error' && errorInfo && (
        <View>
          <Text style={styles.errorText}>{errorInfo.message}</Text>
          {errorInfo.link && (
            <TouchableOpacity
              style={styles.linkButton}
              onPress={() => router.push(errorInfo.link!.href as never)}
            >
              <Text style={styles.linkButtonText}>{errorInfo.link.label}</Text>
            </TouchableOpacity>
          )}
        </View>
      )}

      {phase !== 'booked' && (
        <Text style={styles.aiDisclaimer}>
          Spinr AI can make mistakes — double-check the pickup address before you confirm.
        </Text>
      )}
    </View>
  );
}

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    card: {
      marginLeft: 34,
      padding: 14,
      borderRadius: 14,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      gap: 8,
      alignSelf: 'stretch',
    },
    headerRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    headerText: { fontSize: 15, fontWeight: '700', color: colors.text },
    routeRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    routeText: { flex: 1, fontSize: 13, color: colors.textDim },
    quoteRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 },
    quoteHint: { fontSize: 13, color: colors.textDim },
    fareRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingTop: 4,
    },
    vehicleName: { fontSize: 14, fontWeight: '600', color: colors.text },
    fareAmounts: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    fareStruck: {
      fontSize: 14,
      fontWeight: '600',
      color: colors.textDim,
      textDecorationLine: 'line-through',
    },
    fareText: { fontSize: 18, fontWeight: '700', color: colors.text },
    preferenceGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
    preferencePill: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      paddingHorizontal: 8,
      paddingVertical: 5,
      borderRadius: 999,
      backgroundColor: colors.surfaceLight,
    },
    preferenceText: { fontSize: 11, color: colors.textDim, fontWeight: '600' },
    surgeBadge: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    surgeText: { fontSize: 12, color: colors.warning, fontWeight: '600' },
    priceNoticeRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    priceNoticeText: { flex: 1, fontSize: 12, color: colors.warning, fontWeight: '600' },
    confirmButton: {
      backgroundColor: colors.primary,
      borderRadius: 10,
      paddingVertical: 12,
      alignItems: 'center',
      marginTop: 4,
    },
    confirmDisabled: { opacity: 0.6 },
    confirmText: { color: '#fff', fontSize: 15, fontWeight: '700' },
    fineprint: { fontSize: 11, color: colors.textDim, textAlign: 'center' },
    aiDisclaimer: {
      fontSize: 11,
      color: colors.textDim,
      textAlign: 'center',
      fontStyle: 'italic',
      marginTop: 2,
    },
    bookedRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 },
    bookedText: { fontSize: 14, fontWeight: '600', color: colors.success },
    errorText: { fontSize: 13, color: colors.error },
    linkButton: { paddingVertical: 8 },
    linkButtonText: { fontSize: 14, fontWeight: '600', color: colors.primary },
  });
