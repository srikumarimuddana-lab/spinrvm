import React, { useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Modal, ScrollView,
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

  const calendarDays = useMemo(() => {
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const firstDay = new Date(viewYear, viewMonth, 1).getDay();
    const days: (number | null)[] = Array(firstDay).fill(null);
    for (let d = 1; d <= daysInMonth; d++) days.push(d);
    while (days.length % 7 !== 0) days.push(null);
    return days;
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
    if (!selectedDay) return '';
    const d = new Date(selectedDay + 'T00:00:00');
    const dateStr = d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
    return `${dateStr} at ${formatPreviewTime()}`;
  };

  const handleConfirm = () => {
    if (!selectedDay) return;
    const [y, m, d] = selectedDay.split('-').map(Number);
    onConfirm(new Date(y, m - 1, d, selectedHour, selectedMinute));
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <TouchableOpacity style={styles.overlay} activeOpacity={1} onPress={onClose}>
        <TouchableOpacity activeOpacity={1} style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Schedule Ride</Text>

          <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">

            {/* ── Month navigation ── */}
            <View style={styles.monthNav}>
              <TouchableOpacity onPress={() => navigateMonth(-1)} disabled={!canPrev} style={styles.navBtn}>
                <Ionicons name="chevron-back" size={20} color={canPrev ? colors.text : colors.border} />
              </TouchableOpacity>
              <Text style={styles.monthLabel}>{MONTH_NAMES[viewMonth]} {viewYear}</Text>
              <TouchableOpacity onPress={() => navigateMonth(1)} disabled={!canNext} style={styles.navBtn}>
                <Ionicons name="chevron-forward" size={20} color={canNext ? colors.text : colors.border} />
              </TouchableOpacity>
            </View>

            {/* ── Weekday headers ── */}
            <View style={styles.weekRow}>
              {WEEK_DAYS.map(wd => (
                <Text key={wd} style={styles.weekDay}>{wd}</Text>
              ))}
            </View>

            {/* ── Day grid ── */}
            <View style={styles.grid}>
              {calendarDays.map((day, idx) => {
                if (!day) return <View key={`empty-${idx}`} style={styles.dayCell} />;
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

            {/* ── Hour chips ── */}
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

            {/* ── Minute chips ── */}
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

            {/* ── Preview ── */}
            {selectedDay ? (
              <View style={[styles.preview, { backgroundColor: colors.primary + '10' }]}>
                <Ionicons name="calendar-outline" size={16} color={colors.primary} />
                <Text style={[styles.previewText, { color: colors.primary }]}>{formatPreview()}</Text>
              </View>
            ) : null}

          </ScrollView>

          <TouchableOpacity
            style={[styles.confirmBtn, { backgroundColor: colors.primary }, !selectedDay && { opacity: 0.5 }]}
            onPress={handleConfirm}
            disabled={!selectedDay}
          >
            <Ionicons name="checkmark-circle" size={20} color="#FFF" />
            <Text style={styles.confirmText}>
              {selectedDay ? `Schedule for ${formatPreviewTime()}` : 'Select a Date'}
            </Text>
          </TouchableOpacity>
        </TouchableOpacity>
      </TouchableOpacity>
    </Modal>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end' },
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
    weekDay: {
      flex: 1, textAlign: 'center', fontSize: 12,
      fontWeight: '600', color: colors.textDim,
    },
    grid: { flexDirection: 'row', flexWrap: 'wrap', paddingHorizontal: 8 },
    dayCell: {
      width: '14.28%', aspectRatio: 1,
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
