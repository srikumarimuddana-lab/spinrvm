// Drives the Android Auto head unit from the driver ride-state machine using
// @iternio/react-native-auto-play (Nitro-based, New-Architecture native).
//
// Architecture notes:
// - Importing the package runs its headless-task registration as a side effect,
//   so we lazy-require it behind a Platform.OS === 'android' guard. iOS CarPlay
//   needs an Apple-granted entitlement plus scene-delegate wiring this build
//   does not have, so the package is never loaded on iOS.
// - registerAutoPlay() must be called at JS bundle load (from index.js): Android
//   Auto can launch the JS context car-only, where the phone route layout never
//   mounts. We act only on HybridAutoPlay 'didConnect'.
// - ONE persistent MapTemplate: a live map shown in every ride state. The React
//   surface (carSurface.tsx) reads the same useDriverStore and draws the live
//   map + a branded trip card, so it updates itself; we only push the *template*
//   chrome — the state-driven header action, map buttons, and the ride-offer
//   alert — via setHeaderActions / setMapButtons / showAlert, never by rebuilding
//   the template (which would flicker the surface).
//
// Lyft-style interaction (everything Android Auto allows on a map template):
// - Ride offer  → a high-priority NavigationAlert with Accept / Decline.
// - To pickup   → header "Arrived"; map buttons Navigate (Google/Waze) + zoom.
// - At pickup   → header prompts to finish the PIN start on the phone (OTP is
//                 distraction-sensitive and stays phone-only, per CLAUDE.md).
// - In trip     → header "Complete trip"; map buttons Navigate + zoom.
// All car actions call the SAME useDriverStore actions the phone calls, so every
// dispatch / insurance-period / settlement invariant is preserved.
import { Linking, Platform } from 'react-native';
import { recordNonFatal } from '../../utils/crashlytics';
import { useDriverStore } from '../../store/driverStore';
import {
  buildHandoffUrl,
  defaultNavButtons,
  selectCarRoute,
  type NavProvider,
} from './carRoute';
import { buildOfferCard, type OfferLike } from './carCard';
import { CarMapSurface } from './carSurface';
import { useCarMapCamera } from './carMapCamera';
import { isCarDebugAvailable, pushDebug, setDebugFact, useCarDebug } from './carDebug';
import { getLastCarFix } from './useCarLocation';
import { triggerDriverEmergency } from '../../hooks/useDriverSafetyTrigger';

const NAV_TEMPLATE_ID = 'spinr-aa-nav';
const FALLBACK_OFFER_MS = 15_000;

// Map-button icons (iternio's AutoImage takes a bundled asset). Purpose-built
// monochrome glyphs: a navigation arrow for the hand-off, +/- for zoom.
const NAV_ICON = require('../../assets/images/nav_arrow.png');
const ZOOM_IN_ICON = require('../../assets/images/zoom_in.png');
const ZOOM_OUT_ICON = require('../../assets/images/zoom_out.png');
const RECENTER_ICON = require('../../assets/images/recenter.png');

// `console.log` stays gated on __DEV__ (release builds shouldn't chatter), but
// the line is ALWAYS recorded to the on-surface debug buffer. Previously the
// whole call was inside the __DEV__ guard, which compiled every android-auto
// diagnostic out of exactly the release builds that can run on a head unit —
// a template failure produced a blank screen and no trace anywhere.
const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[android-auto]', ...args);
  pushDebug('info', ...args);
};

// Failures the driver can't see and logcat won't reach unless the phone is
// tethered. Always recorded, and always console.error so Crashlytics/logcat
// pick them up in release builds too.
const logError = (...args: unknown[]) => {
  console.error('[android-auto]', ...args);
  pushDebug('error', ...args);
};
const noop = () => {};

const rideIdOf = (activeRide: unknown): string =>
  ((activeRide as { ride?: { id?: string } } | null)?.ride?.id) ?? '';

