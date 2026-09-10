# Connector scoping policy

Load this before adding, reviewing, or auditing any MCP connector or account-level
integration — repo-declared (`.mcp.json`, `.claude/mcp.example.json`) or
account-level (claude.ai Connectors: GitHub, Figma, Supabase, Vercel, Expo,
Context7, Sentry, Stripe, ...).

## The principle

**Every connector must be scoped to the narrowest unit its vendor supports —
project, not org; org, not account — verified by actually calling the
connector's own list/enumerate tool, not assumed from how it was set up.**

This file exists because that principle was violated twice in the same
session (2026-09-07) without anyone noticing until it was checked directly:

- The account-level **Vercel** connector's team contained a second, unrelated
  project (`desktop-website`) — reachable, pausable, and billable from any
  session using that connector, Spinr or not.
- The account-level **Supabase** connector exposed `execute_sql` and
  `apply_migration` against `Spinr-Prod` — not running in the read-only mode
  the repo's own `.claude/mcp.example.json` template assumes for the
  repo-scoped instance.

Neither was visible from reading `.mcp.json`, `.claude/mcp.example.json`, or
any other repo file — both only surfaced by calling the connector's own
`list_teams`/`list_projects`/`list_organizations`-style tools and looking at
what came back. **A repo-file-only audit cannot catch this class of drift.**
See `spinr-tooling-hygiene-reviewer`'s connector-scope check (below) for how
this is now checked going forward.

## Connector table

Status reflects what was last actually verified, not what the setup intends.
Update this table whenever a connector is reconfigured or re-checked — a
stale "narrowed" row is worse than an honest "needs narrowing" one.

