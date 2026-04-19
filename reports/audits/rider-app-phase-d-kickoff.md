# Phase D — Dimensions 13–16
## Notifications · Performance · Accessibility · i18n / French

---

## DIMENSION 13 — Notifications, AI & FAQ

### What to audit
FCM push notifications for every ride lifecycle event. Foreground + background handlers.
In-app notification center. Support / FAQ reachability.

### Rider-specific risks
- FCM token registered once per auth session (guard `fcmRegisteredRef`) — is this
  re-registered on token rotation (FCM tokens can expire/rotate)?
- Background message handler at module level — is it registered before any navigation?
- Ride status notifications: driver_accepted, driver_arrived, ride_started, ride_completed,
  ride_cancelled — are ALL cases handled with user-visible banner?
- `driver_timeout` (re-dispatch): does rider get a "still searching" notification or
  does the app go silent while re-dispatching?
- Notification deep link: tapping a notification from killed state — does it route to
  the correct ride screen?
- In-app notification center (`notifications.tsx`): unread badge count? tap-to-navigate?
- Support screen: is live chat / contact available or just static FAQs?
- No AI FAQ assistant confirmed — is that a future feature or omission?

### Files to read
```
rider-app/app/_layout.tsx              ← FCM setup, background handler
rider-app/app/notifications.tsx        ← notification center
rider-app/app/support.tsx              ← FAQ / help
shared/services/                       ← Firebase messaging service
```

### Kick-off prompt
```
You are auditing the Spinr rider app for notification reliability and support accessibility (Dimension 13).

Context:
- Framework: audit-framework/dimensions/13-notifications-ai-faq.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/app/_layout.tsx + notifications.tsx + support.tsx + shared/services/

Your task: Work through every checklist item in dimension 13. Specific checks:
1. FCM setup: is setBackgroundMessageHandler() registered at module level (outside React)?
   Is the FCM token re-registered if it rotates (onTokenRefresh listener)?
2. Ride lifecycle notifications: are ALL of these cases handled:
   driver_accepted, driver_arrived, ride_started, ride_completed, ride_cancelled,
   driver_timeout (still searching)?
   Also verify: ride_auto_cancelled vs ride_cancelled naming — inconsistency breaks routing.
   [Driver audit 13-1 and 13-8 — same foreground handler gap likely exists on rider]
3a. Notification deep link field: does every FCM payload include a `deeplink` field
    that the app can use to navigate to the correct screen? [Driver audit 13-3: missing]
3b. Notification tap navigation: when a rider taps a notification, does the app navigate
    to the relevant screen (e.g. driver_arrived → driver-arrived.tsx)?
    Or does it only mark the notification as read? [Driver audit 13-5: tap did nothing]
3c. Notification preferences: are notification preferences (which types to receive)
    synced to the backend or stored only in local AsyncStorage?
    [Driver audit 13-4: local only — lost on app reinstall → HIGH]
3. Deep link from killed state: when a user taps a push notification and the app is
   closed, does it open to the correct ride screen with the correct state loaded?
4. Notification center (notifications.tsx): is there an unread badge count on the tab?
   Does tapping a notification navigate to the relevant screen?
5. Support screen: is there a way to reach a human (live chat, email, phone)?
   Or is it static FAQs only? Is there a dispute/complaint flow?
6. Permission: is notification permission requested with a rationale before the OS dialog?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 13.
```

---

## DIMENSION 14 — Performance & Scalability

### What to audit
Map rendering, FlatList virtualisation in activity/history, image caching,
polling intervals, bundle size, cold-start time.

### Rider-specific risks
- Home screen: map + weather API + GPS + saved places — all on mount simultaneously
- `ride-options.tsx` (800+ lines): fetches estimates + drivers + promos in parallel
  on every render — is there a debounce or cache?
- Activity tab: ride history with FlatList — is it virtualised? paginated?
- Driver location marker update: every WS `driver_location_update` re-renders the map
  marker — is it memoized?
- `FreeCancelTimer.tsx`: Reanimated animation running during driver-arriving — CPU impact?
- Image loading: driver photo in driver-arriving, driver-arrived — cached with expo-image?
- Bundle size: 78 dependencies — is tree-shaking active? Any large unused packages?
- Polling: driver-arriving polls every 3s — is the poll cancelled when WS is connected?

