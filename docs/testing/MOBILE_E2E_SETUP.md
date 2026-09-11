# Mobile App Testing Setup — Android Studio + Maestro (Plain-English Walkthrough)

**Who this is for:** anyone setting up their own laptop to run Spinr's rider-app
and driver-app tests locally, and anyone who wants to understand — in plain
language — what gets tested automatically today, what doesn't yet, and why.

**The short version:** you don't need to build this testing setup from
scratch. Spinr already has 12 automated test scripts (Maestro "flows") for
the rider and driver apps, a documented local-run guide, and a CI (automatic
testing) pipeline that's wired up but not fully switched on yet. This guide
is your step-by-step path to (1) running those tests yourself on your
laptop today, for free, and (2) understanding exactly what's blocking full
"test every build automatically" coverage, including DAST (explained below),
so you know what to unblock and who needs to do it.

---

## Status at a glance

| Area | Status | What it means |
|---|---|---|
| Android — run tests on your own laptop | 🟢 Ready today | Just needs Android Studio + Maestro installed (Part A below) |
| Android — run tests automatically on every relevant PR | 🟡 Built, but switched off | The wiring exists; 2 access keys need to be added by a repo admin (Part D) |
| iOS — run tests on your own laptop | 🟡 Mac + Xcode only | Maestro cannot test iOS from Windows or Linux, full stop (Part B) |
| iOS — run tests automatically in CI | 🔴 Not built yet | Needs an Apple Developer account connected — a business step, not a coding one (Part D) |
| DAST (security-scanning the live app) | 🔴 Scaffolded, not active | Needs a "staging" environment to scan, which doesn't exist yet (Part E) |
| SAST (security-scanning the source code) | 🟢 Already running | Runs on every single pull request today — different from DAST, explained below |

Everything in this table is backed by what's actually in the repo today —
file references are given throughout so you (or anyone) can double-check.

---

## Part A — Set up your laptop for Android testing

### A1. Confirm Android Studio is configured correctly

1. Open **Android Studio**.
2. Go to **More Actions → SDK Manager** (on the Welcome screen) or
   **Tools → SDK Manager** (if a project is already open).
3. On the **SDK Platforms** tab, tick a recent Android version — API 34
   ("UpsideDownCake") is a safe default — and click **Apply** to install it
   if it isn't already.
4. Switch to the **SDK Tools** tab and confirm these are ticked/installed:
   - Android SDK Build-Tools
   - Android Emulator
   - Android SDK Platform-Tools
5. Click **OK** and let it finish installing.

### A2. Create a virtual Android phone (an "emulator")

1. **Tools → Device Manager**.
2. Click **Create Device**.
3. Pick a phone (e.g. **Pixel 7**) → **Next**.
4. Pick a system image (e.g. **API 34**) — click the **Download** link next
   to it if it isn't downloaded yet → **Next → Finish**.
5. Back in Device Manager, click the ▶ **play button** next to your new
   device to boot it. Wait for the Android home screen to appear — this can
   take a minute the first time.
6. Open a terminal and run:
   ```bash
   adb devices
   ```
   You should see one line like `emulator-5554   device`. If you see
   nothing, the emulator isn't fully booted yet — wait and retry.

### A3. Install the Maestro CLI (the test-running tool)

Maestro is free and open-source — no account or sign-up needed to run tests
on your own laptop.

```bash
curl -Ls "https://get.maestro.mobile.dev" | bash
```

Then verify it's on your PATH:
```bash
maestro --version
```
If that command isn't found, the installer told you which line to add to
your shell profile (`~/.zshrc`, `~/.bashrc`, etc.) — add it, then open a new
terminal window.

### A4. Start the Spinr backend on your laptop

The apps need a real backend to talk to (login, ride requests, etc. are not
faked/mocked in these tests).

```bash
cd backend
pip install -r requirements.txt
python3 -m backend.server
```
Leave this running in its own terminal tab.

