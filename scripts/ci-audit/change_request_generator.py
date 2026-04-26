"""
Generate structured Change Request drafts from fix recommendations.

These CRs require human approval before any implementation work begins.
Output: change_requests.json with one entry per proposed improvement.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ─── Detailed CR templates keyed by improvement title ───────────────────────

CR_DETAILS: dict[str, dict[str, Any]] = {
    "Establish realistic coverage baseline and enforce via pytest.ini --cov-fail-under": {
        "category": "Test Infrastructure",
        "priority": "P1",
        "what": (
            "Establish a realistic code coverage baseline for the backend and enforce it "
            "via `--cov-fail-under` in `backend/pytest.ini`. The current pytest.ini explicitly "
            "removes the threshold because the Supabase-backed code path yields ~6% coverage. "
            "The CI workflow uses `--cov-fail-under=70` which is aspirational and regularly fails."
        ),
        "where": [
            "backend/pytest.ini (add --cov-fail-under=<baseline>)",
            ".github/workflows/ci.yml (align pytest command with pytest.ini)",
            "backend/tests/ (add coverage for critical paths)",
        ],
        "why": (
            "The CI workflow and pytest.ini are inconsistent: CI enforces 70% but pytest.ini "
            "documents it was never achievable. This means the 70% gate either never triggers "
            "(if mocked away) or regularly fails, both of which destroy trust in the metric."
        ),
        "benefits": [
            "CI coverage gate becomes meaningful signal instead of noise",
            "Developers know exactly what threshold they must maintain",
            "Prevents coverage regression from silently shipping to production",
            "Creates a documented starting point for improving test coverage over time",
        ],
        "risks_if_not_done": [
            "Coverage continues to be unmeasured — regressions ship undetected",
            "Developer trust in CI degrades (it either always passes or always fails)",
            "Technical debt in test suite compounds",
        ],
        "risks_of_change": [
            "Low: setting a realistic threshold cannot break functionality",
            "If threshold is too high, CI will fail until tests catch up — mitigate by starting low",
        ],
        "rollback": "Remove --cov-fail-under from pytest.ini.",
        "effort": "M",
        "implementation_plan": [
            "1. Run `pytest --cov=. --cov-report=term-missing` and record actual coverage % (baseline)",
            "2. Set `--cov-fail-under=<baseline - 5>` in pytest.ini as a floor (5% headroom)",
            "3. Align the CI workflow to use pytest.ini settings (remove duplicate flags from ci.yml)",
            "4. Incrementally improve tests toward the 70% aspirational target over multiple sprints",
            "5. Raise threshold in 5% increments as coverage improves",
        ],
    },

    "Reduce admin-dashboard ESLint --max-warnings from 600 to <100": {
        "category": "Linting / Formatting",
        "priority": "P2",
        "what": (
            "Systematically reduce the admin-dashboard ESLint `--max-warnings` limit "
            "from 600 to below 100 by fixing underlying lint issues, then enforce the lower "
            "limit in CI. The CI guard rail baseline constant should be updated at each step."
        ),
        "where": [
            "admin-dashboard/package.json (eslint --max-warnings flag)",
            "admin-dashboard/src/** (fix lint issues)",
            ".github/workflows/ci-guardrails.yml (BASELINE constant)",
        ],
        "why": (
            "600 warnings is too many to be useful signal. At this count developers ignore lint "
            "output entirely. New warnings accumulate invisibly. Security-related lint warnings "
            "(such as jsx-a11y accessibility issues or React key warnings) may be missed."
        ),
        "benefits": [
            "Lint output becomes actionable signal again",
            "New warnings are immediately visible and blocked by CI",
            "Reduces bug surface — many warnings indicate real problems",
            "Aligns admin-dashboard with rider/driver-app code quality standards",
        ],
        "risks_if_not_done": [
            "Warning count will grow to 1000+ over time",
            "Real issues (security, runtime errors) hidden in noise",
            "Technical debt harder to fix later",
        ],
        "risks_of_change": [
            "Low: fixing lint is non-functional by definition",
            "Auto-fix changes formatting — full test suite must pass after",
            "Some warnings may indicate real bugs — review each carefully",
        ],
        "rollback": "Raise --max-warnings back to previous value. Revert changed source files.",
        "effort": "L",
        "implementation_plan": [
            "1. Run `npm run lint -- --format=json` and categorise all 600 warnings by rule",
            "2. Auto-fix the easy categories: `npx eslint --fix src/`",
            "3. Manually fix remaining warnings in 50-per-PR batches",
            "4. Lower threshold incrementally: 600 → 400 → 200 → 100 → 50",
            "5. Update BASELINE in ci-guardrails.yml at each step",
            "6. Run `npm test` after each batch to confirm no regressions",
        ],
    },

    "Add pre-commit hooks for ruff and ESLint": {
        "category": "Developer Experience",
        "priority": "P2",
        "what": (
            "Extend the existing Husky pre-commit setup to run ruff and ESLint "
            "on staged files before every commit, catching lint errors before they reach CI."
        ),
        "where": [
            ".husky/pre-commit (new file)",
            "package.json at root (lint-staged config)",
        ],
        "why": (
            "Currently Husky only enforces commit message format (commitlint). "
            "Lint errors discovered in CI cost 3-5 minutes of developer feedback time. "
            "Catching them at commit time takes ~2 seconds and never blocks CI."
        ),
        "benefits": [
            "Lint errors caught in 2 seconds at commit vs 5 minutes in CI",
            "CI lint jobs run cleaner (fewer false failures from developer typos)",
            "Developer feedback loop dramatically faster",
            "Zero-cost guard rail: runs only on staged files (fast even on large repos)",
        ],
        "risks_if_not_done": [
            "Lint errors continue to accumulate and block CI",
            "Developer experience remains poor (slow feedback)",
        ],
        "risks_of_change": [
            "Low: hook can be bypassed with --no-verify if truly needed",
            "Slight commit-time overhead (~2s) — acceptable trade-off",
        ],
        "rollback": "Remove .husky/pre-commit file.",
        "effort": "S",
        "implementation_plan": [
            "1. Install lint-staged: `npm install -D lint-staged` in root",
            "2. Add lint-staged config to root package.json",
            "3. Create .husky/pre-commit that runs `npx lint-staged`",
            "4. Configure ruff for .py files, eslint for .ts/.tsx/.js/.jsx",
            "5. Test with a deliberate lint error: `git commit` should reject it",
            "6. Document bypass procedure (--no-verify) for emergency commits",
        ],
    },

    "Add deployment rollback automation on health check failure": {
        "category": "Deployment Configuration",
        "priority": "P2",
        "what": (
            "Add an automated rollback step to the Railway deployment workflow that "
            "triggers when the post-deploy health check fails. The rollback should "
            "redeploy the previous successful image."
        ),
        "where": [
            ".github/workflows/ci.yml (smoke-test job → new rollback step)",
            ".github/workflows/deploy-backend.yml (add rollback job)",
        ],
        "why": (
            "Currently, a failed health check after deployment notifies via Slack but "
            "leaves the broken deployment running. Users experience downtime until a "
            "developer manually re-deploys. Automated rollback limits blast radius."
        ),
        "benefits": [
            "Mean Time To Recovery (MTTR) reduced from ~30 minutes to <2 minutes",
            "Production downtime minimised automatically",
            "Developers notified but not required to act urgently at 3am",
            "Consistent rollback procedure (not dependent on who is on-call)",
        ],
        "risks_if_not_done": [
            "Production outages last until a developer responds",
            "On-call burden increases over time",
            "MTTR is unpredictable",
        ],
        "risks_of_change": [
            "Medium: rollback may revert intended fixes if health check is flaky",
            "Mitigation: only trigger rollback after 3 consecutive health check failures",
            "Railway API / CLI availability required in CI environment",
        ],
        "rollback": "Remove the rollback step from the workflow. No state change in the repo.",
        "effort": "M",
        "implementation_plan": [
            "1. Research Railway API endpoint for triggering a previous deployment",
            "2. Add RAILWAY_ROLLBACK_DEPLOYMENT_ID output to deploy step",
            "3. Add conditional rollback step: runs if smoke-test fails",
            "4. Test in staging (develop branch) before enabling on main",
            "5. Add Slack notification that explicitly says 'auto-rollback triggered'",
        ],
    },

    "Add .gitleaks.toml for enhanced pre-commit secret detection": {
        "category": "Security Tooling",
        "priority": "P2",
        "what": (
            "Add a `.gitleaks.toml` configuration file with Spinr-specific secret patterns "
            "and a pre-commit gitleaks hook to catch credentials before they reach the repo."
        ),
        "where": [
            ".gitleaks.toml (new file at repo root)",
            ".husky/pre-commit (add gitleaks check)",
        ],
        "why": (
            "TruffleHog runs in CI but only after the secret is already committed and potentially "
            "visible in the PR diff. Gitleaks as a pre-commit hook prevents secrets from being "
            "committed in the first place. Spinr-specific patterns (Supabase keys, Railway tokens, "
            "Expo tokens) are not in the default TruffleHog ruleset."
        ),
        "benefits": [
            "Secrets caught before they ever touch git history",
            "Spinr-specific patterns (Supabase service role keys, etc.) detected",
            "Complements TruffleHog CI scan (defence in depth)",
            "Developer notified immediately on commit attempt",
        ],
        "risks_if_not_done": [
            "Secrets may be committed and remain in git history even after removal",
            "CI-only detection means secrets are briefly exposed in PRs",
            "Supabase service role key or JWT secret leaked → full database access",
        ],
        "risks_of_change": [
            "Low: pre-commit hook, does not touch application code",
            "False positives possible — gitleaks.toml allows allowlist configuration",
        ],
        "rollback": "Remove .gitleaks.toml and revert .husky/pre-commit.",
        "effort": "S",
        "implementation_plan": [
            "1. Add gitleaks to CI: `brew install gitleaks` or use the official GitHub Action",
            "2. Create .gitleaks.toml with Spinr patterns (Supabase URLs, JWT patterns, EXPO_PUBLIC_)",
            "3. Add `gitleaks protect --staged` to .husky/pre-commit",
            "4. Add allowlist for test fixtures that intentionally contain fake secrets",
            "5. Document rotation procedure in CLAUDE.md",
        ],
    },

    "Add requirements.txt pinning via pip-compile": {
        "category": "Dependency Management",
        "priority": "P3",
        "what": (
            "Introduce `pip-compile` (from pip-tools) to generate a fully-pinned "
            "`requirements.txt` from a `requirements.in` source of truth. This ensures "
            "reproducible installs across all environments."
        ),
        "where": [
            "backend/requirements.in (new — abstract dependencies)",
            "backend/requirements.txt (generated — fully pinned)",
            ".github/workflows/ci.yml (add pip-compile check step)",
        ],
        "why": (
            "Current requirements.txt uses some unpinned ranges (e.g. `stripe>=7`). "
            "This means CI may install a different version than production, causing "
            "subtle behavioural differences. Pinning all transitive dependencies "
            "eliminates this class of environment mismatch."
        ),
        "benefits": [
            "Exact same packages installed in CI, staging, and production",
            "Dependency changes are explicit and reviewed in PRs",
            "Security audit (pip-audit) scans exact versions in use",
            "Dependabot PRs update requirements.txt automatically",
        ],
        "risks_if_not_done": [
            "CI and production may diverge on transitive dependency versions",
            "Security CVE scans may miss vulnerabilities in unpinned ranges",
        ],
        "risks_of_change": [
            "Low: pip-compile is a dev-only tool, does not touch application code",
            "Initial pin may pull in different versions than currently in use — run full test suite",
        ],
        "rollback": "Revert requirements.txt to previous version. Remove requirements.in.",
        "effort": "S",
        "implementation_plan": [
            "1. Install pip-tools: `pip install pip-tools`",
            "2. Create backend/requirements.in from current requirements.txt (abstract specs only)",
            "3. Run `pip-compile requirements.in -o requirements.txt` to generate pinned file",
            "4. Verify all tests pass with newly pinned versions",
            "5. Add CI step: `pip-compile --check requirements.in` to detect drift",
            "6. Configure Dependabot to update requirements.in",
        ],
    },

    "Enable Dependabot auto-merge for patch-level security updates": {
        "category": "Dependency Management",
        "priority": "P2",
        "what": (
            "Configure GitHub repository settings to auto-merge Dependabot PRs "
            "for patch-level security updates (when all CI checks pass). "
            "Minor and major version bumps still require human review."
        ),
        "where": [
            ".github/dependabot.yml (already created by this audit — add auto-merge labels)",
            "GitHub repository settings (branch protection + auto-merge)",
        ],
        "why": (
            "Security patch PRs from Dependabot often sit unmerged for weeks because "
            "they require human attention. In the meantime, the known CVE remains in "
            "the codebase. Patch-level updates have minimal breaking change risk and "
            "all CI gates already validate them."
        ),
        "benefits": [
            "Known CVEs patched within hours of Dependabot PR creation",
            "Reduces security maintenance burden on developers",
            "CI gates (pip-audit, Trivy) ensure only safe updates auto-merge",
        ],
        "risks_if_not_done": [
            "Known CVEs remain in codebase for weeks or months",
            "Security posture degrades over time despite having Dependabot enabled",
        ],
        "risks_of_change": [
            "Low for patch versions: semver patch = bug fix, no breaking API changes",
            "CI must catch any regressions before auto-merge completes",
            "Limit to patch versions only — minor/major always require human review",
        ],
        "rollback": "Disable auto-merge in GitHub repository settings.",
        "effort": "XS",
        "implementation_plan": [
            "1. Enable 'Allow auto-merge' in GitHub repository settings → General",
            "2. Add `auto-merge: true` label trigger to Dependabot PRs for patch updates",
            "3. Ensure branch protection requires CI to pass before merge",
            "4. Monitor first 5 auto-merges to confirm no regressions",
        ],
    },
}


def _build_cr(title: str, source_error: str, priority: str) -> dict[str, Any]:
    details = CR_DETAILS.get(title, {})
    year = datetime.now(timezone.utc).year

    return {
        "title": title,
        "source_error": source_error,
        "status": "pending_approval",
        "category": details.get("category", "Other"),
        "priority": priority,
        "what": details.get("what", "See title."),
        "where": details.get("where", []),
        "why": details.get("why", "Identified during CI audit."),
        "benefits": details.get("benefits", []),
        "risks_if_not_done": details.get("risks_if_not_done", []),
        "risks_of_change": details.get("risks_of_change", []),
        "rollback": details.get("rollback", "Revert the change."),
        "estimated_effort": details.get("effort", "M"),
        "implementation_plan": details.get("implementation_plan", []),
        "audit_year": year,
        "github_template": ".github/ISSUE_TEMPLATE/ci_change_request.yml",
    }


def generate(fixes_data: dict[str, Any]) -> dict[str, Any]:
    proposed = fixes_data.get("proposed_change_requests", [])
    crs = []

    for i, cr_stub in enumerate(proposed, 1):
        title  = cr_stub.get("title", "Unknown improvement")
        source = cr_stub.get("source_error", "unknown")
        prio   = cr_stub.get("priority", "P2")
        cr     = _build_cr(title, source, prio)
        cr["cr_id"] = f"CR-{cr['audit_year']}-{i:03d}"
        crs.append(cr)

    return {
        "total": len(crs),
        "change_requests": crs,
        "instructions": (
            "Each CR requires approval before implementation. "
            "File a GitHub issue using .github/ISSUE_TEMPLATE/ci_change_request.yml "
            "and tag it 'needs-approval'. Do not start implementation until the issue "
            "is labelled 'approved'."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Change Request drafts")
    parser.add_argument("--fixes",  required=True, help="Path to fix_recommendations.json")
    parser.add_argument("--output", required=True, help="Path to write change_requests.json")
    args = parser.parse_args()

    fixes_data = json.loads(Path(args.fixes).read_text())
    result = generate(fixes_data)
    Path(args.output).write_text(json.dumps(result, indent=2))

    print(f"Generated {result['total']} Change Request draft(s)")
    for cr in result["change_requests"]:
        print(f"  {cr['cr_id']}: {cr['title']}")


if __name__ == "__main__":
    main()
