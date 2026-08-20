/**
 * Pure presentation model for the Lyft-style trip card drawn on the Android Auto
 * map surface (carSurface.tsx) and reused for the ride-offer alert (register.ts).
 *
 * Framework-free and side-effect-free so the whole card — every label the driver
 * reads at a glance in the car — is unit-testable without a head unit or the
 * native module. The surface turns this into a branded overlay; register turns
 * the `offer` variant into a NavigationAlert with Accept / Decline actions.
 *
 * Single source of truth: it reads the SAME `useDriverStore` shapes the phone
 * uses (ActiveRide for an engaged ride, the incoming-offer payload for a fresh
 * request) — it never fetches or derives anything the phone doesn't already have.
 */
import type { ActiveRide, RideState } from '../../store/driverStore';
import { carColors } from './carTheme';

// Brand tokens, not hand-picked hex. These were '#ee2b2b' (a muddier red than
// the brand's) and Google's '#0f9d58' green — close enough to look intentional,
// wrong enough that the head unit never quite matched the phone. carTheme.ts
// sources them from shared/theme's dark palette.
/** Spinr brand red — the card accent + status pill. */
export const SPINR_RED = carColors.brand;
const NEUTRAL = carColors.raised;
const GO = carColors.success;

/** Which trip leg the card represents — also picks the destination + accent. */
export type TripCardLeg = 'idle' | 'offer' | 'pickup' | 'dropoff' | 'complete';

/**
 * The minimal shape of a fresh dispatch offer the card reads. Kept structural
 * (not the store's private `IncomingRide`) so this module stays decoupled — the
 * surface passes `useDriverStore(s => s.incomingRide)` straight in.
 */
export interface OfferLike {
  ride_id: string;
  pickup_address?: string;
  dropoff_address?: string;
  /** Widened to match the phone's IncomingRide, which types it `string | number`. */
  fare?: string | number;
  distance_km?: number;
  duration_minutes?: number;
  rider_name?: string;
  rider_rating?: number;
  rider_profile_image?: string;
  requires_wav?: boolean;
  surge_multiplier?: number;
  total_bonus?: number;
  incentives?: { name: string; bonus_amount: number; incentive_type: string }[];
  quest_hint?: { title: string } | null;
  // Badge inputs the phone's RideOfferPanel already reads. Optional/falsy-default
  // so an offer payload from a backend that predates any of them renders exactly
  // as it does today (no badge) rather than throwing.
  quiet_mode?: boolean;
  is_scheduled?: boolean;
  payment_method?: string;
}

/** Everything the head-unit card renders. All fields pre-formatted for display. */
export interface TripCard {
  leg: TripCardLeg;
  /** Short state headline shown in the status pill, e.g. "Heading to pickup". */
  statusLabel: string;
  /** Pill / accent colour. */
  accent: string;
  /** Rider first name, or null when there is no engaged/offered rider. */
  riderName: string | null;
  /** Rider rating formatted to one decimal ("4.9"), or null. */
  riderRating: string | null;
  /** Rider profile photo URI, or null (the card falls back to an initial). */
  riderPhoto: string | null;
  /** Earnings bonus on this ride ("+$3.00 bonus"), or null. */
  bonusLabel: string | null;
  /** Active incentive / quest perk to surface, e.g. a quest title, or null. */
  perkLabel: string | null;
  /** The address the driver is currently headed to (pickup or dropoff). */
  destinationLabel: string | null;
  /** "Pick-up" / "Drop-off" caption for the destination row. */
  destinationCaption: string | null;
  /** Trip ETA ("8 min") — duration_minutes rounded, or null. */
  etaLabel: string | null;
  /** Trip distance ("3.2 km"), or null. */
  distanceLabel: string | null;
  /** Fare ("$14.50"), or null. */
  fareLabel: string | null;
  /**
   * What the driver actually banks: fare + bonus ("$17.50"), or null.
   *
   * THE headline number, and the reason this field exists separately from
   * `fareLabel`. The phone's RideOfferPanel leads with fare + bonus at 52px,
   * while the car surface and the offer alert both led with the bare fare — so
   * a bonused ride was advertised to the driver as WORTH LESS in the car than
   * on the phone, on the one screen where they decide whether to take it.
   */
  totalEarningsLabel: string | null;
  /** "$14.50 fare + $3.00 bonus" — only when a bonus makes the split matter. */
  fareBreakdownLabel: string | null;
  /** Rate the ride pays per km ("$4.60/km") — how drivers actually rank offers. */
  perKmLabel: string | null;
  /** Surge badge ("1.5×") when above 1.0, else null. */
  surgeLabel: string | null;
  /** Wheelchair-accessible vehicle requested. */
  wav: boolean;
  /** Rider asked for a quiet ride. */
  quietMode: boolean;
  /** Booked in advance rather than hailed now. */
  isScheduled: boolean;
  /** Rider is paying cash — the driver collects at drop-off. */
  cashPayment: boolean;
  /** Pickup address, kept alongside `dropoffLabel` so the offer can show BOTH. */
  pickupLabel: string | null;
  /** Drop-off address. */
  dropoffLabel: string | null;
  /** Secondary guidance line, e.g. the phone-only start-trip prompt. */
  hint: string | null;
  /**
   * Cumulative earnings context on the completed-trip card, e.g.
   * "Today · $187.40 · 12 rides". Null unless the store has an earnings
   * summary — it is fetched by the phone, so a car-only cold launch legitimately
   * has none and the card simply omits the line.
   */
  earningsTodayLabel: string | null;
}

