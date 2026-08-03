import React, { useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Linking, Platform, Modal } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';

interface ForceUpdateOverlayProps {
  visible: boolean;
  minVersion?: string;
  storeUrl?: string;
}

/**
 * Full-screen, non-dismissible overlay shown when the backend's
 * ForcedUpgradeMiddleware rejects a request with 426 (ACTION_ITEMS.md E3).
 * Mount once near the root of each app and drive `visible` from the
 * onForceUpgrade() callback registered with the shared API client.
 */
export function ForceUpdateOverlay({ visible, minVersion, storeUrl }: ForceUpdateOverlayProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  return (
    <Modal visible={visible} transparent={false} animationType="fade" onRequestClose={() => {}}>
      <View style={styles.container}>
        <View style={styles.content}>
          <Text style={styles.icon}>⬆️</Text>
          <Text style={styles.title}>Update Required</Text>
          <Text style={styles.message}>
            A new version of Spinr is required to continue.
            {minVersion ? ` (minimum version ${minVersion})` : ''}
          </Text>
          {storeUrl && (
            <TouchableOpacity style={styles.updateButton} onPress={() => Linking.openURL(storeUrl)}>
              <Text style={styles.updateButtonText}>
                {Platform.OS === 'ios' ? 'Update on the App Store' : 'Update on Google Play'}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      </View>
    </Modal>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: colors.background,
      padding: 24,
    },
    content: {
      alignItems: 'center',
      maxWidth: 320,
    },
    icon: {
      fontSize: 48,
      marginBottom: 16,
    },
    title: {
      fontSize: 20,
      fontWeight: '600',
      color: colors.text,
      marginBottom: 8,
      textAlign: 'center',
    },
    message: {
      fontSize: 14,
      color: colors.textSecondary,
      textAlign: 'center',
      marginBottom: 24,
      lineHeight: 20,
    },
    updateButton: {
      backgroundColor: colors.primary,
      paddingHorizontal: 32,
      paddingVertical: 12,
      borderRadius: 8,
    },
    updateButtonText: {
      color: '#FFFFFF',
      fontSize: 16,
      fontWeight: '600',
    },
  });
}

export default ForceUpdateOverlay;