### A5. Build the rider app and driver app onto your emulator

These are "managed" Expo apps — there's no pre-built install file sitting in
the repo, so this step does a real (one-time, slower) build the first time,
then fast rebuilds after that.

```bash
cd rider-app
yarn install
npx expo run:android
```
Repeat in a separate terminal for the driver app:
```bash
cd driver-app
yarn install
npx expo run:android
```
Each command builds the app and installs it straight onto the booted
emulator from A2. When it's done you'll see the Spinr app open automatically
on the emulator.

> **Why not just use Expo Go (the generic Expo testing app)?** Both apps use
> native features Expo Go can't host — Google Maps, Firebase, Sentry,
> Stripe's payment SDK (rider app), and a few others. `expo run:android`
> builds a real, dedicated version of the app instead, which is what these
> tests are designed against.

### A6. Run the existing automated tests

The test scripts already exist — you're not writing new ones for this part,
just running them. They live in `.maestro/rider/` (5 flows) and
`.maestro/driver/` (7 flows).

```bash
# One single test
maestro test .maestro/rider/01_login.yaml

# All rider-app tests, in order
maestro test .maestro/rider/

# All driver-app tests, in order
maestro test .maestro/driver/

# Absolutely everything
maestro test .maestro/

# Interactive mode — watch the test click through the app live, useful
# for figuring out why a test can't find a button/label
maestro studio
```

Each test file's first line (`appId: com.spinr.user` for rider,
`appId: com.spinr.driver` for driver) tells Maestro which app to open — you
don't need to be in a particular folder to run them.

**Login/OTP note:** every flow logs in using a test phone number and the
code `1234` — this "dev bypass" code only works when the backend isn't
running in production mode, which your local backend from A4 satisfies. No
real text message is sent.

**One real limitation to know about:** tests 03 onward in the driver folder,
and 04–05 in the rider folder, involve an actual live ride between a rider
and a driver (a real offer sent over the network, not a fake one). To run
those locally you need **two emulators (or one emulator + your phone)
running at once** — one as the rider requesting a ride, one as the driver
accepting it — timed by hand. This isn't a bug, it's just what testing a
real two-sided marketplace looks like without a second test-automation
layer standing in for the other side. Full details and troubleshooting:
`.maestro/README.md`.

---

## Part B — Set up for iOS testing

**Read this first:** Maestro's iOS support only works on a Mac with Xcode
installed. There is no way around this — not from Windows, not from Linux,
locally or in the cloud-free way Part A describes for Android. If your
laptop isn't a Mac, skip to **"If you're not on a Mac"** below.

### If you're on a Mac

1. Install **Xcode** from the Mac App Store (it's large — 10GB+ — budget
   time and disk space).
2. Open Xcode once, accept the license, and let it finish installing
   additional components when prompted.
3. Install the command-line tools:
   ```bash
   xcode-select --install
   ```