### Files to read
```
rider-app/app/(tabs)/index.tsx          ← mount-time data fetching
rider-app/app/ride-options.tsx          ← parallel fetches
rider-app/app/(tabs)/activity.tsx       ← FlatList + pagination
rider-app/app/driver-arriving.tsx       ← polling + FreeCancelTimer
rider-app/components/FreeCancelTimer.tsx
rider-app/metro.config.js               ← bundle config
rider-app/package.json                 ← dependency count
```

### Kick-off prompt
```
You are auditing the Spinr rider app for performance and scalability (Dimension 14).

Context:
- Framework: audit-framework/dimensions/14-performance-scalability.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/app/ + rider-app/components/ + rider-app/metro.config.js

Your task: Work through every checklist item in dimension 14. Specific checks:
1. Home screen mount: how many API calls fire simultaneously on /(tabs)/index.tsx mount?
   Are they parallelised (Promise.all) or sequential? Is any result cached?
2. ride-options.tsx: does fetchEstimates + fetchNearbyDrivers + fetchAvailablePromos
   all fire on mount? Is there debouncing if the user navigates back and forward?
3. Activity tab FlatList: is it using FlatList with keyExtractor and getItemLayout?
   CRITICAL CHECK: Is keyExtractor returning a stable unique ID (ride.id) or
   Math.random()? [Driver audit 14-7: Math.random() was CRITICAL — full re-render every tick]
   Is ride history paginated or does it load all records at once?
4. Driver location updates: when WS sends driver_location_update every ~1s, does the
   map CarMarker re-render on every update? Is it wrapped in React.memo?
5. Polling: in driver-arriving.tsx, does the 3s poll continue running when the WS is
   connected and delivering updates? It should be suspended when WS is healthy.
6. FreeCancelTimer: is the Reanimated animation cancelled properly when the timer
   screen unmounts (e.g., driver accepted before timer ends)?
7. Driver photo: is it loaded with expo-image (with caching) or plain Image?
8. Are there any console.log statements left in production code paths?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 14.
```

---

## DIMENSION 15 — Accessibility (WCAG 2.1 / AODA)

### What to audit
All 36 screens must be operable with VoiceOver (iOS) and TalkBack (Android).
Touch targets ≥ 44pt. Text contrast ≥ 4.5:1. SOS button must be accessible.

### Rider-specific risks
- Star rating UI in `ride-completed.tsx`: 5 star buttons — do they have accessibilityLabel
  ("1 star", "2 stars" etc.) and accessibilityRole="button"?
- Map screens: map itself is not accessible but overlay buttons (SOS, chat, cancel)
  must be — do they have labels and roles?
- FreeCancelTimer: animated countdown — is the remaining time announced to screen reader
  via accessibilityLiveRegion?
- OTP screen: 4 input boxes — are they labelled individually? Focus order correct?
- Search autocomplete: are suggestion list items announced with their full address?
- Tip amount buttons in ride-completed: "$2", "$5", "$10" — are these tappable at 44pt?
- Driver photo: decorative image — should be accessibilityElementsHidden={true}
- Android back on modals: are bottom sheets using accessibilityViewIsModal={true}?

### Files to read
```
rider-app/app/ride-completed.tsx        ← star rating, tip buttons
rider-app/app/driver-arriving.tsx       ← SOS, cancel buttons
rider-app/app/otp.tsx                   ← OTP input accessibility
rider-app/app/search-destination.tsx    ← autocomplete list
rider-app/components/FreeCancelTimer.tsx
shared/components/SOSButton.tsx
```

### Kick-off prompt
```
You are auditing the Spinr rider app for accessibility compliance (Dimension 15).
This is required for AODA (Canada) and App Store Review.

Context:
- Framework: audit-framework/dimensions/15-accessibility-wcag.md
- Ground rules: audit-framework/ground-rules.md
- Scope: ALL rider-app/app/ screens + shared/components/

Your task: Work through every checklist item in dimension 15. Specific checks:
1. Star rating in ride-completed.tsx: do the 5 star buttons each have an
   accessibilityLabel ("Rate 1 star" through "Rate 5 stars") and accessibilityRole="button"?
2. Tip buttons: are the "$2", "$5", "$10" and custom tip buttons ≥ 44×44pt?
   Do they have accessibilityLabel values?
3. SOS button (shared/components/SOSButton.tsx): does it have accessibilityLabel="Emergency SOS"
   accessibilityRole="button" and accessibilityHint explaining the hold gesture?
4. FreeCancelTimer: does it use accessibilityLiveRegion="polite" to announce remaining time?
5. OTP input: are the 4 boxes labelled individually? Is focus order correct (1→2→3→4)?
6. Search autocomplete: are suggestion items in a FlatList with accessibilityLabel?
7. Driver photo: is it marked accessibilityElementsHidden={true} (decorative)?
8. Bottom sheets / modals: do they use accessibilityViewIsModal={true} to trap focus?
9. Text contrast: what are the primary text and button colours? Do they meet 4.5:1 ratio
   against their backgrounds?
10. Map overlay buttons (SOS, chat, cancel): can a VoiceOver user reach and activate them?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 15.
```

