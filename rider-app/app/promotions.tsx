import React, { useEffect, useState, useMemo } from 'react';
import {
  View, StyleSheet, TouchableOpacity, FlatList, TextInput,
  ActivityIndicator,
} from 'react-native';
import { Text } from '@shared/components/Text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT } from '@shared/utils/responsive';

interface Promo {
  promo_id: string;
  code: string;
  discount_type: string;
  discount_value: number;
  max_discount?: number;
  description: string;
  expiry_date?: string;
}

export default function PromotionsScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [promos, setPromos] = useState<Promo[]>([]);
  const [loading, setLoading] = useState(true);
  const [code, setCode] = useState('');
  const [applying, setApplying] = useState(false);
  const loadPromos = async () => {
    setLoading(true);
    try {
      const res = await api.get<Promo[]>('/promo/available?ride_fare=20');
      setPromos((res.data as Promo[]) || []);
    } catch {}
    finally { setLoading(false); }
  };

  // Mount-only load; deps are empty so the state loadPromos sets can't
  // retrigger this effect.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadPromos(); }, []);

  const handleApply = async () => {
    const c = code.trim().toUpperCase();
    if (!c) return;
    setApplying(true);
    try {
      const res = await api.post<{ discount_type?: string; discount_value?: number }>('/promo/validate', { code: c, ride_fare: 20 });
      showToast('Promo Valid!', `${res.data.discount_type === 'percentage' ? `${res.data.discount_value}% off` : `$${res.data.discount_value} off`} — will apply on your next ride.`, 'success');
      setCode('');
      loadPromos();
    } catch (err: any) {
      // Neutral title: the failure isn't always a bad code (rate limit,
      // expired promo, server hiccup) — let the backend's message say why.
      showToast("Couldn't Apply Code", getApiErrorMessage(err, 'This promo code is not valid.'), 'danger');
    } finally { setApplying(false); }
  };

  const formatDiscount = (p: Promo) => {
    if (p.discount_type === 'percentage') {
      return `${p.discount_value}% off${p.max_discount ? ` (max $${p.max_discount})` : ''}`;
    }
    return `$${p.discount_value.toFixed(2)} off`;
  };

  const formatExpiry = (date?: string) => {
    if (!date) return '';
    try {
      const d = new Date(date);
      return `Expires ${d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' })}`;
    } catch { return ''; }
  };

  const renderPromo = ({ item }: { item: Promo }) => (
    <View style={styles.promoCard}>
      <View style={styles.promoLeft}>
        <View style={styles.promoIcon}>
          <Ionicons name="pricetag" size={20} color={colors.primary} />
        </View>
      </View>
      <View style={{ flex: 1 }}>
        <View style={styles.codeRow}>
          <Text style={styles.promoCode}>{item.code}</Text>
          <View style={styles.discountBadge}>
            <Text style={styles.discountText}>{formatDiscount(item)}</Text>
          </View>
        </View>
        {item.description ? <Text style={styles.promoDesc}>{item.description}</Text> : null}
        {item.expiry_date ? <Text style={styles.promoExpiry}>{formatExpiry(item.expiry_date)}</Text> : null}
      </View>
    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Promotions</Text>
        <View style={{ width: 44 }} />
      </View>

      {/* Enter code */}
      <View style={styles.inputSection}>
        <TextInput
          style={styles.input}
          placeholder="Enter promo code"
          placeholderTextColor="#BBB"
          value={code}
          onChangeText={(t) => setCode(t.toUpperCase())}
          autoCapitalize="characters"
        />
        <TouchableOpacity
          style={[styles.applyBtn, (!code.trim() || applying) && { opacity: 0.5 }]}
          onPress={handleApply}
          disabled={!code.trim() || applying}
        >
          {applying ? <ActivityIndicator size="small" color="#FFF" /> : <Text style={styles.applyText}>Apply</Text>}
        </TouchableOpacity>
      </View>

      {/* Available promos */}
      {loading ? (
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : (
        <FlatList
          data={promos}
          renderItem={renderPromo}
          keyExtractor={(item) => item.promo_id}
          contentContainerStyle={styles.list}
          ListHeaderComponent={
            promos.length > 0 ? <Text style={styles.sectionTitle}>Available for you</Text> : null
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="gift-outline" size={48} color="#DDD" />
              <Text style={styles.emptyTitle}>No promotions available</Text>
              <Text style={styles.emptySub}>Enter a promo code above or check back later</Text>
            </View>
          }
        />
      )}
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: SPACING.md, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: 20 },

    inputSection: { flexDirection: 'row', gap: 10, paddingHorizontal: 20, paddingVertical: SPACING.md },
    input: {
      flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 14, paddingHorizontal: SPACING.md, paddingVertical: 14,
      fontSize: FONT.bodyLg, fontWeight: '600', color: colors.text, letterSpacing: 1,
    },
    applyBtn: { backgroundColor: colors.primary, borderRadius: 14, paddingHorizontal: SPACING.lg, justifyContent: 'center' },
    applyText: { fontSize: 15, fontWeight: '700', color: '#FFF' },

    sectionTitle: { fontSize: FONT.bodySm, fontWeight: '700', color: colors.textDim, letterSpacing: 0.5, marginBottom: 12 },

    promoCard: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight, borderRadius: 16, padding: SPACING.md, marginBottom: 12,
      borderLeftWidth: 4, borderLeftColor: colors.primary,
    },
    promoLeft: { marginRight: 14 },
    promoIcon: {
      width: 44, height: 44, borderRadius: 12, backgroundColor: colors.dangerBg,
      justifyContent: 'center', alignItems: 'center',
    },
    codeRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: SPACING.xs },
    promoCode: { fontSize: FONT.bodyLg, fontWeight: '800', color: colors.text, letterSpacing: 1 },
    discountBadge: { backgroundColor: colors.successBg, paddingHorizontal: SPACING.sm, paddingVertical: 2, borderRadius: 6 },
    discountText: { fontSize: 12, fontWeight: '700', color: colors.success },
    promoDesc: { fontSize: FONT.bodySm, color: colors.textDim, marginBottom: 2 },
    promoExpiry: { fontSize: FONT.label, color: colors.textDim },

    empty: { alignItems: 'center', paddingVertical: 40 },
    emptyTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 12 },
    emptySub: { fontSize: FONT.bodySm, color: colors.textDim, marginTop: SPACING.xs, textAlign: 'center' },
  });
}
