# Change Impact & Risk Log — A43 deeper evidence: admin-bypass, not just a missing required check

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (spinr platform) |
| Surface(s) | none (process/governance investigation only — no code or data changed) |
| Domain (Sentry tag) | admin |
| PR / commit link | this doc + `ACTION_ITEMS.md` A43 addendum |
| Related issue or gap ID | A43 (`main`'s branch protection gap that let PR #5048 merge 47 seconds after opening) |

**No code or data was changed by this investigation.** Every fact below comes from
read-only GitHub API calls (`list_repository_collaborators`, `pull_request_read`
methods `get`/`get_reviews`/`get_comments`, `list_branches`) run directly in this
session against the real repository.

## 1. Issue / gap identified

A43 already established that PR #5048 (34 files, rides/payments/auth surface)
merged 47 seconds after opening, before `backend-test` finished and after
`Required PR fields filled` had already reported `failure`. The original write-up
could not distinguish two possible root causes: "these checks simply aren't in
`main`'s required-status-checks list" versus "an admin/bypass merge path was
used" — and no tool in this session could read the actual branch-protection
configuration to settle it.

## 2. Root cause — new evidence narrows this significantly

Four additional facts, none of which needed branch-protection read access:

1. **The PR's author and its merger are the same account, and that account holds
   `admin` role on the repo.** `list_repository_collaborators` returns exactly two
   collaborators: `srikumarimuddana-lab` (`role_name: admin`) and
   `ittalenthireca-sketch` (`role_name: write`). PR #5048's `user` (author) and
   `merged_by` fields are both `srikumarimuddana-lab`.
2. **A review was requested and never given, and the merge did not wait for it.**
   #5048's `requested_reviewers` lists `ittalenthireca-sketch` explicitly.
   `get_reviews` on #5048 returns an empty list — zero reviews were ever
   submitted, by anyone, at any time.
3. **None of the repo's ten custom "CI Guard Rails" gates had reported at merge
   time — not just `backend-test`.** The "CI Guard Rails Summary" bot comment
   (the same comment format this session has seen post automatically on every
   PR checked this session) shows all ten gates green on #5048, but its own
   timestamp is `2026-09-06T03:30:09Z` — **31 minutes after** the
   `2026-09-06T02:58:46Z` merge. So at the moment the merge button was clicked,
   the entire custom guard-rail summary this repo relies on for "failures must
   be resolved... before merging" hadn't posted at all, in either direction.
4. **The PR's own body contains a self-authored safety gate that was left
   unsatisfied.** Its "Tier 7 · High-risk stop condition & unmerge trigger"
   section has two checkboxes — "Rollback command verified" and "On-call
   informed before merge" — both left unchecked (`[ ]`) in the merged PR.

## 3. What this evidence supports (and doesn't)

Taken together (admin-authored, admin-merged, zero reviews despite one
requested, zero guard-rail gates reported, self-declared gate left unchecked),
this is **much more consistent with a GitHub repository-admin bypass** — the
standard behavior where required status checks and required reviews do not
apply to a repo admin's own merge unless the branch protection rule's "Do not
allow bypassing the above settings" (a.k.a. "Include administrators") option is
turned on — **than with "the checks just aren't on the required list."** If only
the check list were the gap, a required-review rule (if one exists) should still
have blocked a zero-review merge for a non-admin acting on the same branch
protection; the fact that the one account with admin rights on this two-person
repo could do it anyway is the specific signature of an admin-exempt
configuration.

**This is still not a direct read of the Settings → Branches page or the repo's
audit log** — no tool in this session's GitHub MCP toolset exposes either,
confirmed again this pass (checked `list_branches` for a `main`-specific
`protected` flag; with ~700+ stale `claude/*` branches in this repo, finding
`main`'s row would need many more paginated calls for a bare boolean that, even
if found, would not reveal *which* checks are required or whether admins are
exempt from them — not pursued further as not worth the token cost for that
specific, non-decisive fact). The action item is unchanged in kind (a human with
repo-admin access needs to open Settings → Branches), but is now much more
specific in what to look for.

## 4. Action (updated, narrower ask)

A human with repo-admin access should open **Settings → Branches → main → Edit**
and check specifically whether **"Do not allow bypassing the above settings"**
(GitHub's current label; formerly "Include administrators") is turned on. If it
is off, turning it on is the single change that would have prevented this
specific incident's mechanism — it requires no change to which checks are
listed as required, only closes the admin-exemption path the evidence above
points to.

## 5. Risk & impact on existing functionality

Not applicable — this is a read-only investigation; no code, config, or data was
changed.

## 6. User-experience effect

None — internal process/governance finding only.

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-09-10-a43-admin-bypass-evidence.md` | New — this file | Record the deeper evidence |
| `ACTION_ITEMS.md` | A43 addendum | Narrow the open action item with the new evidence |

## 8. Rollback plan

Not applicable — no code or data changed.

## 9. Verification performed

- [x] Every claim above is sourced to a specific, quoted GitHub API response
  captured in this session (`list_repository_collaborators`, `pull_request_read`
  get/get_reviews/get_comments) — not inferred from the original A43 write-up's
  own narrative.
- [x] Cross-checked the "CI Guard Rails Summary" comment's own timestamp against
  the PR's `merged_at` timestamp directly, rather than trusting the comment's
  content alone (a green summary posted after the fact says nothing about gate
  status at merge time).
- [ ] Did not attempt to read `main`'s branch-protection configuration or the
  repository audit log — confirmed (again, more thoroughly) that no tool in
  this session's toolset exposes either.

## What was NOT verified

- Whether "Do not allow bypassing the above settings" is actually off — this is
  the one fact that would fully close A43, and it requires a human with repo
  Settings access; not verifiable from this session under any approach tried.
- Whether an `AskUserQuestion`-equivalent escalation happened out-of-band for
  this specific merge (A43's original action item 1) — still unconfirmed,
  unrelated to the evidence gathered here.
- Whether this same admin-bypass pattern was used on any other high-risk PR
  besides #5048 — not checked; scoped to this one incident.

## 10. Sign-off

- [x] Blast radius is stated, not assumed (zero — read-only investigation)
- [x] No silent behavior change — nothing changed
- [ ] Rollback plan — not applicable, no change made
