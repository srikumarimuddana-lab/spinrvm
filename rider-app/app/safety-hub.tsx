/**
 * Safety Hub — the "view your safety settings and resources" home.
 *
 * Until this existed, Emergency Contacts and Report a Safety Issue sat as two
 * unrelated rows in the Account menu and a rider who wanted to *check* their
 * safety setup had nowhere to go. This is the calm, out-of-ride counterpart to
 * the SOS panel: same information, but read at leisure rather than in distress.
 *
 * Deliberately NOT a second SOS. There is no alert-firing control here — the
 * panic button lives on the map and in-ride screens where it belongs. The one
 * emergency affordance is the same never-auto-dial "call" row, because someone
 * who opens this screen during an incident should not have to navigate away.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, Linking } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useSafetyPanelConfig } from '@shared/hooks/useSafetyPanelConfig';
import { useEmergencyContacts } from '@shared/hooks/useEmergencyContacts';
import { getSOSLocation } from '@shared/utils/sosLocation';

// Hoisted out of SafetyHubScreen (react-hooks/static-components) — was
// redeclared on every render, resetting any implicit state each time.
// Takes `styles`/`chevronColor` as props instead of closing over them.
function Row({
  icon,
  color,
  title,
  subtitle,
  onPress,
  styles,
  chevronColor,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  title: string;
  subtitle: string;
  onPress: () => void;
  styles: ReturnType<typeof createStyles>;
  chevronColor: string;
}) {
  return (
    <Pressable style={styles.row} onPress={onPress} accessibilityRole="button" accessibilityLabel={title}>
      <Ionicons name={icon} size={20} color={color} />
      <View style={styles.rowText}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.rowSub}>{subtitle}</Text>
      </View>
      <Ionicons name="chevron-forward" size={16} color={chevronColor} />
    </Pressable>
  );
}

export default function SafetyHubScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = React.useMemo(() => createStyles(colors), [colors]);
  const [coords, setCoords] = useState<{ lat?: number; lng?: number }>({});
  const cfg = useSafetyPanelConfig(coords.lat, coords.lng, true);
  const { contacts } = useEmergencyContacts();

  // Needed only to resolve which service area the rider is in, and therefore
  // whether a local-authority row applies. Bounded lookup — never blocks.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const c = await getSOSLocation();
      if (!cancelled) setCoords(c);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <SafeAreaView style={styles.screen} edges={['top', 'bottom']}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} accessibilityRole="button" accessibilityLabel="Go back" hitSlop={12}>
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </Pressable>
        <Text style={styles.headerTitle}>Safety</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
        <Text style={styles.sectionLabel}>In an emergency</Text>
        <View style={styles.card}>
          <Row
            icon="call"
            color="#DC2626"
            title={`Call ${cfg.emergencyNumber}`}
            subtitle="Opens your dialer — Spinr never calls for you"
            onPress={() => Linking.openURL(`tel:${cfg.emergencyNumber}`)}
            styles={styles}
            chevronColor={colors.textDim}
          />
        </View>
        <Text style={styles.hint}>
          During a ride, hold the SOS button to alert your emergency contacts and our safety team, or
          tap it for all safety options.
        </Text>

        <Text style={styles.sectionLabel}>Set up</Text>
        <View style={styles.card}>
          <Row
            icon="people"
            color="#EF4444"
            title="Emergency contacts"
            subtitle={
              contacts.length > 0
                ? `${contacts.length} saved — they're texted when you send an alert`
                : 'None saved — nobody will be texted if you send an alert'
            }
            onPress={() => router.push('/emergency-contacts' as any)}
            styles={styles}
            chevronColor={colors.textDim}
          />
          <View style={styles.divider} />
          <Row
            icon="alert-circle"
            color="#F59E0B"
            title="Report a safety issue"
            subtitle="Tell our team about something that happened"
            onPress={() => router.push('/report-safety' as any)}
            styles={styles}
            chevronColor={colors.textDim}
          />
        </View>

        {!!cfg.authority && (
          <>
            <Text style={styles.sectionLabel}>Local transport authority</Text>
            <View style={styles.card}>
              <Row
                icon="business"
                color="#3B82F6"
                title={cfg.authority.phone ? `Call ${cfg.authority.name}` : cfg.authority.name}
                subtitle={`Report a ride concern — not for emergencies${
                  cfg.authority.hours ? ` · ${cfg.authority.hours}` : ''
                }`}
                onPress={() =>
                  cfg.authority?.phone
                    ? Linking.openURL(`tel:${cfg.authority.phone}`)
                    : cfg.authority?.url
                    ? Linking.openURL(cfg.authority.url)
                    : undefined
                }
                styles={styles}
                chevronColor={colors.textDim}
              />
            </View>
          </>
        )}

        {!!cfg.safetyTeamEmail && (
          <>
            <Text style={styles.sectionLabel}>Contact Spinr Safety</Text>
            <View style={styles.card}>
              <Row
                icon="mail"
                color="#3B82F6"
                title="Email Spinr Safety"
                subtitle={cfg.safetyTeamEmail}
                onPress={() => Linking.openURL(`mailto:${cfg.safetyTeamEmail}`)}
                styles={styles}
                chevronColor={colors.textDim}
              />
            </View>
          </>
        )}

        <Text style={styles.footnote}>
          Spinr alerts your emergency contacts and our safety team. It is not a replacement for
          emergency services.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
    },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    body: { paddingHorizontal: 16, paddingBottom: 32 },
    sectionLabel: {
      fontSize: 12,
      fontWeight: '700',
      letterSpacing: 0.6,
      textTransform: 'uppercase',
      color: colors.textDim,
      marginTop: 20,
      marginBottom: 8,
    },
    card: {
      backgroundColor: colors.surface,
      borderRadius: 14,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: colors.border,
      overflow: 'hidden',
    },
    divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border, marginLeft: 50 },
    row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 14 },
    rowText: { flex: 1, minWidth: 0 },
    rowTitle: { fontSize: 15, fontWeight: '600', color: colors.text },
    rowSub: { fontSize: 12, color: colors.textDim, marginTop: 2 },
    hint: { fontSize: 12, color: colors.textDim, marginTop: 8, lineHeight: 17 },
    footnote: { fontSize: 11, color: colors.textDim, marginTop: 24, textAlign: 'center', lineHeight: 15 },
  });
