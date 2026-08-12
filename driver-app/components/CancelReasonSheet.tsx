import React, { useEffect, useMemo, useState } from 'react';
import {
  Text, TouchableOpacity, View, StyleSheet, Modal, TextInput,
  KeyboardAvoidingView, Platform, Pressable, ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

// Default driver-facing cancellation reasons (last = free-text only).
//
// 'Service animal — could not accommodate' is matched (case-insensitive
// substring) by backend/routes/drivers/ride_cancel.py to flag the refusal
// for trust & safety — service animal accommodation is mandatory (CLAUDE.md
// Accessibility). Keep the wording containing "service animal" if you edit
// this string, or update the backend matcher in the same change.
export const DRIVER_CANCEL_REASONS = [
  'Rider no-show',
  "Can't reach rider",
  'Unsafe or wrong pickup',
  'Vehicle issue',
  'Pickup too far',
  'Service animal — could not accommodate',
  'Other',
];

interface CancelReasonSheetProps {
  visible: boolean;
  title?: string;
  message?: string;
  reasons?: string[];
  confirmLabel?: string;
  onConfirm: (reason: string) => void | Promise<void>;
  onClose: () => void;
}

export default function CancelReasonSheet({
  visible,
  title = 'Why are you cancelling?',
  message,
  reasons = DRIVER_CANCEL_REASONS,
  confirmLabel = 'Confirm cancellation',
  onConfirm,
  onClose,
}: CancelReasonSheetProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [isPending, setIsPending] = useState(false);

  useEffect(() => {
    if (visible) {
      setSelected(null);
      setNote('');
    }
  }, [visible]);

  const composed = (() => {
    const n = note.trim();
    if (selected && selected !== 'Other') return n ? `${selected} — ${n}` : selected;
    return n;
  })();
  const canConfirm = !!composed && !isPending;

  const handleConfirm = async () => {
    if (!canConfirm) return;
    setIsPending(true);
    try {
      await onConfirm(composed);
    } finally {
      setIsPending(false);
      onClose();
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={() => !isPending && onClose()}>
      <Pressable style={styles.backdrop} onPress={() => !isPending && onClose()} />
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.kav}
        pointerEvents="box-none"
      >
        <View style={styles.card}>
          <View style={styles.handle} />
          <Text allowFontScaling={false} style={styles.title}>{title}</Text>
          {message ? <Text allowFontScaling={false} style={styles.message}>{message}</Text> : null}

          <ScrollView style={styles.list} keyboardShouldPersistTaps="handled">
            {reasons.map((r) => {
              const active = selected === r;
              return (
                <TouchableOpacity
                  key={r}
                  style={[styles.row, active && styles.rowActive]}
                  onPress={() => setSelected(r)}
                  activeOpacity={0.7}
                  disabled={isPending}
                >
                  <Ionicons
                    name={active ? 'radio-button-on' : 'radio-button-off'}
                    size={20}
                    color={active ? colors.primary : colors.textSecondary}
                  />
                  <Text allowFontScaling={false} style={[styles.rowText, active && { color: colors.text }]}>{r}</Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>

          <TextInput
            style={styles.note}
            placeholder="Add a note (optional)"
            placeholderTextColor={colors.textSecondary}
            value={note}
            onChangeText={setNote}
            multiline
            editable={!isPending}
          />

          <TouchableOpacity
            style={[styles.button, styles.destructiveButton, !canConfirm && { opacity: 0.5 }]}
            onPress={handleConfirm}
            activeOpacity={0.8}
            disabled={!canConfirm}
          >
            <Text allowFontScaling={false} style={styles.buttonText}>{confirmLabel}</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.button, styles.cancelButton]} onPress={onClose} activeOpacity={0.7} disabled={isPending}>
            <Text allowFontScaling={false} style={[styles.buttonText, styles.cancelButtonText]}>Keep ride</Text>
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)' },
    kav: { flex: 1, justifyContent: 'flex-end' },
    card: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      paddingHorizontal: 24,
      paddingTop: 10,
      paddingBottom: 36,
    },
    handle: { alignSelf: 'center', width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border, marginBottom: 12 },
    title: { fontSize: 19, fontWeight: '700', color: colors.text, marginBottom: 6 },
    message: { fontSize: 13, color: colors.textSecondary, lineHeight: 18, marginBottom: 12 },
    list: { maxHeight: 280, marginBottom: 12 },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 12,
      paddingHorizontal: 12,
      borderRadius: 12,
      backgroundColor: colors.surfaceLight,
      marginBottom: 4,
    },
    rowActive: { backgroundColor: colors.primary + '18' },
    rowText: { fontSize: 15, color: colors.textSecondary, flex: 1 },
    note: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      padding: 12,
      minHeight: 56,
      color: colors.text,
      fontSize: 14,
      marginBottom: 16,
      textAlignVertical: 'top',
    },
    button: { width: '100%', paddingVertical: 14, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginTop: 8 },
    buttonText: { fontSize: 16, fontWeight: '600', color: '#fff' },
    destructiveButton: { backgroundColor: '#EF4444' },
    cancelButton: { backgroundColor: colors.surfaceLight },
    cancelButtonText: { color: colors.textSecondary },
  });
}
