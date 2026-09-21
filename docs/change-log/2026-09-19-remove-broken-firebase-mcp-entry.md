# Change Impact Log — Remove broken/unused `firebase` MCP entry from `.mcp.json`

**Date:** 2026-09-19

## Issue/gap identified
`.mcp.json`'s `firebase` entry auto-loads for every Claude Code session opened in this repo but has never worked — it requires `SPINR_FIREBASE_MCP_SERVICE_ACCOUNT_PATH`, which is an unset placeholder, so the server crashes on startup with `CONNECTION_CLOSED` every time.

## Root cause
Confirmed by running `firebase-tools mcp` directly with no credentials present: it attempts Application Default Credentials, falls back to a GCP metadata-server lookup (which 403s off-GCP), and throws during `getAuthenticatedUser` before the MCP connection ever opens. Not a subcommand issue — `firebase-tools@15.30.2` accepts both `mcp` and `experimental:mcp` as aliases, confirmed via `--help` output.

## Fix/remediation
Removed the `firebase` block from `.mcp.json` entirely. A decision was made not to wire in a real service-account credential to fix it: the tool's actual MCP surface (enumerated directly from the installed package's `lib/mcp/tools/`) includes several standing-write capabilities — `deploy` (pushes hosting/functions/rules to the live project), `send_message` (sends a real FCM push to a real device/topic), `set_data` (writes to Realtime Database), `update_template` (publishes Remote Config), `update_user` (disables/enables real Auth accounts) — none of which are needed for the task that prompted this investigation (verifying registered Android SHA certificate fingerprints, which the plain Firebase CLI already does read-only, no MCP required).

## Risk & impact on existing functionality
Isolated — grep confirmed `firebase` appears only in this one block of `.mcp.json`; not referenced in `.claude/mcp.example.json` or `.claude/context/connector-scoping.md`. No other server, script, or doc depends on this entry. Removing it only stops an already-nonfunctional connection attempt; no session has ever successfully used it (confirmed `CONNECTION_CLOSED` every attempt).

## User experience effect
None. Internal Claude Code tooling config only; no rider/driver/corporate-admin/internal-admin facing surface touched.

## Files modified
| File | What changed | Why |
|---|---|---|
| `.mcp.json` | Removed the `firebase` MCP server block | Never connected (missing credential placeholder); reintroducing it would require a real Firebase service-account key with a broad write-capable tool surface for no task that currently needs it |

## Before/after snippet
```diff
     "google-maps": { ... },

-    "firebase": {
-      "$comment": "Requires a Firebase service account JSON scoped to a single Spinr project, ...",
-      "command": "npx",
-      "args": ["-y", "firebase-tools", "experimental:mcp"],
-      "env": {
-        "GOOGLE_APPLICATION_CREDENTIALS": "${SPINR_FIREBASE_MCP_SERVICE_ACCOUNT_PATH}"
-      }
-    },
-
     "context7": { ... },
```

## Rollback plan
Pure config deletion, fully reversible with `git revert` of this commit — no migration, no live data, no deployed service affected. If Firebase MCP access is wanted later, re-add the block with a real service account scoped to the read-only `roles/firebase.viewer` IAM role rather than restoring this version verbatim (this version had no role guidance beyond "least privilege," which left the door open to an over-broad grant).

## Verification performed
- `python3 -c "import json; json.load(open('.mcp.json'))"` — confirms the file is still valid JSON after the edit.
- `grep -ri firebase .claude/mcp.example.json .claude/context/connector-scoping.md` — confirmed no other file references this entry.
- Not a `rider-app`/`driver-app`/`admin-dashboard` change, so the production-build requirement in CLAUDE.md's release gates doesn't apply.

## What was NOT verified
- Did not test starting a fresh Claude Code session against this repo to confirm the `firebase` entry no longer appears in `claude mcp list` — the edit is a straightforward JSON key removal with no conditional logic, so this was reasoned about rather than re-run end-to-end.
