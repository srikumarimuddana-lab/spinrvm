/**
 * AI assistant types shared between the rider app, SupportScreen and the
 * backend SSE contract (backend/routes/ai.py).
 */

export interface AiChatRequest {
  message: string;
  conversation_id: string | null;
  stream: boolean;
  /** Rider's current device location, sent opportunistically so booking
   * tools can bias place search / resolve "my location" pickups.
   * Ephemeral on the backend — never logged or persisted. */
  location?: { lat: number; lng: number } | null;
}

export interface AiConfig {
  enabled: boolean;
  disclaimer: string;
}

/** Complete CreateRideRequest payload prepared by propose_ride_booking.
 * The card fetches the authoritative estimate itself; Confirm goes through
 * rideStore.createRide() — the AI never books. */
export interface BookingProposal {
  pickup_lat: number;
  pickup_lng: number;
  pickup_address: string;
  dropoff_lat: number;
  dropoff_lng: number;
  dropoff_address: string;
  vehicle_type_id?: string;
  promo_code?: string;
  scheduled_time?: string;
  payment_method?: 'card' | 'wallet';
  /** The rider explicitly confirmed a trip whose pickup and dropoff are the
   * same place (assistant's same-location guardrail). Lets the card bypass
   * the client-side proximity guard for this one confirmed booking. */
  same_location_confirmed?: boolean;
}

export interface LocationSuggestionCandidate {
  name?: string | null;
  address?: string | null;
  lat: number;
  lng: number;
  in_service_area?: boolean;
  service_area?: string | null;
}

/** One vehicle option inside a fare_quote action. Amounts are exact
 * Decimal strings (taxes and fees included) from the same estimate engine
 * as the booking screen; the best eligible promo is pre-applied. */
export interface FareQuoteOption {
  vehicle_type_id?: string | null;
  vehicle_type?: string | null;
  capacity?: number | null;
  image_url?: string | null;
  eta_minutes?: number | null;
  drivers_nearby?: number | null;
  /** Distance of the nearest driver from the pickup. */
  closest_driver_km?: number | null;
  surge_multiplier?: number | null;
  /** Labelled fare line items from the estimate engine (display-only;
   * `amount: null` lines are modifiers like surge). */
  breakdown?: { label: string; amount: number | null; type: string }[];
  /** Total before promo savings. */
  total: string;
  promo_code?: string;
  promo_savings?: string;
  /** What the rider pays after the auto-applied promo. */
  final_total: string;
}

export type AiAction =
  | { type: 'booking_proposal'; proposal: BookingProposal }
  | {
      type: 'location_suggestions';
      query: string;
      location_role?: 'pickup' | 'dropoff' | null;
      candidates: LocationSuggestionCandidate[];
    }
  | {
      type: 'fare_quote';
      distance_km?: number | null;
      duration_minutes?: number | null;
      currency?: string;
      /** Resolved trip endpoints — included so a tapped option can send a
       * self-contained booking message (conversation history keeps only
       * message text, never tool results). Coordinates are the exact
       * (post-reconcile) points the quote was priced on; the tap sends them
       * back verbatim so the model never re-geocodes a priced trip. */
      pickup_address?: string;
      dropoff_address?: string;
      pickup_lat?: number;
      pickup_lng?: number;
      dropoff_lat?: number;
      dropoff_lng?: number;
      recommended_vehicle_type_id?: string | null;
      quotes: FareQuoteOption[];
    }
  | { type: 'open_support'; category: string; link: string; message?: string };

export type AiSseEvent =
  | { event: 'meta'; data: { conversation_id: string; user_message_id: string } }
  | { event: 'token'; data: { text: string } }
  | { event: 'tool'; data: { name: string; status: 'start' | 'end'; ok?: boolean } }
  | { event: 'action'; data: AiAction }
  | {
      event: 'done';
      data: {
        message_id: string;
        usage: { input_tokens: number; output_tokens: number };
        stop_reason: string;
      };
    }
  | { event: 'error'; data: { code: string; message: string } };

/** One bubble in the AI chat UI. `kind` distinguishes native injections
 * (ride status updates, cards) from model output. */
export interface AiChatMessage {
  id: string;
  role: 'user' | 'assistant';
  kind: 'text' | 'booking_proposal' | 'location_suggestions' | 'fare_quote' | 'support_action' | 'ride_status';
  content: string;
  action?: AiAction;
  createdAt: number;
}

export interface AiConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}