4. Install CocoaPods (needed to build React Native's iOS side):
   ```bash
   sudo gem install cocoapods
   ```
5. Boot a simulator — either open the **Simulator** app directly, or:
   ```bash
   xcrun simctl list devices          # see what's available
   xcrun simctl boot "iPhone 15"      # boot one
   open -a Simulator
   ```
6. Build and install each app the same way as Android, just with `run:ios`:
   ```bash
   cd rider-app && npx expo run:ios
   cd driver-app && npx expo run:ios
   ```
7. Run the same `maestro test .maestro/...` commands from A6 — Maestro
   auto-detects a booted simulator.

### If you're not on a Mac

You genuinely cannot run Maestro's iOS tests locally. Your options:
- **Borrow/use a Mac** (even briefly) for the one-time local run above.
- **Wait for the iOS CI lane** — see Part D. This needs an Apple Developer
  account connected to the project first, which hasn't happened yet.
- **TestFlight manual testing** — once an iOS build is submitted to Apple's
  TestFlight (Apple's beta-testing platform), you can test the real app by
  hand on any iPhone without needing a Mac yourself. This is manual, not
  automated, but it's the practical fallback today.

---

## Part C — What "smoke," "regression," and "end-to-end" mean here

You asked for smoke, regression, and end-to-end (E2E) coverage — in plain
terms:

- **Smoke test** = "does the app even turn on and do the basics." Fast,
  runs first, catches a completely broken build immediately.
- **Regression test** = "did something that used to work get broken by a
  new change." This is really the *whole* test suite run repeatedly over
  time — every existing flow re-run after every new change is a regression
  check by definition.
- **End-to-end (E2E) test** = a full real-world journey across multiple
  screens *and* multiple people/systems — e.g. a rider requests a ride, a
  driver receives and accepts it, drives, and completes the trip. These are
  the most valuable and the most expensive to run.

Here's how Spinr's 12 existing flows map onto that, with the tier I'd assign
each one:

| Flow | Tier | What it checks |
|---|---|---|
| `rider/01_login.yaml` | Smoke | Phone entry → OTP → reaches home screen |
| `driver/01_login.yaml` | Smoke | Same, driver side |
| `driver/02_go_online.yaml` | Smoke | Driver can toggle online/offline |
| `rider/02_request_and_cancel_ride.yaml` | Regression | Destination search, fare shown, cancel works |
| `rider/03_schedule_and_cancel_ride.yaml` | Regression | Scheduled-ride reminder logic |
| `driver/06_payout.yaml` | Regression | Payout request (needs a seeded balance) |
| `driver/03_accept_ride.yaml` → `driver/07_in_trip_chat.yaml` | **E2E** (two-sided) | Real dispatch offer → accept → OTP → chat → complete trip |
| `rider/04_mid_trip_chat.yaml`, `rider/05_sos_button.yaml` | **E2E** (two-sided) | Rider side of the same live-trip scenarios, plus the SOS button |

**What's *not* covered yet** — and honestly can't be, by any single-laptop
tool like Maestro — is tracked candidly in
`docs/MOBILE_SMOKE.md` (marked 🔲): things like real push-notification
permission prompts, background location while the phone is locked, actual
SMS delivery to emergency contacts, and app-kill/relaunch mid-trip. Those
stay manual, pre-release, on-device checks by design — not a gap I'd
recommend trying to force into Maestro.

---

## Part D — Making tests run automatically "at every new build"

This is the part that isn't fully switched on yet, and here's exactly why —
not vague "it's complicated," but the two specific blockers.

### What already exists (Android)

- `.github/workflows/maestro-e2e.yml` — a real, working CI pipeline. It
  builds a real native Android app (not a shortcut/mock), uploads it to
  Maestro's cloud device farm, and runs all 12 flows there.
- `.github/workflows/label-run-maestro.yml` — automatically flags a pull
  request for Maestro testing the moment someone changes real
  rider-app/driver-app app code (not just tests or docs) — so nobody has to
  remember to ask for it.

So the automation to fire on "every relevant new build" (specifically,
every PR that touches real mobile app code) **is built**. It just can't
complete yet, for one reason:

### The blocker: two missing access keys

Running a test on Maestro's cloud device farm, and building the app via
Expo's cloud build service, both require an access key/token — like a
password that lets the automation act on your behalf:

1. **`EXPO_TOKEN`** — lets CI build the app via Expo's build service.
2. **`MAESTRO_CLOUD_API_KEY`** — lets CI upload the built app and run it on
   Maestro's hosted device farm (get one at console.mobile.dev).

Neither has been confirmed as present in this repo's GitHub settings. Until
both are added, every automatic run fails immediately at the "log in" step
— it's not broken code, it's missing keys.

**Who can fix this, and where:**
- A repo admin goes to the repository's **Settings → Secrets and variables
  → Actions** page and adds both as **Repository secrets**.
- This is a 5-minute task for whoever already holds those two accounts'
  credentials — I can't do it from here (I don't have write access to your
  repo secrets, and it shouldn't be handed to an AI session regardless).
- After adding them, run the workflow once by hand (**Actions tab →
  "Maestro Mobile E2E" → Run workflow**) to prove it actually completes —
  not just that the file's syntax is valid.

This exact gap is already tracked as **`ACTION_ITEMS.md` item B25** if you
want the full history.

### Why it isn't just switched on for *every* push, everywhere

Both the cloud build and the cloud device farm cost real money per run.
That's why the trigger is scoped to "a PR that actually touches native
mobile code," not every single commit on every branch — a deliberate cost
control already built into the workflow, not an oversight. This means
testing happens *before* a change merges (catching the bug pre-merge),
which is arguably better than testing only after the real production
mobile build ships.

### iOS in CI

There's currently no iOS lane in CI at all. It needs an Apple Developer
account connected inside Expo's build service (EAS) first — that's a
business/procurement step (Apple Developer Program enrollment, ~$99/year,
plus someone configuring it in EAS), not something a code change can do.
Until then, iOS testing means: your own local Mac run (Part B), or manual
TestFlight testing.

---

## Part E — DAST (security-testing the live, running app)

**In plain terms:** there are two different kinds of automated security
testing, and Spinr already does one of them on every single pull request:

- **SAST** (Static Application Security Testing) = reading the *source
  code* for known-bad patterns, without running anything. Spinr already
  runs this on **every PR** via `.github/workflows/security-gates.yml`
  (Semgrep, Bandit, ESLint's security rules, dependency/secret scanning,
  and more).
- **DAST** (Dynamic Application Security Testing) = actually attacking a
  **live, running** version of the app the way a real attacker would, from
  the outside, to find things static analysis can't see (misconfigurations,
  things that only show up at runtime).

### What exists for DAST

`.github/workflows/dast-zap-baseline.yml` runs OWASP ZAP (a well-known,
free security scanning tool) in "baseline" mode — it passively watches
traffic and does light, safe crawling; it does **not** send actual attack
payloads (no SQL-injection attempts, no fuzzing). That's deliberate: an
active attack-style scan against a real environment can create junk data or
trip rate limits, and needs a human watching, not unattended automation.

### The blocker: there's nowhere to point it yet

DAST needs a live URL to attack. Spinr doesn't have a **staging
environment** yet — right now, deploys go straight from `main` to
production, with nothing in between. The scan workflow already knows this:
it checks for a `STAGING_URL` value, finds none, logs a clear
"not configured, skipping" message, and exits cleanly rather than pretending
to have scanned anything. Standing up that staging environment is tracked
as **`ACTION_ITEMS.md` item E1** — it's an infrastructure project (a
separate Fly.io app + a throwaway Supabase project with fake data), not a
testing-config change, and it's the actual prerequisite here.

