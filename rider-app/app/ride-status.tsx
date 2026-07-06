import React, { useEffect, useState, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
  BackHandler,
  Modal,
  TextInput,
  KeyboardAvoidingView,
  ScrollView,
  Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import api from '@shared/api/client';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import CancelReasonSheet from '../components/CancelReasonSheet';
import { FreeCancelTimer } from '../components/FreeCancelTimer';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function RideStatusScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, simulateDriverArrival, clearRide, updateRideNotes } = useRideStore();

  // Post-confirm "note for driver" — replaces the input that used to
  // live on ride-options. Editable until the trip starts; backend
  // returns 409 after that so the chip auto-hides.
  const [notesSheetOpen, setNotesSheetOpen] = useState(false);
  const [driverPhotoError, setDriverPhotoError] = useState(false);
  const [notesDraft, setNotesDraft] = useState('');
  const [savingNotes, setSavingNotes] = useState(false);
  const currentNotes = (currentRide as { rider_notes?: string | null } | null)?.rider_notes || '';
  const canEditNotes = currentRide
    ? ['searching', 'driver_assigned', 'driver_accepted', 'driver_arrived'].includes(currentRide.status)
    : false;

  const [pulseAnim] = useState(new Animated.Value(1));
  const [dotAnim] = useState(new Animated.Value(0));

  const [searchElapsed, setSearchElapsed] = useState(0);
  useEffect(() => {
    if (currentRide?.status !== 'searching') { setSearchElapsed(0); return; }
    setSearchElapsed(0);
    const id = setInterval(() => setSearchElapsed(s => s + 1), 1000);
    return () => clearInterval(id);
  }, [currentRide?.status]);

  // M-4: Offer acceptance countdown — derived from server-provided
  // offer_expires_at (ISO) or offer_timeout_seconds, with 15 s fallback.
  const [offerSecondsLeft, setOfferSecondsLeft] = useState<number>(15);
  const [offerTotalSeconds, setOfferTotalSeconds] = useState<number>(15);

  useEffect(() => {
    if (currentRide?.status !== 'driver_assigned') return;

    const ride = currentRide as any;
    const offerExpiresAt: string | null | undefined = ride.offer_expires_at;
    const offerTimeoutSeconds: number = ride.offer_timeout_seconds ?? 15;

    const computeTotal = (): number => {
      if (offerExpiresAt) {
        const expiresMs = new Date(offerExpiresAt).getTime();
        const createdMs = expiresMs - offerTimeoutSeconds * 1000;
        return Math.max(1, Math.round((expiresMs - createdMs) / 1000));
      }
      return offerTimeoutSeconds;
    };

    const computeLeft = (): number => {
      if (offerExpiresAt) {
        return Math.max(0, Math.round((new Date(offerExpiresAt).getTime() - Date.now()) / 1000));
      }
      return offerTimeoutSeconds;
    };

    const total = computeTotal();
    setOfferTotalSeconds(total);
    setOfferSecondsLeft(computeLeft());

    const interval = setInterval(() => {
      const left = computeLeft();
      setOfferSecondsLeft(left);
      if (left <= 0) clearInterval(interval);
    }, 500);

    return () => clearInterval(interval);
  }, [currentRide?.status, (currentRide as any)?.offer_expires_at, (currentRide as any)?.offer_timeout_seconds]);

  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message?: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', variant: 'info' });
  const [reasonVisible, setReasonVisible] = useState(false);

  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const performCancel = async (reason?: string) => {
    try {
      await cancelRide(reason);
      clearRide();
      router.replace('/(tabs)' as any);
    } catch {
      showToast('Could not cancel', 'The server rejected the request. Please try again.', 'danger');
    }
  };

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      // Poll as a fallback. The WebSocket client in
      // hooks/useRiderSocket.ts delivers ride-state updates in
      // real-time, so this only fires if the WS drops.
      //
      // Poll fast (3s) while the state is still being negotiated:
      //   - no ride loaded yet
      //   - `searching` (looking for a driver)
      //   - `driver_assigned` (driver dispatched, waiting for accept — the
      //     `driver_accepted` WS push can be silently lost if the rider's
      //     socket is still connecting or was on a different VM when the
      //     backend published)
      // Drop to 15s once the driver has accepted — location updates then
      // come over the WS exclusively and state changes are infrequent.
      const status = currentRide?.status;
      const fastPoll =
        !currentRide || status === 'searching' || status === 'driver_assigned';
      const interval = setInterval(() => fetchRide(rideId), fastPoll ? 3000 : 15000);
      return () => clearInterval(interval);
    }
  }, [rideId, currentRide?.status]);

  useEffect(() => {
    // Pulse animation for searching
    if (currentRide?.status === 'searching') {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 1.2, duration: 800, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 800, useNativeDriver: true }),
        ])
      ).start();
    }

    // Dot animation
    Animated.loop(
      Animated.timing(dotAnim, { toValue: 3, duration: 1500, useNativeDriver: false })
    ).start();
  }, [currentRide?.status]);

  const handleBackPress = () => {
    if (!currentRide) {
      router.back();
      return;
    }

    // UX-001: Use server-provided cancellation fee (from app_settings) instead
    // of the old hardcoded Math.min(5, fare * 0.2) formula.
    const cancellationFee = (currentRide as any).cancellation_fee ?? 3.0;
    const freeCancelSecondsLeft = (currentRide as any).free_cancel_seconds_remaining ?? null;
    const isFreeCancel = freeCancelSecondsLeft === null || freeCancelSecondsLeft > 0;
    const status = currentRide.status;

    // A driver is involved → collect a reason before cancelling. Plain search
    // cancels (no driver yet) skip the reason step.
    const askReason = () => setReasonVisible(true);

    if (status === 'driver_arrived') {
      setConfirmSheet({
        visible: true,
        title: 'Driver is waiting',
        message: `Your driver has arrived. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`,
        variant: 'warning',
        buttons: [
          { text: `Cancel & Pay $${cancellationFee.toFixed(2)}`, style: 'destructive', onPress: askReason },
          { text: 'Keep Ride', style: 'cancel' },
        ],
      });
    } else if (status === 'driver_assigned' || status === 'driver_accepted') {
      setConfirmSheet({
        visible: true,
        title: 'Cancel ride?',
        message: isFreeCancel
          ? 'Your driver is on the way. Cancel for free right now.'
          : `Cancellation fee of $${cancellationFee.toFixed(2)} applies.`,
        variant: 'warning',
        buttons: [
          { text: isFreeCancel ? 'Cancel (Free)' : `Cancel & Pay $${cancellationFee.toFixed(2)}`, style: 'destructive', onPress: askReason },
          { text: 'Keep Ride', style: 'cancel' },
        ],
      });
    } else {
      setConfirmSheet({
        visible: true,
        title: 'Cancel search?',
        message: 'Stop looking for a driver? No charge.',
        variant: 'info',
        buttons: [
          { text: 'Cancel', onPress: () => performCancel() },
          { text: 'Keep searching', style: 'cancel' },
        ],
      });
    }
  };

  // Handle hardware back button (Android)
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      handleBackPress();
      return true; // prevent default back
    });
    return () => sub.remove();
  }, [currentRide?.status]);

  // handleSimulateArrival and handleRideComplete removed for production

  const renderSearching = () => {
    const mins = Math.floor(searchElapsed / 60);
    const secs = searchElapsed % 60;
    const timerText = mins > 0 ? `${mins}:${secs.toString().padStart(2, '0')}` : `0:${secs.toString().padStart(2, '0')}`;
    const takingLonger = searchElapsed >= 120;
    return (
      <View style={styles.statusContainer}>
        <Animated.View style={[styles.searchingCircle, { transform: [{ scale: pulseAnim }] }]}>
          <Ionicons name="car" size={40} color="#FFFFFF" />
        </Animated.View>
        <Text style={styles.statusTitle}>Finding your driver</Text>
        <Text style={styles.statusSubtitle}>
          {takingLonger ? 'Taking longer than usual — hang tight' : 'This usually takes 1-3 minutes'}
        </Text>
        <Text style={styles.searchTimer}>{timerText}</Text>
        {takingLonger && (
          <TouchableOpacity
            style={styles.cancelSearchBtn}
            onPress={handleBackPress}
            activeOpacity={0.7}
          >
            <Text style={styles.cancelSearchText}>Cancel search</Text>
          </TouchableOpacity>
        )}
      </View>
    );
  };

  const renderDriverAssigned = () => (
    <View style={styles.driverContainer}>
      {/* Driver Info Card */}
      <View style={styles.driverCard}>
        <View style={styles.driverHeader}>
          <View style={styles.driverAvatar}>
            {currentDriver?.photo_url && !driverPhotoError ? (
              <Image
                source={{ uri: currentDriver.photo_url }}
                style={styles.driverAvatarImg}
                resizeMode="cover"
                onError={() => setDriverPhotoError(true)}
              />
            ) : (
              <Ionicons name="person" size={32} color={colors.textDim} />
            )}
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{currentDriver?.name}</Text>
            <View style={styles.ratingContainer}>
              <Ionicons name="star" size={14} color="#FFB800" />
              <Text style={styles.ratingText}>{currentDriver?.rating}</Text>
              <Text style={styles.tripsText}>• {currentDriver?.total_rides} trips</Text>
            </View>
          </View>
          {/* No call button: rider↔driver contact is chat-only. */}
        </View>

        <View style={styles.vehicleCard}>
          <View style={styles.vehicleInfo}>
            <Text style={styles.vehicleMake}>
              {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
            </Text>
            <Text style={styles.licensePlate}>{currentDriver?.license_plate}</Text>
          </View>
          <Ionicons name="car" size={32} color={colors.primary} />
        </View>
      </View>

      {/* Status Info */}
      <View style={styles.statusInfo}>
        <View style={styles.statusIcon}>
          <Ionicons
            name={currentRide?.status === 'driver_assigned' ? 'hourglass' : 'navigate'}
            size={24}
            color={colors.primary}
          />
        </View>
        <View style={styles.statusTextContainer}>
          <Text style={styles.statusLabel}>
            {currentRide?.status === 'driver_assigned'
              ? 'Driver found — confirming your ride'
              : 'Driver accepted — on the way'}
          </Text>
          <Text style={styles.statusEta}>
            {currentRide?.status === 'driver_assigned'
              ? 'Hang tight, we\'ll update you as soon as they accept'
              : 'Arriving in ~5 min'}
          </Text>
        </View>
      </View>

      {/* M-4: Offer acceptance countdown — only shown while driver_assigned */}
      {currentRide?.status === 'driver_assigned' && (
        <View style={{ marginTop: 16, marginBottom: 4 }}>
          <View style={styles.offerProgressTrack}>
            <View
              style={[
                styles.offerProgressFill,
                {
                  width: `${Math.max(0, Math.min(100, (offerSecondsLeft / offerTotalSeconds) * 100))}%`,
                },
              ]}
            />
          </View>
          <Text style={styles.offerProgressLabel}>
            {offerSecondsLeft > 0
              ? `Driver has ${offerSecondsLeft}s to accept`
              : 'Finding another driver…'}
          </Text>
        </View>
      )}

      {/* UX-001: Free cancellation countdown */}
      <View style={{ marginTop: 12 }}>
        <FreeCancelTimer
          driverAcceptedAt={(currentRide as any)?.driver_accepted_at}
          rideStatus={currentRide?.status}
          freeCancelWindowSeconds={(currentRide as any)?.free_cancel_window_seconds ?? 120}
          cancellationFee={(currentRide as any)?.cancellation_fee ?? 3.0}
        />
      </View>

    </View>
  );

  const renderDriverArrived = () => (
    <View style={styles.arrivedContainer}>
      {/* OTP Display */}
      <View style={styles.otpCard}>
        <Text style={styles.otpLabel}>Share this PIN with your driver</Text>
        <View style={styles.otpBox}>
          {currentRide?.pickup_otp.split('').map((digit, index) => (
            <View key={index} style={styles.otpDigit}>
              <Text style={styles.otpDigitText}>{digit}</Text>
            </View>
          ))}
        </View>
        <Text style={styles.otpHint}>Driver will enter this to start the trip</Text>
      </View>

      {/* Driver Info */}
      <View style={styles.driverCard}>
        <View style={styles.driverHeader}>
          <View style={styles.driverAvatar}>
            {currentDriver?.photo_url && !driverPhotoError ? (
              <Image
                source={{ uri: currentDriver.photo_url }}
                style={styles.driverAvatarImg}
                resizeMode="cover"
                onError={() => setDriverPhotoError(true)}
              />
            ) : (
              <Ionicons name="person" size={32} color={colors.textDim} />
            )}
          </View>
          <View style={styles.driverInfo}>
            <Text style={styles.driverName}>{currentDriver?.name}</Text>
            <Text style={styles.arrivedText}>
              <Ionicons name="checkmark-circle" size={14} color="#10B981" /> Arrived at pickup
            </Text>
          </View>
          {/* No call button: rider↔driver contact is chat-only. */}
        </View>

        <View style={styles.vehicleCard}>
          <View style={styles.vehicleInfo}>
            <Text style={styles.vehicleMake}>
              {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
            </Text>
            <Text style={styles.licensePlate}>{currentDriver?.license_plate}</Text>
          </View>
          <Ionicons name="car" size={32} color={colors.primary} />
        </View>
      </View>

    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={handleBackPress}>
          <Ionicons name="close" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>
          {!currentRide && 'Loading...'}
          {currentRide?.status === 'searching' && 'Finding driver...'}
          {currentRide?.status === 'driver_assigned' && 'Driver confirming...'}
          {currentRide?.status === 'driver_accepted' && 'Driver on the way'}
          {currentRide?.status === 'driver_arrived' && 'Driver arrived'}
        </Text>
        <View style={{ width: 44 }} />
      </View>

      {/* Map Placeholder */}
      <View style={styles.mapPlaceholder}>
        <Ionicons name="map" size={48} color="#CCC" />
        <Text style={styles.mapText}>Map View</Text>
      </View>

      {/* Bottom Sheet — scrollable so tall states (driver_arrived: OTP card +
          driver card + note chip + cancel, plus the dev bar) can't clip off the
          bottom of a small screen. flexShrink lets the sheet yield height to the
          scroll area instead of overflowing. */}
      <View style={styles.bottomSheet}>
        <ScrollView
          contentContainerStyle={styles.bottomSheetContent}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
        >
        {!currentRide ? (
          <View style={styles.statusContainer}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.statusTitle}>Loading ride details...</Text>
          </View>
        ) : (
          <>
            {currentRide.status === 'searching' && renderSearching()}
            {(currentRide.status === 'driver_assigned' || currentRide.status === 'driver_accepted') && renderDriverAssigned()}
            {currentRide.status === 'driver_arrived' && renderDriverArrived()}

            {/* Post-confirm note chip — replaces the input that used to
                live on ride-options. Hidden after the trip starts because
                the backend rejects late edits (409). */}
            {canEditNotes && (
              <TouchableOpacity
                style={styles.notesChip}
                activeOpacity={0.7}
                onPress={() => {
                  setNotesDraft(currentNotes);
                  setNotesSheetOpen(true);
                }}
              >
                <Ionicons name="chatbubble-ellipses-outline" size={18} color={colors.primary} />
                <Text style={styles.notesChipText} numberOfLines={1}>
                  {currentNotes ? `Note: ${currentNotes}` : 'Add note for driver'}
                </Text>
                <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
              </TouchableOpacity>
            )}

            {/* Cancel Button */}
            <TouchableOpacity style={styles.cancelButton} onPress={handleBackPress}>
              <Text style={styles.cancelButtonText}>
                {currentRide.status === 'searching' ? 'Cancel Search' : 'Cancel Ride'}
              </Text>
            </TouchableOpacity>

            {/* DEV CONTROLS — remove in production */}
            {__DEV__ && (
              <View style={styles.devBar}>
                <Text style={styles.devLabel}>DEV: {currentRide.status}</Text>
                {currentRide.status === 'searching' && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try { await simulateDriverArrival(); } catch (err) { console.error('[ride-status]', err); }
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Assign Driver</Text>
                  </TouchableOpacity>
                )}
                {(currentRide.status === 'driver_assigned' || currentRide.status === 'driver_accepted') && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try { await api.post(`/drivers/rides/${currentRide.id}/arrive`); } catch (err) { console.error('[ride-status]', err); }
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Arrive at Pickup</Text>
                  </TouchableOpacity>
                )}
                {currentRide.status === 'driver_arrived' && (
                  <TouchableOpacity style={styles.devBtn} onPress={() => {
                    router.replace({ pathname: '/driver-arriving', params: { rideId: currentRide.id } } as any);
                  }}>
                    <Text style={styles.devBtnText}>Go to Arriving Screen</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
          </>
        )}
        </ScrollView>
      </View>
      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
      />
      <CancelReasonSheet
        visible={reasonVisible}
        onConfirm={performCancel}
        onClose={() => setReasonVisible(false)}
      />

      {/* Note-for-driver editor — opened from the chip above. Backend
          rejects late edits (post-pickup) so 409 surfaces as a toast. */}
      <Modal
        visible={notesSheetOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setNotesSheetOpen(false)}
      >
        <KeyboardAvoidingView
          behavior="padding"
          style={styles.notesSheetBackdrop}
        >
          <TouchableOpacity
            style={StyleSheet.absoluteFill}
            activeOpacity={1}
            onPress={() => setNotesSheetOpen(false)}
          />
          <View style={styles.notesSheet}>
            <View style={styles.notesSheetHandle} />
            <Text style={styles.notesSheetTitle}>Note for driver</Text>
            <Text style={styles.notesSheetSubtitle}>
              Optional. Up to 200 characters — gate code, special instructions, etc.
            </Text>
            <TextInput
              value={notesDraft}
              onChangeText={setNotesDraft}
              placeholder="e.g. Gate code 4521 or backdoor pickup"
              placeholderTextColor={colors.textDim}
              maxLength={200}
              multiline
              style={styles.notesSheetInput}
            />
            <View style={styles.notesSheetActions}>
              <TouchableOpacity
                style={[styles.notesSheetButton, styles.notesSheetButtonCancel]}
                onPress={() => setNotesSheetOpen(false)}
                disabled={savingNotes}
              >
                <Text style={styles.notesSheetButtonCancelText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.notesSheetButton, styles.notesSheetButtonSave]}
                disabled={savingNotes}
                onPress={async () => {
                  setSavingNotes(true);
                  try {
                    await updateRideNotes(notesDraft);
                    setNotesSheetOpen(false);
                  } catch (err: unknown) {
                    const message =
                      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
                      (err as Error)?.message ||
                      'Could not save note.';
                    showToast('Note not saved', message, 'warning');
                  } finally {
                    setSavingNotes(false);
                  }
                }}
              >
                <Text style={styles.notesSheetButtonSaveText}>
                  {savingNotes ? 'Saving…' : 'Save'}
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.border,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: {
      width: 44,
      height: 44,
      justifyContent: 'center',
      alignItems: 'center',
    },
    headerTitle: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    mapPlaceholder: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: '#E0E7E0',
    },
    mapText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 8,
    },
    bottomSheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      minHeight: 280,
      flexShrink: 1,
      overflow: 'hidden',
    },
    bottomSheetContent: {
      padding: 24,
    },
    statusContainer: {
      alignItems: 'center',
      paddingVertical: 20,
    },
    searchingCircle: {
      width: 100,
      height: 100,
      borderRadius: 50,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 20,
    },
    statusTitle: {
      fontSize: 22,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 8,
    },
    statusSubtitle: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    searchTimer: {
      fontSize: 22,
      fontWeight: '700',
      color: colors.textDim,
      marginTop: 12,
      letterSpacing: 1,
    },
    cancelSearchBtn: {
      marginTop: 16,
      paddingHorizontal: 24,
      paddingVertical: 10,
      borderRadius: 20,
      backgroundColor: colors.error + '15',
      borderWidth: 1,
      borderColor: colors.error + '30',
    },
    cancelSearchText: {
      fontSize: 14,
      fontWeight: '600',
      color: colors.error,
    },
    demoButton: {
      marginTop: 20,
      paddingHorizontal: 20,
      paddingVertical: 10,
      backgroundColor: colors.border,
      borderRadius: 20,
    },
    demoButtonText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    driverContainer: {
      paddingTop: 8,
    },
    driverCard: {
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 16,
      marginBottom: 16,
    },
    driverHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      marginBottom: 16,
    },
    driverAvatar: {
      width: 56,
      height: 56,
      borderRadius: 28,
      backgroundColor: colors.border,
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 12,
      overflow: 'hidden',
    },
    driverAvatarImg: {
      width: 56,
      height: 56,
      borderRadius: 28,
    },
    driverInfo: {
      flex: 1,
    },
    driverName: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 4,
    },
    ratingContainer: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    ratingText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      marginLeft: 4,
    },
    tripsText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginLeft: 8,
    },
    vehicleCard: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      backgroundColor: colors.surface,
      borderRadius: 12,
      padding: 12,
    },
    vehicleInfo: {},
    vehicleMake: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    licensePlate: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginTop: 2,
    },
    statusInfo: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: '#FFF5F5',
      borderRadius: 12,
      padding: 16,
      marginBottom: 16,
    },
    statusIcon: {
      width: 48,
      height: 48,
      borderRadius: 24,
      backgroundColor: '#FFE8E8',
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 12,
    },
    statusTextContainer: {},
    statusLabel: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    statusEta: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    offerProgressTrack: {
      height: 4,
      backgroundColor: colors.border,
      borderRadius: 2,
      overflow: 'hidden',
    },
    offerProgressFill: {
      height: 4,
      backgroundColor: colors.primary,
      borderRadius: 2,
    },
    offerProgressLabel: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 6,
      textAlign: 'center',
    },
    simulateButton: {
      backgroundColor: colors.border,
      borderRadius: 12,
      padding: 14,
      alignItems: 'center',
    },
    simulateButtonText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    arrivedContainer: {},
    otpCard: {
      backgroundColor: colors.primary,
      borderRadius: 16,
      padding: 24,
      alignItems: 'center',
      marginBottom: 20,
    },
    otpLabel: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: 'rgba(255,255,255,0.8)',
      marginBottom: 16,
    },
    otpBox: {
      flexDirection: 'row',
      gap: 12,
      marginBottom: 12,
    },
    otpDigit: {
      width: 52,
      height: 60,
      backgroundColor: 'rgba(255,255,255,0.2)',
      borderRadius: 12,
      justifyContent: 'center',
      alignItems: 'center',
    },
    otpDigitText: {
      fontSize: 28,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: '#FFFFFF',
    },
    otpHint: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: 'rgba(255,255,255,0.7)',
    },
    arrivedText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#10B981',
    },
    completeButton: {
      backgroundColor: '#10B981',
      borderRadius: 28,
      padding: 18,
      alignItems: 'center',
    },
    completeButtonText: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },
    cancelButton: {
      marginTop: 16,
      padding: 14,
      alignItems: 'center',
    },
    cancelButtonText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.primary,
    },
    devBar: {
      flexDirection: 'row',
      alignItems: 'center',
      flexWrap: 'wrap',
      gap: 8,
      marginTop: 16,
      padding: 12,
      backgroundColor: '#FEF3C7',
      borderRadius: 12,
      borderWidth: 1,
      borderColor: '#F59E0B',
    },
    devLabel: {
      fontSize: 11,
      fontWeight: '700',
      color: '#92400E',
      marginRight: 4,
    },
    devBtn: {
      backgroundColor: '#F59E0B',
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 8,
    },
    devBtnText: {
      fontSize: 12,
      fontWeight: '700',
      color: '#FFF',
    },
    /* Post-confirm note chip + sheet (replaces ride-options notes input) */
    notesChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 12,
      paddingHorizontal: 14,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      marginTop: 12,
    },
    notesChipText: {
      flex: 1,
      fontSize: 14,
      fontWeight: '500',
      color: colors.text,
    },
    notesSheetBackdrop: {
      flex: 1,
      backgroundColor: 'rgba(0,0,0,0.35)',
      justifyContent: 'flex-end',
    },
    notesSheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 20,
      borderTopRightRadius: 20,
      paddingHorizontal: 20,
      paddingTop: 12,
      paddingBottom: 24,
    },
    notesSheetHandle: {
      alignSelf: 'center',
      width: 40,
      height: 4,
      borderRadius: 2,
      backgroundColor: colors.border,
      marginBottom: 12,
    },
    notesSheetTitle: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
      marginBottom: 4,
    },
    notesSheetSubtitle: {
      fontSize: 13,
      color: colors.textDim,
      marginBottom: 16,
    },
    notesSheetInput: {
      minHeight: 90,
      maxHeight: 180,
      borderRadius: 12,
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 14,
      paddingVertical: 12,
      fontSize: 15,
      color: colors.text,
      textAlignVertical: 'top',
    },
    notesSheetActions: {
      flexDirection: 'row',
      gap: 12,
      marginTop: 16,
    },
    notesSheetButton: {
      flex: 1,
      paddingVertical: 14,
      borderRadius: 12,
      alignItems: 'center',
    },
    notesSheetButtonCancel: {
      backgroundColor: colors.surfaceLight,
    },
    notesSheetButtonCancelText: {
      fontSize: 15,
      fontWeight: '600',
      color: colors.text,
    },
    notesSheetButtonSave: {
      backgroundColor: colors.primary,
    },
    notesSheetButtonSaveText: {
      fontSize: 15,
      fontWeight: '700',
      color: '#FFF',
    },
  });
}
