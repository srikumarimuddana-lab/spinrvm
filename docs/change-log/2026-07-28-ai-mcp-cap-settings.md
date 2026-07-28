# Change Impact & Risk Log — persist `ai_mcp_daily_tool_cap` in settings (Codex review fix)

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (PR #2774 Codex review follow-up) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | ai |
| PR / commit link | PR #2774, branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Codex P2 review comment on `backend/ai/mcp_server.py:171` |

## 1. Issue / gap identified

The `/mcp` daily cap read `settings.get("ai_mcp_daily_tool_cap")`, but that key existed nowhere — not in `AppSettings`, the admin PATCH model, the settings table, or the UI — so the advertised independent cap could never be set and the expression always fell back to `ai_daily_message_cap`.

## 2. Root cause

The cap commit added the read without the settings plumbing; Spinr settings are typed columns (schemas.py + `settings` table), not free-form keys.

## 3. Fix / remediation

Full plumbing following the 145/208/211 pattern (numbered 267 after a rebase onto newer `main`, which had taken 264–266): `AppSettings.ai_mcp_daily_tool_cap: int = 0` (0 = unset → fall back), `SettingsUpdateRequest` field (`ge=0, le=5000`), migration `267_settings_add_ai_mcp_cap.sql` (additive `ADD COLUMN IF NOT EXISTS ... DEFAULT 0`), and an admin Settings field next to the MCP switch ("0 = use the chat daily-message cap").

## 4. Risk & impact on existing functionality

- Blast radius: settings schema/PATCH/UI + one additive column on the single-row `settings` table. `mcp_server.py` unchanged — `0 or fallback` semantics already matched.
- `spinr-migration-reviewer` verdict: SAFE TO APPLY, no blockers; one consistent-with-convention warning (no DB CHECK constraint mirroring the Pydantic bound — same as every other `ai_*` cap column).
- Without the migration applied, a Settings save including the new field would PGRST204-503 (the exact failure mode 145/208/211 headers describe) — migration ships in the same PR and applies before deploy per standard order.

## 5. User-experience effect

Internal-admin only: one new numeric field in Settings → AI section.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/schemas.py` | `ai_mcp_daily_tool_cap: int = 0` | Typed default |
| `backend/routes/admin/settings.py` | PATCH field `ge=0, le=5000` | Admin can set it |
| `backend/migrations/267_settings_add_ai_mcp_cap.sql` | Additive column, default 0 | Persistence |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Numeric input beside MCP switch | UI |
| `backend/tests/test_ai_admin_settings.py` | Round-trip + bounds test | Regression pin |

## 7. Before/after

Before: `settings.get("ai_mcp_daily_tool_cap")` → always `None` → always the fallback.
After: admin-settable 0–5000; `0` keeps the documented fallback to `ai_daily_message_cap`.

## 8. Rollback plan

Column is additive with default 0 (rollback SQL documented in the migration header); reverting the code alone restores the always-fallback behavior with the column harmlessly present. `/mcp` remains kill-switched by `ai_mcp_enabled`.

## 9. Verification performed

- `pytest backend/tests/test_ai_admin_settings.py test_ai_settings.py test_ai_mcp.py` — 33 passed.
- `spinr-migration-reviewer` subagent run on migration 264 — SAFE TO APPLY.
- Admin dashboard: `npx vitest run` + **real `npm run build`** (Next.js production build).

## 10. What was NOT verified

- Migration not applied to a live/staging Supabase in this session (additive, idempotent; applies via `scripts/migrate.py` on deploy).
- Settings save round-trip not exercised against a real PostgREST instance (mocked in unit tests).
