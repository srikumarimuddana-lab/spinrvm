# Change Impact & Risk Log — C43: prepare (not apply) RLS-enable migration

- **Issue/gap identified:** `settings` (holds Stripe/Twilio/Google Maps API
  keys), `document_files`, `driver_csv_import`, and `driver_bank_import`
  have Row Level Security disabled in production — no policy layer stands
  between an anon/publishable Supabase key and these tables via PostgREST.

- **Root cause:** these 4 tables were never migrated to enable RLS when the
  rest of the schema adopted the documented `backend/migrations/CLAUDE.md`
  RLS pattern (found via the Supabase MCP connector's advisory scan,
  ACTION_ITEMS.md C43, 2026-08-25). No single migration ever ran
  `ENABLE ROW LEVEL SECURITY` on them.

- **Fix/remediation:** **prepared only, not applied.** New migration
  `backend/migrations/379_enable_rls_settings_document_files_driver_imports.sql`
  runs exactly 4 `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` statements, no
  new policies (RLS-enabled-with-zero-policies is deny-all to
  `anon`/`authenticated`, matching what these tables' *real* access pattern
  already is today). This PR does not run it against any real environment —
  the user explicitly reconfirmed this session (2026-08-31) that the
  2026-08-25 deferral (hold off until the legacy-migration/A41-family work
  concludes) is still in force.

- **Risk & impact on existing functionality:** for the migration file itself
  — none, since it is not applied. For the eventual real apply (out of
  scope for this PR, tracked separately): the only in-repo readers/writers
  of these 4 tables are backend routes using `SUPABASE_SERVICE_ROLE_KEY`
  (`backend/supabase_client.py`), which bypasses RLS regardless of whether
  it's enabled — grepped `rider-app`, `driver-app`, `admin-dashboard` for
  any `createClient(...)` call reading these tables with an anon key: zero
  real matches (the only anon-key `createClient` anywhere in the repo is a
  dead, unwired scaffold file with placeholder credentials). This is a
  static grep, not a staging/canary-verified guarantee — flagged as such in
  both C43's own text and the readiness doc below.

- **User experience effect:** none — rider, driver, corporate-admin, and
  internal-admin flows are all unaffected, since nothing in this PR runs
  against a real database and, when it eventually does, every real caller
  already goes through the service-role key.

- **Files modified:**

  | file path | what changed | why |
  |---|---|---|
  | `backend/migrations/379_enable_rls_settings_document_files_driver_imports.sql` | New migration: 4 `ENABLE ROW LEVEL SECURITY` statements, no policies | Prepares C43's fix, ready to apply once the deferral lifts |
  | `docs/runbooks/c43-rls-enable-readiness.md` | New readiness doc: what the migration does, why it's currently believed low-risk, the re-confirmation grep to re-run before ever applying | Documents the exact pre-apply checklist for whoever picks this up |
  | `ACTION_ITEMS.md` | C43 entry updated: migration prepared-not-applied, deferral restated as still in force | Keep the backlog accurate |

- **Before/after snippet:** not applicable — this is a purely additive new
  migration file; no existing behavior-changing diff to show.

- **Rollback plan:** for this PR, `git revert` is sufficient (the migration
  was never applied). For the eventual real apply, the migration's own
  header comment gives the exact rollback: the same 4 `ALTER TABLE`
  statements with `DISABLE ROW LEVEL SECURITY`, which was verified to work
  against a scratch local Postgres instance (see Verification below).

- **Verification performed:** applied the migration to a scratch local
  Postgres 16 instance against 4 dummy tables loosely matching the real
  names/shapes — confirmed `pg_class.relrowsecurity = t` on all 4 after
  apply, then ran the rollback statements and confirmed
  `relrowsecurity = f`, then dropped the scratch database. Re-ran the
  blast-radius grep (`createClient` across all three frontend surfaces)
  myself this pass — zero matches, consistent with C43's prior finding. Ran
  a manual `spinr-migration-reviewer` checklist pass (Agent/Task tool
  unavailable this session) — no blockers; flagged as warnings only: lock
  scope on `settings` (small table, low practical risk) and the standing
  deny-all-future-consumer risk if a frontend ever adds a real anon-key
  Supabase client.

- **What was NOT verified:** never tested against staging or production —
  no `DATABASE_URL`/service-role credentials available this session, and
  the task was explicitly scoped as prepare-only. Whether the project's
  anon/publishable key has ever been distributed anywhere reachable by an
  external party is still unconfirmed (same gap C43's own text already
  flags, not resolved here). Exact column-level contents of `settings`
  (plaintext vs. encrypted/referenced-by-ID secrets) also not checked.
