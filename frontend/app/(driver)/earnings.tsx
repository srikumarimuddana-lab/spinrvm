import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export default function DriverEarnings() {
  return (
    <View style={styles.container}>
      <Text style={styles.header}>Earnings</Text>
      <View style={styles.card}>
          <Text>Total Earnings</Text>
          <Text style={styles.amount}>$0.00</Text>
      </View>
      <Text style={styles.subtext}>Earnings will appear here after you complete rides.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, backgroundColor: '#fff' },
  header: { fontSize: 24, fontWeight: 'bold', marginBottom: 20 },
  card: { padding: 20, backgroundColor: '#f0f0f0', borderRadius: 12, alignItems: 'center' },
  amount: { fontSize: 32, fontWeight: 'bold', color: 'green', marginTop: 10 },
  subtext: { marginTop: 20, color: '#666', textAlign: 'center' }
});
