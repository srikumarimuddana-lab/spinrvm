# Rider App — P4 Sprint Verification (10 items)

**Sprint file:** `reports/remediation/rider-P4-future-features.md`
**Branch:** `claude/audit-continuation-batch-2`

**Result: 7 DONE · 3 PARTIAL · 0 PENDING · 0 BLOCKED · 0 UNVERIFIABLE · 0 SUPERSEDED**

P4 is the "post-launch roadmap" sprint — surprisingly far along. Loyalty,
wallet P2P, dark mode, multi-language (es/zh), AI FAQ, promo selection
sheet, and scheduled-ride reminder cron all shipped. The three PARTIALs
are scoped to ~18 h of follow-up work.

Cumulative rider verification — **all 5 sprints complete**:

| Sprint | Total | DONE | PARTIAL | PENDING |
|---|---:|---:|---:|---:|
| P0  | 8 | 8 | 0 | 0 |
| P1a | 17 | 17 | 0 | 0 |
| P1b | 16 | 16 | 0 | 0 |
| P2a | 17 | 8 | 4 | 5 |
| P2b | 16 | 10 | 1 | 5 |
| P3  | 8 | 8 | 0 | 0 |
| P4  | 10 | 7 | 3 | 0 |
| **Total** | **92** | **74 (80%)** | **8** | **10** |

**Rider module verification: COMPLETE.** 80 % full DONE; remaining 18 items
are tractable (estimate ~30 h of cleanup) and tracked in the per-sprint
files.

---

## Per-Item Verification

| ID | Title | Status | Evidence |
|---|---|---|---|
| P4-1  | Live "track my ride" web view | PARTIAL | `rides.py:1732` backend; `ride-tracking-webview.tsx:13,44` scaffold |
| P4-2  | Promo selection bottom-sheet UI | DONE | `ride-options.tsx:706-772` |
| P4-3  | Ride receipt PDF / share | PARTIAL | `ride-completed.tsx:124` text share only — no PDF/image |
| P4-4  | Dark mode toggle | DONE | `settings.tsx:34` + `ThemeContext.tsx:77` |
| P4-5  | Multi-language (Spanish + Simplified Chinese) | DONE | `i18n/index.ts:14-21` + `es.json` + `zh.json` |
| P4-6  | AI FAQ assistant (Claude Haiku 4.5) | DONE | `SupportScreen.tsx:83-107` |
| P4-7  | Ride sharing / carpool | PARTIAL | `carpool.tsx:27-49` UI scaffold; backend ride type not wired |
| P4-8  | Loyalty program full impl | DONE | `loyalty.tsx:85-523` (tiers, redeem, history) |
| P4-9  | Wallet peer-to-peer transfer UI | DONE | `wallet.tsx:90-119` + `walletStore.ts:124` |
| P4-10 | Scheduled-ride push reminder | DONE | `scheduled_rides.py:75-98` (FCM `scheduled_ride_reminder`) |

---

## PARTIAL summary

| ID | What's done | What's missing | Effort |
|---|---|---|---|
| P4-1 | Backend `/track/{share_token}` returns ride state; rider-side webview scaffold | Hosted tracking page or embedded MapView — webview renders no UI | 4 h |
| P4-3 | Native Share with ASCII receipt text | PDF/image generation (`expo-print` or `react-native-html-to-pdf`) | 6 h |
| P4-7 | `carpool.tsx` UI scaffold + `createFareSplit()` endpoint | Ride-type selection in `ride-options`, driver-side carpool match logic | 8 h |

---

## Verification YAML

===VERIFICATION-YAML===
- id: rider-P4-1
  source_finding: "R-P4-1"
  status: PARTIAL
  evidence:
    file: backend/routes/rides.py
    lines: [1732, 1739]
    snippet: "async def track_shared_ride(share_token: str): ride = ..."
    test_file: rider-app/app/ride-tracking-webview.tsx
    test_lines: [13, 44]
  reason: "Backend share endpoint returns share_token; frontend webview scaffold exists but renders no tracking UI"
  owner: rider-app
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 4
  duplicate_of: null
  notes: "Need hosted tracking page or embedded MapView component"

