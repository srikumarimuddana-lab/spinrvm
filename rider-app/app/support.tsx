import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import SupportScreen from '@shared/components/SupportScreen';

export default function RiderSupportScreen() {
  // Opened from the stuck end-of-ride payment screen with ?rideId=&topic=
  // payment_failed — drop the rider straight into a pre-filled Contact ticket.
  const { rideId, topic } = useLocalSearchParams<{ rideId?: string; topic?: string }>();
  const isPaymentIssue = topic === 'payment_failed';
  return (
    <SupportScreen
      role="rider"
      initialTab={isPaymentIssue ? 'contact' : 'faq'}
      initialCategory={isPaymentIssue ? 'payment_failed' : 'general'}
      initialIssue={
        isPaymentIssue
          ? `I couldn't complete payment for my ride${rideId ? ` (ride ${rideId})` : ''}. My card was declined and I'm unable to pay or change cards.`
          : ''
      }
    />
  );
}
