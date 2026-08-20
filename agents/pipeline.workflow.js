export const meta = {
  name: 'spinr-sdlc-pipeline',
  description: 'Run one piece of Spinr work through ideation -> operations (see agents/PIPELINE_DESIGN.md)',
  whenToUse: 'When asked to "kick off a surface" end to end -- from a one-line idea through requirements, design, architecture, development, QA, security, change review, release, and an operations watch note. Reads agents/roles/*.md and agents/GUARDRAILS.md for role context and hard/soft stops; reads CLAUDE.md for the live project rules.',
  phases: [
    { title: 'Ideation & Requirements' },
    { title: 'Design & Architecture' },
    { title: 'Development' },
    { title: 'QA' },
    { title: 'Security' },
    { title: 'Change Review' },
    { title: 'Release' },
  ],
}

// args (all required): {
//   slug: string        -- short kebab-case id, used as the run folder name (agents/runs/<slug>/)
//   title: string        -- one-line title for the piece of work
//   description: string  -- the actual problem statement / idea
//   scope: 'chore' | 'small-feature' | 'live-surface'  -- see PIPELINE_DESIGN.md's scaling rule
// }
const { slug, title, description, scope } = args
const runDir = `agents/runs/${slug}`

function roleContext(...roles) {
  const roleFiles = roles.map((r) => `agents/roles/${r}.md`).join(', ')
  return `Before doing anything, read: agents/GUARDRAILS.md, ${roleFiles}, and the repo root CLAUDE.md (for the live project rules -- it is the system of record, agents/GUARDRAILS.md is only a summary). Follow every rule in those files exactly; do not guess at a convention you haven't confirmed by reading it.`
}

const PROGRESS_INSTRUCTION = (heading) =>
  `Append a new '## ${heading}' section to ${runDir}/progress-report.md (create the file with a top-level '# Run: ${title}' heading and a one-line description first, if it does not exist yet). Write in plain, specific language -- what you did, what you decided, and anything you could NOT verify (per GUARDRAILS.md's "things the pipeline will not pretend it verified" rule) -- not a generic status line.`

const DECISIONS_INSTRUCTION =
  `For any non-obvious call you made, append an entry to ${runDir}/decisions.md in the exact format PIPELINE_DESIGN.md specifies (Decision / Stage / What was decided / Why / Alternatives considered / Reversible?). Skip this file entirely if nothing you did this stage was a real judgment call.`

const CHALLENGES_INSTRUCTION =
  `If anything about this stage did not go smoothly -- a surprising blast-radius result, something you were not confident about, a check that failed -- append it to ${runDir}/challenges-and-issues.md. A non-empty file here is a good sign, not a failure; don't force an entry if the stage was genuinely clean.`

const QA_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['PASS', 'FAIL'] },
    reason: { type: 'string' },
  },
  required: ['verdict', 'reason'],
}

const SECURITY_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['PASS', 'FAIL'] },
    reason: { type: 'string' },
  },
  required: ['verdict', 'reason'],
}

const CHANGE_REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['REQUIRES_HUMAN', 'CLEAR_FOR_RELEASE'] },
    reasoning: { type: 'string' },
    changeImpactLogRequired: { type: 'boolean' },
  },
  required: ['verdict', 'reasoning', 'changeImpactLogRequired'],
}

log(`Starting pipeline run "${slug}" (scope: ${scope}) -- ${title}`)

// ---- Stage 1-2: Ideation & Requirements (Leadership + Product/Design/Engineering) ----
phase('Ideation & Requirements')
const ideation = await agent(
  `You are running Stage 1 (Ideation) + Stage 2 (Requirements) of the Spinr SDLC pipeline.

Piece of work:
  Title: ${title}
  Description: ${description}
  Scope class: ${scope}

${roleContext('leadership', 'product-design-engineering')}

Do this, in order:
1. Propose candidate approaches (2-4 for a small-feature/live-surface scope, 1-2 for a
   chore) with a real, specific tradeoff for each -- not "option A: good, option B: bad".
2. Pick the recommended one and say exactly why.
3. Write concrete acceptance criteria: what must be true when this is done, specific
   enough that a later stage can check it mechanically. Also state anything explicitly
   out of scope.
4. ${PROGRESS_INSTRUCTION('Stage 1-2: Ideation & Requirements')}
5. ${DECISIONS_INSTRUCTION}

Return, as your final text: the chosen approach and the acceptance criteria (plain text, not JSON).`,
  { phase: 'Ideation & Requirements', label: 'ideation+requirements' }
)

