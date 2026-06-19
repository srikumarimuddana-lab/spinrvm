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
  kind?: 'change_card' | 'retry' | 'cancel' | 'support';
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
  /**
   * In-app "Change Card" escape: the card the rider picked after a decline /
   * no-card failure. Forwarded to the backend so the failed ride is re-charged
   * on THIS card (fresh charge) instead of the booking-time card or hold.
   */
  paymentMethodId?: string;
}

// Every failure on the end-of-ride payment screen must offer a way OUT — the
// screen blocks the hardware back button, so an alert with only "OK" traps the
// rider in a retry loop. "Contact Support" is the universal escape; "Change
// Card" is added wherever a different card can plausibly fix the charge.
const SUPPORT_BTN: PaymentAlertButton = { text: 'Contact Support', kind: 'support' };
const CHANGE_CARD_BTN: PaymentAlertButton = { text: 'Change Card', kind: 'change_card' };

const DECLINE_ALERT = (message: string): PaymentAlert => ({
  title: 'Card declined',
  message: message || 'Your card was declined.',
  variant: 'danger',
  buttons: [CHANGE_CARD_BTN, SUPPORT_BTN],
});

const NO_PAYMENT_METHOD_ALERT = (message: string): PaymentAlert => ({
  title: 'Add a payment method',
  message: message || 'There is no card on file for this trip. Add a card to pay.',
  variant: 'danger',
  buttons: [{ text: 'Add Card', kind: 'change_card' }, SUPPORT_BTN],
});

const AUTH_FAILED_ALERT = (message: string): PaymentAlert => ({
  title: 'Authentication failed',
  message: message || 'Card authentication did not complete.',
  variant: 'danger',
  buttons: [CHANGE_CARD_BTN, { text: 'Try Again', kind: 'retry' }, SUPPORT_BTN],
});

const PROCESSOR_ERROR_ALERT: PaymentAlert = {
  title: "Can't process payment right now",
  message: 'Payment service is temporarily unavailable. Please try again shortly.',
  variant: 'warning',
  buttons: [{ text: 'Try Again', kind: 'retry' }, SUPPORT_BTN],
};

const UNKNOWN_ERROR_ALERT: PaymentAlert = {
  title: 'Payment error',
  message: "Something went wrong and we couldn't complete the payment. Try a different card, or contact support.",
  variant: 'danger',
  buttons: [CHANGE_CARD_BTN, SUPPORT_BTN],
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
const FINALIZING_ALERT: PaymentAlert = {
  title: 'Finalizing your trip',
  message: 'Your ride is complete but payment is still being confirmed. Please try again in a moment.',
  variant: 'info',
  buttons: [{ text: 'Retry', kind: 'retry' }],
};

const RETRY_BACKOFF_MS = [1500, 2500, 3500];
const MAX_409_RETRIES = RETRY_BACKOFF_MS.length;

export async function attemptRidePayment(
  deps: PaymentAttemptDeps,
  _retryAttempt = 0,
): Promise<PaymentAttemptResult> {
  const { api, stripe, rideId, tipAmount, paymentMethodId } = deps;
  const body: Record<string, any> = { tip_amount: tipAmount };
  // Only include when set so a normal retry keeps charging the booking card.
  if (paymentMethodId) body.payment_method_id = paymentMethodId;

  try {
    const resp = await api.post(`/rides/${rideId}/process-payment`, body);
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
        body,
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

    // No card on file for the trip (booking-time card removed / rejected auth
    // left nothing usable). Backend returns a structured 402; the rider needs
    // to ADD a card, not just retry.
    if (status === 402 && code === 'no_payment_method') {
      return { ok: false, alert: NO_PAYMENT_METHOD_ALERT(message || '') };
    }

    // Card needs 3DS we couldn't complete off-session, or any other 402 — a
    // different card is the most likely fix.
    if (status === 402 && code === 'authentication_required') {
      return { ok: false, alert: AUTH_FAILED_ALERT(message || '') };
    }
    if (status === 402) {
      return { ok: false, alert: DECLINE_ALERT(message || '') };
    }

    if (status === 502) {
      return { ok: false, alert: PROCESSOR_ERROR_ALERT };
    }

    // 400 (e.g. legacy no-payment-method, validation) — never a dead end on a
    // back-blocked screen. Offer Change Card + Contact Support.
    if (status === 400) {
      return { ok: false, alert: UNKNOWN_ERROR_ALERT };
    }

    if (status === 409 && typeof message === 'string' && message.includes('requires completed state')) {
      if (_retryAttempt >= MAX_409_RETRIES) {
        return { ok: false, alert: FINALIZING_ALERT };
      }
      await new Promise(resolve => setTimeout(resolve, RETRY_BACKOFF_MS[_retryAttempt]));
      return attemptRidePayment(deps, _retryAttempt + 1);
    }

    return { ok: false, alert: UNKNOWN_ERROR_ALERT };
  }
}