| Connector | Type | Scoping mechanism the vendor supports | Status as last verified | Verified |
|---|---|---|---|---|
| Supabase | Account-level | MCP URL params `?project_ref=<id>&read_only=true`, or a project-scoped read-only PAT | **DECIDED 2026-09-08 (human-directed): full read/write/other permissions retained through October 31, 2026 — explicit, time-boxed exception, not a silent "leave it broad."** `spinrmobileapp` (`soavhtdhefowwvforzwb`, org `Spinr_MobileApp` / `hjdwxavwnkmtizqjukoh`) is confirmed PRODUCTION (see the correction below this line for how that was established). The user was told directly that this connector's full read/write/admin tool set (`execute_sql`, `apply_migration`, `pause_project`, `restore_project`, branch lifecycle) now reaches confirmed production, and that this row's own policy calls for narrowing to `read_only=true`. Their explicit decision: **do not narrow yet** — write access is still needed (used same-day to apply `backend/migrations/408_rides_refund_amount.sql` to production via `apply_migration`, see ACTION_ITEMS.md C88) — and the connector stays at full read/write/other permissions **until 2026-10-31**. **Revisit trigger: narrow this connector to `read_only=true` (or a project-scoped read-only PAT) on or before 2026-10-31, or get a fresh, dated exception from the user extending it — do not let this become the permanent state by default.** Whoever picks this up next (human or a future session) should treat an undated "still full access" past that line as drift, the same way this table's own history treats an unverified "narrowed" claim as worse than an honest "needs narrowing" one. **CORRECTION 2026-09-08 (human-confirmed): `spinrmobileapp` is PRODUCTION, not pre-production as every earlier entry in this row assumed.** Every prior "pre-production, revisit before production" framing was wrong from the day this connector was reconnected (2026-09-07) — it inferred production status from project *names* (`Spinr-Prod` vs `spinrmobileapp`) observed via a live API call, never cross-checked against which project the deployed backend's `SUPABASE_URL` secret actually points at. **Status of `Spinr-Prod` (`cfrazforbupizntxvvtp`) / `MobileAppStaging` (`mvmyygoinicjdpqprizr`), org `swarnkiran88@gmail.com's Org`: unresolved and not pursued further** — the human confirmed `spinrmobileapp` is the project to work with; C89's "second connector to reach Spinr-Prod" recommendation is stale unless a future need for that other org is identified. Do not read either project's name as a reliable signal of its role — this table's own history is the counter-example. | 2026-09-08 |
| Vercel | Account-level | `vercel mcp --project` (project-scoped MCP config) or `vercel tokens add --project <id>`; fallback: separate team per project | **Resolved** — narrowed via the GitHub-integration repository picker (authorized only `spinrvm`), not the CLI path this row originally suggested. `list_projects` under `srikumarimuddana-lab's projects` (`team_6CIFJ2AZo1oPYfPWLD9fjcyh`) now returns only `spinrvm` (`prj_y9RfnTOPt7BEM3He8iTMOpmlrpoo`) — `desktop-website` no longer appears. Confirmed live, not just assumed from the settings change. Re-confirmed unchanged via `/tooling-check`'s live check on 2026-09-08 — same one team, same one project. | 2026-09-08 |
| GitHub | Account-level | Per-repo GitHub App installation ("Only select repositories") | **Resolved** — human-confirmed the App is set to "Only select repositories," not "All repositories." Could not be verified live from inside a session (no enumerate tool, and testing it would require reading outside the declared repo scope), so this one relies on the human's confirmation rather than a tool check — noted as the exception to this table's usual "verified live" standard. | 2026-09-07 |
| Sentry | Account-level (`.mcp.json` also declares it) | URL path scoping: `https://mcp.sentry.dev/mcp/{org}/{project}` (Sentry's own docs recommend project-level scoping) | **Piloting (pull-only) — scope still unresolved.** Sentry is provisioned via a Fly.io extension (Fly's marketplace can provision a real Sentry org/project and bill it through the Fly subscription); that only affects who pays, not the scoping mechanism, which is still an ordinary Sentry org/project. Could not find the org/project slug: no Sentry dashboard link surfaced in the Fly portal, and the `sentry` MCP connector itself needs an interactive OAuth login no non-interactive session can run, so **no live enumerate check has been possible from any Claude Code session** — this is the one row in this table not verified by calling the connector, unlike Vercel/Supabase/Context7 above. `.mcp.json` stays on the unscoped base URL (`https://mcp.sentry.dev`) by explicit decision rather than guessing a slug. Real residual risk, stated plainly: if the underlying Sentry account has other orgs/projects beyond Spinr's, the unscoped connector can see (and depending on the server build, act on) all of them from any session using it — the same class of over-exposure as the original Vercel/Supabase findings, just unconfirmed either way here. **Pilot guardrail (2026-09-08, see `docs/audit/2026-09-08-agentic-tooling-atlas.md`):** approved for a pull-only pilot — read issues/traces/breadcrumbs to speed up debugging — but Seer (or any Sentry-suggested fix) is never applied or merged without going through the same human review and Change Impact Log gate as any other change; see CLAUDE.md's PR review handling section. Activating the pilot still requires a human to run `claude mcp add --transport http sentry https://mcp.sentry.dev` (or the claude.ai connector settings equivalent) and complete the browser OAuth login themselves — no Claude Code session, interactive or not, can do this on the user's behalf. Revisit trigger: narrow the URL the moment someone can reach the Sentry dashboard directly (check email for a Sentry invite from the Fly provisioning step, or ask whoever owns the Fly account for the login) and read the org/project slugs off its URL. | 2026-09-08 |
| Context7 | Repo-only now (`.mcp.json`) | N/A — stateless, credential-free public docs lookup; no cross-project data possible either way, so this was a tidiness fix, not a safety one | **Resolved** — the account-level `Context7` connector was disabled; only the repo-scoped `context7` tool set is present now. Confirmed live via `ToolSearch` (no more duplicate capital/lowercase pair). | 2026-09-07 |

## Checklist for adding a new connector

Before adding any new MCP server or account-level connector to Spinr work:

1. **Does the vendor support project-level (not just org/account-level) scoping?**
   Check their docs before assuming account-wide is the only option — every
   vendor in the table above turned out to support narrower scoping once
   actually checked.
2. **Use the narrowest scope from day one.** Don't connect broad "to get it
   working" and mean to narrow it later — that's exactly how both real
   findings above happened.
3. **Record it in the table above** — mechanism, scope, verification date.
4. **If it's account-level and carries real credentials** (not a stateless
   public-data lookup like Context7), verify live: call the connector's own
   list/enumerate tool and confirm the result matches what you intended to
   grant, not just what you remember configuring.
5. **Mutating or billing-capable tools are the ones that matter most.**
   A connector that's merely over-visible (can list things it shouldn't) is
   a lesser problem than one that's over-capable (can pause, delete, apply a
   migration, or spend money on a project it shouldn't reach). Prioritize
   fixing scope on connectors with write/billing tools first.
