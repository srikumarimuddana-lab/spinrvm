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
| Supabase | Account-level | MCP URL params `?project_ref=<id>&read_only=true`, or a project-scoped read-only PAT | **Accepted as-is, revisit before production.** Reconnected same-day under a different account, now authenticated into org `Spinr_MobileApp` (`hjdwxavwnkmtizqjukoh`), scoped by account membership to its one project, `spinrmobileapp` (`soavhtdhefowwvforzwb`) — no other project exists in this org, so there's no cross-project reach today even though `project_ref`/`read_only` weren't applied. Full read/write/admin tool surface (`execute_sql`, `apply_migration`, `pause_project`, `restore_project`, branch lifecycle) kept by explicit decision — read+write is the actual requirement while this project is pre-production. Re-apply `read_only=true` (or drop to a data-only scoped token) before `spinrmobileapp` goes to production — don't let "it's fine for now" become the permanent state. **Side effect of the account switch:** `Spinr-Prod` (`cfrazforbupizntxvvtp`) and `MobileAppStaging` (`mvmyygoinicjdpqprizr`) under the original `swarnkiran88@gmail.com's Org` are no longer reachable from this connector at all — re-adding that (inviting this account into that org too, or a second connector) is a separate, still-open decision, not yet made. | 2026-09-07 |
| Vercel | Account-level | `vercel mcp --project` (project-scoped MCP config) or `vercel tokens add --project <id>`; fallback: separate team per project | **Needs narrowing** — team `srikumarimuddana-lab's projects` (`team_6CIFJ2AZo1oPYfPWLD9fjcyh`) contains `spinrvm` (`prj_y9RfnTOPt7BEM3He8iTMOpmlrpoo`) **and** unrelated `desktop-website` | 2026-09-07 |
| Sentry | Account-level (`.mcp.json` also declares it) | URL path scoping: `https://mcp.sentry.dev/mcp/{org}/{project}` (Sentry's own docs recommend project-level scoping) | **Needs narrowing** — `.mcp.json` points at the unscoped base URL (`https://mcp.sentry.dev`); not authenticated this session, so org/project reach unverified live | 2026-09-07 |
| GitHub | Account-level | Per-repo GitHub App installation ("Only select repositories") | **Believed correct, needs a human confirming the toggle** — session identity is a dedicated bot account (`ittalenthireca-sketch`), declared scope this session was `srikumarimuddana-lab/spinrvm` only. No enumerate tool exists to verify further from inside a session, and doing so would violate the repo-scope restriction anyway — this row can only be closed by a human checking GitHub → Settings → Applications | 2026-09-07 |
| Context7 | Both (repo `.mcp.json` + account-level) | N/A — stateless, credential-free public docs lookup; no cross-project data possible either way | **Duplicate, zero risk** — pick one for tidiness, not safety | 2026-09-07 |

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
