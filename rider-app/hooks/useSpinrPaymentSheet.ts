/**
 * Hook that wraps the Stripe PaymentSheet flow for Spinr.
 *
 * Usage:
 *   const { presentSheet, isLoading } = useSpinrPaymentSheet();
 *   const result = await presentSheet({ rideId, amount, tipAmount });
 *   if (result.ok) { ... }
 *
 * The hook calls POST /payments/payment-sheet to get the three Stripe
 * secrets, initialises the PaymentSheet, and then presents it. The sheet
 * shows Google Pay / Apple Pay + any saved cards.  If the rider confirms,
 * the PaymentIntent is captured server-side and the hook returns ok: true.
 *
 * Falls back to null (caller should use the legacy confirmPayment path)
 * when the PaymentSheet cannot be initialised (e.g. no stripe keys in env).
 */

import { useState, useCallback } from 'react';
import { useStripe } from '@stripe/stripe-react-native';
import api from '@shared/api/client';

export interface PaymentSheetResult {
  ok: boolean;
  errorMessage?: string;
}

interface PresentSheetOptions {
  rideId: string;
  amount: number;
  tipAmount?: number;
}

export function useSpinrPaymentSheet() {
  const { initPaymentSheet, presentPaymentSheet } = useStripe();
  const [isLoading, setIsLoading] = useState(false);

  const presentSheet = useCallback(
    async ({ rideId, amount, tipAmount = 0 }: PresentSheetOptions): Promise<PaymentSheetResult> => {
      setIsLoading(true);
      try {
        const total = amount + tipAmount;
        const res = await api.post<{
          paymentIntent: string;
          ephemeralKey: string;
          customer: string;
          publishableKey: string;
        }>('/payments/payment-sheet', {
          amount: total,
          ride_id: rideId,
        });

        const { paymentIntent, ephemeralKey, customer } = res.data;

        const { error: initError } = await initPaymentSheet({
          merchantDisplayName: 'Spinr',
          customerId: customer,
          customerEphemeralKeySecret: ephemeralKey,
          paymentIntentClientSecret: paymentIntent,
          allowsDelayedPaymentMethods: false,
          googlePay: {
            merchantCountryCode: 'CA',
            testEnv: process.env.EXPO_PUBLIC_ENV !== 'production',
          },
          applePay: {
            merchantCountryCode: 'CA',
          },
          returnURL: 'spinr://stripe-redirect',
        });

        if (initError) {
          return { ok: false, errorMessage: initError.message };
        }

        const { error: presentError } = await presentPaymentSheet();
        if (presentError) {
          if (presentError.code === 'Canceled') {
            return { ok: false, errorMessage: 'Payment cancelled.' };
          }
          return { ok: false, errorMessage: presentError.message };
        }

        return { ok: true };
      } catch (e: any) {
        return { ok: false, errorMessage: e?.message ?? 'Payment failed. Please try again.' };
      } finally {
        setIsLoading(false);
      }
    },
    [initPaymentSheet, presentPaymentSheet],
  );

  return { presentSheet, isLoading };
}