const firstName = (full?: string): string | null => {
  const t = full?.trim();
  if (!t) return null;
  return t.split(/\s+/)[0];
};

const money = (v?: string | number | null): string | null => {
  const t = (v ?? '').toString().trim();
  return t.length > 0 ? `$${t}` : null;
};

/** Parse a money-ish value ("14.50", 14.5) to a number. Null when unusable. */
const amount = (v?: string | number | null): number | null => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  const t = (v ?? '').toString().trim();
  if (t.length === 0) return null;
  const n = Number.parseFloat(t);
  return Number.isFinite(n) ? n : null;
};

/**
 * Fare + bonus — what the driver banks on this ride.
 *
 * Deliberately the SAME plain-float arithmetic as the phone's RideOfferPanel
 * (`baseFare + totalBonus`). These two surfaces quote the same offer to the same
 * driver seconds apart, so they must agree to the cent; a "more correct" rounding
 * strategy here would be a worse bug than the imprecision it fixed. Both are
 * display-only — the authoritative money is computed backend-side in Decimal and
 * settled from there, never from either of these strings.
 */
export const totalEarnings = (
  fare?: string | number | null,
  bonus?: number | null,
): number | null => {
  const base = amount(fare);
  if (base === null) return null;
  const extra = typeof bonus === 'number' && Number.isFinite(bonus) && bonus > 0 ? bonus : 0;
  return base + extra;
};

/** "$17.50" — fare + bonus, the number the offer leads with. */
export const totalEarningsLabel = (
  fare?: string | number | null,
  bonus?: number | null,
): string | null => {
  const t = totalEarnings(fare, bonus);
  return t === null ? null : `$${t.toFixed(2)}`;
};

/**
 * "$14.50 fare + $3.00 bonus", or null when there is no bonus to explain.
 *
 * Without it the hero number silently disagrees with the fare the rider is
 * quoted, which reads as a bug rather than a bonus.
 */
export const fareBreakdownLabel = (
  fare?: string | number | null,
  bonus?: number | null,
): string | null => {
  const base = amount(fare);
  if (base === null) return null;
  if (typeof bonus !== 'number' || !Number.isFinite(bonus) || bonus <= 0) return null;
  return `$${base.toFixed(2)} fare + $${bonus.toFixed(2)} bonus`;
};

/** "$4.60/km" — total earnings over trip distance. Null without both. */
export const perKmLabel = (
  fare?: string | number | null,
  bonus?: number | null,
  distanceKm?: number | null,
): string | null => {
  const t = totalEarnings(fare, bonus);
  if (t === null) return null;
  if (typeof distanceKm !== 'number' || !Number.isFinite(distanceKm) || distanceKm <= 0) return null;
  return `$${(t / distanceKm).toFixed(2)}/km`;
};

