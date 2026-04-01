import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList, TextInput,
  Alert, Platform, ActivityIndicator, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

const PLACE_TYPES = [
  { key: 'Home', icon: 'home', color: COLORS.primary, bg: '#FEF2F2' },
  { key: 'Work', icon: 'briefcase', color: '#3B82F6', bg: '#DBEAFE' },
  { key: 'Gym', icon: 'fitness', color: '#10B981', bg: '#ECFDF5' },
  { key: 'School', icon: 'school', color: '#F59E0B', bg: '#FEF3C7' },
  { key: 'Other', icon: 'star', color: '#8B5CF6', bg: '#EDE9FE' },
];

interface Prediction {
  place_id: string;
  description: string;
  structured_formatting?: { main_text: string; secondary_text: string };
}

export default function SavedPlacesScreen() {
  const router = useRouter();
  const { savedAddresses, fetchSavedAddresses, addSavedAddress, deleteSavedAddress } = useRideStore();
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);

  // Add form state
  const [placeName, setPlaceName] = useState('');
  const [selectedType, setSelectedType] = useState('Home');
  const [searchText, setSearchText] = useState('');
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [selectedPlace, setSelectedPlace] = useState<{ address: string; lat: number; lng: number } | null>(null);
  const [searching, setSearching] = useState(false);
  const searchTimeout = React.useRef<any>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    await fetchSavedAddresses();
    setLoading(false);
  };

  const searchPlaces = (query: string) => {
    setSearchText(query);
    setSelectedPlace(null);
    if (!query || query.length < 2 || !GOOGLE_MAPS_API_KEY) {
      setPredictions([]);
      return;
    }
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(async () => {
      setSearching(true);
      try {
        const url = `https://maps.googleapis.com/maps/api/place/autocomplete/json?input=${encodeURIComponent(query)}&key=${GOOGLE_MAPS_API_KEY}&language=en&components=country:ca`;
        const res = await fetch(url);
        const data = await res.json();
        setPredictions(data.predictions || []);
      } catch {}
      finally { setSearching(false); }
    }, 300);
  };

  const selectPrediction = async (prediction: Prediction) => {
    if (!GOOGLE_MAPS_API_KEY) return;
    try {
      const url = `https://maps.googleapis.com/maps/api/place/details/json?place_id=${prediction.place_id}&fields=geometry,formatted_address&key=${GOOGLE_MAPS_API_KEY}`;
      const res = await fetch(url);
      const data = await res.json();
      if (data.result) {
        setSelectedPlace({
          address: data.result.formatted_address,
          lat: data.result.geometry.location.lat,
          lng: data.result.geometry.location.lng,
        });
        setSearchText(prediction.structured_formatting?.main_text || prediction.description);
        setPredictions([]);
      }
    } catch {}
  };

  const handleSave = async () => {
    if (!selectedPlace) { Alert.alert('Error', 'Search and select an address'); return; }
    const name = placeName.trim() || selectedType;
    setSaving(true);
    try {
      await addSavedAddress({
        name,
        address: selectedPlace.address,
        lat: selectedPlace.lat,
        lng: selectedPlace.lng,
        icon: selectedType.toLowerCase(),
      });
      setShowAdd(false);
      resetForm();
    } catch (err: any) {
      Alert.alert('Error', err.message || 'Failed to save place');
    } finally { setSaving(false); }
  };

  const handleDelete = (id: string, name: string) => {
    Alert.alert('Remove Place', `Remove "${name}" from saved places?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Remove', style: 'destructive', onPress: () => deleteSavedAddress(id) },
    ]);
  };

  const resetForm = () => {
    setPlaceName(''); setSelectedType('Home'); setSearchText(''); setSelectedPlace(null); setPredictions([]);
  };

  const getPlaceConfig = (name: string) => {
    const lower = name?.toLowerCase() || '';
    return PLACE_TYPES.find(t => lower.includes(t.key.toLowerCase())) || PLACE_TYPES[PLACE_TYPES.length - 1];
  };

  const renderPlace = ({ item }: { item: any }) => {
    const config = getPlaceConfig(item.name);
    return (
      <View style={styles.placeItem}>
        <View style={[styles.placeIcon, { backgroundColor: config.bg }]}>
          <Ionicons name={config.icon as any} size={20} color={config.color} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.placeName}>{item.name}</Text>
          <Text style={styles.placeAddr} numberOfLines={1}>{item.address}</Text>
        </View>
        <TouchableOpacity onPress={() => handleDelete(item.id, item.name)} style={{ padding: 8 }}>
          <Ionicons name="trash-outline" size={18} color="#CCC" />
        </TouchableOpacity>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Saved Places</Text>
        <View style={{ width: 44 }} />
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator size="large" color={COLORS.primary} /></View>
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <FlatList
            data={savedAddresses}
            renderItem={renderPlace}
            keyExtractor={(item) => item.id}
            contentContainerStyle={styles.list}
            ListEmptyComponent={
              <View style={styles.empty}>
                <Ionicons name="bookmark-outline" size={48} color="#DDD" />
                <Text style={styles.emptyTitle}>No saved places yet</Text>
                <Text style={styles.emptySub}>Add your home, work, or favourite spots for faster booking</Text>
              </View>
            }
            ListFooterComponent={
              showAdd ? (
                <View style={styles.addForm}>
                  {/* Type selector */}
                  <Text style={styles.formLabel}>Type</Text>
                  <View style={styles.typeRow}>
                    {PLACE_TYPES.map((t) => (
                      <TouchableOpacity
                        key={t.key}
                        style={[styles.typeChip, selectedType === t.key && { backgroundColor: t.bg, borderColor: t.color }]}
                        onPress={() => { setSelectedType(t.key); if (!placeName) setPlaceName(t.key); }}
                      >
                        <Ionicons name={t.icon as any} size={16} color={selectedType === t.key ? t.color : '#999'} />
                        <Text style={[styles.typeChipText, selectedType === t.key && { color: t.color }]}>{t.key}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {/* Name */}
                  <Text style={styles.formLabel}>Label</Text>
                  <TextInput
                    style={styles.input}
                    placeholder="e.g. Home, Mom's house"
                    placeholderTextColor="#BBB"
                    value={placeName}
                    onChangeText={setPlaceName}
                  />

                  {/* Address search */}
                  <Text style={styles.formLabel}>Address</Text>
                  <TextInput
                    style={styles.input}
                    placeholder="Search for an address"
                    placeholderTextColor="#BBB"
                    value={searchText}
                    onChangeText={searchPlaces}
                  />
                  {searching && <ActivityIndicator size="small" color={COLORS.primary} style={{ marginTop: 8 }} />}

                  {predictions.length > 0 && (
                    <View style={styles.predList}>
                      {predictions.slice(0, 5).map((p) => (
                        <TouchableOpacity key={p.place_id} style={styles.predItem} onPress={() => selectPrediction(p)}>
                          <Ionicons name="location-outline" size={16} color="#999" />
                          <Text style={styles.predText} numberOfLines={1}>
                            {p.structured_formatting?.main_text || p.description}
                          </Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  )}

                  {selectedPlace && (
                    <View style={styles.selectedAddr}>
                      <Ionicons name="checkmark-circle" size={16} color="#10B981" />
                      <Text style={styles.selectedAddrText} numberOfLines={2}>{selectedPlace.address}</Text>
                    </View>
                  )}

                  {/* Actions */}
                  <View style={styles.formActions}>
                    <TouchableOpacity style={styles.cancelBtn} onPress={() => { setShowAdd(false); resetForm(); }}>
                      <Text style={styles.cancelText}>Cancel</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.saveBtn, !selectedPlace && { opacity: 0.5 }]}
                      onPress={handleSave}
                      disabled={!selectedPlace || saving}
                    >
                      {saving ? <ActivityIndicator size="small" color="#FFF" /> : <Text style={styles.saveText}>Save Place</Text>}
                    </TouchableOpacity>
                  </View>
                </View>
              ) : (
                <TouchableOpacity style={styles.addBtn} onPress={() => setShowAdd(true)}>
                  <Ionicons name="add-circle" size={22} color={COLORS.primary} />
                  <Text style={styles.addBtnText}>Add New Place</Text>
                </TouchableOpacity>
              )
            }
          />
        </KeyboardAvoidingView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F0F0F0',
  },
  backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
  headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  list: { padding: 20 },

  // Place item
  placeItem: {
    flexDirection: 'row', alignItems: 'center', paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: '#F5F5F5',
  },
  placeIcon: {
    width: 44, height: 44, borderRadius: 12, justifyContent: 'center', alignItems: 'center', marginRight: 14,
  },
  placeName: { fontSize: 15, fontWeight: '600', color: '#1A1A1A' },
  placeAddr: { fontSize: 12, color: '#999', marginTop: 2 },

  // Empty
  empty: { alignItems: 'center', paddingVertical: 40 },
  emptyTitle: { fontSize: 17, fontWeight: '700', color: '#1A1A1A', marginTop: 12 },
  emptySub: { fontSize: 13, color: '#999', marginTop: 4, textAlign: 'center', paddingHorizontal: 20 },

  // Add button
  addBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    paddingVertical: 16, borderRadius: 14, borderWidth: 2, borderColor: COLORS.primary,
    borderStyle: 'dashed', marginTop: 16,
  },
  addBtnText: { fontSize: 15, fontWeight: '700', color: COLORS.primary },

  // Add form
  addForm: { backgroundColor: '#F9F9F9', borderRadius: 18, padding: 20, marginTop: 16 },
  formLabel: { fontSize: 12, fontWeight: '600', color: '#888', marginBottom: 6, marginTop: 14 },
  input: {
    backgroundColor: '#FFF', borderRadius: 12, paddingHorizontal: 16, paddingVertical: 14,
    fontSize: 15, color: '#1A1A1A', borderWidth: 1, borderColor: '#ECECEC',
  },
  typeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  typeChip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20,
    borderWidth: 1.5, borderColor: '#E5E5E5', backgroundColor: '#FFF',
  },
  typeChipText: { fontSize: 13, fontWeight: '600', color: '#999' },

  predList: { backgroundColor: '#FFF', borderRadius: 12, marginTop: 8, borderWidth: 1, borderColor: '#ECECEC' },
  predItem: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 14, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F5F5F5',
  },
  predText: { flex: 1, fontSize: 14, color: '#1A1A1A' },

  selectedAddr: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginTop: 10, padding: 12, backgroundColor: '#F0FFF4', borderRadius: 10,
  },
  selectedAddrText: { flex: 1, fontSize: 13, color: '#059669' },

  formActions: { flexDirection: 'row', gap: 12, marginTop: 20 },
  cancelBtn: { flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 12, backgroundColor: '#F0F0F0' },
  cancelText: { fontSize: 15, fontWeight: '600', color: '#666' },
  saveBtn: { flex: 2, alignItems: 'center', paddingVertical: 14, borderRadius: 12, backgroundColor: COLORS.primary },
  saveText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
});
