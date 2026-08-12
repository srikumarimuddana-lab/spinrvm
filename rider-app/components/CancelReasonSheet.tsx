import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Text, TouchableOpacity, View, StyleSheet, useWindowDimensions } from 'react-native';
import BottomSheet, { BottomSheetView, BottomSheetBackdrop, BottomSheetTextInput } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

// Default rider-facing cancellation reasons. The last entry is treated as the
// "free text only" option, but a note can be added alongside any reason.
export const RIDER_CANCEL_REASONS = [
  'Driver is too far / long wait',
  'Booked by mistake',
  'Found another ride',
  'Pickup location is wrong',
  'Changed my plans',
  'Other',
];

interface CancelReasonSheetProps {
  visible: boolean;
  title?: string;
  message?: string;
  reasons?: string[];
  confirmLabel?: string;
  /** Receives the composed reason string (preset + optional note). */
  onConfirm: (reason: string) => void | Promise<void>;
  onClose: () => void;
}

export default function CancelReasonSheet({
  visible,
  title = 'Why are you cancelling?',
  message,
  reasons = RIDER_CANCEL_REASONS,
  confirmLabel = 'Confirm cancellation',
  onConfirm,
  onClose,
}: CancelReasonSheetProps) {
  const { colors } = useTheme();
  const { height: windowHeight } = useWindowDimensions();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const sheetRef = useRef<BottomSheet>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [isPending, setIsPending] = useState(false);

  useEffect(() => {
    if (visible) {
      // Resets local form state when the sheet opens; visible is the only
      // dep, and neither selected nor note is a dep, so no loop.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelected(null);
      setNote('');
      sheetRef.current?.snapToIndex(0);
    } else {
      sheetRef.current?.close();
    }
  }, [visible]);

  const handleSheetChange = useCallback(
    (index: number) => {
      if (index === -1 && !isPending) onClose();
    },
    [onClose, isPending],
  );

  const renderBackdrop = useCallback(
    (props: BottomSheetBackdropProps) => (
      <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} opacity={0.5} pressBehavior={isPending ? 'none' : 'close'} />
    ),
    [isPending],
  );

  const composed = (() => {
    const n = note.trim();
    if (selected && selected !== 'Other') return n ? `${selected} — ${n}` : selected;
    return n; // Other / no preset → just the note
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

  if (!visible) return null;

  return (
    <BottomSheet
      ref={sheetRef}
      index={0}
      enableDynamicSizing
      maxDynamicContentSize={windowHeight * 0.8}
      enablePanDownToClose={!isPending}
      onChange={handleSheetChange}
      backdropComponent={renderBackdrop}
      backgroundStyle={styles.sheetBg}
      handleIndicatorStyle={styles.handle}
      style={styles.sheet}
      keyboardBehavior="interactive"
    >
      <BottomSheetView style={styles.content}>
        <Text style={styles.title}>{title}</Text>
        {message ? <Text style={styles.message}>{message}</Text> : null}

        <View style={styles.list}>
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
                <Text style={[styles.rowText, active && { color: colors.text }]}>{r}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <BottomSheetTextInput
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
          <Text style={styles.buttonText}>{confirmLabel}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.button, styles.cancelButton]} onPress={onClose} activeOpacity={0.7} disabled={isPending}>
          <Text style={[styles.buttonText, styles.cancelButtonText]}>Keep ride</Text>
        </TouchableOpacity>
      </BottomSheetView>
    </BottomSheet>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    sheet: { zIndex: 9998 },
    sheetBg: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.12,
      shadowRadius: 16,
      elevation: 20,
    },
    handle: { backgroundColor: colors.border, width: 40 },
    content: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 36 },
    title: { fontSize: 19, fontWeight: '700', color: colors.text, marginBottom: 6 },
    message: { fontSize: 13, color: colors.textSecondary, lineHeight: 18, marginBottom: 12 },
    list: { gap: 4, marginBottom: 12 },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 12,
      paddingHorizontal: 12,
      borderRadius: 12,
      backgroundColor: colors.surfaceLight,
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
