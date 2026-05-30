import React, { useState, useEffect } from 'react';
import { View, Text, FlatList, ActivityIndicator, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import api from '@shared/api/client';

export default function ActivityScreen() {
  const [rides, setRides] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get('/drivers/rides/history?limit=20&offset=0')
      .then(res => {
        const data = res.data as any;
        setRides(Array.isArray(data) ? data : (data?.rides ?? []));
        setTotal(data?.total ?? 0);
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <SafeAreaView style={s.center} edges={['top']}>
        <ActivityIndicator size="large" />
        <Text>Loading...</Text>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={s.center} edges={['top']}>
        <Text style={s.error}>Error: {error}</Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <Text style={s.title}>Activity — {total} rides</Text>
      <FlatList
        data={rides}
        keyExtractor={item => item.id}
        renderItem={({ item }) => (
          <View style={s.row}>
            <Text style={s.status}>{item.status}</Text>
            <Text style={s.addr} numberOfLines={1}>{item.pickup_address ?? 'No address'}</Text>
            <Text style={s.date}>{item.created_at?.slice(0, 10)}</Text>
          </View>
        )}
        ListEmptyComponent={<Text style={s.empty}>No rides returned</Text>}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  title: { fontSize: 20, fontWeight: '700', padding: 16 },
  row: { padding: 14, borderBottomWidth: 1, borderBottomColor: '#eee' },
  status: { fontSize: 12, fontWeight: '700', color: '#555', marginBottom: 2 },
  addr: { fontSize: 15, color: '#111' },
  date: { fontSize: 12, color: '#999', marginTop: 2 },
  error: { color: 'red', padding: 16, textAlign: 'center' },
  empty: { padding: 32, textAlign: 'center', color: '#999' },
});
