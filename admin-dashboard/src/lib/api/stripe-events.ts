// Admin Stripe Events API — stuck-event visibility and resolution.
// Super-admin-only. Matches backend/routes/admin/stripe_events.py.

import { request } from "./client";

export interface StuckStripeEvent {
    event_id: string;
    event_type: string | null;
    received_at: string | null;
    age_minutes: number | null;
}

export interface StuckEventsResponse {
    items: StuckStripeEvent[];
    count: number;
    offset: number;
    limit: number;
}

export interface StuckEventCount {
    count: number;
}

export interface StripeEventDetail {
    event_id: string;
    event_type: string | null;
    received_at: string | null;
    processed_at: string | null;
    age_minutes: number | null;
    payload: Record<string, unknown> | null;
    is_stuck: boolean;
}

export interface ReplayResult {
    replayed: boolean;
    event_id: string;
    event_type: string;
}

export interface DismissResult {
    dismissed: boolean;
    event_id: string;
    reason: string;
}

export async function getStuckStripeEvents(
    limit = 50,
    offset = 0,
): Promise<StuckEventsResponse> {
    return request<StuckEventsResponse>(
        `/api/admin/stripe-events/stuck?limit=${limit}&offset=${offset}`,
    );
}

export async function getStuckEventCount(): Promise<StuckEventCount> {
    return request<StuckEventCount>("/api/admin/stripe-events/stuck/count");
}

export async function getStripeEventDetail(
    eventId: string,
): Promise<StripeEventDetail> {
    return request<StripeEventDetail>(
        `/api/admin/stripe-events/${encodeURIComponent(eventId)}`,
    );
}

export async function replayStripeEvent(
    eventId: string,
): Promise<ReplayResult> {
    return request<ReplayResult>(
        `/api/admin/stripe-events/${encodeURIComponent(eventId)}/replay`,
        {
            method: "POST",
            body: JSON.stringify({ confirm: "REPLAY" }),
        },
    );
}

export async function dismissStripeEvent(
    eventId: string,
    reason: string,
): Promise<DismissResult> {
    return request<DismissResult>(
        `/api/admin/stripe-events/${encodeURIComponent(eventId)}/dismiss`,
        {
            method: "POST",
            body: JSON.stringify({ reason }),
        },
    );
}
