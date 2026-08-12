import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList, TextInput,
  ActivityIndicator, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import api, { getApiErrorMessage } from '@shared/api/client';
import { usePlacesAutocomplete } from '@shared/hooks/usePlacesAutocomplete';
import type { PlacePrediction as Prediction } from '@shared/api/places';

const PLACE_TYPES = [
  { key: 'Home', icon: 'home', color: '#FF3B30', bg: '#FEF2F2' },
  { key: 'Work', icon: 'briefcase', color: '#3B82F6', bg: '#DBEAFE' },
  { key: 'Gym', icon: 'fitness', color: '#10B981', bg: '#ECFDF5' },
  { key: 'School', icon: 'school', color: '#F59E0B', bg: '#FEF3C7' },
  { key: 'Other', icon: 'star', color: '#8B5CF6', bg: '#EDE9FE' },
];

export default function SavedPlacesScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const { savedAddresses, fetchSavedAddresses, addSavedAddress, deleteSavedAddress, userLocation } = useRideStore();
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);

  // Add form state
  const [placeName, setPlaceName] = useState('');
  const [selectedType, setSelectedType] = useState('Home');
  const [searchText, setSearchText] = useState('');
  const [selectedPlace, setSelectedPlace] = useState<{ address: string; lat: number; lng: number } | null>(null);

  // Bias to the rider's GPS so a "Walmart" search returns the nearby store
  // instead of generic matches across Canada. Previously this screen sent
  // no bias at all.
  const bias = userLocation
    ? { lat: userLocation.latitude, lng: userLocation.longitude, radiusMeters: 20000 }
    : null;
  const {
    predictions,
    loading: searching,
    clear: clearPredictions,
    rotateSessionToken,
    sessionToken,
  } = usePlacesAutocomplete(selectedPlace ? '' : searchText, bias);

  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', message: '', variant: 'info' });

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
  };

  const selectPrediction = async (prediction: Prediction) => {
    try {
      const params = new URLSearchParams({
        place_id: prediction.place_id,
        session_token: sessionToken,
      });
      const { data } = await api.get<{ lat: number | null; lng: number | null; formatted_address: string }>(
        `/maps/places/details?${params.toString()}`,
      );
      // Selection closes the billing session — start a fresh one for the next save.
      rotateSessionToken();
      if (data && data.lat != null && data.lng != null) {
        setSelectedPlace({
          address: data.formatted_address,
          lat: data.lat,
          lng: data.lng,
        });
        setSearchText(prediction.structured_formatting?.main_text || prediction.description);
        clearPredictions();
      }
    } catch {}
  };

  const handleSave = async () => {
    if (!selectedPlace) { showToast('Address Required', 'Please search for and select an address', 'warning'); return; }
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
      showToast('Save Failed', getApiErrorMessage(err, 'Could not save this place. Please try again.'), 'danger');
    } finally { setSaving(false); }
  };

  const handleDelete = (id: string, name: string) => {
    setConfirmSheet({
      visible: true,
      title: 'Remove Place',
      message: `Remove "${name}" from saved places?`,
      variant: 'warning',
      buttons: [
        { text: 'Remove', style: 'destructive', onPress: () => deleteSavedAddress(id) },
        { text: 'Cancel', style: 'cancel' },
      ],
    });
  };

  const resetForm = () => {
    setPlaceName(''); setSelectedType('Home'); setSearchText(''); setSelectedPlace(null); clearPredictions();
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
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Saved Places</Text>
        <View style={{ width: 44 }} />
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
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
                        <Ionicons name={t.icon as any} size={16} color={selectedType === t.key ? t.color : colors.textDim} />
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
                  {searching && <ActivityIndicator size="small" color={colors.primary} style={{ marginTop: 8 }} />}

                  {predictions.length > 0 && (
                    <View style={styles.predList}>
                      {predictions.slice(0, 5).map((p) => (
                        <TouchableOpacity key={p.place_id} style={styles.predItem} onPress={() => selectPrediction(p)}>
                          <Ionicons name="location-outline" size={16} color={colors.textDim} />
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
                  <Ionicons name="add-circle" size={22} color={colors.primary} />
                  <Text style={styles.addBtnText}>Add New Place</Text>
                </TouchableOpacity>
              )
            }
          />
        </KeyboardAvoidingView>
      )}
      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: 20 },

    // Place item
    placeItem: {
      flexDirection: 'row', alignItems: 'center', paddingVertical: 14,
      borderBottomWidth: 1, borderBottomColor: colors.surfaceLight,
    },
    placeIcon: {
      width: 44, height: 44, borderRadius: 12, justifyContent: 'center', alignItems: 'center', marginRight: 14,
    },
    placeName: { fontSize: 15, fontWeight: '600', color: colors.text },
    placeAddr: { fontSize: 12, color: colors.textDim, marginTop: 2 },

    // Empty
    empty: { alignItems: 'center', paddingVertical: 40 },
    emptyTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 12 },
    emptySub: { fontSize: 13, color: colors.textDim, marginTop: 4, textAlign: 'center', paddingHorizontal: 20 },

    // Add button
    addBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      paddingVertical: 16, borderRadius: 14, borderWidth: 2, borderColor: colors.primary,
      borderStyle: 'dashed', marginTop: 16,
    },
    addBtnText: { fontSize: 15, fontWeight: '700', color: colors.primary },

    // Add form
    addForm: { backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 20, marginTop: 16 },
    formLabel: { fontSize: 12, fontWeight: '600', color: '#888', marginBottom: 6, marginTop: 14 },
    input: {
      backgroundColor: colors.surface, borderRadius: 12, paddingHorizontal: 16, paddingVertical: 14,
      fontSize: 15, color: colors.text, borderWidth: 1, borderColor: colors.border,
    },
    typeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    typeChip: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20,
      borderWidth: 1.5, borderColor: colors.border, backgroundColor: colors.surface,
    },
    typeChipText: { fontSize: 13, fontWeight: '600', color: colors.textDim },

    predList: { backgroundColor: colors.surface, borderRadius: 12, marginTop: 8, borderWidth: 1, borderColor: colors.border },
    predItem: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingHorizontal: 14, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.surfaceLight,
    },
    predText: { flex: 1, fontSize: 14, color: colors.text },

    selectedAddr: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      marginTop: 10, padding: 12, backgroundColor: '#F0FFF4', borderRadius: 10,
    },
    selectedAddrText: { flex: 1, fontSize: 13, color: '#059669' },

    formActions: { flexDirection: 'row', gap: 12, marginTop: 20 },
    cancelBtn: { flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 12, backgroundColor: colors.border },
    cancelText: { fontSize: 15, fontWeight: '600', color: colors.textDim },
    saveBtn: { flex: 2, alignItems: 'center', paddingVertical: 14, borderRadius: 12, backgroundColor: colors.primary },
    saveText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
  });
}
