# Change Impact & Risk Log — Dead RBAC Module Removal + Money Gate Made Blocking

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard, CI |
| Domain (Sentry tag) | admin, payments |
| Severity | Permission-model correctness + a real money-convention violation on a rider-facing path |
| Found by | Follow-up on the two open decisions from the heatmap pre-deploy review; the money finding was found by **running semgrep**, which had not been possible before |

Two items, committed separately.

---

## Item 1 — the `heatmap` permission granted nothing

### 1. Issue / gap identified

`heatmap` was offered as a grantable admin module in three places
(`AVAILABLE_MODULES`, `ALL_MODULES`, and the staff page's checkbox list) and
was part of the shipped `operations` role preset. **No backend route checked
it.** Its only effect was showing or hiding the sidebar link.

So granting it implied an access grant that did not exist, and denying it
implied a restriction that was never enforced. An admin holding `heatmap`
without `rides` saw the link and then a page whose every request 403'd.

### 2. Root cause

The Heat Map page's data comes from four routers, each already gated by its
own module:

| Endpoint | Module enforcing it |
|---|---|
| `/rides/heatmap-data` (the map itself) | `rides` |
| `/surge/status` (live demand cards) | `service_areas` |
| `/analytics/demand-forecast` | `dashboard` |
| `/settings/heatmap` | `settings` |

`heatmap` was added as a *navigation* label and then read as a permission.
Nothing tied the grantable list to the enforced set, so the mismatch was
invisible.

### 3. Fix / remediation

Removed `heatmap` from `AVAILABLE_MODULES`, `ALL_MODULES`, the two inline
module literals in `auth.py`, the `operations` preset, and the staff page.
The sidebar entry now gates on **`rides`** — the module behind the map itself,
so an admin who cannot load the map no longer sees a link to it.

Same shape as the `bulk_operations` removal already documented in
`routes/admin/__init__.py`; that comment is referenced from the new one.

**Two further drifts surfaced while doing this, both pre-existing:**

- **`bulk_operations`** was still a checkbox on the staff page after being
  deliberately removed backend-side. The Data Transfer surface is
  `require_super_admin` now, so the checkbox implied a full-fidelity,
  unredacted PII export could be delegated. Removed.
- **`compliance`** drifts the *other* way: `require_module("compliance")` is a
  real, enforced gate, but the string is in no grantable list — so that router
  is super_admin-only **by omission**, exactly the accident the
  `bulk_operations` comment warns about. Removed from the picker because it
  cannot currently be granted. **Making it grantable is a product decision
  about who may reach tax and compliance reporting, and is deliberately not
  taken here.**

New `backend/tests/test_admin_module_list_parity.py` (8 tests) compares all
three lists against each other and against the set of module strings actually
passed to `require_module()` anywhere under `backend/routes/`.

### 4. Risk & impact on existing functionality

**Blast radius — every consumer of the module string, grep-verified:**

| Site | Effect |
|---|---|
| `staff.py` `AVAILABLE_MODULES` | removed; create/update filter against it |
| `staff.py` `ROLE_PRESETS["operations"]` | removed |
| `auth.py` `ALL_MODULES` | removed (super-admin JWT claim) |
| `auth.py` admin-001 refresh literal | removed |
| `sidebar.tsx` | repointed `heatmap` → `rides` |
| staff page `ALL_MODULES` + preset | removed |
| 4 backend test fixtures | updated |
| 1 admin test fixture | updated |
| **Any `require_module("heatmap")`** | **none exist — that is the whole finding** |

**Existing DB rows are unaffected and self-cleaning.** `admin_staff` rows may
still carry `"heatmap"` in their `modules` array. Nothing reads it, and both
the create and update handlers filter submitted modules against
`AVAILABLE_MODULES`, so the next edit to a staff member drops it. **No
migration is needed** — and deliberately none was written, since a migration
mutating live permission rows carries far more risk than an inert string.

**What could regress:**

- **The Heat Map link disappearing for the `operations` role** was the real
  hazard here, and it is the reason the sidebar change is part of the same
  commit rather than a follow-up. `operations` holds `rides`, so the link
  stays. Verified by the new parity test, which fails if any sidebar entry
  gates on a non-grantable module.
- **A custom-role admin granted `heatmap` but not `rides`** loses the link.
  They were previously seeing a page whose map request 403'd, so this is the
  fix, not the regression — but it is a visible change for that (probably
  empty) set of accounts.
- **Existing JWTs still carry `heatmap`** until they expire. Inert.

**Explicitly unaffected:** every actual authorization decision. No
`require_module` call changed, no route's gate changed, and no endpoint became
more or less reachable. This changes what the permission *list* claims, not
what the backend *enforces*.

### 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate-admin surface.
- **Mid-session visible:** an admin with the staff editor open sees three fewer
  checkboxes after reload. No permission any account currently relies on is
  withdrawn.
- **Copy:** none changed.

---

## Item 2 — the money rule is now merge-blocking, and it found real bugs first

### 1. Issue / gap identified

`spinr-no-float-in-money` (SR-03) has been advisory since it was written. The
previous session corrected its allowlist (two of six paths did not exist, so it
silently covered four files) but could not promote it to blocking because
semgrep was not installed in that environment.

### 2. Root cause of what the run found

Semgrep was installed and run. **It reported three findings in
`services/fare_service.py` — a file the previous pass had declared clean after
a careful file-by-file read.** That is the substantive lesson here: a code
review is not a substitute for running the rule.

Assessed individually:

- **Two were a real convention violation** (`fare_service.py:301-303`). The
  area-fee receipt line item was built with raw `float()`, while the *same
  fee's* contribution to the total, twelve lines down, uses `_d()` — and every
  other consumer (`receipt_pdf.py`, `email_receipt.py`) uses `_d()` too. So one
  rider-facing money value bypassed the Decimal path the project mandates.
- **One was a display label** (`fare_service.py:294`): `float()` inside an
  f-string to render the surge multiplier. No arithmetic.

### 3. Fix / remediation

**The two real ones** now route through Decimal, matching the file's own
convention:

```python
fee_amount = _d(fee.get("calculated_value", 0))
if fee_amount > 0:
    lines.append({"label": ..., "amount": _f(fee_amount), "type": "fee"})
```

**Chosen deliberately to be output-identical.** `_d` is `Decimal(str(v))` and
`_f` is `float(v)`, so every emitted amount is byte-for-byte what it was. An
earlier draft added `_round()`, which *would* have changed displayed amounts
(a `2.675` fee rendering as `2.68`) and could have made line items stop summing
to the displayed total. On a live rider-facing money path, fixing the type
discipline without touching a single number is the right trade — and it still
makes the rule clean for real rather than by allowlist.

**The label** keeps its `float()` with an inline `# nosemgrep:
spinr-no-float-in-money` and the reason. Switching it to the Decimal's own repr
would render `Decimal("1.50")` as `"1.50"` where float renders `"1.5"` —
silently changing rider-visible receipt copy to satisfy a lint rule.

**The gate** is now a separate blocking step, `Money-safety gate (SR-03,
blocking)`, in `security-gates.yml`. It re-runs the ruleset, filters the JSON
to SR-03, annotates each finding with the fix guidance, and exits 1. It keeps
the same exit-code discipline as the advisory scan — a gate that *failed to
run* must never be mistaken for a gate that passed.

The broad scan stays advisory. The other Spinr rules have 4 known untriaged
findings (2 background-loop idempotency, 1 ride-state guard, 1 Stripe
idempotency) — **counts from the real run, not an estimate** — and each needs
triage before it can block.

### 4. Risk & impact on existing functionality

**Blast radius of the code change:** `build_fare_breakdown_lines` has exactly
one production caller — `routes/rides/estimates.py:585`, the rider-facing fare
estimate (`fare_breakdown` in the response). Grep-verified across all surfaces.
The change is confined to the area-fee line item; every other line
(ride, surge, booking, airport, tax) is untouched.

**What could regress:**

- **Nothing in the emitted numbers**, by construction — see above. The risk
  that remains is that this reasoning is wrong for some exotic input, which is
  why `test_fare_service.py` and `test_surge_line_item.py` were run (40 tests,
  all pass) rather than relying on the argument alone.
- **The blocking gate can now fail a PR.** That is the point, but it is a new
  way for CI to go red. It fails only on SR-03, only on files in the rule's
  allowlist, and the annotation tells the author exactly what to do including
  the `nosemgrep` escape hatch for genuine display-only cases.
- **A semgrep infrastructure failure now blocks merges** where it previously
  warned. Deliberate and explicit: the step distinguishes exit 1 (findings)
  from any other exit (broken gate) and reports the latter as an error saying
  "This is a broken gate, not a clean one."

### 5. User-experience effect

- **Rider-facing:** none. The estimate's fare breakdown renders identical
  values before and after.
- **Developer-facing:** a PR introducing float arithmetic in an allowlisted
  money module now fails CI instead of filing a warning nobody reads.

---

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/staff.py` | `heatmap` removed from `AVAILABLE_MODULES` + `operations` preset; note explaining why and that DB rows are self-cleaning | Item 1 |
| `backend/routes/admin/auth.py` | `heatmap` removed from `ALL_MODULES` and the admin-001 refresh literal; noted a pre-existing drift in that literal rather than widening it as a side effect | Item 1 |
| `admin-dashboard/src/components/sidebar.tsx` | Heat Map entry gates on `rides` | Item 1 |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | `heatmap`, `bulk_operations`, `compliance` removed from the picker; preset updated | Item 1 |
| `backend/tests/test_admin_module_list_parity.py` | New: 8 tests tying all three lists to the enforced gate set | Item 1 |
| 4 backend + 1 admin test fixture | `heatmap` removed from module lists | Item 1 |
| `backend/services/fare_service.py` | Area-fee line item routed through Decimal; surge label given a justified `nosemgrep` | Item 2 |
| `.semgrep/spinr-rules.yml` | Allowlist comment corrected — the hand-verification it claimed was wrong | Item 2 |
| `.github/workflows/security-gates.yml` | New blocking SR-03 step; advisory-scan comment replaced with real finding counts | Item 2 |

## 7. Before / after

**Item 1 — the sidebar, which is where the near-miss was:**

```tsx
// Before — gated on a module that enforced nothing.
{ href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "heatmap" },
```

```tsx
// After — gated on the module that actually protects the page's data.
// Removing "heatmap" WITHOUT this line would have hidden the page from the
// operations role entirely.
{ href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "rides" },
```

**Item 2 — the money path:**

```python
# Before — the only line item built with raw float(), while the same fee's
# contribution to the total used _d() twelve lines below.
val = fee.get("calculated_value", 0)
if float(val) > 0:
    lines.append({"label": fee.get("name", "Fee"), "amount": float(val), "type": "fee"})
```

```python
# After — Decimal in, float only at the response boundary. Same emitted value.
fee_amount = _d(fee.get("calculated_value", 0))
if fee_amount > 0:
    lines.append({"label": fee.get("name", "Fee"), "amount": _f(fee_amount), "type": "fee"})
```

## 8. Rollback plan

Two independent commits.

- **Item 1** — `git revert`. Nothing is persisted and no migration ran, so
  there is no data to unwind. Existing `admin_staff` rows were never modified;
  reverting restores the checkbox and the three module lists as they were. If
  only the *sidebar* change needs undoing, that is a one-line revert — but note
  the two must move together in both directions, or the Heat Map link vanishes
  for the operations role.
- **Item 2** — the gate can be disabled without a code revert by deleting the
  "Money-safety gate" step, and the `fare_service.py` change reverts cleanly
  with no data implication (it emits identical values, so no receipt already
  issued differs either way).

## 9. Verification performed

- [x] **Semgrep actually run** (1.173.0, local): SR-03 went 3 findings → **0**.
      This is a tool result, not a code read — the distinction that made this
      whole item necessary.
- [x] **Backend: 182 tests** across the parity, RBAC, JWT-modules,
      business-logic, staff-coverage, fare-service and surge-line-item suites —
      all pass. Full suite run separately.
- [x] The new parity tests were **written before the sidebar fix and observed
      failing**, which is how the "Heat Map disappears for operations" hazard
      was caught rather than shipped.
- [x] The parity test's own first draft produced a **false positive**
      (`support_tickets` reported as ungated because the scan read only the
      mount file, missing route-level `Depends(require_module(...))`). Fixed by
      scanning all of `backend/routes/`. Recorded because a test that
      confidently reports a wrong finding is worse than no test.
- [x] **Admin dashboard: 232 tests, 25 files, 0 failures.**
- [x] `ruff check` + `ruff format --check` clean on every touched Python file.
- [x] `tsc --noEmit` clean; **`npm run build` — a real production build.**
- [x] Workflow YAML parsed and step order verified.

## 10. What was NOT verified

- **No real admin session was logged in.** The RBAC change is verified by unit
  tests and by reading the enforcement sites; a module-limited admin account
  was not actually used to load the Heat Map page. The parity test asserts the
  *relationship* between the lists, not the rendered outcome for a real user.
- **The blocking gate has not run in CI yet.** It is verified by running
  semgrep locally with the same ruleset and by parsing the workflow, but the
  step itself has not executed on GitHub Actions — in particular the
  `semgrep/semgrep:latest` container's `python3` availability for the filter
  step is assumed from the adjacent validation step that already uses it.
- **`_d`/`_f` being output-identical is reasoned plus test-covered, not
  exhaustively proven.** The existing fare tests pass and the helpers are
  `Decimal(str(v))` / `float(v)`, but no property test was written over
  arbitrary `calculated_value` inputs.
- **The three removed checkboxes were not checked against production data.**
  Whether any live `admin_staff` row currently holds `heatmap`,
  `bulk_operations` or `compliance` is unknown from here. It does not change
  correctness (all three are inert or unreachable), but if an operator believes
  someone has compliance access today, that belief should be checked against
  the row rather than the UI.
- **`pricing`, `surge` and `ai_console` are left as known debt**, pinned in the
  parity test's allowlists rather than fixed. `pricing` and `surge` are
  grantable but gate no route (same class as `heatmap`); `ai_console` gates a
  sidebar link while `ai_console_router` is mounted **with no dependency at
  all**, so any admin can call those endpoints while only super_admin sees the
  link. Reported, not decided — see §Follow-ups.

## 11. Follow-ups this raised

| Item | Why it wasn't done here |
|---|---|
| `compliance` — make grantable, or switch the mount to `require_super_admin` | Product decision about who may reach tax/compliance reporting |
| `ai_console_router` is mounted with **no gate** | Any admin can call the AI console API today; only super_admin sees the link. Needs a deliberate decision, not a guess |
| `pricing` / `surge` grant nothing | Same class as `heatmap`, but removing them touches the `operations` preset and two more sidebar entries — a separate, scoped change |
| Promote the other 3 Spinr semgrep rules | 4 findings need triage (real bug vs over-broad rule) first |
| `auth.py`'s admin-001 refresh literal omits `audit`/`support_tickets` | Inert (super_admin bypasses), and widening it as a side effect of this change would be a silent permission change |
