# Driver socket lifecycle ownership

| Field | Detail |
|---|---|
| Issue | A background close can schedule a reconnect; network events also reconnect while backgrounded. An in-flight token refresh can create a socket after teardown, and replaced sockets can still dispatch messages. |
| Root cause | Connection entry points lack one lifecycle eligibility check. The async refresh mutex has no cancellation identity, and message callbacks lack the ownership check already present in onclose. |
| Alternative | Increasing retry delays does not cancel obsolete attempts or prevent background reconnects. A full transport rewrite would disturb auth/replay/location delivery unnecessarily. Repair the existing hook's lifecycle guards. |
| Scope | Direct consumer: driver-app/app/driver/(tabs)/index.tsx. Affects phone dashboard sockets on Android/iOS, including phone navigation launched from the dashboard. Android Auto's independent carSession/car channel and background GPS/REST transport remain separate. No known-forks entry applies. |
| Risk/UX | Overly broad cancellation could strand foreground reconnects. Preserve transient inactive handling, the three-second background debounce, foreground retry ladder and auth handshake. No new screen, timer interval, auth policy or online/offline intent rule is introduced. |
| Tests | New harness mounts the real useDriverDashboard hook with React, simulated native AppState/network/socket boundaries, fake timers and a controllable token-refresh promise. Location, audio, storage and API adapters are mocked; this is not an on-device test. |
| Rollback | Revert the mobile commit and distribute the previous compatible JS update/build through the existing release process. No schema or data rollback. No new runtime flag: this enforces the already-shipped background-close policy. |
| Not verified | Actual APK/IPA, Android navigation or screen-lock behavior, Android Auto device, production networking and GPS continuity. Rider/driver apps have no automated visual regression tooling. |

Files:

| Path | Change | Why |
|---|---|---|
| driver-app/hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts | Behavioral lifecycle regression harness | Exercise real hook callbacks, not source-text matching |
| driver-app/hooks/useDriverDashboard.ts | Lifecycle eligibility and connection ownership guards | Cancel stale attempts and suppress background reconnects |
| docs/change-log/2026-09-15-driver-socket-lifecycle.md | Impact and verification record | Make staging limits explicit |

Before: the close handler retries whenever the driver is online; token refresh can return after cleanup; messages from replaced sockets reach the handler.

Intended after: connecting requires a current dashboard lifecycle and non-background app state, including after token refresh. Background/cleanup invalidates pending attempts. Only the current socket can authenticate or dispatch messages. Intentional background closure keeps the existing debounce, and foreground/network regain use the existing retry path.

Regression baseline: five intended behavioral failures and one passing short-transition control against unchanged main. Ran the real hook through Jest 30.3/React 19.3 with a minimal Babel/Node test harness and mocked native boundaries while the full Expo dependency installation was still running. No production source was changed for that run. The initial test commit deliberately records the failures; the implementation commit must make them pass before publication. This phase does not claim to repair missing native GPS samples or prove that the rider's car marker moves on a physical phone.