// ---- Stage 3-4: Plan & Design + Architecture (Product/Design/Engineering) ----
phase('Design & Architecture')
const design = await agent(
  `Stage 3 (Plan & Design) + Stage 4 (Architecture) of the Spinr SDLC pipeline, for: ${title}

What Ideation & Requirements decided:
${ideation}

${roleContext('product-design-engineering')}

Do this, in order:
1. Decide additive-vs-destructive approach and whether a feature flag is needed
   (per CLAUDE.md's pre-merge release gates).
2. Name exactly which files/functions will change.
3. Run the blast-radius check BEFORE any code is written: grep for every OTHER
   caller/reader of anything shared that's being touched, and list them by name. If
   there are none, say "isolated, no other callers" explicitly -- don't just omit it.
4. ${PROGRESS_INSTRUCTION('Stage 3-4: Design & Architecture')}
5. ${DECISIONS_INSTRUCTION}
6. ${CHALLENGES_INSTRUCTION}

Return, as your final text: the file list and the full blast-radius finding.`,
  { phase: 'Design & Architecture', label: 'design+architecture' }
)

// ---- Stage 5: Development, with one bounded retry loop off Stage 6 (QA) ----
async function runDevelopment(instructions, heading) {
  return agent(
    `Stage 5 (Development) of the Spinr SDLC pipeline, for: ${title}

${instructions}

${roleContext('product-design-engineering')}

Implement the change for real, on the current git branch. Follow CLAUDE.md's
conventions exactly (Decimal-only money math where relevant, the dual import
pattern, ride-state transition guards where relevant, additive-over-destructive).
Keep the diff scoped to what Architecture named -- do not widen it. Commit your
change with git (do not push).

${PROGRESS_INSTRUCTION(heading)}
${DECISIONS_INSTRUCTION}

Return, as your final text: a summary of exactly what changed and why.`,
    { phase: 'Development', label: heading }
  )
}

async function runQA(devSummary, heading) {
  return agent(
    `Stage 6 (QA) of the Spinr SDLC pipeline, for: ${title}

What Development just changed:
${devSummary}

${roleContext('product-design-engineering')}

Run the relevant tests for what changed. If nothing automated applies, say precisely
why and what manual check substitutes -- "looks fine" is not an answer. If something
is broken, describe exactly what and why; do not fix it yourself, that's Development's
job on a retry.

${PROGRESS_INSTRUCTION(heading)}

Return a verdict: PASS if the change is verified working, FAIL if not, with the
specific reason either way.`,
    { phase: 'QA', label: heading, schema: QA_SCHEMA }
  )
}

phase('Development')
let devSummary = await runDevelopment(
  `Architecture plan from the previous stage:\n${design}`,
  'Stage 5: Development'
)

phase('QA')
let qaResult = await runQA(devSummary, 'Stage 6: QA')

if (qaResult && qaResult.verdict === 'FAIL') {
  log(`QA failed: ${qaResult.reason} -- looping back to Development once, per PIPELINE_DESIGN.md's cyclic-graph rule.`)
  phase('Development')
  devSummary = await runDevelopment(
    `QA rejected the previous attempt for this reason: ${qaResult.reason}\n\nOriginal architecture plan:\n${design}\n\nFix the specific issue QA found -- do not re-scope beyond that.`,
    'Stage 5 (retry): Development'
  )
  phase('QA')
  qaResult = await runQA(devSummary, 'Stage 6 (retry): QA')
}

if (!qaResult || qaResult.verdict !== 'PASS') {
  log('QA still failing after one retry -- stopping here per GUARDRAILS.md ("escalate, don\'t silently ship, when in doubt"). This run needs a human.')
  return {
    runDir,
    status: 'BLOCKED_AT_QA',
    reason: qaResult ? qaResult.reason : 'QA agent did not return a verdict',
  }
}

// ---- Stage 7: Security, with one bounded retry loop back to Development ----
async function runSecurity(devSummary, heading) {
  return agent(
    `Stage 7 (Security) of the Spinr SDLC pipeline, for: ${title}

What Development changed (QA-passed):
${devSummary}

${roleContext('trust-safety-security')}

Review the actual diff on this branch (git diff/git log against the branch point) for
the concerns in your role file: auth, PII, money-arithmetic, insurance-period
classification, and fraud-surface issues, as relevant to what changed. Verify any
finding against the real code before reporting it -- do not report a suspicion as a
finding.

${PROGRESS_INSTRUCTION(heading)}

Return a verdict: PASS if clear, FAIL with the specific finding (file:line) if not.`,
    { phase: 'Security', label: heading, schema: SECURITY_SCHEMA }
  )
}

