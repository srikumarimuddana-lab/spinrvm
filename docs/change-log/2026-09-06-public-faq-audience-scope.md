# Change Impact & Risk Log — public website FAQ search saw only one audience

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-06 |
| Author | Claude Code (session: spinr-backend-error) |
| Surface(s) | backend (public spinr.ca assistant); no app/dashboard code touched |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/spinr-backend-error-7d8dzt` |
| Related issue or gap ID | Reported from live testing: website widget answered "I don't have that detail on hand" to "what are the requirements for drivers" |

## 1. Issue / gap identified

A visitor asked the spinr.ca chat widget "what are the requirment for drivers" and got
"I don't have that detail on hand… contact Spinr support" — while the help centre has
had a full answer to exactly that question since migration 212.

## 2. Root cause

`search_faqs` scoped an anonymous website turn to a **single** FAQ audience derived from
the `visitor_type` field on the request, defaulting to `rider` when absent or invalid
(`_faq_audience`, `routes/ai.py` `visitor_type=body.visitor_type or "rider"`). A turn
resolved as `rider` queried `audience IN ('both','rider')` — 25 of the 60 active rows —
so all 35 driver-tagged articles, including
`"What are the requirements to drive with Spinr in Saskatchewan?"`, were **unreachable**
no matter how the question was phrased. The model then behaved exactly as `_WEB_CORE`
instructs when a tool returns nothing relevant.

Two things made this hard to see:

- The search did not come back *empty* — `_lexical_results` scores any token overlap, and
  stopwords ("what", "are", "the", "for" are all >2 chars and are not stripped) meant
  unrelated rider articles scored and filled the top 5. It looked like a ranking miss.
- Whether the widget sends `visitor_type` at all is unverifiable from this repo (the
  widget lives in the website project). The backend default makes the failure identical
  either way, which is why the fix is on the backend side rather than a widget bug report.

Confirmed against production (`faqs` table, project `soavhtdhefowwvforzwb`): 35 active
driver rows, 18 rider, 7 both; none area-scoped, so `service_area_ids` filtering was not
involved. The article exists and is active.

## 3. Fix / remediation

`visitor_type` now **ranks** results instead of **gating** which rows exist:

- `_faq_audiences()` (replaces `_faq_audience()`) returns the full public corpus
  `['both','rider','driver']` for the `web` tool audience. In-app rider/driver turns are
  unchanged — they still get `['both', <their own audience>]`.
- `_preferred_audience()` returns the visitor's declared side for web turns only, and
  `None` in-app.
- Both rankers apply a small tie-break toward that side: `+1` in `_lexical_results`
  (less than the 2 points one extra question-word is worth) and `+0.02` cosine in
  `_semantic_results`, applied **after** the `ai_faq_semantic_min_score` floor so the
  preference can never promote a row past the relevance bar.
- `_faq_view()` now includes the row's `audience`, so a driver-side answer shown to
  someone reading as a rider can be framed as "for drivers" rather than as a rule about
  their own trips.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `backend/ai/tools_support.py`.** `_faq_audience`,
`_lexical_results`, `_semantic_results` and `_faq_view` are module-private and have no
callers outside that file (verified by grep across `backend/`). The only cross-module
contract that changes is the JSON payload of one tool result, consumed by the model.

Who else reads the same data:

| Consumer | Effect |
|---|---|
| `routes/faqs.py` (public FAQ list) | none — separate query, untouched |
| in-app rider/driver assistant (`ai/orchestrator.py`) | none — `_faq_audiences` returns the old value for non-web audiences; pinned by `test_in_app_audiences_are_untouched` |
| `ai/response_cache.py` | none — `public_assistant.py` does not use the cache; in-app cache keys are unchanged |
| admin AI console | none |

Regression this change *introduces*: a public turn can now see both sides of a
question that exists in both corpora with different answers (e.g. cancellation). The
tie-break plus the new `audience` field are the mitigation — the declared side ranks
first, and the model can attribute the other side. This is a ranking preference, not a
guarantee; a materially better match from the other side will still win, by design.

Not a money, ride-state, dispatch, auth or insurance path. No background loop touched.

## 5. User-experience effect

- **Website visitors only.** Questions the help centre already answers now get answered
  instead of being deflected to support. No copy was written or changed — every word
  shown comes from existing `faqs` rows.
- **Not visible mid-session to anyone using the apps** — the rider and driver in-app
  assistants are byte-identical in behaviour.
- No notification, no validation rule, no new UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/tools_support.py` | `_faq_audience` → `_faq_audiences` (web returns all three); new `_preferred_audience`; `preferred` tie-break in `_lexical_results`/`_semantic_results`; `audience` added to `_faq_view` | Make driver content reachable from the public site while keeping the declared side ranked first |
| `backend/tests/test_ai_tools_support.py` | New `TestSearchFaqsPublicWeb` (6 cases) | Pin the regression, the tie-break, and that in-app audiences are untouched |

## 7. Before / after

```python
# Before — one audience; driver rows unreachable for a rider-typed visitor
def _faq_audience(user):
    audience = user.get("ai_audience", "rider")
    if audience == WEB:
        requested = user.get("_web_visitor_type")
        return requested if requested in _BOTH else "rider"
    return audience
...
{"is_active": True, "audience": {"$in": ["both", faq_audience]}}
```

```python
# After — web searches the whole public corpus; visitor_type only ranks
def _faq_audiences(user):
    audience = user.get("ai_audience", "rider")
    if audience == WEB:
        return ["both", "rider", "driver"]
    return ["both", audience]
...
{"is_active": True, "audience": {"$in": faq_audiences}}
```

