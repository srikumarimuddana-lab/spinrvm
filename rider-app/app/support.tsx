import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import SupportScreen from '@shared/components/SupportScreen';

export default function RiderSupportScreen() {
  const { rideId, tab } = useLocalSearchParams<{ rideId?: string; tab?: string }>();
  return (
    <SupportScreen
      role="rider"
      rideId={rideId}
      initialTab={(tab as any) ?? 'faq'}
    />
  );
}
