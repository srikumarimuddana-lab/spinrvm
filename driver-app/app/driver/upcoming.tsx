import React, { useCallback, useState } from 'react';
import { View, StyleSheet, FlatList, ActivityIndicator } from 'react-native';
import { Text } from '@shared/components/Text';
import { useFocusEffect } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';

type UpcomingRide = {
  id: string;
  status?: string;
  scheduled_time?: string;
  pickup_address?: string;
  dropoff_address?: string;
};

export default function UpcomingRidesScreen() {
  const { colors } = useTheme();
  const [rides, setRides] = useState<UpcomingRide[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.get<{ rides: UpcomingRide[] }>('/drivers/rides/upcoming').then(
      (res) => {
        setRides(res.data?.rides ?? []);
        setError(null);
        setLoading(false);
      },
      () => {
        setError('Could not load upcoming trips.');
        setLoading(false);
      },
    );
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: colors.background }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={[styles.wrap, { backgroundColor: colors.background }]}>
      <Text style={[styles.title, { color: colors.text }]}>Upcoming trips</Text>
      {error ? <Text style={{ color: colors.error }}>{error}</Text> : null}
      <FlatList
        data={rides}
        keyExtractor={(item) => item.id}
        ListEmptyComponent={<Text style={{ color: colors.textSecondary }}>No upcoming scheduled trips.</Text>}
        renderItem={({ item }) => (
          <View style={[styles.card, { borderColor: colors.border }]}>
            <Text style={{ color: colors.text, fontWeight: '600' }}>
              {item.scheduled_time ? new Date(item.scheduled_time).toLocaleString('en-CA') : 'Scheduled'}
            </Text>
            <Text style={{ color: colors.textSecondary }}>{item.pickup_address || 'Pickup'}</Text>
            <Text style={{ color: colors.textSecondary }}>{item.dropoff_address || 'Drop-off'}</Text>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  title: { fontSize: 22, fontWeight: '700', marginBottom: 12 },
  card: { borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 10 },
});