Dry run on the seeded corpus with the visitor's exact words (typo included),
`"what are the requirment for drivers"`:

```
BEFORE (rider-typed visitor, rider+both):     AFTER (same visitor, whole corpus):
  · What payment methods can I use?             · What are the CRC requirements?
  · What is surge pricing…?                     · What are the requirements to drive
  · What safety features does Spinr have?         with Spinr in Saskatchewan?
  · How do I get a receipt for my trip?         · What are the vehicle requirements?
  · Can I schedule a ride for later?            · How are taxes handled for drivers?
  => requirements article present: False        => requirements article present: True
```

## 8. Rollback plan

Pure code revert — `git revert` is a real rollback here. Nothing is written to the
database by this change, no migration, no schema change, no persisted state, so there is
no applied side effect to undo. Reverting restores the single-audience filter on the next
deploy; the previous behaviour needs no data repair.

The paired production settings change (semantic FAQ search, below) rolls back
independently and without a deploy:

```sql
UPDATE settings
   SET ai_faq_semantic_enabled = false,
       ai_embedding_provider   = NULL,
       ai_embedding_model      = NULL
 WHERE id = 'app_settings';
```

Takes effect within the 60 s `settings_loader` TTL. Persisted `faqs.embedding` vectors can
be left in place — they are ignored while the flag is off, and re-used if it is turned
back on.

## 9. Verification performed

- **Ranking dry run against the real patched source** (functions extracted from
  `ai/tools_support.py` with `ast` and executed directly, so the code under test is the
  shipped code, not a copy): the before/after table in §7. The failing query now returns
  the correct article; the in-app path with `preferred=None` reproduces the old ordering.
- **Behavioural checks, all passing**: web audience resolves to all three rows for every
  `visitor_type` value including missing/invalid; in-app rider and driver unchanged; the
  tie-break orders equal matches by declared side without hiding the other; a materially
  better match from the other side still outranks the preference; `_faq_view` shape.
- **Production data confirmed** by direct query — 35 active driver FAQs, the requirements
  article present and active, no area scoping involved.
- `ruff check` and `ruff format` clean on both modified files.

## 9b. CI outcome (added 2026-09-07, after merge)

PR #5056 was merged before its own CI finished. The results, once they landed:

- `Run backend test suite with coverage (shared)` — **passed**.
- `backend-test` — **failed**: `7 failed, 14001 passed, 6 skipped, 1 xfailed`.
  **All 7 failures were the new tests in this change; nothing pre-existing
  broke.** The production diff in `ai/tools_support.py` is therefore sound —
  every existing test of the in-app assistants and of
  `test_ai_public_assistant.py` passed against it.

Root cause of the 7: the new tests drove `search_faqs` through
`execute_tool(..., audience="web")` with a user dict carrying no `id`, the
shape the real anonymous caller uses. `_execute_tool_inner` fails closed on a
missing `user["id"]` **before any handler runs**, so every one of those calls
returned `{"error": "not authorized"}` — hence `assert False`, `KeyError:
'results'`, and (from `next()` over an empty `await_args_list` inside a
coroutine) `RuntimeError: coroutine raised StopIteration`. Fixed by calling the
handler directly, with the reasoning pinned in the test class docstring so it
is not "corrected" back later by handing the test user an id.

**This surfaced a more important finding, already tracked as ACTION_ITEMS.md
AI18** ("Anonymous web assistant's tool path is dead in production"): that same
fail-closed identity check means every anonymous `search_faqs` /
`get_company_info` call on the live website returns "not authorized", and the
model answers from the system prompt alone. So the reported symptom — "what are
the requirements for drivers" answered with "I don't have that detail on hand" —
is caused by AI18, **not** by the audience scoping this change fixes. The
audience-scoping bug documented above is real and independently worth fixing,
but on its own it does not restore the website assistant: it makes driver rows
reachable by a search that is not currently running at all. AI18 needs its own
fix (an explicit anonymous-scope allow-list on the two web `ToolSpec`s, honoured
only for audience `web` — never a blanket relaxation of the identity check) plus
an integration test through the real `execute_tool`.

## 10. What was NOT verified

- **`pytest` was never run locally.** PyPI is unreachable from this environment (403
  through the agent proxy), so backend dependencies could not be installed. This is what
  let the 7 broken tests reach `main` — see §9b. The corrected tests were re-verified by
  loading the real `ai/tools_support.py` through its dual-import fallback (heavy leaf deps
  stubbed) and executing the actual test bodies against it, which does exercise the real
  `search_faqs` code path — but it is still not pytest with the real `conftest.py`
  fixtures and the `anyio` parametrization. CI remains the authority.
- **No live reproduction.** `api-spinr.spinr.ca` is blocked from this environment (403 on
  CONNECT), so the end-to-end turn was not exercised against the deployed backend, and the
  conclusion that the widget's "I am a" pill was not reaching the backend as
  `visitor_type` is inferred from code + data, not observed on the wire.
- **The website widget was not inspected.** It is not in this repo (checked all five repos
  on the account). Whether it sends `visitor_type` at all, and what the source label it
  renders under answers refers to, remain open on the website side.
- **Semantic ranking quality is unmeasured.** Turning `ai_faq_semantic_enabled` on changes
  retrieval for every FAQ question on every surface. There is no eval set for FAQ recall in
  this repo, so the improvement is expected, not measured. The `+0.02` audience boost was
  chosen to be small relative to a 0.30 floor; it has not been tuned against real queries.
- No frontend surface was touched, so visual-regression tooling is not applicable here.