// The dispatch offer the car shows, preferring the push payload (richer) and
// falling back to the fetched ActiveRide.
const offerOf = (incomingRide: unknown, activeRide: unknown): OfferLike | null => {
  if (incomingRide) return incomingRide as OfferLike;
  const ride = (activeRide as { ride?: Record<string, unknown> } | null)?.ride;
  const rider = (activeRide as { rider?: Record<string, unknown> } | null)?.rider;
  if (!ride) return null;
  return {
    ride_id: String(ride.id ?? ''),
    pickup_address: ride.pickup_address as string | undefined,
    dropoff_address: ride.dropoff_address as string | undefined,
    fare: ride.total_fare as string | undefined,
    distance_km: ride.distance_km as number | undefined,
    duration_minutes: ride.duration_minutes as number | undefined,
    rider_name: rider?.first_name as string | undefined,
    rider_rating: rider?.rating as number | undefined,
    requires_wav: ride.requires_wav as boolean | undefined,
    surge_multiplier: ride.surge_multiplier as number | undefined,
  };
};

const offerDurationMs = (offer: OfferLike | null): number => {
  const { countdownSeconds } = useDriverStore.getState();
  if (typeof countdownSeconds === 'number' && countdownSeconds > 0) {
    return countdownSeconds * 1000;
  }
  const raw = (offer as { countdown_seconds?: number } | null)?.countdown_seconds;
  return typeof raw === 'number' && raw > 0 ? raw * 1000 : FALLBACK_OFFER_MS;
};

