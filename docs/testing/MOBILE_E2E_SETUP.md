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
| iOS — run tests on your own laptop | 🟡 Mac + Xcode only | Maestro cannot test iOS from Windows or Linux **locally**, full stop (Part B) |
| iOS — run tests **in the cloud** (no Mac needed) | 🟡 One real blocker, not "needs a Mac" | Needs an Apple Developer account connected inside EAS — a business step, not a coding one, and **not** a Mac requirement (Part F, added 2026-09-11) |
| iOS — run tests automatically in CI, hands-off | 🟡 Built, first real run unproven | `maestro-ios-macos-runner.yml` (Part F) — no secrets/Apple account needed; trigger it once to confirm it actually works |
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

**Read this first:** running Maestro's iOS tests **locally, on your own
laptop**, only works on a Mac with Xcode installed — there's no way around
that specific case, not from Windows, not from Linux. That is *not* the
same as saying iOS testing needs a Mac, full stop — it doesn't. **If you
want iOS testing without owning a Mac, skip straight to Part F** — cloud
device testing (Maestro Cloud or a couple of strong alternatives) runs iOS
tests from an ordinary Linux CI machine, no Mac anywhere in the chain. Come
back to this Part B only if you specifically want to run things by hand on
your own machine.

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

You genuinely cannot run Maestro's iOS tests **locally on your own
machine**. Your options:
- **Use cloud device testing instead — Part F.** This is the real answer
  for most people in this situation: no Mac required anywhere, real or
  simulated iPhones run in someone else's data center, and it reuses the
  same 12 flow files. Start there.
- **Borrow/use a Mac** (even briefly) if you specifically want to debug a
  flow interactively with `maestro studio`, which only makes sense running
  locally.
