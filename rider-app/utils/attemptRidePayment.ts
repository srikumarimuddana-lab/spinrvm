/**
 * Pure payment-attempt orchestration for the ride-completed flow.
 *
 * Extracted from app/ride-completed.tsx so the three-branch response
 * handling (success / 3DS / decline / processor-error) can be unit
 * tested without rendering the screen.
 *
 * The caller supplies API + confirmPayment (both injectable) and
 * receives back `{ ok, charged?, alert? }`. The caller is responsible
 * for navigation and showing the alert — this helper is pure-data.
 */

export type AlertVariant = 'info' | 'success' | 'warning' | 'danger';

export interface PaymentAlertButton {
  text: string;
  kind?: 'change_card' | 'retry' | 'cancel';
}

export interface PaymentAlert {
  title: string;
  message: string;
  variant: AlertVariant;
  buttons?: PaymentAlertButton[];
}

export interface PaymentAttemptResult {
  ok: boolean;
  charged?: number;
  alert?: PaymentAlert;
}

export interface ApiLike {
  post: (url: string, body?: any) => Promise<{ data?: any }>;
}

export interface StripeLike {
  /**
   * Mirrors the shape of @stripe/stripe-react-native's confirmPayment.
   * Returns either { error } or { paymentIntent } (with status).
   */
  confirmPayment: (clientSecret: string) => Promise<{
    error?: { message?: string };
    paymentIntent?: { status?: string };
  }>;
}

export interface PaymentAttemptDeps {
  api: ApiLike;
  stripe: StripeLike | null;
  rideId: string;
  tipAmount: number;
}

const DECLINE_ALERT = (message: string): PaymentAlert => ({
  title: 'Card declined',
  message: message || 'Your card was declined.',
  variant: 'danger',
  buttons: [
    { text: 'Change Card', kind: 'change_card' },
    { text: 'Cancel', kind: 'cancel' },
  ],
});

const AUTH_FAILED_ALERT = (message: string): PaymentAlert => ({
  title: 'Authentication failed',
  message: message || 'Card authentication did not complete.',
  variant: 'danger',
  buttons: [
    { text: 'Change Card', kind: 'change_card' },
    { text: 'Try Again', kind: 'retry' },
  ],
});

const PROCESSOR_ERROR_ALERT: PaymentAlert = {
  title: "Can't process payment right now",
  message: 'Payment service is temporarily unavailable. Please try again shortly.',
  variant: 'warning',
};

const UNKNOWN_ERROR_ALERT: PaymentAlert = {
  title: 'Payment error',
  message: 'Something went wrong. Please try again.',
  variant: 'danger',
};

const STRIPE_UNAVAILABLE_ALERT: PaymentAlert = {
  title: 'Payment unavailable',
  message: 'Card authentication is not available on this device.',
  variant: 'danger',
};

/**
 * Attempt to charge the rider's card for a completed ride. Wraps the
 * POST /rides/{id}/process-payment call with 3DS + decline handling.
 *
 * Contract with backend (backend/routes/rides.py::process_payment):
 *   - 200 { success: true, charged_amount }
 *   - 200 { already_paid: true, charged_amount }
 *   - 200 { success: false, status: "requires_action", client_secret, payment_intent_id }
 *   - 402 { detail: { code: "card_declined", decline_code, message, suggested_action } }
 *   - 502 { detail: { code: "payment_processor_error", message } }
 */
export async function attemptRidePayment(
  deps: PaymentAttemptDeps,
): Promise<PaymentAttemptResult> {
  const { api, stripe, rideId, tipAmount } = deps;

  try {
    const resp = await api.post(`/rides/${rideId}/process-payment`, { tip_amount: tipAmount });
    const data = (resp && resp.data) || {};

    // Case 1 — straight success or already-paid idempotent return
    if (data.success === true || data.already_paid === true) {
      return { ok: true, charged: data.charged_amount };
    }

    // Case 2 — 3DS / SCA challenge
    if (data.status === 'requires_action' && data.client_secret) {
      if (!stripe || !stripe.confirmPayment) {
        return { ok: false, alert: STRIPE_UNAVAILABLE_ALERT };
      }

      const { error: confirmError, paymentIntent } = await stripe.confirmPayment(data.client_secret);

      if (confirmError) {
        return { ok: false, alert: AUTH_FAILED_ALERT(confirmError.message || '') };
      }

      const piStatus = (paymentIntent?.status || '').toLowerCase();
      if (piStatus !== 'succeeded') {
        return { ok: false, alert: AUTH_FAILED_ALERT('') };
      }

      // 3DS succeeded — re-POST to finalize. Backend sees the PI is
      // succeeded and flips payment_status=paid. Idempotency key on
      // the backend side guarantees no double charge.
      const finalize = await api.post(
        `/rides/${rideId}/process-payment`,
        { tip_amount: tipAmount },
      );
      const fin = (finalize && finalize.data) || {};
      if (fin.success === true || fin.already_paid === true) {
        return { ok: true, charged: fin.charged_amount };
      }
      return { ok: false, alert: UNKNOWN_ERROR_ALERT };
    }

    // Unexpected 2xx shape — don't claim success we can't prove
    return { ok: false, alert: UNKNOWN_ERROR_ALERT };
  } catch (err: any) {
    const status = err?.response?.status;
    const body = err?.response?.data || {};
    // Backend wraps its detail dict under `detail` per FastAPI convention.
    // Older clients may see the dict directly (no wrapper) — check both.
    const detail = body.detail || body;
    const code = detail?.code;
    const message = detail?.message || body?.message || (typeof detail === 'string' ? detail : '');

    if (status === 402 && code === 'card_declined') {
      return { ok: false, alert: DECLINE_ALERT(message || '') };
    }

    if (status === 502) {
      return { ok: false, alert: PROCESSOR_ERROR_ALERT };
    }

    // 409 "requires completed state" — the WS event arrived before the DB
    // write was visible to this replica. Retry once after 1.5 s; by then
    // Supabase will have committed the status = "completed" update.
    if (status === 409 && typeof message === 'string' && message.includes('requires completed state')) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      return attemptRidePayment(deps);
    }

    return { ok: false, alert: UNKNOWN_ERROR_ALERT };
  }
}
