import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, FlatList, ActivityIndicator } from 'react-native';
import { useAuthStore } from '../../store/authStore';
import SpinrConfig from '../../config/spinr.config';

export default function DriverRides() {
  const { token } = useAuthStore();
  const [rides, setRides] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchRides();
  }, []);

  const fetchRides = async () => {
    try {
      const res = await fetch(`${SpinrConfig.backendUrl}/api/drivers/rides/pending`, { // Using pending endpoint for now, maybe add history
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      setRides(data);
    } catch (e) {
      console.log(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Recent Rides</Text>
      {loading ? (
        <ActivityIndicator />
      ) : (
        <FlatList
          data={rides}
          keyExtractor={(item: any) => item.id}
          renderItem={({ item }) => (
            <View style={styles.card}>
              <Text style={styles.date}>{new Date(item.created_at).toLocaleDateString()}</Text>
              <Text style={styles.fare}>${item.total_fare}</Text>
              <Text>{item.status}</Text>
            </View>
          )}
          ListEmptyComponent={<Text>No rides yet.</Text>}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, backgroundColor: '#fff' },
  header: { fontSize: 24, fontWeight: 'bold', marginBottom: 20 },
  card: { padding: 15, backgroundColor: '#f9f9f9', marginBottom: 10, borderRadius: 8 },
  date: { color: '#666' },
  fare: { fontSize: 18, fontWeight: 'bold', color: 'green' }
});