export default function registerAutoPlay(): void {
  if (Platform.OS !== 'android') {
    return;
  }

  // Importing the package instantiates its Nitro HybridObject natively, which
  // THROWS on any binary that doesn't contain the iternio module (Expo Go, or an
  // older installed build receiving this JS over-the-air). This runs at bundle
  // load from index.js, so an unguarded throw here would crash the entire phone
  // app at startup — degrade to "no car support" instead and surface it in logs.
  // Deliberately untyped (require has always returned `any` here): typing it
  // `typeof import(...)` makes tsc enforce iternio's 1|2|3|4 button-tuple types
  // on arrays we build dynamically — churn with no runtime benefit.
  let autoPlay: any;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    autoPlay = require('@iternio/react-native-auto-play');
  } catch (e) {
    // console.error alone never reaches a dashboard (Sentry drops console
    // breadcrumbs) — record a Crashlytics non-fatal so a cohort losing car
    // support is visible remotely. recordNonFatal is bundle-load-safe and
    // no-ops where Firebase native is absent (Expo Go).
    console.error(
      '[android-auto] native module unavailable — Android Auto disabled for this session. ' +
        'This binary predates the @iternio/react-native-auto-play dependency (a new EAS build is required).',
      e,
    );
    recordNonFatal(e, { module: 'androidAuto', reason: 'native_module_unavailable' });
    return;
  }
  const { HybridAutoPlay, MapTemplate, Flag } = autoPlay;

  // A single "Navigate" hand-off on Android Auto — the platform default nav app
  // (Google), matching Lyft's one in-car navigate affordance rather than two
  // ambiguous icon-only buttons. Waze stays wired for the dormant CarPlay path.
  const navProvider: NavProvider = defaultNavButtons(Platform.OS)[0]?.provider ?? 'google';

  // Hand live turn-by-turn to the driver's nav app for the current destination.
  const handoffToNav = (provider: NavProvider) => {
    const { rideState, activeRide } = useDriverStore.getState();
    const route = selectCarRoute(rideState, activeRide);
    if (!route) {
      log('hand-off skipped — no active route');
      return;
    }
    const { latitude, longitude } = route.destination;
    const url = buildHandoffUrl(provider, route.destination, Platform.OS);
    // Universal web Maps URL as a last resort so a missing/disabled nav app never
    // leaves the button dead (google.navigation: throws with no Maps app).
    const webFallback = `https://www.google.com/maps/dir/?api=1&destination=${latitude},${longitude}&travelmode=driving`;
    log('hand-off →', provider, url);
    Linking.openURL(url).catch(() =>
      Linking.openURL(webFallback).catch((e) => logError('hand-off failed:', e)),
    );
  };

  // Zoom buttons drive the (non-interactive) surface through the shared camera
  // store; carSurface.tsx reads `delta` and reframes the map.
  const zoomButtons = [
    {
      type: 'custom' as const,
      image: { type: 'asset' as const, image: ZOOM_OUT_ICON },
      onPress: () => useCarMapCamera.getState().zoomOut(),
    },
    {
      type: 'custom' as const,
      image: { type: 'asset' as const, image: ZOOM_IN_ICON },
      onPress: () => useCarMapCamera.getState().zoomIn(),
    },
  ];

  // Recenter: clears the pan and resumes following the driver. Always present —
  // once the head unit can pan the map (onDidPan below), a driver who has
  // dragged away needs a way back, and hunting for it only when it appears
  // would be worse than a button that is occasionally a no-op. Leaves zoom
  // alone; the driver chose that deliberately.
  const recenterButton = {
    type: 'custom' as const,
    image: { type: 'asset' as const, image: RECENTER_ICON },
    onPress: () => {
      useCarMapCamera.getState().recenter();
      log('recentred on driver');
    },
  };

  // Navigate button only while navigating; zoom buttons in every state.
  const mapButtonsFor = (hasRoute: boolean) => {
    const navHandoffButtons = hasRoute
      ? [
          {
            type: 'custom' as const,
            image: { type: 'asset' as const, image: NAV_ICON },
            onPress: () => handoffToNav(navProvider),
          },
        ]
      : [];
    // Android Auto's action strip caps at 4. In a ride that is Navigate +
    // Recenter + 2 zoom = exactly 4; idle drops Navigate and sits at 3.
    return [...navHandoffButtons, recenterButton, ...zoomButtons];
  };

  // The single state-driven header action (top-right on Android). Accept/Decline
  // live in the offer alert, so the header is the *progress* action for the leg.
  const headerActionsFor = (rideState: string) => {
    const mkAction = (title: string, style: 'confirm' | 'normal', onPress: () => void) => ({
      type: 'text' as const,
      title,
      style: style === 'confirm' ? ('confirm' as const) : ('normal' as const),
      flags: Flag.Primary,
      onPress,
    });

    // SOS rides ALONGSIDE the leg's progress action rather than replacing it —
    // a driver must never have to give up "Complete trip" to reach safety, and
    // must never have to hunt for safety in a menu. Text action, so it needs no
    // icon asset and reads unambiguously at a glance. `headerActions` is typed
    // Array<NitroAction>, so a second entry is legitimate.
    const sosAction = mkAction('SOS', 'normal', confirmEmergency);

    /** Leg progress action + SOS. */
    const act = (title: string, style: 'confirm' | 'normal', onPress: () => void) => ({
      android: [mkAction(title, style, onPress), sosAction],
    });

    switch (rideState) {
      case 'navigating_to_pickup':
        return act('Arrived', 'confirm', () => {
          const id = rideIdOf(useDriverStore.getState().activeRide);
          if (id) useDriverStore.getState().arriveAtPickup(id).catch((e) => logError('arrive failed:', e));
        });
      case 'arrived_at_pickup':
        // OTP start-trip stays on the phone — the header just routes the driver there.
        return act('Start on phone', 'normal', () =>
          showInfoAlert('Start the trip on your phone', 'Verify the rider’s PIN to begin.'),
        );
      case 'trip_in_progress':
        return act('Complete trip', 'confirm', () => {
          const id = rideIdOf(useDriverStore.getState().activeRide);
          if (id) useDriverStore.getState().completeRide(id).catch((e) => logError('complete failed:', e));
        });
      default:
        // Idle / offer / completed have no progress action, which leaves the
        // header free. Outside production, hand it to the debug panel — the car
        // screen is otherwise undiagnosable without tethering the phone to a
        // laptop, and idle is exactly the state a blank surface shows up in.
        // A ride in progress always keeps its progress action; debug never
        // displaces it.
        // No SOS here: the emergency endpoint is ride-scoped, so with no active
        // ride there is nothing to report against. Offering a button that
        // silently does nothing would be worse than not offering it.
        return isCarDebugAvailable()
          ? { android: [mkAction('Debug', 'normal', () => useCarDebug.getState().toggle())] }
          : undefined;
    }
  };

  let template: InstanceType<typeof MapTemplate> | null = null;
  let lastKey: string | null = null;

  // ---- Automatic navigation hand-off ---------------------------------------
  // On accepting a ride (and again when the trip starts) the driver's nav app is
  // launched for the current leg, so they don't have to reach for a button
  // before pulling away.
  //
  // The hard part is firing on a real transition and ONLY a real transition. A
  // head unit that swaps to the reversing camera and back re-runs this sync, and
  // relaunching Google Maps every time the driver reverses would be intolerable.
  // So:
  //   - `handedOffFor` records the leg already handed off (ride id + leg), so a
  //     repeated sync for the same leg is a no-op.
  //   - `primed` stays false until the first sync of a session has completed.
  //     Connecting mid-ride therefore ADOPTS the current leg silently instead of
  //     launching nav at the driver. Only a transition observed while already
  //     connected counts as an accept.
  // Both are cleared on a genuine disconnect, so the next session re-adopts.
  let handedOffFor: string | null = null;
  let primed = false;

  // ---- Ride-offer alert lifecycle -----------------------------------------
  // iternio warns that updating an alert re-shows it, so we show exactly once
  // per offer (keyed by ride id) and dismiss when the offer state is left.
  let alertSeq = 1;
  let shownOfferId: string | null = null;
  let shownAlertNum: number | null = null;

  // ---- Emergency (SOS) -----------------------------------------------------
  // Ride-scoped: the endpoint is POST /rides/{id}/emergency, so this is offered
  // only while there is an active ride — which is also when a driver is most
  // exposed.
  //
  // TWO TAPS, DELIBERATELY. A single mis-tap on a head unit must never page the
  // safety team and alarm the driver's emergency contacts, so the header action
  // raises a confirmation and only the confirm actually sends. That is the whole
  // reason this is not a one-press button.
  //
  // Per CLAUDE.md, Spinr's SOS "notifies emergency contacts and our safety team
  // and *offers* one-tap 911; it never auto-dials". The result alert therefore
  // offers 911 as an explicit driver-initiated action and never places a call by
  // itself.
  let sosInFlight = false;

  const sendEmergency = (rideId: string) => {
    // Guard the double-tap, not the retry: triggerDriverEmergency already owns
    // the only retry policy (3 attempts, 1s/2s backoff) and its docstring warns
    // that wrapping it again is how one press became nine POSTs.
    if (sosInFlight) {
      log('SOS already in flight — ignoring repeat press');
      return;
    }
    sosInFlight = true;

    const fix = getLastCarFix();
    log('SOS sending for ride', rideId, fix ? 'with position' : 'WITHOUT position (no fix yet)');
    setDebugFact('sos', 'sending…');

    triggerDriverEmergency(rideId, fix?.latitude, fix?.longitude)
      .then((res) => {
        const n = res.contacts.length;
        const notified = res.contacts.filter((c) => c.notified).length;
        log('SOS sent — contacts notified', `${notified}/${n}`);
        setDebugFact('sos', `sent · ${notified}/${n} contacts`);
        showEmergencySentAlert(notified, n);
      })
      .catch((e) => {
        // A failed SOS that looks like a success is the worst possible outcome
        // on this surface — the driver would believe help is coming. Say so
        // loudly and tell them to use the phone.
        logError('SOS FAILED to send:', e);
        setDebugFact('sos', 'FAILED');
        showEmergencyFailedAlert();
      })
      .finally(() => {
        sosInFlight = false;
      });
  };

  const showEmergencySentAlert = (notified: number, total: number) => {
    if (!template) return;
    const id = alertSeq++;
    const subtitle =
      total > 0
        ? `Safety team alerted · ${notified} of ${total} emergency contacts notified`
        : 'Safety team alerted';
    try {
      template.showAlert({
        id,
        title: { text: 'Emergency alert sent' },
        subtitle: { text: subtitle },
        // Offered, never automatic — the driver decides.
        primaryAction: {
          title: 'Call 911',
          style: 'destructive',
          onPress: () => {
            template?.dismissAlert(id);
            Linking.openURL('tel:911').catch((e) => logError('911 dial failed:', e));
          },
        },
        secondaryAction: {
          title: 'Close',
          style: 'default',
          onPress: () => template?.dismissAlert(id),
        },
        durationMs: 20000,
        priority: 'high',
      });
    } catch (e) {
      logError('SOS result alert failed:', e);
    }
  };

  const showEmergencyFailedAlert = () => {
    if (!template) return;
    const id = alertSeq++;
    try {
      template.showAlert({
        id,
        title: { text: 'Emergency alert FAILED to send' },
        subtitle: { text: 'Use your phone, or call 911 directly.' },
        primaryAction: {
          title: 'Call 911',
          style: 'destructive',
          onPress: () => {
            template?.dismissAlert(id);
            Linking.openURL('tel:911').catch((e) => logError('911 dial failed:', e));
          },
        },
        secondaryAction: {
          title: 'Close',
          style: 'default',
          onPress: () => template?.dismissAlert(id),
        },
        durationMs: 30000,
        priority: 'high',
      });
    } catch (e) {
      logError('SOS failure alert failed:', e);
    }
  };

  const confirmEmergency = () => {
    const rideId = rideIdOf(useDriverStore.getState().activeRide);
    if (!rideId) {
      log('SOS pressed with no active ride — nothing to report against');
      return;
    }
    if (!template) return;
    const id = alertSeq++;
    try {
      template.showAlert({
        id,
        title: { text: 'Send emergency alert?' },
        subtitle: { text: 'Alerts Spinr safety and your emergency contacts with your location.' },
        primaryAction: {
          title: 'Send alert',
          style: 'destructive',
          onPress: () => {
            template?.dismissAlert(id);
            sendEmergency(rideId);
          },
        },
        secondaryAction: {
          title: 'Cancel',
          style: 'default',
          onPress: () => template?.dismissAlert(id),
        },
        // Long enough to read and decide while driving; no auto-send on timeout.
        durationMs: 20000,
        priority: 'high',
      });
    } catch (e) {
      logError('SOS confirm alert failed:', e);
    }
  };

  const showInfoAlert = (title: string, subtitle?: string) => {
    if (!template) return;
    const id = alertSeq++;
    try {
      template.showAlert({
        id,
        title: { text: title },
        subtitle: subtitle ? { text: subtitle } : undefined,
        primaryAction: { title: 'OK', style: 'default', onPress: () => template?.dismissAlert(id) },
        durationMs: 6000,
        priority: 'medium',
      });
    } catch (e) {
      log('info alert failed:', e);
    }
  };

  const clearOfferAlert = () => {
    if (template && shownAlertNum != null) {
      try {
        template.dismissAlert(shownAlertNum);
      } catch (e) {
        log('dismiss alert failed:', e);
      }
    }
    shownAlertNum = null;
    shownOfferId = null;
  };

  const syncOfferAlert = (rideState: string, offer: OfferLike | null) => {
    if (rideState !== 'ride_offered') {
      if (shownOfferId) clearOfferAlert();
      return;
    }
    const rideId = offer?.ride_id ?? '';
    if (!rideId || rideId === shownOfferId || !template) return;

    clearOfferAlert(); // dismiss any stale offer before showing the new one
    const card = buildOfferCard(offer);
    const sub = [
      card.fareLabel,
      card.bonusLabel,
      card.etaLabel ? `${card.etaLabel} away` : null,
      card.surgeLabel ? `${card.surgeLabel} surge` : null,
    ]
      .filter(Boolean)
      .join(' · ');

    const num = alertSeq++;
    shownOfferId = rideId;
    shownAlertNum = num;
    try {
      template.showAlert({
        id: num,
        title: { text: card.riderName ? `New ride · ${card.riderName}` : 'New ride request' },
        subtitle: sub.length > 0 ? { text: sub } : undefined,
        primaryAction: {
          title: 'Accept',
          style: 'default',
          onPress: () => {
            useDriverStore.getState().acceptRide(rideId).catch((e) => logError('accept failed:', e));
          },
        },
        secondaryAction: {
          title: 'Decline',
          style: 'destructive',
          onPress: () => {
            useDriverStore.getState().declineRide(rideId).catch((e) => logError('decline failed:', e));
          },
        },
        durationMs: offerDurationMs(offer),
        priority: 'high',
        // The store runs its own offer countdown / auto-decline, so a visual
        // timeout here must NOT also decline — just let the alert fall away.
        onDidDismiss: (reason: string) => {
          if (reason !== 'system' && shownAlertNum === num) shownAlertNum = null;
        },
      });
    } catch (e) {
      log('offer alert failed:', e);
      shownOfferId = null;
      shownAlertNum = null;
    }
  };

  // ---- Per-change reconciliation ------------------------------------------
  const apply = () => {
    if (!template || !HybridAutoPlay.isConnected?.()) return;

    const { rideState, activeRide, incomingRide } = useDriverStore.getState();
    const route = selectCarRoute(rideState, activeRide);

    // Header + map buttons only change on a state / leg / ride transition.
    const key = `${rideState}:${rideIdOf(activeRide)}:${route ? route.leg : 'none'}`;
    if (key !== lastKey) {
      lastKey = key;
      try {
        template.setMapButtons(mapButtonsFor(!!route));
        template.setHeaderActions(headerActionsFor(rideState));
      } catch (e) {
        log('chrome update failed:', e);
      }
    }

    // Auto hand-off — see `handedOffFor` / `primed` above for why this cannot
    // simply fire whenever a nav leg is present.
    const navLegKey = route ? `${rideIdOf(activeRide)}:${route.leg}` : null;
    if (!primed) {
      handedOffFor = navLegKey; // adopt the leg we connected into, silently
      primed = true;
    } else if (navLegKey && navLegKey !== handedOffFor) {
      handedOffFor = navLegKey;
      log('auto hand-off for leg', navLegKey);
      setDebugFact('navHandoff', `${route?.leg} → ${navProvider}`);
      handoffToNav(navProvider);
    } else if (!navLegKey) {
      handedOffFor = null; // ride ended; the next leg is a fresh hand-off
    }

    syncOfferAlert(rideState, offerOf(incomingRide, activeRide));
  };

  let unsubscribe: (() => void) | null = null;

  const onConnect = () => {
    // Guard: only build the surface for a genuinely connected head unit.
    if (!HybridAutoPlay.isConnected?.()) return;

    // Idempotent: the cold-launch-already-connected branch and the 'didConnect'
    // listener can both fire for one connection — build (and reset) at most once.
    // A genuine reconnect goes through didDisconnect (template = null) first, so
    // the reset still runs for a real new session.
    //
    // The camera reset MUST stay inside this guard. It used to sit above it,
    // which contradicted the comment and meant every didConnect re-framed the
    // map: a head unit that swaps to the reversing camera and back re-fires
    // didConnect without a disconnect, so a driver who had zoomed out watched
    // the map snap back to the default span each time.
    if (!template) {
      useCarMapCamera.getState().reset(); // start each session framed at the default zoom
      try {
        template = new MapTemplate({
          id: NAV_TEMPLATE_ID,
          component: CarMapSurface,
          onStopNavigation: noop, // we never run our own turn-by-turn
          // Head-unit pan gestures. Without this the surface's controlled
          // `region` would fight the driver: the native map would move under
          // their finger and then snap back on the next GPS fix. Routing the
          // translation into the camera store makes the pan stick until they
          // press Recenter.
          onDidPan: (translation: { x: number; y: number }) => {
            useCarMapCamera.getState().pan(translation.x, translation.y);
          },
          mapButtons: mapButtonsFor(false),
        });
        template.setRootTemplate();
        log('MapTemplate created + setRootTemplate OK');
        setDebugFact('template', 'created');
      } catch (e) {
        logError('setRootTemplate (map) failed:', e);
        setDebugFact('template', 'FAILED');
        template = null;
        return;
      }
      lastKey = null; // force a chrome refresh on a fresh session
      shownOfferId = null;
      shownAlertNum = null;
    }

    apply();
    if (!unsubscribe) {
      unsubscribe = useDriverStore.subscribe(apply);
    }
  };

  try {
    HybridAutoPlay.addListener('didConnect', onConnect);
    HybridAutoPlay.addListener('didDisconnect', () => {
      unsubscribe?.();
      unsubscribe = null;
      clearOfferAlert();
      template = null;
      lastKey = null;
      // Next session re-adopts its leg rather than launching nav on connect.
      handedOffFor = null;
      primed = false;
    });
    // Cold-launch while a head unit is ALREADY connected: the connect event may
    // have fired during native init before this listener existed, so apply now.
    if (HybridAutoPlay.isConnected?.()) {
      onConnect();
    }
  } catch (e) {
    // Was a dev-only log(): in production a listener-registration failure left
    // Android Auto dead with zero diagnostics. Surface it like the require
    // guard above — loud local log + a remotely visible Crashlytics non-fatal.
    console.error('[android-auto] registration failed — Android Auto disabled for this session.', e);
    recordNonFatal(e, { module: 'androidAuto', reason: 'registration_failed' });
  }
}
