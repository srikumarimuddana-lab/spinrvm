# Change Impact & Risk Log — F05 (cache-flag loss) + F07 (MCP staff principals)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F05 + F07, the AI security assessment on PR #5138, remediation order 3 |

Two findings in one entry because the assessment groups them as the same gate:
*"fix cache metadata and MCP principal policy **before enabling those
features**"*. Both features currently default off, so neither fix is a
behaviour change for a live rider today — but both must land before the flag
flips.

## 1. Issue / gap identified

**F05** — `search_faqs` marks a location-dependent answer with `_no_cache` so
the cross-user response cache never replays one service area's policy answer to
a rider in another area. `_cap_result` rebuilt any result over 4,000 characters
as `{_truncated, preview, **_GUARDRAIL_KEYS}` and `_no_cache` was not on that
list, so the flag vanished. The orchestrator then saw an ordinary FAQ-only turn
and could cache it under `(audience, normalized question)` with no service-area
component.

**F07** — `/mcp` rejected `user.get("role") == "admin"`: one of the six roles
the verified staff token pipeline returns. `super_admin`, `operations`,
`support`, `finance` and `custom` all reached the downstream MCP app. The
assessment's offline middleware probes confirmed all five.

## 2. Root cause

**F05** — the flag was an ordinary result key competing with model-facing
content, so it was subject to a truncation path that only knows about
model-facing content. The bug was invisible below the cap and appeared only
above it — which is exactly the shape a long area-scoped FAQ result has.

**F07** — a private, hard-coded copy of the role list that had drifted from the
one `_verify_admin_payload` actually uses, and gating on the untrusted `role`
string rather than on `_admin_verified`, the marker the codebase's own
`get_admin_user` already documents as the authoritative signal.

## 3. Fix / remediation

**F05** — `_no_cache` moved into a new `_META_KEYS` tuple handled exactly like
`_client_action`: popped before scrub/serialize, re-attached after truncation.
It is orchestrator-only metadata, never model-facing, so it now neither counts
against the 4,000-char budget nor is destroyed by the rebuild.

**F07** — new `_is_customer_principal()` gates on `_admin_verified` first
(authoritative), with `ADMIN_STAFF_ROLES` as a fail-closed backstop. That role
set is hoisted from a local inside `_verify_admin_payload` to a module constant
in `backend/dependencies/__init__.py`, so the admitting pipeline and the
excluding surface now read one list and cannot drift again.

## 4. Risk & impact on existing functionality

**Blast radius: backend AI surface only. No ride, dispatch, money, wallet,
insurance-period, or state-machine path is touched.**

Greps performed:

- `grep -rn "_no_cache" backend/` → producer `ai/tools_support.py:379`;
  consumers `ai/orchestrator.py:445` (pops it, sets `cache_disqualified`) and
  `ai/public_assistant.py:163` (`_strip_meta`, strips it from the model
  message). Both keep working: the key is still present on the result dict at
  the same point in the flow, only now it survives truncation as well.
- `grep -rn "_cap_result" backend/` → two consumers, `execute_tool` (chat) and
  `/mcp`'s `_call_tool`. Both benefit; neither reads `_META_KEYS` directly.
- `grep -rn "_admin_roles\|ADMIN_STAFF_ROLES" backend/` → the set had exactly
  one reader before the hoist (`_verify_admin_payload`); now two.
  `_verify_admin_payload`'s behaviour is unchanged — same six strings, same
  membership test, `frozenset` vs `set` makes no difference to `in`.

Regressions considered:

- **Could the F05 fix make every turn uncacheable** (silently disabling the FAQ
  cache instead of correcting its isolation)? No — `_META_KEYS` only carries a
  key that is *already present*; an unscoped FAQ result never gains it. Pinned
  by `test_unscoped_result_is_not_marked_no_cache`.
- **Could the meta re-attach break a tool returning a non-dict?** It did — the
  first version called `result.update(meta)` unguarded and raised
  `AttributeError` on a list result, which would have turned a working tool
  into a failed one. Caught by the offline probe before commit, guarded with
  `if meta:`, and pinned by `test_non_dict_result_survives_meta_handling`.
- **Could the F07 fix lock out legitimate MCP clients?** Only a client
  authenticating with a staff token, which is the finding. Rider and driver
  tokens are explicitly pinned as still accepted.

## 5. User-experience effect

- **Nobody, today.** `ai_mcp_enabled` and the FAQ response cache both default
  off (`backend/schemas.py`), so neither path is live on defaults. Deployed
  values were not queried from production — see §10.
- **If the FAQ cache is on:** riders stop receiving another service area's
  answer to a long area-scoped FAQ question. That is a correctness improvement,
  not a visible UX change, and it is not visible mid-session.
