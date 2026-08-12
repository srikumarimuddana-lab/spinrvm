import React, { useState, useMemo, useEffect } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Pressable,
  ScrollView, Animated, Dimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useAnimatedValue } from '../hooks/useAnimatedValue';

const { height: SCREEN_H } = Dimensions.get('window');

const WEEK_DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const MINUTES = [0, 15, 30, 45];

function toDateStr(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
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

  // ─── Animations ───────────────────────────────────────────────────────────
  // Always mounted — no conditional rendering. Fabric crashes when you insert
  // children into an absoluteFill view after mount. We animate instead.
  const slideAnim = useAnimatedValue(SCREEN_H);
  const fadeAnim = useAnimatedValue(0);

  useEffect(() => {
    Animated.parallel([
      Animated.spring(slideAnim, {
        toValue: visible ? 0 : SCREEN_H,
        damping: visible ? 22 : 35,
        stiffness: 200,
        useNativeDriver: true,
      }),
      Animated.timing(fadeAnim, {
        toValue: visible ? 1 : 0,
        duration: 220,
        useNativeDriver: true,
      }),
    ]).start();
    // Both are stable Animated.Value instances (useAnimatedValue, created once).
  }, [visible, slideAnim, fadeAnim]);

  // ─── State ────────────────────────────────────────────────────────────────
  // useState's lazy initializer (not useMemo) — React only guarantees a
  // single evaluation for the former; useMemo's cache is not guaranteed to
  // run its factory exactly once, so an impure `Date.now()` call belongs
  // here, not there (react-hooks/purity).
  const [initDate] = useState(() => new Date(Date.now() + 30 * 60_000));
  const minStr = toDateStr(minDate);
  const maxStr = toDateStr(maxDate);
  const todayStr = toDateStr(new Date());

  const [viewYear, setViewYear] = useState(initDate.getFullYear());
  const [viewMonth, setViewMonth] = useState(initDate.getMonth());
  const [selectedDay, setSelectedDay] = useState(toDateStr(initDate));

  const initH = initDate.getHours();
  const [hour12, setHour12] = useState(initH % 12 || 12);
  const [minute, setMinute] = useState(Math.ceil(initDate.getMinutes() / 15) * 15 % 60);
  const [isPM, setIsPM] = useState(initH >= 12);

  // ─── Calendar cells — always 42, stable array ────────────────────────────
  const cells = useMemo<(number | null)[]>(() => {
    const dim = new Date(viewYear, viewMonth + 1, 0).getDate();
    const first = new Date(viewYear, viewMonth, 1).getDay();
    const arr: (number | null)[] = Array(first).fill(null);
    for (let d = 1; d <= dim; d++) arr.push(d);
    while (arr.length < 42) arr.push(null);
    return arr;
  }, [viewYear, viewMonth]);

  const dayStr = (d: number) =>
    `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

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
    let m = viewMonth + dir, y = viewYear;
    if (m < 0) { m = 11; y--; }
    if (m > 11) { m = 0; y++; }
    setViewMonth(m); setViewYear(y);
  };

  const stepHour = (dir: 1 | -1) =>
    setHour12(h => (h === 1 && dir === -1) ? 12 : (h === 12 && dir === 1) ? 1 : h + dir);

  const hour24 = isPM
    ? (hour12 === 12 ? 12 : hour12 + 12)
    : (hour12 === 12 ? 0 : hour12);

  const timeStr = () => `${hour12}:${String(minute).padStart(2, '0')} ${isPM ? 'PM' : 'AM'}`;

  const previewStr = () => {
    const d = new Date(selectedDay + 'T00:00:00');
    return d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' }) + ' · ' + timeStr();
  };

  const handleConfirm = () => {
    const [y, m, d] = selectedDay.split('-').map(Number);
    onConfirm(new Date(y, m - 1, d, hour24, minute));
  };

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <View
      style={[StyleSheet.absoluteFill, styles.overlay]}
      pointerEvents={visible ? 'box-none' : 'none'}
    >
      {/* Backdrop — always mounted, opacity-controlled */}
      <Animated.View
        style={[StyleSheet.absoluteFill, styles.backdrop, { opacity: fadeAnim }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Sheet — always mounted, slides up/down via translateY */}
      <Animated.View style={[styles.sheet, { transform: [{ translateY: slideAnim }] }]}>

        {/* Handle + header */}
        <View style={styles.handleWrap}>
          <View style={[styles.handle, { backgroundColor: colors.border }]} />
        </View>
        <View style={styles.header}>
          <TouchableOpacity style={[styles.iconBtn, { backgroundColor: colors.surfaceLight }]} onPress={onClose}>
            <Ionicons name="close" size={18} color={colors.textDim} />
          </TouchableOpacity>
          <Text style={[styles.title, { color: colors.text }]}>Schedule Ride</Text>
          <View style={{ width: 36 }} />
        </View>

        <ScrollView
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={styles.scrollContent}
        >

          {/* ── Month nav ─────────────────────────────────────────── */}
          <View style={styles.monthRow}>
            <TouchableOpacity
              onPress={() => navigateMonth(-1)}
              disabled={!canPrev}
              style={[styles.iconBtn, { backgroundColor: colors.surfaceLight, opacity: canPrev ? 1 : 0.3 }]}
            >
              <Ionicons name="chevron-back" size={18} color={colors.text} />
            </TouchableOpacity>
            <Text style={[styles.monthTitle, { color: colors.text }]}>
              {MONTH_NAMES[viewMonth]} {viewYear}
            </Text>
            <TouchableOpacity
              onPress={() => navigateMonth(1)}
              disabled={!canNext}
              style={[styles.iconBtn, { backgroundColor: colors.surfaceLight, opacity: canNext ? 1 : 0.3 }]}
            >
              <Ionicons name="chevron-forward" size={18} color={colors.text} />
            </TouchableOpacity>
          </View>

          {/* ── Week day labels ──────────────────────────────────── */}
          <View style={styles.weekRow}>
            {WEEK_DAYS.map(w => (
              <Text key={w} style={[styles.weekLabel, { color: colors.textDim }]}>{w}</Text>
            ))}
          </View>

          {/* ── Calendar grid — 6 rows × 7 cols, no conditional children ── */}
          <View style={styles.gridWrap}>
            {Array.from({ length: 6 }, (_, row) => (
              <View key={row} style={styles.gridRow}>
                {Array.from({ length: 7 }, (_, col) => {
                  const idx = row * 7 + col;
                  const day = cells[idx];

                  if (!day) {
                    return <View key={`blank-${idx}`} style={styles.dayCell} />;
                  }

                  const ds = dayStr(day);
                  const disabled = ds < minStr || ds > maxStr;
                  const selected = ds === selectedDay;
                  const isToday = ds === todayStr;

                  return (
                    <TouchableOpacity
                      key={`day-${ds}`}
                      style={[
                        styles.dayCell,
                        selected && { backgroundColor: colors.primary },
                        !selected && isToday && { borderWidth: 2, borderColor: colors.primary },
                      ]}
                      onPress={() => !disabled && setSelectedDay(ds)}
                      disabled={disabled}
                      activeOpacity={0.7}
                    >
                      <Text style={[
                        styles.dayText,
                        { color: colors.text },
                        disabled && { color: colors.border, opacity: 0.4 },
                        selected && { color: '#FFF', fontWeight: '700' },
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

          {/* ── Divider ───────────────────────────────────────────── */}
          <View style={[styles.divider, { backgroundColor: colors.border }]} />

          {/* ── Time picker ──────────────────────────────────────── */}
          <Text style={[styles.sectionLabel, { color: colors.textDim }]}>Select Time</Text>

          <View style={styles.timeRow}>

            {/* Hour spinner */}
            <View style={[styles.spinnerCard, { backgroundColor: colors.surfaceLight, borderColor: colors.border }]}>
              <TouchableOpacity onPress={() => stepHour(1)} style={styles.stepBtn} hitSlop={{ top: 8, bottom: 4, left: 16, right: 16 }}>
                <Ionicons name="chevron-up" size={20} color={colors.primary} />
              </TouchableOpacity>
              <Text style={[styles.spinnerNum, { color: colors.text }]}>{String(hour12).padStart(2, '0')}</Text>
              <TouchableOpacity onPress={() => stepHour(-1)} style={styles.stepBtn} hitSlop={{ top: 4, bottom: 8, left: 16, right: 16 }}>
                <Ionicons name="chevron-down" size={20} color={colors.primary} />
              </TouchableOpacity>
              <Text style={[styles.spinnerLabel, { color: colors.textDim }]}>HR</Text>
            </View>

            <Text style={[styles.colon, { color: colors.textDim }]}>:</Text>

            {/* Minute pills */}
            <View style={styles.minuteGroup}>
              {MINUTES.map(m => {
                const active = minute === m;
                return (
                  <TouchableOpacity
                    key={m}
                    style={[
                      styles.minPill,
                      { borderColor: active ? colors.primary : colors.border },
                      active && { backgroundColor: colors.primary },
                    ]}
                    onPress={() => setMinute(m)}
                    activeOpacity={0.75}
                  >
                    <Text style={[styles.minPillText, { color: active ? '#FFF' : colors.text }]}>
                      {String(m).padStart(2, '0')}
                    </Text>
                    <Text style={[styles.minPillLabel, { color: active ? 'rgba(255,255,255,0.7)' : colors.textDim }]}>
                      MIN
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            {/* AM / PM */}
            <View style={[styles.ampmCard, { backgroundColor: colors.surfaceLight, borderColor: colors.border }]}>
              <TouchableOpacity
                style={[styles.ampmBtn, !isPM && { backgroundColor: colors.primary }]}
                onPress={() => setIsPM(false)}
              >
                <Text style={[styles.ampmText, { color: !isPM ? '#FFF' : colors.textDim }]}>AM</Text>
              </TouchableOpacity>
              <View style={[styles.ampmDivider, { backgroundColor: colors.border }]} />
              <TouchableOpacity
                style={[styles.ampmBtn, isPM && { backgroundColor: colors.primary }]}
                onPress={() => setIsPM(true)}
              >
                <Text style={[styles.ampmText, { color: isPM ? '#FFF' : colors.textDim }]}>PM</Text>
              </TouchableOpacity>
            </View>
          </View>

          {/* ── Preview chip ─────────────────────────────────────── */}
          <View style={[styles.preview, { backgroundColor: colors.primary + '14' }]}>
            <Ionicons name="calendar-outline" size={17} color={colors.primary} />
            <Text style={[styles.previewText, { color: colors.primary }]}>{previewStr()}</Text>
          </View>

        </ScrollView>

        {/* ── Confirm button ───────────────────────────────────────── */}
        <View style={[styles.footer, { borderTopColor: colors.border }]}>
          <TouchableOpacity
            style={[styles.confirmBtn, { backgroundColor: colors.primary }]}
            onPress={handleConfirm}
            activeOpacity={0.85}
          >
            <Ionicons name="checkmark-circle-outline" size={21} color="#FFF" />
            <Text style={styles.confirmText}>Schedule  ·  {timeStr()}</Text>
          </TouchableOpacity>
        </View>
      </Animated.View>
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    overlay: { justifyContent: 'flex-end', zIndex: 999 },
    backdrop: { backgroundColor: 'rgba(0,0,0,0.55)' },
    sheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 26,
      borderTopRightRadius: 26,
      maxHeight: '92%',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -6 },
      shadowOpacity: 0.18,
      shadowRadius: 16,
      elevation: 24,
    },
    handleWrap: { alignItems: 'center', paddingTop: 10, paddingBottom: 2 },
    handle: { width: 38, height: 4, borderRadius: 2 },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingBottom: 8,
      paddingTop: 4,
    },
    iconBtn: {
      width: 36, height: 36,
      borderRadius: 18,
      justifyContent: 'center', alignItems: 'center',
    },
    title: { fontSize: 17, fontWeight: '700' },
    scrollContent: { paddingBottom: 8 },

    // Month nav
    monthRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      marginBottom: 10,
    },
    monthTitle: { fontSize: 17, fontWeight: '700' },

    // Week labels
    weekRow: { flexDirection: 'row', paddingHorizontal: 8, marginBottom: 2 },
    weekLabel: {
      flex: 1, textAlign: 'center',
      fontSize: 11, fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: 0.4,
      paddingVertical: 4,
    },

    // Grid
    gridWrap: { paddingHorizontal: 8 },
    gridRow: { flexDirection: 'row' },
    dayCell: {
      flex: 1,
      aspectRatio: 1,
      justifyContent: 'center',
      alignItems: 'center',
      borderRadius: 100,
      margin: 2,
    },
    dayText: { fontSize: 14, fontWeight: '500' },

    // Divider
    divider: { height: StyleSheet.hairlineWidth, marginHorizontal: 16, marginTop: 16, marginBottom: 14 },

    // Section label
    sectionLabel: {
      fontSize: 11, fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: 0.6,
      paddingHorizontal: 16,
      marginBottom: 10,
    },

    // Time row
    timeRow: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 16,
      gap: 10,
      marginBottom: 16,
    },

    // Hour spinner card
    spinnerCard: {
      alignItems: 'center',
      borderRadius: 16,
      paddingVertical: 6,
      paddingHorizontal: 10,
      borderWidth: 1.5,
      minWidth: 60,
    },
    stepBtn: { paddingVertical: 4, paddingHorizontal: 8 },
    spinnerNum: { fontSize: 30, fontWeight: '800', lineHeight: 40, letterSpacing: -0.5 },
    spinnerLabel: { fontSize: 9, fontWeight: '700', letterSpacing: 0.5, marginTop: 2 },
    colon: { fontSize: 26, fontWeight: '800', marginBottom: 12 },

    // Minute pills
    minuteGroup: {
      flex: 1,
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 6,
    },
    minPill: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      paddingVertical: 8,
      borderRadius: 12,
      borderWidth: 1.5,
      minWidth: 38,
    },
    minPillText: { fontSize: 15, fontWeight: '800', lineHeight: 18 },
    minPillLabel: { fontSize: 8, fontWeight: '700', letterSpacing: 0.4, marginTop: 1 },

    // AM / PM card
    ampmCard: {
      borderRadius: 14,
      borderWidth: 1.5,
      overflow: 'hidden',
    },
    ampmBtn: {
      paddingVertical: 12,
      paddingHorizontal: 12,
      alignItems: 'center',
    },
    ampmText: { fontSize: 12, fontWeight: '800', letterSpacing: 0.3 },
    ampmDivider: { height: 1 },

    // Preview
    preview: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      marginHorizontal: 16,
      paddingVertical: 13,
      paddingHorizontal: 16,
      borderRadius: 14,
    },
    previewText: { fontSize: 14, fontWeight: '600', flex: 1 },

    // Footer + confirm
    footer: {
      borderTopWidth: StyleSheet.hairlineWidth,
      paddingHorizontal: 16,
      paddingTop: 12,
      paddingBottom: 30,
    },
    confirmBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 10,
      paddingVertical: 17,
      borderRadius: 18,
    },
    confirmText: { fontSize: 16, fontWeight: '700', color: '#FFF', letterSpacing: 0.2 },
  });
}
