# Scheduled rides: service-area timing

Authorized: configurable early matching and rider/accepted-driver reminders, default ten minutes. No advance reservation marketplace or pickup guarantee. Area enabled=false preserves legacy matching at pickup; global scheduled_dispatch_enabled remains the kill switch.

Recovery note: the previously reviewed local branch was lost when the workspace reset before push. Reconstruct from the recorded design, rerun checks, and publish this draft incrementally. Earlier test results are historical, not evidence for this reconstructed branch.

Commit groups (at most three files each):
1. Strict config + additive migration + config/timing tests.
2. Admin API persistence + tests.
3. Scheduler area windows and accepted-driver reminders + tests.
4. Idempotent reminder inbox/suppression handling + tests.
5. Search retry/deadline and atomic timeout/sweeper + tests.
6. No-show, cancellation and response countdown guards + tests.
7. Offer payload time; mobile store/hydration/display/routing, each with focused tests.
8. Rider legacy local reminder cleanup + tests/startup.
9. Admin settings tab + tests/page wiring.
10. Change impact log, actual-diff review, verification and draft PR.

Alternative considered: long-term reservations. Rejected as a larger availability/state lifecycle than near-pickup offers require. Ship disabled; apply migration before backend, publish mobile updates, then staging/canary activation. Tests cover timer boundaries, reassignment, retry dedup, suppressed delivery, early arrival/cancellation, deadline races and configuration validation. Document build/device/live-DB limits. No production migration, deployment, activation or merge authorized by this PR task.