- **If MCP is on:** a staff-token MCP client starts getting 403. Intended.
- No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/tools.py` | Added `_META_KEYS`; `_cap_result` pops/re-attaches it around truncation, guarded for non-dict results. | F05. Cache metadata belongs outside the model-facing truncation path, not budgeted as content. |
| `backend/ai/mcp_server.py` | New `_is_customer_principal()`; middleware gate calls it instead of comparing `role` to `"admin"`. | F07. |
| `backend/dependencies/__init__.py` | Hoisted `_admin_roles` to module-level `ADMIN_STAFF_ROLES`. | So the admitting pipeline and the excluding surface share one list. |
| `backend/tests/test_ai_tools_core.py` | +4 tests: flag survives truncation, survives short results, absent when unscoped, non-dict safety. | F05 regression cover. |
| `backend/tests/test_ai_mcp.py` | +4 tests (2 parametrised over all 6 roles), +1 pinning the shared constant. | F07 regression cover. |

## 7. Before / after

```python
# Before — ai/tools.py::_cap_result
serialized = json.dumps(result, default=str)
if len(serialized) > TOOL_RESULT_MAX_CHARS:
    preserved = {k: result[k] for k in _GUARDRAIL_KEYS if ... k in result}
    result = {"_truncated": True, "preview": serialized[:TOOL_RESULT_MAX_CHARS], **preserved}
    # _no_cache is gone here — the turn is now cacheable
```

```python
# After
meta = {k: result[k] for k in _META_KEYS if k in result}   # popped before serialize
...
if len(serialized) > TOOL_RESULT_MAX_CHARS:
    result = {"_truncated": True, "preview": ..., **preserved}
if meta:
    result.update(meta)                                     # re-attached after
```

```python
# Before — ai/mcp_server.py
if user.get("role") == "admin":      # 1 of 6 verified staff roles
    await _send_json(send, 403, ...)
```

```python
# After
if not _is_customer_principal(user):  # _admin_verified, + ADMIN_STAFF_ROLES backstop
    await _send_json(send, 403, ...)
```

## 8. Rollback plan

Both features are already behind existing kill switches that predate this
change, and both default off:

- **F07 / MCP** — set `ai_mcp_enabled` false in `app_settings` (admin
  dashboard, no redeploy). The middleware 503s before any auth work.
  `ai_assistant_enabled` false is the wider lever and also stops it.
- **F05 / FAQ response cache** — turn the FAQ response cache off in
  `app_settings`, which removes the cache path entirely rather than relying on
  the flag being correct.

No migration, no schema change, no data written by either fix, so `git revert`
is a sufficient code-level rollback with nothing to unwind.

**One cleanup is required at deploy, not rollback:** cache entries stored
*before* this fix may already be poisoned (a scoped answer cached globally).
The assessment calls for clearing them. They expire on their own 3,600s TTL, so
a deploy during a low-traffic window self-heals within an hour; flushing the
`ai:faq:*` keys makes it immediate. Not done here — this branch changes code
only and touches no Redis data.

## 9. Verification performed

- [x] **Blast-radius greps performed** — `_no_cache`, `_cap_result`,
      `_admin_roles`/`ADMIN_STAFF_ROLES` across `backend/`; consumers listed in §4.
- [x] **Offline probes** against the real function source (extracted and exec'd,
      since the modules can't be imported without the unavailable deps):
      - F05: 10 cases — long scoped FAQ truncated *and* flag preserved, short
        scoped, unscoped-stays-cacheable, flag not model-facing, `_client_action`
        still survives, guardrail keys still survive, non-dict result.
        **All 10 pass.** This probe *found the non-dict regression* described in §4.
      - F07: 18 cases — all six verified staff roles rejected, marker-only
        rejected, all six role-strings-without-marker rejected, rider/driver/
        no-role-claim admitted, no-id and empty rejected. **All 18 pass.**
      Scripts: `scratchpad/probe_f05.py`, `scratchpad/probe_f07.py` (throwaway
      harnesses, not committed; the same cases are committed as pytest).
- [x] `ruff check` + `ruff format --check` clean on all changed files.
- [x] Reviewed against `CLAUDE.md`: dual-import pattern preserved in
      `ai/mcp_server.py`; no PII in any new line; no error swallowed.
- [x] Feature-flag question answered: both surfaces already have kill switches
      and both default off, so no new flag was added — adding one would be the
      speculative configurability `CLAUDE.md` warns against.

## 10. What was NOT verified

- **The pytest suite was not run.** PyPI is unreachable from this environment
  (gateway 403 on `pypi.org`), so backend dependencies cannot be installed. The
  9 new tests are **unrun** and must go green in CI before merge. The offline
  probes exercise the real function source but not `execute_tool`, the
  orchestrator's cache decision, or the ASGI middleware end-to-end.
- **The end-to-end cache-store decision was not exercised.** The probe proves
  `_no_cache` survives `_cap_result`; it does not prove
  `orchestrator.py`'s `cache_disqualified` → `response_cache.store_cached`
  chain then behaves correctly. The assessment's own integration probe covered
  that against the *unfixed* code; re-running it against this fix is the
  acceptance evidence still owed.
- **Production flag values were not queried.** "Both default off" is read from
  `backend/schemas.py` defaults, not from the live `app_settings` row. If either
  is actually on in production, the §5 effects are live rather than latent.
- **Existing poisoned cache entries were not cleared** — see §8.
- **`ADMIN_STAFF_ROLES` completeness was not independently re-derived.** It
  matches the set `_verify_admin_payload` used before the hoist, character for
  character; whether that set is itself the complete list of roles the staff
  pipeline can mint was taken from the assessment and the existing code, not
  re-audited against `admin/staff.py`'s `ROLE_PRESETS`.
- **MCP credential scoping was not addressed.** The assessment also recommends
  dedicated, narrow, revocable MCP credentials instead of sharing general app
  tokens. That is a product change, deliberately out of scope here, and F07
  remains only partly answered until it is done.
