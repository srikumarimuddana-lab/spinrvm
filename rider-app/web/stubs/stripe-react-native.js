/**
 * @stripe/stripe-react-native stub for Expo web builds.
 *
 * The real package's CardField pulls in RN's native Fabric renderer specs
 * (codegenNativeComponent, ReactFabric, etc.) which have no web
 * implementation and abort `expo export --platform web`. Card payments are a
 * native-only surface (no web checkout flow exists), so on web we render a
 * placeholder and no-op the Stripe hooks instead of shipping a broken bundle.
 *
 * This file is resolved by metro.config.js via resolver.resolveRequest when
 * platform === 'web'. It is NEVER bundled into native builds.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export function StripeProvider({ children }) {
  return children ?? null;
}

const CardField = React.forwardRef(function CardField(_props, _ref) {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Card entry available on iOS & Android</Text>
    </View>
  );
});

const CardFieldInput = {};

const STRIPE_UNAVAILABLE_ERROR = { message: 'Card payments are not available on web. Use the Spinr mobile app.' };

function useStripe() {
  return {
    createPaymentMethod: async () => ({ paymentMethod: undefined, error: STRIPE_UNAVAILABLE_ERROR }),
    confirmPayment: async () => ({ paymentIntent: undefined, error: STRIPE_UNAVAILABLE_ERROR }),
    initPaymentSheet: async () => ({ error: STRIPE_UNAVAILABLE_ERROR }),
    presentPaymentSheet: async () => ({ error: STRIPE_UNAVAILABLE_ERROR }),
  };
}

const styles = StyleSheet.create({
  container: {
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#F8F8F8',
    minHeight: 50,
    borderRadius: 8,
    padding: 12,
  },
  text: { color: '#999', fontSize: 13, textAlign: 'center' },
});

export { CardField, CardFieldInput, useStripe };
