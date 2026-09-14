# Change Impact & Risk — PR #5348 review coverage

Review found untested recovery branches: SecureStore becoming unavailable after
a refresh 401 or at initialization's final read, failures after the first token
write, and session teardown callbacks following a failed logout marker write.

The driver Jest suite now exercises those cases using the real shared auth store.
Partial writes on both iOS and Android must not publish access or CSRF state.
Unreadable credentials must remain recoverable without deletion or stale replay.
Registered teardown must run even when both marker persistence and cache clearing
fail; a cache failure still rejects and is not silently softened.

Blast radius: tests only; no runtime, schema, API, payment, or visual behavior
change. Rollback: git-revert-safe. No production data or credentials used.

Validation: driver initialization and refresh-race suites: 31 tests passed.
Rider/shared API refresh suites: 20 tests passed. Eight additional parameterized
cases were added to the initialization suite; production behavior is unchanged.
