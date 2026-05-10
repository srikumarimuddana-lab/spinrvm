import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';

interface ScreenHeaderProps {
  title: string;
  onBack?: () => void;
  rightAction?: React.ReactNode;
  transparent?: boolean;
}

export function ScreenHeader({ title, onBack, rightAction, transparent }: ScreenHeaderProps) {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { colors } = useTheme();

  return (
    <View
      style={[
        styles.header,
        { paddingTop: insets.top + 12 },
        !transparent && { backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
      ]}
    >
      <TouchableOpacity onPress={onBack ?? (() => router.back())} style={styles.backBtn}>
        <Ionicons name="arrow-back" size={24} color={transparent ? '#fff' : colors.text} />
      </TouchableOpacity>
      <Text style={[styles.headerTitle, { color: transparent ? '#fff' : colors.text }]} numberOfLines={1}>
        {title}
      </Text>
      {rightAction ?? <View style={{ width: 32 }} />}
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingBottom: 16,
    paddingHorizontal: 20,
  },
  backBtn: {
    padding: 4,
    width: 32,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '700',
    flex: 1,
    textAlign: 'center',
  },
});

export default ScreenHeader;