const km = (v?: number | null): string | null =>
  typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(1)} km` : null;

const mins = (v?: number | null): string | null =>
  typeof v === 'number' && Number.isFinite(v) ? `${Math.max(1, Math.round(v))} min` : null;

const rating = (v?: number | null): string | null =>
  typeof v === 'number' && Number.isFinite(v) ? v.toFixed(1) : null;

/** Surge badge only above the 1.0 baseline; trims trailing zeros (1.50 → "1.5×"). */
export const surgeBadge = (v?: number | null): string | null => {
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 1.0001) return null;
  const s = v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return `${s}×`;
};

const photo = (uri?: string | null): string | null => {
  const t = uri?.trim();
  return t && t.length > 0 ? t : null;
};

/** Earnings bonus chip, only when there is a positive bonus on the ride. */
export const bonusLabel = (total?: number | null): string | null =>
  typeof total === 'number' && Number.isFinite(total) && total > 0
    ? `+$${total.toFixed(2)} bonus`
    : null;

/** The single most relevant perk: an active quest title, else the top incentive. */
export const perkLabel = (
  incentives?: { name: string }[] | null,
  quest?: { title?: string } | null,
): string | null => {
  const q = quest?.title?.trim();
  if (q) return q;
  const inc = incentives?.find((i) => i?.name?.trim())?.name?.trim();
  return inc || null;
};

/** The two strings the Android Auto ride-offer alert is built from. */
export interface OfferAlertText {
  title: string;
  /** Newline-separated; the NavigationAlert renders '\n' as line breaks. */
  subtitle: string | null;
}

/**
 * Title + subtitle for the ride-offer NavigationAlert.
 *
 * Lives here, next to the card it reads, rather than inline in register.ts —
 * register.ts imports the native module, so anything inline there is only
 * testable by re-implementing it in the test, and that copy silently drifts
 * from the code it claims to cover.
 *
 * THE MONEY LEADS THE TITLE. The title is the largest text Android Auto will
 * render for us, and it used to be spent on the words "New ride" — a label for
 * something the Accept/Decline buttons already make obvious — while the amount
 * sat below it in body text. And it is the TOTAL, not the bare fare: fare +
 * bonus is what the driver banks and what the phone quotes, and the two
 * surfaces must never advertise one ride at two different prices.
 */
export function buildOfferAlertText(card: TripCard): OfferAlertText {
  const headline = card.totalEarningsLabel ?? card.fareLabel;
  const who = card.riderName
    ? card.riderRating
      ? `${card.riderName} ★${card.riderRating}`
      : card.riderName
    : null;
  const title = headline
    ? [headline, who].filter(Boolean).join(' · ')
    : who
      ? `New ride · ${who}`
      : 'New ride request';

  // Line 1 explains the headline rather than repeating it: the fare/bonus split
  // (so a headline above the rider's fare reads as a bonus, not a bug) and the
  // per-km rate, which is how drivers rank one offer against another.
  const rateLine = [card.fareBreakdownLabel, card.perKmLabel].filter(Boolean).join(' · ');
  const detailLine = [
    card.etaLabel ? `${card.etaLabel} away` : null,
    card.distanceLabel,
    card.surgeLabel ? `${card.surgeLabel} surge` : null,
    card.wav ? 'WAV' : null,
    card.quietMode ? 'Quiet ride' : null,
    card.isScheduled ? 'Pre-booked' : null,
    card.cashPayment ? 'Cash' : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const destLine = card.dropoffLabel ? `→ ${card.dropoffLabel}` : null;
  const subtitle = [rateLine || null, detailLine || null, destLine].filter(Boolean).join('\n');

  return { title, subtitle: subtitle.length > 0 ? subtitle : null };
}

/** The fresh-offer card (state === 'ride_offered'), sourced from the dispatch payload. */
export function buildOfferCard(offer: OfferLike | null): TripCard {
  return {
    leg: 'offer',
    statusLabel: 'New ride request',
    accent: SPINR_RED,
    riderName: firstName(offer?.rider_name),
    riderRating: rating(offer?.rider_rating),
    riderPhoto: photo(offer?.rider_profile_image),
    bonusLabel: bonusLabel(offer?.total_bonus),
    perkLabel: perkLabel(offer?.incentives, offer?.quest_hint),
    destinationLabel: offer?.pickup_address?.trim() || null,
    destinationCaption: 'Pick-up',
    etaLabel: mins(offer?.duration_minutes),
    distanceLabel: km(offer?.distance_km),
    fareLabel: money(offer?.fare),
    totalEarningsLabel: totalEarningsLabel(offer?.fare, offer?.total_bonus),
    fareBreakdownLabel: fareBreakdownLabel(offer?.fare, offer?.total_bonus),
    perKmLabel: perKmLabel(offer?.fare, offer?.total_bonus, offer?.distance_km),
    surgeLabel: surgeBadge(offer?.surge_multiplier),
    wav: offer?.requires_wav === true,
    quietMode: offer?.quiet_mode === true,
    isScheduled: offer?.is_scheduled === true,
    cashPayment: offer?.payment_method === 'cash',
    pickupLabel: offer?.pickup_address?.trim() || null,
    dropoffLabel: offer?.dropoff_address?.trim() || null,
    hint: 'Accept or decline on the request card',
    earningsTodayLabel: null,
  };
}

/**
 * Build the trip card for the current ride state. For `ride_offered` it prefers
 * the live offer payload (richer, present on the push path); otherwise it reads
 * the engaged ActiveRide. Returns an idle card when there is nothing to show.
 */
/** Cumulative earnings the completed-trip card can show, when available. */
export interface EarningsContext {
  /** Pre-formatted money string from the store, e.g. "187.40". */
  total: string;
  rides: number;
}

/**
 * "Today · $187.40 · 12 rides" — or null when there is nothing trustworthy to
 * show. A zeroed total on a screen that exists to make earnings feel real is
 * worse than no line at all.
 */
function formatEarningsToday(e: EarningsContext | null): string | null {
  if (!e) return null;
  const total = Number(e.total);
  if (!Number.isFinite(total) || total <= 0) return null;
  const rides = e.rides > 0 ? ` · ${e.rides} ride${e.rides === 1 ? '' : 's'}` : '';
  return `Today · $${total.toFixed(2)}${rides}`;
}

export function buildTripCard(
  state: RideState,
  activeRide: ActiveRide | null,
  offer: OfferLike | null = null,
  earnings: EarningsContext | null = null,
): TripCard {
  if (state === 'ride_offered') {
    // Prefer the dispatch offer; fall back to ActiveRide fields when only the
    // fetched-active path populated the store (no incoming push payload).
    if (offer) return buildOfferCard(offer);
    const ride = activeRide?.ride;
    return buildOfferCard(
      ride
        ? {
            ride_id: ride.id,
            pickup_address: ride.pickup_address,
            dropoff_address: ride.dropoff_address,
            fare: ride.total_fare,
            distance_km: ride.distance_km,
            duration_minutes: ride.duration_minutes,
            rider_name: activeRide?.rider?.first_name,
            rider_rating: activeRide?.rider?.rating,
            rider_profile_image: activeRide?.rider?.profile_image,
            requires_wav: ride.requires_wav,
            surge_multiplier: ride.surge_multiplier,
            total_bonus: activeRide?.total_bonus,
            incentives: activeRide?.incentives,
            quest_hint: activeRide?.quest_hint,
            quiet_mode: ride.quiet_mode,
            payment_method: ride.payment_method,
          }
        : null,
    );
  }

  const ride = activeRide?.ride ?? null;
  const riderName = firstName(activeRide?.rider?.first_name);
  const riderRating = rating(activeRide?.rider?.rating);
  const riderPhoto = photo(activeRide?.rider?.profile_image);
  const bonus = bonusLabel(activeRide?.total_bonus);
  const perk = perkLabel(activeRide?.incentives, activeRide?.quest_hint);
  const fareLabel = money(ride?.total_fare);
  const totalLabel = totalEarningsLabel(ride?.total_fare, activeRide?.total_bonus);
  const breakdown = fareBreakdownLabel(ride?.total_fare, activeRide?.total_bonus);
  const perKm = perKmLabel(ride?.total_fare, activeRide?.total_bonus, ride?.distance_km);
  const surgeLabel = surgeBadge(ride?.surge_multiplier);
  const wav = ride?.requires_wav === true;
  const quietMode = ride?.quiet_mode === true;
  const cashPayment = ride?.payment_method === 'cash';
  const pickupLabel = ride?.pickup_address?.trim() || null;
  const dropoffLabel = ride?.dropoff_address?.trim() || null;
  const etaLabel = mins(ride?.duration_minutes);
  const distanceLabel = km(ride?.distance_km);
  // Shared across every engaged-ride leg below. Grouped so a new field is added
  // in ONE place — the five near-identical literals were already the most
  // error-prone part of this builder.
  const moneyFields = {
    fareLabel,
    totalEarningsLabel: totalLabel,
    fareBreakdownLabel: breakdown,
    perKmLabel: perKm,
  };
  const flagFields = {
    quietMode,
    isScheduled: false,
    cashPayment,
    pickupLabel,
    dropoffLabel,
  };

  switch (state) {
    case 'navigating_to_pickup':
      return {
        leg: 'pickup',
        statusLabel: 'Heading to pickup',
        accent: SPINR_RED,
        riderName,
        riderRating,
        riderPhoto,
        bonusLabel: bonus,
        perkLabel: perk,
        destinationLabel: ride?.pickup_address?.trim() || null,
        destinationCaption: 'Pick-up',
        etaLabel,
        distanceLabel,
        ...moneyFields,
        surgeLabel,
        wav,
        ...flagFields,
        hint: null,
        earningsTodayLabel: null,
      };

    case 'arrived_at_pickup':
      return {
        leg: 'pickup',
        statusLabel: 'Arrived at pickup',
        accent: GO,
        riderName,
        riderRating,
        riderPhoto,
        bonusLabel: bonus,
        perkLabel: perk,
        destinationLabel: ride?.pickup_address?.trim() || null,
        destinationCaption: 'Pick-up',
        etaLabel: null,
        distanceLabel,
        ...moneyFields,
        surgeLabel,
        wav,
        ...flagFields,
        // OTP start-trip is distraction-sensitive — stays on the phone (CLAUDE.md).
        hint: 'Verify the rider’s PIN on your phone to start the trip',
        earningsTodayLabel: null,
      };

    case 'trip_in_progress':
      return {
        leg: 'dropoff',
        statusLabel: 'Trip in progress',
        accent: SPINR_RED,
        riderName,
        riderRating,
        riderPhoto,
        bonusLabel: bonus,
        perkLabel: perk,
        destinationLabel: ride?.dropoff_address?.trim() || null,
        destinationCaption: 'Drop-off',
        etaLabel,
        distanceLabel,
        ...moneyFields,
        surgeLabel,
        wav,
        ...flagFields,
        hint: null,
        earningsTodayLabel: null,
      };

    case 'trip_completed':
      return {
        leg: 'complete',
        statusLabel: 'Trip complete',
        accent: GO,
        riderName,
        riderRating: null,
        riderPhoto: null,
        bonusLabel: bonus,
        perkLabel: null,
        destinationLabel: null,
        destinationCaption: null,
        etaLabel: null,
        distanceLabel: null,
        ...moneyFields,
        surgeLabel: null,
        wav: false,
        quietMode: false,
        isScheduled: false,
        cashPayment,
        pickupLabel: null,
        dropoffLabel: null,
        hint: 'Open Spinr on your phone for your next ride',
        // The one place cumulative earnings belong: right after a driver has
        // finished a trip, showing what the day has added up to. Omitted
        // entirely when the store has no summary (a car-only cold launch never
        // ran the phone's fetch) rather than rendering a misleading "$0.00".
        earningsTodayLabel: formatEarningsToday(earnings),
      };

    case 'idle':
    default:
      return {
        leg: 'idle',
        statusLabel: 'No active ride',
        accent: NEUTRAL,
        riderName: null,
        riderRating: null,
        riderPhoto: null,
        bonusLabel: null,
        perkLabel: null,
        destinationLabel: null,
        destinationCaption: null,
        etaLabel: null,
        distanceLabel: null,
        fareLabel: null,
        totalEarningsLabel: null,
        fareBreakdownLabel: null,
        perKmLabel: null,
        surgeLabel: null,
        wav: false,
        quietMode: false,
        isScheduled: false,
        cashPayment: false,
        pickupLabel: null,
        dropoffLabel: null,
        hint: 'Ride requests will appear here',
        earningsTodayLabel: null,
      };
  }
}
