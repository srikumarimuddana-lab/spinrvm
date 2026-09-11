# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| Related gap ID | `ACTION_ITEMS.md` AI17, sub-item F1 |

## 1. Issue / gap identified

`ACTION_ITEMS.md`'s AI17/F1 asked for a "word-boundary-buffered stream
filter... applied at the token yield, behind a settings flag... default
off," believing `filter_tool_leakage` still only ran on the persisted
reply. Investigating before implementing found that premise is stale: the
exact fix already shipped, unconditionally and more thoroughly, in PR
#5138 ("F08") — `backend/ai/stream_filter.py`'s `StreamingOutputFilter`
buffers and re-scrubs the whole turn so far before releasing a prefix,
applying both `filter_tool_leakage` and `scrub_pii` to every token before
it reaches the client. Confirmed both landing commits (`978a807`,
`3175fe8`) are on `main`. Implementing F1 literally (a flag defaulting
off) would have meant shipping a working, security-audited privacy
protection as off-by-default — a regression, not a fix. Escalated to the
user rather than doing that; they asked for an admin kill-switch instead
(default ON, matching current behaviour) as an operational lever, not a
privacy toggle.

## 2. Root cause

`ACTION_ITEMS.md`'s AI17 entry was written describing the pre-F08 gap and
was never updated after F08 closed it — a backlog-hygiene gap, not a code
gap. No settings-controllable kill-switch existed for the incremental
release mechanism itself (as opposed to the filtering, which was already
unconditional and correct).

## 3. Fix / remediation

Added `ai_stream_incremental_enabled` (bool, default `TRUE`) to the
`settings` table (migration 409) and threaded it through
`backend/ai/orchestrator.py`'s `run_chat_turn`: when `TRUE` (default,
unchanged behaviour), the reply streams token-by-token exactly as today.
When `FALSE`, `StreamingOutputFilter` is constructed with a `holdback` far
larger than any real reply (`_DISABLE_INCREMENTAL_HOLDBACK = 1 << 30`),
which makes its own `_release(final=False)` always return `""` — so
nothing leaves the filter until `flush()`, and the whole (still fully
scrubbed) reply ships as one chunk. No changes to `stream_filter.py`
itself; the module's existing constructor parameters already supported
this degraded mode.

## 4. Risk & impact on existing functionality

- Default value (`TRUE`) reproduces today's behaviour exactly — verified
  by a new test (`test_incremental_streaming_defaults_on_when_setting_absent`)
  proving a settings dict missing the key still streams incrementally
  (explicit `True` default in the `.get()` call, not relying on truthy
  coercion of a missing key).
- `filter_tool_leakage`/`scrub_pii` are applied identically regardless of
  the flag — the only thing the flag changes is release timing/chunking,
  never what content reaches the client. Verified by
  `test_incremental_streaming_kill_switch_still_filters_everything`
  (flag off, cross-chunk-boundary phone number + tool name, both still
  redacted).
- `run_chat_turn` is the only caller of `StreamingOutputFilter`
  (`admin/ai_console`'s `adminAiChat` and the public/support assistants
  are non-streaming, single-response endpoints — checked, they have no
  live-stream leak window to begin with).
- Blast radius: isolated to `orchestrator.py`'s streaming loop. The
  `SettingsUpdateRequest`/`AppSettings`/allowlist-drift trio is the
  established, low-risk pattern for adding any settings-table flag in
  this repo (mirrors `location_marker_write_gate_enabled`, migration 370).

## 5. User-experience effect

None by default (flag is `TRUE`, matching today). If an admin flips it to
`FALSE` during an incident, riders/drivers/admin-console testers see the
AI assistant's reply arrive all at once instead of streaming in — a
UX degradation, never a content difference, since the same scrub always
runs before anything is released either way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/409_ai_stream_incremental_kill_switch.sql` | New `settings.ai_stream_incremental_enabled` column, `DEFAULT TRUE` | Persist the flag |
| `backend/schemas.py` | `AppSettings` gained the field, default `True` | Settings read-model |
| `backend/routes/admin/settings.py` | `SettingsUpdateRequest` gained the field | Admin write-allowlist |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Added the column name to `KNOWN_SETTINGS_COLUMNS` | Keep the drift guard accurate |
| `backend/ai/orchestrator.py` | Reads the flag once per turn; `out_filter` construction branches on it | The actual kill-switch wiring |
| `backend/tests/test_ai_orchestrator.py` | 2 new tests (flag off → single-chunk + still filtered; key absent → defaults on) | Cover both directions |

## 7. Before / after

```python
# Before
out_filter = StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)

# After
out_filter = (
    StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)
    if incremental_streaming_enabled
    else StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT, holdback=_DISABLE_INCREMENTAL_HOLDBACK)
)
```

## 8. Rollback plan

`git revert` for the code; the migration's own rollback
(`DROP COLUMN IF EXISTS ai_stream_incremental_enabled`) restores exactly
today's behaviour, since the read site's explicit `True` default already
matches production with the column absent. No data migration either
direction.

## 9. Verification performed

- [x] Automated tests: `python3 -m pytest tests/test_ai_orchestrator.py tests/test_admin_settings_write_allowlist_drift.py tests/test_ai_stream_filter.py tests/test_ai_pii.py` — 281 passed, including 2 new
- [x] `ruff check` on all touched files — clean
- [x] Blast-radius grep performed: confirmed `StreamingOutputFilter` has exactly one call site (`orchestrator.py`), and every other AI-chat-shaped endpoint (`support_assistant.py`, `public_assistant.py`, admin `adminAiChat`) is non-streaming and structurally can't have this leak window
- [ ] Manual repro / staging check — not done this session (no live Supabase/staging access)

**Not verified**: no live device/browser run confirming the admin settings toggle actually renders and saves correctly in the admin-dashboard settings UI (no admin-dashboard files were touched — the existing generic settings-save UI already handles any `SettingsUpdateRequest` field without per-field UI code, per the existing pattern for boolean AI flags).
