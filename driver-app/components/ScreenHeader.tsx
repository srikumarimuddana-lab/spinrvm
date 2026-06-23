import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';

interface ScreenHeaderProps {
  title: string;
  subtitle?: string;
  onBack?: () => void;
  rightAction?: React.ReactNode;
  /** Extra content rendered inside the gradient, below the title row (e.g. a search bar). */
  children?: React.ReactNode;
}

/**
 * Shared brand gradient header for driver detail screens (Quests, Lost & Found,
 * Help Center, Spinr Pass, Documents, Vehicle Info, Settings…).
 *
 * USAGE — keep the screen rendering reliably:
 *  - Render as the FIRST child of a `<View style={{ flex: 1 }}>` root, ABOVE the
 *    screen's ScrollView. NEVER place it inside the ScrollView content.
 *  - It owns the top safe-area inset, so the root must NOT be a SafeAreaView
 *    with `edges={['top']}`.
 *  - The ScrollView's contentContainerStyle must use plain padding — never
 *    `flexGrow: 1` (it collapses the content on-device).
 */
export function ScreenHeader({ title, subtitle, onBack, rightAction, children }: ScreenHeaderProps) {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { colors } = useTheme();

  return (
    <LinearGradient
      colors={[colors.primary, colors.primaryDark]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[styles.wrap, { paddingTop: insets.top + 10 }]}
    >
      <View style={styles.row}>
        <TouchableOpacity onPress={onBack ?? (() => router.back())} style={styles.back} activeOpacity={0.7}>
          <Ionicons name="arrow-back" size={22} color="#fff" />
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1}>{title}</Text>
        <View style={styles.right}>{rightAction}</View>
      </View>
      {!!subtitle && <Text style={styles.subtitle}>{subtitle}</Text>}
      {children}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 16, paddingBottom: 18, borderBottomLeftRadius: 24, borderBottomRightRadius: 24 },
  row: { flexDirection: 'row', alignItems: 'center' },
  back: { width: 40, height: 40, borderRadius: 20, backgroundColor: 'rgba(255,255,255,0.18)', alignItems: 'center', justifyContent: 'center' },
  title: { flex: 1, textAlign: 'center', fontSize: 18, fontWeight: '800', color: '#fff' },
  right: { minWidth: 40, alignItems: 'flex-end' },
  subtitle: { color: 'rgba(255,255,255,0.9)', fontSize: 13, lineHeight: 19, marginTop: 8, textAlign: 'center' },
});

export default ScreenHeader;