- id: rider-P4-2
  source_finding: "R-P4-2"
  status: DONE
  evidence:
    file: rider-app/app/ride-options.tsx
    lines: [706, 772]
    snippet: "Modal visible={showPromoSheet}; availablePromos.map; applyPromo(promo)"
    test_file: null
    test_lines: null
  reason: "Promo selection bottom-sheet modal with apply/cancel actions and visual feedback"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Promo banner toggle at line 418; sheet handles no-promo + apply"

- id: rider-P4-3
  source_finding: "R-P4-3"
  status: PARTIAL
  evidence:
    file: rider-app/app/ride-completed.tsx
    lines: [124, 127]
    snippet: "Share.share({ message: buildReceiptText(), title: 'Spinr Ride Receipt' })"
    test_file: null
    test_lines: null
  reason: "Native share works with ASCII receipt text; no PDF or image generation"
  owner: rider-app
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 6
  duplicate_of: null
  notes: "buildReceiptText() at line 78-122; PDF generation missing"

- id: rider-P4-4
  source_finding: "R-P4-4"
  status: DONE
  evidence:
    file: rider-app/app/settings.tsx
    lines: [34, 36]
    snippet: "handleDarkModeToggle: setTheme(value ? 'dark' : 'light')"
    test_file: shared/theme/ThemeContext.tsx
    test_lines: [77, 79]
  reason: "Toggle persists theme via setTheme() → AsyncStorage; ThemeContext resolves colors per preference"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P4-5
  source_finding: "R-P4-5"
  status: DONE
  evidence:
    file: rider-app/i18n/index.ts
    lines: [14, 21]
    snippet: "Language = 'en' | 'fr' | 'es' | 'zh'; LANGUAGES with es / zh entries"
    test_file: rider-app/i18n/es.json
    test_lines: [1, 20]
  reason: "Spanish + Simplified Chinese translation files present; language picker supports all 4"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "es.json and zh.json complete; preference persisted via useLanguageStore"

- id: rider-P4-6
  source_finding: "R-P4-6"
  status: DONE
  evidence:
    file: shared/components/SupportScreen.tsx
    lines: [83, 107]
    snippet: "askClaude → POST api.anthropic.com/v1/messages model=claude-haiku-4-5"
    test_file: null
    test_lines: null
  reason: "Claude Haiku 4.5 chat assistant in Chat tab; FAQ + Contact tabs alongside"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Chat tab calls askClaude() at line 243; FAQs from /support/faqs"

- id: rider-P4-7
  source_finding: "R-P4-7"
  status: PARTIAL
  evidence:
    file: rider-app/app/carpool.tsx
    lines: [27, 50]
    snippet: "useWalletStore.createFareSplit; phones state input list"
    test_file: rider-app/store/walletStore.ts
    test_lines: [139, 149]
  reason: "Frontend scaffold + backend fare-split endpoint exist; carpool ride type not wired in ride-creation flow"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 8
  duplicate_of: null
  notes: "Missing: ride-type selector in ride-options + driver-side carpool match logic"

- id: rider-P4-8
  source_finding: "R-P4-8"
  status: DONE
  evidence:
    file: rider-app/app/loyalty.tsx
    lines: [85, 523]
    snippet: "loadData → Promise.all([api.get('/loyalty'), api.get('/loyalty/history')])"
    test_file: null
    test_lines: null
  reason: "Tier progression, point multipliers, redemption modal, full history all wired to backend"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Redeem POST /loyalty/redeem at line 130; history pagination via FlatList"

- id: rider-P4-9
  source_finding: "R-P4-9"
  status: DONE
  evidence:
    file: rider-app/app/wallet.tsx
    lines: [90, 119]
    snippet: "handleTransfer → walletStore.transfer(phone, amount); modal at 259-334"
    test_file: rider-app/store/walletStore.ts
    test_lines: [124, 136]
  reason: "P2P transfer modal with phone+amount validation; backend POST /wallet/transfer functional"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P4-10
  source_finding: "R-P4-10"
  status: DONE
  evidence:
    file: backend/utils/scheduled_rides.py
    lines: [75, 98]
    snippet: "_send_reminder → FCM data={'type': 'scheduled_ride_reminder', 'ride_id': ...}"
    test_file: null
    test_lines: null
  reason: "Cron loop every 60s sends scheduled_ride_reminder push 15 min before pickup; idempotent via reminder flag"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "scheduled_ride_dispatcher_loop at line 146-154; reminder_sent flag prevents duplicates"
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P4 module=rider items=10 done=7 partial=3 pending=0 blocked=0 unverifiable=0 superseded=0
