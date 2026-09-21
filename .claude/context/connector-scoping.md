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
| Vercel | Account-level | `vercel mcp --project` (project-scoped MCP config) or `vercel tokens add --project <id>`; fallback: separate team per project | **DRIFTED BACK OPEN — re-narrowing needed.** The 2026-09-08 "Resolved" entry below was re-checked live on 2026-09-21 during a ride-log review session: `list_projects` under `srikumarimuddana-lab's projects` (`team_6CIFJ2AZo1oPYfPWLD9fjcyh`) now returns **three** projects again — `spinrvm` (`prj_y9RfnTOPt7BEM3He8iTMOpmlrpoo`), `spinr-lms` (`prj_9pTGMClW3X3rSRqLVczFXYrdgBYK`), and `desktop-website` (`prj_Uvo7G8izJu5cFWUmgIspemhSxOp8`) — the exact cross-project exposure this table exists to catch. No destructive/mutating call was made against either non-Spinr project from that session. Cause not diagnosed (the GitHub-integration repository picker that narrowed it in September may have been reset, or the connector may have been reconnected/reconfigured since). **Action needed: a human re-narrows the connector (repository picker or a project-scoped token) and this row gets re-verified live, the same way the original finding was caught** — do not trust an unverified "Resolved" claim, including this file's own prior entry, per this table's stated policy that a stale "narrowed" row is worse than an honest "needs narrowing" one. Prior (now-superseded) history: narrowed via the GitHub-integration repository picker on 2026-09-08, confirmed live then and re-confirmed via `/tooling-check` later that day — see git history of this file for the full prior entry if useful context. | 2026-09-21 (drift found; not yet re-narrowed) |
| GitHub | Account-level | Per-repo GitHub App installation ("Only select repositories") | **Resolved** — human-confirmed the App is set to "Only select repositories," not "All repositories." Could not be verified live from inside a session (no enumerate tool, and testing it would require reading outside the declared repo scope), so this one relies on the human's confirmation rather than a tool check — noted as the exception to this table's usual "verified live" standard. | 2026-09-07 |
| Sentry | Project-level (`.mcp.json` declares `https://mcp.sentry.dev/mcp/spinr-backend/crimson-smoke-7445`) | URL path scoping: `https://mcp.sentry.dev/mcp/{org}/{project}` (Sentry's own docs recommend project-level scoping) | **Scoped and verified live, 2026-09-21.** The user completed the OAuth login (previously the blocker on every prior audit of this row) and confirmed the connector authorized. Called `find_organizations()` → exactly one org, `spinr-backend` (`https://spinr-backend.sentry.io`, region `https://us.sentry.io`), then `find_projects(organizationSlug: "spinr-backend")` → exactly one project, `crimson-smoke-7445` — consistent with `backend/routes/admin/sentry.py`'s TAG MODE (one Sentry project, `surface` tag fan-out across backend/rider-app/driver-app/admin, not one project per surface). `.mcp.json` narrowed from the prior unscoped base URL to this org/project path — the residual "could see/act on other orgs" risk this row previously flagged no longer applies, since the connector's own scoping mechanism now restricts it to this one project regardless of what else may exist on the underlying Sentry account. **Pilot guardrail unchanged (2026-09-08, see `docs/audit/2026-09-08-agentic-tooling-atlas.md`):** Seer (or any Sentry-suggested fix) is still never applied or merged without going through the same human review and Change Impact Log gate as any other change — narrowing the connector's reach doesn't relax that; see CLAUDE.md's PR review handling section and the `spinr-sentry-triage` skill (added 2026-09-21) that formalizes this discovery→root-cause→PR loop. | 2026-09-21 |
| Context7 | Repo-only now (`.mcp.json`) | N/A — stateless, credential-free public docs lookup; no cross-project data possible either way, so this was a tidiness fix, not a safety one | **Resolved** — the account-level `Context7` connector was disabled; only the repo-scoped `context7` tool set is present now. Confirmed live via `ToolSearch` (no more duplicate capital/lowercase pair). | 2026-09-07 |
| Railway | Account-level | Per-project scoping via `projectId` parameter on every Railway MCP tool call (`list-services`, `get-logs`, `get-service-metrics`, etc.) — no narrower unit than project/service exists | **Scoped and verified live, 2026-09-21.** `list-projects()` returns **two** projects on this account: `cooperative-harmony` (`086abfee-d144-4543-bfcd-6a7674340c07`) — confirmed via `list-services` to contain the `spinrvm` service (`8900d1be-705e-4cf3-9ce3-8193595eaad0`, `production` environment) that CLAUDE.md's Deployment section documents as this repo's actual backend — and `beautiful-harmony` (`18dbd899-755f-4fc1-ba51-83bc4cd6157e`), an unrelated project on the same account. **`beautiful-harmony` must never be read from or written to by any Spinr agent/tooling** — any `spinr-*` agent granted Railway tools must be instructed to call only with `projectId: "086abfee-d144-4543-bfcd-6a7674340c07"` (`cooperative-harmony`) and reject/flag any result that doesn't match, since the account-level connector itself has no way to hard-restrict which project a tool call reaches (this is the same class of gap as the Supabase row below — mechanism enforcement, not just scope, matters for a connector without project-level auth). | 2026-09-21 |
| Supabase — accepted-risk note for a second consumer | Account-level (same connector as the row above) | Same connector, same current state | **2026-09-21 addendum, not a re-decision:** `spinr-sentry-triage-investigator` was granted `mcp__Supabase__execute_sql` (restricted by its own prompt instructions to a `SELECT`-only query against `audit_logs`) to wire real audit-trail correlation into its existing "Correlate" step. Per direct user decision (asked explicitly, 2026-09-21): **this proceeds with the existing full read/write/admin connector, restricted only by the investigator's own written instructions — not a real permission boundary** — same accepted-risk posture the row above already documents for this connector generally, now explicitly extended to a second consumer rather than left implicit. This does **not** change the row above's `2026-10-31` narrowing deadline; if anything it raises the cost of letting that deadline slip, since a second agent's correctness now also depends on the connector staying (at minimum) not-worse than today. | 2026-09-21 |

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
