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
  fare?: string;
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
  /** Surge badge ("1.5×") when above 1.0, else null. */
  surgeLabel: string | null;
  /** Wheelchair-accessible vehicle requested. */
  wav: boolean;
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

const money = (v?: string | null): string | null => {
  const t = (v ?? '').toString().trim();
  return t.length > 0 ? `$${t}` : null;
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
    surgeLabel: surgeBadge(offer?.surge_multiplier),
    wav: offer?.requires_wav === true,
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
  const surgeLabel = surgeBadge(ride?.surge_multiplier);
  const wav = ride?.requires_wav === true;
  const etaLabel = mins(ride?.duration_minutes);
  const distanceLabel = km(ride?.distance_km);

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
        fareLabel,
        surgeLabel,
        wav,
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
        fareLabel,
        surgeLabel,
        wav,
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
        fareLabel,
        surgeLabel,
        wav,
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
        fareLabel,
        surgeLabel: null,
        wav: false,
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
        surgeLabel: null,
        wav: false,
        hint: 'Ride requests will appear here',
        earningsTodayLabel: null,
      };
  }
}