- **TestFlight manual testing** — once an iOS build is submitted to Apple's
  TestFlight (Apple's beta-testing platform), you can test the real app by
  hand on any iPhone without needing a Mac yourself. This is manual, not
  automated, but it's a useful fallback for exploratory testing.

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

## Part F — Cloud device testing: Maestro Cloud vs. the alternatives

*(Added 2026-09-11, in response to: "can we test the Android and iOS app on
Maestro or any other product in the cloud, and what's the best solution
going forward?")*

### The headline finding

**None of the cloud options below need you to own a Mac.** That's the
biggest thing this research changed from what Part B says above. Owning a
Mac only matters if you want to run Maestro **locally, on your own
laptop**. Every cloud device-testing product — Maestro Cloud included —
builds and runs the iOS app on infrastructure the vendor owns, triggered
from an ordinary Linux CI machine (this repo's GitHub Actions runners are
already Linux). The same is true of Expo's own build service (EAS Build):
it compiles the iOS app on Expo's cloud Mac infrastructure no matter what
machine you run `eas build` from.

### So what's actually blocking cloud iOS testing today?

One thing, and it isn't a Mac: **an Apple Developer Program account
($99/year) needs to be connected inside EAS** so it can produce a signed
iOS build. That's the same blocker already named in Part D/`ACTION_ITEMS.md`
B25 — this research doesn't change it, it just clears up *why* it's the
blocker (identity/signing, not compute).

**One genuinely free interim option, worth doing before spending anything:**
EAS can build an iOS **Simulator** build (`"simulator": true` in a build
profile) with **no Apple Developer account at all** — it can't install on a
real iPhone, but Maestro Cloud can run a Simulator build in its hosted
farm. That means iOS cloud testing could start this week, at zero new
cost beyond normal EAS/Maestro Cloud usage, using the same 12 existing flow
files, well before anyone decides on the $99/year account. Today,
`rider-app/eas.json`/`driver-app/eas.json`'s `test` profile (the one
`maestro-e2e.yml` uses) has no iOS entry at all — adding one, plus an iOS
job in `maestro-e2e.yml` mirroring the existing Android job, is a small,
isolated change I can draft on request (see the question at the end of
this guide).

### The options, compared

| Product | Runs your existing 12 Maestro flows as-is? | Real iOS devices (not just simulator)? | Needs a Mac anywhere? | Rough cost (unverified against primary pricing pages — confirm before budgeting) | Verdict for Spinr |
|---|---|---|---|---|---|
| **Maestro Cloud (mobile.dev)** | ✅ Yes — it's Maestro's own hosted runner | Simulators confirmed; real-device coverage referenced but not independently confirmed | ❌ No | ~$250/device/month (per concurrent slot), 7-day free trial | 🟢 **Top pick** — already wired into `maestro-e2e.yml`, zero rewrite, zero new integration work |
| **BrowserStack App Automate** | ✅ Yes — native, documented Maestro support | ✅ Yes, real devices | ❌ No | ~$249/month (1 parallel slot), scales with parallelism | 🟢 Strongest fallback if you want confirmed real-device coverage or Maestro Cloud's pricing doesn't work out |
| **LambdaTest / TestMu AI** | ✅ Added Maestro + HyperExecute support (Jan 2026) | Real device cloud | ❌ No | Not confirmed this session | 🟡 New enough to be worth a trial, not enough track record yet to lead with |
| **Bitrise (Device Cloud for Maestro)** | ✅ Marketplace step | Real devices | ❌ No | Bundled CI + device-farm pricing | 🟡 Only worth it if you'd also want to replace GitHub Actions itself |
| **devicelab-dev/maestro-runner** (open-source) | ✅ Maestro-compatible | Depends which backend farm you point it at | ❌ No | Free tool — you still pay whatever device farm it drives | 🟡 Interesting, but a small third-party project — pilot it, don't bet production QA on it sight-unseen |
| **Sauce Labs** | ❌ No native support (Appium/Espresso/XCTest only) | ✅ Yes | ❌ No | ~$199–249/month+ | 🔴 Would mean abandoning or rewriting all 12 existing flows — not worth it |
| **Firebase Test Lab** | ❌ No — Espresso/XCTest/Robo only | ✅ Yes | ❌ No | Free daily quota, then pay-as-you-go | 🔴 Same rewrite problem — Spinr's existing Firebase usage doesn't change this |
| **AWS Device Farm** | ❌ No native support (generic custom scripting only) | ✅ Yes | ❌ No | Not confirmed | 🔴 More DIY glue work than any option above, no upside to compensate |

**One naming trap worth knowing about:** AWS Marketplace lists something
called "Maestro Cloud Control (MCC)" — that's an unrelated cloud-gaming/VDI
product, nothing to do with mobile.dev's Maestro. Don't let the name
confuse a vendor search.

### If nobody on the team has Mac access at all — the cheapest real answer

The comparison above assumes paying a device-farm vendor (Maestro Cloud,
BrowserStack, etc.) for iOS coverage. If the team has **zero Mac access,
anywhere**, there's a materially cheaper option that avoids a device-farm
subscription entirely: **GitHub Actions' own macOS-hosted CI runners.**

- GitHub's hosted `macos-*` runners come with **Xcode and the iOS
  Simulator pre-installed** — no setup beyond writing the workflow file.
- A job on one of these runners can do the *entire* loop itself: `npx expo
  run:ios` (a real native build, compiled right there — no EAS Build cloud
  cost), boot the iOS Simulator, then run the free Maestro CLI against it
  — the same pattern already used for Android locally in Part A, just
  happening inside CI instead of on someone's laptop. No Apple Developer
  account is needed for this (Simulator-only, same as the free path
  described above). Maestro's own docs have a guide for exactly this setup
  ("Maestro GitHub Action for iOS"), and it's a well-established community
  pattern, not an exotic one.
- **This is now built**, as of 2026-09-11:
  `.github/workflows/maestro-ios-macos-runner.yml`. It builds both apps on
  a `macos-15` runner via `npx expo run:ios --configuration Release`,
  boots the Simulator, and runs the same 12 `.maestro/` flows locally
  against it — no EAS Build, no Maestro Cloud, no `EXPO_TOKEN` or
  `MAESTRO_CLOUD_API_KEY` needed anywhere in this path. Trigger it via the
  Actions tab ("Maestro Mobile E2E (iOS, local build...)" → Run workflow)
  or by applying a **`run-maestro-ios`** label to a PR — a deliberately
  separate label from the Android lane's `run-maestro`, not yet wired into
  the auto-labeler, so this new/unproven lane only runs when someone asks
  for it. **Not yet exercised against a real macOS runner** — the YAML is
  syntax-validated but this environment has no way to run a real GitHub
  Actions macOS job, so the first real trigger is the actual proof this
  works; see the workflow file's own header comment for the full list of
  known limitations (no backend started in-job, Simulator device left to
  Expo's own default selection).
- **Cost — and this is the headline number**: macOS runner minutes are
  billed at roughly **$0.062/minute** on a private repo (GitHub cut this
  price ~23% on 2026-01-01) and count against your included free-minutes
  pool 10× faster than Linux minutes, so budget it as real, if modest,
  spend rather than "free." A 20-30 minute iOS build+test job costs
  roughly **$1.25–$1.90 per run**; run it a few times a week and that's
  **roughly $16–$24/month total** — compare that to Maestro Cloud's
  reported ~$250/device/month or BrowserStack's ~$249/month. For
  occasional iOS runs (not literally every commit), this is an order of
  magnitude cheaper than any device-farm subscription, with no new vendor
  account at all.
- **Known gotchas** (from community write-ups of this exact setup): the
  CocoaPods install step is usually the slowest part — cache
  `Pods`/`~/Library/Caches/CocoaPods` between runs; a freshly-booted
  Simulator can be slow/flaky on a cold runner, so script an explicit
  `xcrun simctl boot` with a wait/retry rather than trusting Maestro to
  boot it for you.
- **Not worth it yet**: a self-hosted Mac mini (e.g., via MacStadium) only
  becomes cheaper than GitHub's hosted minutes at roughly **5,000+ CI
  minutes/month** of usage — nowhere near "a few times a week." Stick with
  GitHub's hosted macOS runners unless usage grows to near-daily.

*(Confidence note: GitHub's/Expo's/Maestro's own docs sites were
unreachable from this research tool's network, so the numbers above are
cross-checked across several independent sources rather than pulled from
one official page — worth a quick spot-check against GitHub's live Actions
billing page before treating the dollar figures as final.)*

### Why Sauce Labs, Firebase Test Lab, and AWS Device Farm are ruled out here

All three would throw away the 12 flow files that already exist and pass
locally — Spinr would be rewriting the same test coverage in a different
tool's language (Appium/Espresso/XCTest) for no functional gain. That's a
real cost with no offsetting benefit today. Worth revisiting only if a
specific requirement shows up that only one of them can meet (e.g., a
compliance reason to use AWS specifically).

### What "best solution going forward" means concretely

1. **Android: use Maestro Cloud** — it's not a new decision, it's finishing
   a decision already made and half-built in this repo. Do Part D's two
   steps (add the 2 secrets, fire the workflow once) to get Android cloud
   testing live.
2. **iOS, given no Mac access on the team: skip device-farm vendors
   entirely and use a GitHub Actions macOS runner instead** (the section
   just above). It reuses the same 12 flow files, needs no Apple Developer
   account, and runs at roughly **$16-24/month** for occasional use versus
   ~$250/month for a Maestro Cloud or BrowserStack iOS device. **Built
   2026-09-11** as `.github/workflows/maestro-ios-macos-runner.yml` — apply
   the `run-maestro-ios` label to a PR, or trigger it manually from the
   Actions tab, to run it. Its first real run is still unproven (see the
   file's own header comment) — treat that first trigger as validation,
   not as an already-confirmed-working pipeline.
3. **Decide on the Apple Developer Program account ($99/yr) separately, on
   its own timeline** — it's only needed later, for real-device coverage
   or TestFlight distribution, not for the Simulator-based CI path above.
4. **Keep Maestro Cloud/BrowserStack in your back pocket for iOS** only if
   the GitHub Actions runner path turns out to be too flaky or too slow in
   practice — it costs nothing to know they're an option, since neither
   needs a flow rewrite either.

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
3. **For iOS — given the team has no Mac access — use the GitHub Actions
   macOS-runner workflow from Part F**, not a Maestro Cloud/BrowserStack
   iOS subscription. It reuses the same 12 flows, needs no Apple Developer
   account, and runs at roughly $16-24/month for occasional use instead of
   ~$250/month for a device-farm iOS lane. **This is now built** —
   `.github/workflows/maestro-ios-macos-runner.yml` — trigger its first
   real run (Actions tab → Run workflow, or apply the `run-maestro-ios`
   label to a PR) to prove it actually works end to end; this update is
   the confirmation that iOS testing was never actually blocked on owning
   a Mac — it just needed the right CI approach.
4. **The staging environment (for DAST) is the one genuinely bigger
   project here** — sequence it based on what's next (prioritize it ahead
   of a security review or public launch).

---

## Next steps you can hand out in parallel (no conflicts, no shared files)

| Track | Owner | Depends on | Touches |
|---|---|---|---|
| A. Run local Android tests, work through the 🔲 manual items in `docs/MOBILE_SMOKE.md` | You / QA | Nothing | Your laptop only |
| B. Add `EXPO_TOKEN` + `MAESTRO_CLOUD_API_KEY` repo secrets, hand-fire the workflow once | Repo/org admin | Nothing | GitHub repo settings only |
| C. Trigger `maestro-ios-macos-runner.yml`'s first real run to validate it (Actions tab or the `run-maestro-ios` label) | Whoever's driving mobile CI | Nothing — it's already built, needs no secrets | `.github/workflows/maestro-ios-macos-runner.yml` (already written; just needs to be run) |
| D. Decide on and provision an Apple Developer account inside EAS (real-device iOS, TestFlight) | Whoever owns Apple Developer access | Nothing — independent of Track C | EAS project config only |
| E. Stand up the staging environment (`ACTION_ITEMS.md` E1), then set `STAGING_URL` | Infra/DevOps | Nothing (independent project) | New Fly.io app + Supabase project |

None of these five touch the same files or systems (Track C and D both
touch "iOS CI" as a topic but not the same actual settings — C is a code
change, D is an account/credential action), so they're safe to run at the
same time without stepping on each other.

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