---

## DIMENSION 16 — i18n / Localisation (French Required)

### What to audit
All user-visible strings must be internationalised. French (fr-CA) is required by
Canada's Official Languages Act for federal businesses. Saskatchewan is the launch
market but French support is still legally required.

### Rider-specific risks
- No i18n library confirmed in dependencies (no `i18next`, `react-i18next`, `expo-localization`
  based i18n) — if strings are hardcoded in JSX, entire app must be refactored for launch
- Phone format: Canadian format (XXX) XXX-XXXX must display correctly for both locales
- Currency: CAD displayed as "$X.XX" — must show "X,XX $" in French-Canadian locale
- Date/time: scheduled ride picker — French locale date format
- Error messages from backend: are they in English only?
- App Store: French app description and screenshots required for Canada

### Files to read
```
rider-app/package.json                 ← i18n libraries?
rider-app/app/login.tsx               ← hardcoded strings?
rider-app/app/ride-options.tsx        ← hardcoded strings?
rider-app/app/ride-completed.tsx      ← hardcoded strings?
rider-app/app/settings.tsx            ← language setting?
shared/theme/                         ← any localisation config?
```

### Kick-off prompt
```
You are auditing the Spinr rider app for internationalisation and French localisation (Dimension 16).
French support is legally required in Canada (Official Languages Act).

Context:
- Framework: audit-framework/dimensions/16-i18n-localisation.md
- Ground rules: audit-framework/ground-rules.md (Canadian market — French is required)
- Scope: ALL rider-app/app/ screens + rider-app/package.json + shared/theme/

Your task: Work through every checklist item in dimension 16. Specific checks:
1. Is any i18n library installed? Check package.json for: i18next, react-i18next,
   expo-localization, @formatjs/intl, lingui, or similar.
2. Are user-visible strings hardcoded in JSX? Sample 5 screens: login.tsx, ride-options.tsx,
   ride-completed.tsx, account.tsx, support.tsx. Count hardcoded English strings.
3. Is there a language setting in settings.tsx that allows switching to French?
4. Currency display: is fare amount formatted with Intl.NumberFormat('fr-CA', {style:'currency'})?
5. Date/time: scheduled ride datetime picker — does it localise to French date format?
6. Backend error messages: are they returned in a language that can be swapped, or always English?
7. App Store metadata: is there a French app name and description prepared?
8. Phone number display: does it format as (XXX) XXX-XXXX in English and correctly in French-CA?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 16.
```

---

## Phase D End-of-Phase Checkpoint

Before closing the audit:
- [ ] Findings written under TASK 13–16 in audit file
- [ ] i18n gap severity confirmed (likely HIGH if no library exists at all)
- [ ] Accessibility gaps for SOS and star rating in P1
- [ ] FCM token rotation gap logged
- [ ] Performance: polling vs WS gap logged
- [ ] Final tally table filled in audit file
- [ ] All CRITICAL and HIGH items distributed across P0/P1 sprint files

---

## Full Audit Completion Checklist

After all 4 phases are done:

```bash
# Verify all 16 tasks are in the audit file
grep "^TASK " reports/audits/2026-04-19-rider-app-v1.txt | wc -l
# Should output: 16

# Count findings by severity
grep "CRITICAL" reports/audits/2026-04-19-rider-app-v1.txt | wc -l
grep "^.*HIGH" reports/audits/2026-04-19-rider-app-v1.txt | wc -l
grep "MEDIUM" reports/audits/2026-04-19-rider-app-v1.txt | wc -l

# Commit audit file
git add reports/audits/2026-04-19-rider-app-v1.txt
git add reports/audits/rider-app-phase-*.md
git commit -m "audit: rider-app production-readiness v1 — all 16 dimensions"
git push -u origin claude/rider-app-audit-iVxpH
```
