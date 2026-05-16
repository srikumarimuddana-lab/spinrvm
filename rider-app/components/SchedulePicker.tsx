import React, { useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Pressable, ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const WEEK_DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const MINUTES = [0, 15, 30, 45];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

function toDateStr(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

interface Props {
  visible: boolean;
  onClose: () => void;
  onConfirm: (date: Date) => void;
  minDate: Date;
  maxDate: Date;
}

export default function SchedulePicker({ visible, onClose, onConfirm, minDate, maxDate }: Props) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const initDate = new Date(Date.now() + 30 * 60000);
  const minStr = toDateStr(minDate);
  const maxStr = toDateStr(maxDate);
  const todayStr = toDateStr(new Date());

  const [viewYear, setViewYear] = useState(initDate.getFullYear());
  const [viewMonth, setViewMonth] = useState(initDate.getMonth());
  const [selectedDay, setSelectedDay] = useState(toDateStr(initDate));
  const [selectedHour, setSelectedHour] = useState(initDate.getHours());
  const [selectedMinute, setSelectedMinute] = useState(
    Math.ceil(initDate.getMinutes() / 15) * 15 % 60,
  );

  // Always 6 rows × 7 cols = 42 fixed cells — child count never changes
  const calendarRows = useMemo(() => {
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const firstDay = new Date(viewYear, viewMonth, 1).getDay();
    const flat: (number | null)[] = Array(firstDay).fill(null);
    for (let d = 1; d <= daysInMonth; d++) flat.push(d);
    while (flat.length < 42) flat.push(null);
    const rows: (number | null)[][] = [];
    for (let i = 0; i < 42; i += 7) rows.push(flat.slice(i, i + 7));
    return rows; // always 6 rows
  }, [viewYear, viewMonth]);

  const dayStr = (day: number) =>
    `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;

  const canPrev = useMemo(() => {
    const pm = viewMonth === 0 ? 11 : viewMonth - 1;
    const py = viewMonth === 0 ? viewYear - 1 : viewYear;
    return toDateStr(new Date(py, pm + 1, 0)) >= minStr;
  }, [viewYear, viewMonth, minStr]);

  const canNext = useMemo(() => {
    const nm = viewMonth === 11 ? 0 : viewMonth + 1;
    const ny = viewMonth === 11 ? viewYear + 1 : viewYear;
    return `${ny}-${String(nm + 1).padStart(2, '0')}-01` <= maxStr;
  }, [viewYear, viewMonth, maxStr]);

  const navigateMonth = (dir: 1 | -1) => {
    let m = viewMonth + dir;
    let y = viewYear;
    if (m < 0) { m = 11; y--; }
    if (m > 11) { m = 0; y++; }
    setViewMonth(m);
    setViewYear(y);
  };

  const formatHour = (h: number) => `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`;

  const formatPreviewTime = () => {
    const h12 = selectedHour % 12 || 12;
    const ampm = selectedHour < 12 ? 'AM' : 'PM';
    return `${h12}:${String(selectedMinute).padStart(2, '0')} ${ampm}`;
  };

  const formatPreview = () => {
    const d = new Date(selectedDay + 'T00:00:00');
    const dateStr = d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
    return `${dateStr} at ${formatPreviewTime()}`;
  };

  const handleConfirm = () => {
    const [y, m, d] = selectedDay.split('-').map(Number);
    onConfirm(new Date(y, m - 1, d, selectedHour, selectedMinute));
  };

  // Rendered as absoluteFill inside the screen — no Modal, no separate native window.
  // Visibility via pointerEvents so the view tree stays stable (no insert/remove).
  return (
    <View
      style={[StyleSheet.absoluteFill, styles.container]}
      pointerEvents={visible ? 'box-none' : 'none'}
    >
      {visible && (
        <>
          <Pressable style={[StyleSheet.absoluteFill, styles.backdrop]} onPress={onClose} />
          <View style={styles.sheet}>
            <View style={styles.handle} />
            <Text style={styles.title}>Schedule Ride</Text>

            <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">

              {/* Month navigation */}
              <View style={styles.monthNav}>
                <TouchableOpacity onPress={() => navigateMonth(-1)} disabled={!canPrev} style={styles.navBtn}>
                  <Ionicons name="chevron-back" size={20} color={canPrev ? colors.text : colors.border} />
                </TouchableOpacity>
                <Text style={styles.monthLabel}>{MONTH_NAMES[viewMonth]} {viewYear}</Text>
                <TouchableOpacity onPress={() => navigateMonth(1)} disabled={!canNext} style={styles.navBtn}>
                  <Ionicons name="chevron-forward" size={20} color={canNext ? colors.text : colors.border} />
                </TouchableOpacity>
              </View>

              {/* Weekday headers — always 7 children */}
              <View style={styles.weekRow}>
                {WEEK_DAYS.map(wd => (
                  <Text key={wd} style={styles.weekDay}>{wd}</Text>
                ))}
              </View>

              {/* Calendar grid — always 6 rows × 7 cols, stable child counts */}
              <View>
                {calendarRows.map((row, ri) => (
                  <View key={`row-${ri}`} style={styles.gridRow}>
                    {row.map((day, ci) => {
                      if (!day) return <View key={`e-${ri}-${ci}`} style={styles.dayCell} />;
                      const ds = dayStr(day);
                      const disabled = ds < minStr || ds > maxStr;
                      const selected = ds === selectedDay;
                      const isToday = ds === todayStr;
                      return (
                        <TouchableOpacity
                          key={ds}
                          style={[
                            styles.dayCell,
                            selected && { backgroundColor: colors.primary },
                            !selected && isToday && styles.todayRing,
                          ]}
                          onPress={() => setSelectedDay(ds)}
                          disabled={disabled}
                          activeOpacity={0.7}
                        >
                          <Text style={[
                            styles.dayText,
                            disabled && styles.dayDisabled,
                            selected && styles.daySelected,
                            !disabled && !selected && isToday && { color: colors.primary, fontWeight: '700' },
                          ]}>
                            {day}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                ))}
              </View>

              {/* Hour chips */}
              <View style={styles.section}>
                <Text style={styles.sectionLabel}>Hour</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 2 }}>
                  {HOURS.map(h => (
                    <TouchableOpacity
                      key={h}
                      style={[styles.chip, selectedHour === h && { backgroundColor: colors.primary + '15', borderColor: colors.primary }]}
                      onPress={() => setSelectedHour(h)}
                    >
                      <Text style={[styles.chipText, selectedHour === h && { color: colors.primary }]}>
                        {formatHour(h)}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
              </View>

              {/* Minute chips */}
              <View style={styles.section}>
                <Text style={styles.sectionLabel}>Minute</Text>
                <View style={styles.minuteRow}>
                  {MINUTES.map(m => (
                    <TouchableOpacity
                      key={m}
                      style={[styles.minuteChip, selectedMinute === m && { backgroundColor: colors.primary + '15', borderColor: colors.primary }]}
                      onPress={() => setSelectedMinute(m)}
                    >
                      <Text style={[styles.minuteText, selectedMinute === m && { color: colors.primary }]}>
                        :{String(m).padStart(2, '0')}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>

              {/* Preview */}
              <View style={[styles.preview, { backgroundColor: colors.primary + '10' }]}>
                <Ionicons name="calendar-outline" size={16} color={colors.primary} />
                <Text style={[styles.previewText, { color: colors.primary }]}>{formatPreview()}</Text>
              </View>

            </ScrollView>

            <TouchableOpacity
              style={[styles.confirmBtn, { backgroundColor: colors.primary }]}
              onPress={handleConfirm}
            >
              <Ionicons name="checkmark-circle" size={20} color="#FFF" />
              <Text style={styles.confirmText}>Schedule for {formatPreviewTime()}</Text>
            </TouchableOpacity>
          </View>
        </>
      )}
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { justifyContent: 'flex-end', zIndex: 999 },
    backdrop: { backgroundColor: 'rgba(0,0,0,0.5)' },
    sheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 20, borderTopRightRadius: 20,
      paddingBottom: 30, maxHeight: '90%',
    },
    handle: {
      width: 40, height: 4, borderRadius: 2,
      backgroundColor: colors.border, alignSelf: 'center', marginTop: 10,
    },
    title: {
      fontSize: 18, fontWeight: '700', color: colors.text,
      textAlign: 'center', marginVertical: 14,
    },
    monthNav: {
      flexDirection: 'row', alignItems: 'center',
      justifyContent: 'space-between', paddingHorizontal: 16, marginBottom: 8,
    },
    navBtn: { width: 36, height: 36, justifyContent: 'center', alignItems: 'center' },
    monthLabel: { fontSize: 16, fontWeight: '600', color: colors.text },
    weekRow: { flexDirection: 'row', paddingHorizontal: 8, marginBottom: 4 },
    weekDay: { flex: 1, textAlign: 'center', fontSize: 12, fontWeight: '600', color: colors.textDim },
    gridRow: { flexDirection: 'row', paddingHorizontal: 8 },
    dayCell: {
      flex: 1, aspectRatio: 1,
      justifyContent: 'center', alignItems: 'center', borderRadius: 100,
    },
    todayRing: { borderWidth: 1.5, borderColor: colors.primary },
    dayText: { fontSize: 14, fontWeight: '500', color: colors.text },
    dayDisabled: { color: colors.border },
    daySelected: { color: '#FFF', fontWeight: '700' },
    section: { paddingHorizontal: 16, marginTop: 16 },
    sectionLabel: { fontSize: 13, fontWeight: '600', color: colors.textDim, marginBottom: 8 },
    chip: {
      paddingHorizontal: 14, paddingVertical: 10, borderRadius: 10,
      borderWidth: 1.5, borderColor: colors.border, backgroundColor: colors.surfaceLight,
    },
    chipText: { fontSize: 13, fontWeight: '600', color: colors.text },
    minuteRow: { flexDirection: 'row', gap: 10 },
    minuteChip: {
      flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 10,
      borderWidth: 1.5, borderColor: colors.border, backgroundColor: colors.surfaceLight,
    },
    minuteText: { fontSize: 15, fontWeight: '700', color: colors.text },
    preview: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 8, paddingVertical: 12, marginHorizontal: 16, marginTop: 16, borderRadius: 10,
    },
    previewText: { fontSize: 14, fontWeight: '600' },
    confirmBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
      marginHorizontal: 16, marginTop: 14, marginBottom: 4, paddingVertical: 16, borderRadius: 14,
    },
    confirmText: { fontSize: 16, fontWeight: '700', color: '#FFF' },
  });
}
