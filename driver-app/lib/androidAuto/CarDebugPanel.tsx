/**
 * The on-surface debug panel, drawn over the car map.
 *
 * Toggled by the template's idle header action (register.ts) via `useCarDebug`,
 * because the projected surface is non-interactive — Android Auto routes all
 * interaction through template buttons, so a React `Pressable` here would never
 * receive a touch.
 *
 * Display-only, and never shown in production (`isCarDebugAvailable`).
 */
import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useCarDebug, type DebugLine } from './carDebug';

/**
 * Age of a line relative to the NEWEST line, not to wall-clock now.
 *
 * Deliberate: reading `Date.now()` during render is impure (it makes the output
 * depend on when React happens to re-render) and the repo's react-hooks/purity
 * rule rejects it. The newest entry is the effective "now" for this panel, and
 * what actually matters when reading a failure is the spacing BETWEEN events —
 * "template created, then 4s later map ready, then nothing" — which this shows
 * exactly. The newest line reads 0s.
 */
function ageLabel(at: number, newest: number): string {
  const secs = Math.max(0, Math.round((newest - at) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h`;
}

export function CarDebugPanel(): React.ReactElement | null {
  const enabled = useCarDebug((s) => s.enabled);
  const lines = useCarDebug((s) => s.lines);
  const facts = useCarDebug((s) => s.facts);

  if (!enabled) return null;

  // `lines` is newest-first (carDebug.push unshifts), so [0] is the reference
  // point every other row is measured against.
  const newest = lines.length > 0 ? lines[0].at : 0;
  const factRows = Object.entries(facts);

  return (
    <View style={styles.root} pointerEvents="none">
      <Text style={styles.heading}>Spinr · Android Auto debug</Text>

      {factRows.length > 0 && (
        <View style={styles.facts}>
          {factRows.map(([k, v]) => (
            <View key={k} style={styles.factRow}>
              <Text style={styles.factKey}>{k}</Text>
              <Text style={styles.factValue} numberOfLines={1}>{v}</Text>
            </View>
          ))}
        </View>
      )}

      <ScrollView style={styles.log} scrollEnabled={false}>
        {lines.length === 0 ? (
          <Text style={styles.empty}>No events recorded yet.</Text>
        ) : (
          lines.map((l: DebugLine, i: number) => (
            <Text
              key={`${l.at}-${i}`}
              style={[styles.line, l.level === 'error' && styles.lineError]}
              numberOfLines={3}
            >
              <Text style={styles.age}>{ageLabel(l.at, newest)} </Text>
              {l.text}
            </Text>
          ))
        )}
      </ScrollView>

      <Text style={styles.footer}>Header “Debug” toggles this panel</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    // Half-width: the map stays visible alongside, so you can see whether it is
    // drawing at the same time as reading why it might not be.
    width: '52%',
    backgroundColor: 'rgba(6,6,10,0.92)',
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  heading: { color: '#FFFFFF', fontSize: 15, fontWeight: '700', marginBottom: 8 },
  facts: { marginBottom: 10 },
  factRow: { flexDirection: 'row', marginBottom: 2 },
  factKey: { color: '#8A8A99', fontSize: 12, width: 108 },
  factValue: { color: '#E6E6EE', fontSize: 12, flex: 1, fontWeight: '600' },
  log: { flex: 1 },
  line: { color: '#C9C9D4', fontSize: 11, marginBottom: 3, lineHeight: 15 },
  lineError: { color: '#FF6B6B', fontWeight: '600' },
  age: { color: '#6E6E7C' },
  empty: { color: '#6E6E7C', fontSize: 12, fontStyle: 'italic' },
  footer: { color: '#5A5A66', fontSize: 10, marginTop: 8 },
});