**Once a staging environment exists:**
1. Set `STAGING_URL` as a repository variable or secret, pointed at that
   staging URL **only** (never production — the workflow doesn't even have
   an input to override the target, by design, so this can't accidentally
   point at prod).
2. The existing weekly scan (Mondays, 03:00 UTC) and manual "Run workflow"
   trigger both start producing real findings, uploaded as a downloadable
   report for a human to review — it doesn't auto-open issues or fail
   builds by itself.

A full third-party penetration test (a hired external firm actually trying
to break in) is the other half of this, tracked in the same
`ACTION_ITEMS.md` item (**E6**) — that's a procurement/budget decision for
a person to make, not something any workflow can do.

---

## My recommendation

Do these in this order — each one is independently useful even if the next
one is delayed:

1. **Do Part A now.** It costs nothing, needs no one else's permission, and
   gives you real, working Android test coverage on your own laptop today.
2. **Get the two secrets added and the workflow test-fired (Part D).** This
   is the highest-value, lowest-effort unlock available — a 5-minute admin
   task turns "tests exist but never run" into "tests run automatically on
   every relevant PR." Do this before investing more in new test flows;
   there's no point writing more tests for a pipe that's currently closed.
3. **iOS CI and the staging environment are real projects, not quick
   fixes** — sequence them based on what you're shipping next (if an iOS
   release is imminent, prioritize Apple Developer provisioning; if a
   security review or launch is imminent, prioritize the staging
   environment for DAST).