phase('Security')
let securityResult = await runSecurity(devSummary, 'Stage 7: Security')

if (securityResult && securityResult.verdict === 'FAIL') {
  log(`Security found an issue: ${securityResult.reason} -- looping back to Development once.`)
  phase('Development')
  devSummary = await runDevelopment(
    `Security rejected the previous attempt for this reason: ${securityResult.reason}\n\nOriginal architecture plan:\n${design}\n\nFix the specific issue -- do not re-scope beyond that.`,
    'Stage 5 (security retry): Development'
  )
  phase('QA')
  qaResult = await runQA(devSummary, 'Stage 6 (security retry): QA')
  if (!qaResult || qaResult.verdict !== 'PASS') {
    log('QA failed again after the security-driven retry -- stopping here, needs a human.')
    return { runDir, status: 'BLOCKED_AT_QA_AFTER_SECURITY_RETRY', reason: qaResult ? qaResult.reason : 'no verdict' }
  }
  phase('Security')
  securityResult = await runSecurity(devSummary, 'Stage 7 (retry): Security')
}

if (!securityResult || securityResult.verdict !== 'PASS') {
  log('Security still failing after one retry -- stopping here, needs a human.')
  return {
    runDir,
    status: 'BLOCKED_AT_SECURITY',
    reason: securityResult ? securityResult.reason : 'Security agent did not return a verdict',
  }
}

// ---- Stage 8: Change Review (Finance/Legal/People) ----
phase('Change Review')
const changeReview = await agent(
  `Stage 8 (Change Review) of the Spinr SDLC pipeline, for: ${title}. Scope class: ${scope}.

What changed (QA- and Security-passed):
${devSummary}

${roleContext('finance-legal-people')}

Decide whether a Change Impact & Risk Log entry is required -- per CLAUDE.md, only for
changes touching a live-tested surface (rides, dispatch, payments, auth, corporate,
safety). If required, write the full entry (using docs/templates/CHANGE_IMPACT_LOG.md's
fields) into ${runDir}/decisions.md. If not required, say so explicitly and why not.

Then decide, per agents/GUARDRAILS.md's soft-stop list: does this change need an
explicit human go-ahead before Release, or can it proceed straight to opening a draft
PR? Live-tested-surface changes always require a human, no matter how confident earlier
stages were.

${PROGRESS_INSTRUCTION('Stage 8: Change Review')}

Return your verdict (REQUIRES_HUMAN or CLEAR_FOR_RELEASE), your reasoning, and whether
a Change Impact Log entry was required.`,
  { phase: 'Change Review', label: 'change-review', schema: CHANGE_REVIEW_SCHEMA }
)

// ---- Stage 9-10: Release + Operations watch note ----
phase('Release')
if (!changeReview || changeReview.verdict === 'REQUIRES_HUMAN') {
  log(`Change Review flagged this for human sign-off: ${changeReview ? changeReview.reasoning : 'no verdict returned'}. Stopping before Release -- not opening a PR without that go-ahead.`)
  return {
    runDir,
    status: 'AWAITING_HUMAN_SIGNOFF',
    reasoning: changeReview ? changeReview.reasoning : null,
  }
}

const release = await agent(
  `Stage 9 (Release) + Stage 10 (Operations watch) of the Spinr SDLC pipeline, for: ${title}

Change Review cleared this for release. Everything that changed:
${devSummary}

${roleContext('product-design-engineering', 'operations-support')}

Do this, in order:
1. Push the current branch (git push -u origin <branch-name>) if it has commits not
   yet on origin.
2. Open a DRAFT pull request. Check for a PR template first and follow it; otherwise
   write a clear description of what changed and why, referencing this run's
   ${runDir}/progress-report.md.
3. ${PROGRESS_INSTRUCTION('Stage 9: Release')}
4. Then, as Stage 10 (Operations), append one more section to
   ${runDir}/progress-report.md titled '## Stage 10: Operations watch' -- what to watch
   after this merges, for how long, and what "this broke" would look like, per
   agents/roles/operations-support.md.

Return the PR URL as your final text.`,
  { phase: 'Release', label: 'release' }
)

log(`Pipeline run "${slug}" complete.`)

return {
  runDir,
  status: 'RELEASED',
  ideation,
  design,
  qaResult,
  securityResult,
  changeReview,
  release,
}
