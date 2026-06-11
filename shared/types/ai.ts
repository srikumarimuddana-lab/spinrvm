/**
 * AI assistant types shared between the rider app, SupportScreen and the
 * backend SSE contract (backend/routes/ai.py).
 */

export interface AiChatRequest {
  message: string;
  conversation_id: string | null;
  stream: boolean;
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
}

export type AiAction =
  | { type: 'booking_proposal'; proposal: BookingProposal }
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
  kind: 'text' | 'booking_proposal' | 'support_action' | 'ride_status';
  content: string;
  action?: AiAction;
  createdAt: number;
}

export interface AiConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}