---

## Next steps you can hand out in parallel (no conflicts, no shared files)

| Track | Owner | Depends on | Touches |
|---|---|---|---|
| A. Run local Android tests, work through the 🔲 manual items in `docs/MOBILE_SMOKE.md` | You / QA | Nothing | Your laptop only |
| B. Add `EXPO_TOKEN` + `MAESTRO_CLOUD_API_KEY` repo secrets, hand-fire the workflow once | Repo/org admin | Nothing | GitHub repo settings only |
| C. Provision an Apple Developer account inside EAS for the iOS CI lane | Whoever owns Apple Developer access | Nothing | EAS project config only |
| D. Stand up the staging environment (`ACTION_ITEMS.md` E1), then set `STAGING_URL` | Infra/DevOps | Nothing (independent project) | New Fly.io app + Supabase project |

None of these four touch the same files or systems, so they're safe to run
at the same time without stepping on each other.

---

## A note on access scoping (keep this project-specific)

When you (or whoever holds the accounts) create the two access keys in
Part D, scope each one narrowly, matching the same principle already used
elsewhere in this repo (the DAST workflow *only* reads `STAGING_URL` and
has no way to target another URL — that's a good pattern to copy):

- **`EXPO_TOKEN`** — create it as a token scoped to Spinr's own Expo/EAS
  project specifically, not a personal, account-wide token that could also
  touch unrelated apps.
- **`MAESTRO_CLOUD_API_KEY`** — scope it to the Maestro Cloud
  team/project used for Spinr only.
- Store both only as this repository's GitHub Actions secrets — never in a
  shared, cross-project secret store, and never reused for another client's
  CI pipeline.

---

## Quick command reference

```bash
# One-time installs
curl -Ls "https://get.maestro.mobile.dev" | bash      # Maestro CLI
xcode-select --install                                  # Mac only, for iOS

# Every time you test
cd backend && python3 -m backend.server                 # terminal 1
cd rider-app && npx expo run:android                    # terminal 2 (or run:ios on Mac)
cd driver-app && npx expo run:android                   # terminal 3

# Run tests
maestro test .maestro/rider/01_login.yaml
maestro test .maestro/rider/
maestro test .maestro/driver/
maestro test .maestro/
maestro studio        # interactive debugging
```

## Where to look next

- `.maestro/README.md` — the terser, engineer-facing version of Part A/B,
  with troubleshooting tips.
- `docs/E2E_TESTING.md` — how Playwright (web) testing works for
  admin-dashboard, and the mobile section this guide expands on.
- `docs/MOBILE_SMOKE.md` — the full manual smoke-test checklist, with
  ✅/🔲 markers showing exactly what's automated vs. still manual.
- `docs/runbooks/dast-and-pentest.md` — the DAST/pentest runbook this guide
  summarizes.
- `ACTION_ITEMS.md` — search for **B25** (Maestro CI blockers), **E1**
  (staging environment), **E6** (DAST + pentest) for the full history.

**One small housekeeping note:** root `CLAUDE.md`'s "Claude-Adjacent
Directories" table currently lists `.maestro/` as "Maestro orchestration
config" (implying an AI-agent tool). That's stale — `.maestro/` is this
mobile UI test suite, confirmed by its own README. Worth a one-line fix
next time someone's touching that table; not changed here to keep this PR
scoped to the testing guide itself.
