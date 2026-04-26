"""
Generate fix recommendations for classified CI errors.

Safe fixes (CI config only) are recommended directly.
Risky fixes (touch application code) are escalated to Change Requests.

Input:  classified_errors.json + impact_report.json
Output: fix_recommendations.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ─── Fix playbooks: one per error category ───────────────────────────────────

@dataclass
class FixStep:
    action: str
    command: str = ""
    files_to_check: list[str] = field(default_factory=list)
    risk_level: str = "low"  # low | medium | high
    requires_cr: bool = False


@dataclass
class FixRecommendation:
    error_ref: str
    category: str
    severity: str
    surface: str
    is_safe_to_auto_fix: bool
    requires_change_request: bool
    fix_summary: str
    steps: list[FixStep]
    guard_rail_suggestion: str
    estimated_effort: str  # XS | S | M | L | XL
    change_request_reason: str = ""


PLAYBOOKS: dict[str, dict[str, Any]] = {
    "test": {
        "safe_to_auto_fix": False,
        "fix_summary": "Investigate test failure, identify root cause, fix the code or the test (never silence the test).",
        "steps": [
            FixStep("Reproduce failure locally", "pytest <test_path> -v", risk_level="low"),
            FixStep("Check if failure is in test code or application code", risk_level="low"),
            FixStep("Fix root cause in application code (preferred)", risk_level="medium"),
            FixStep("If test is wrong, fix the test to match correct behaviour", risk_level="medium"),
            FixStep("Verify no other tests break: run full suite", "pytest -v", risk_level="low"),
            FixStep("Add guard rail: ensure isolation fixtures are present", risk_level="low"),
        ],
        "guard_rail": "Add autouse cleanup fixtures in conftest.py to isolate test state. Confirm test markers are correct.",
        "effort": "M",
        "cr_candidates": [
            "Test isolation improvements (autouse fixtures)",
            "Shared test factory patterns to reduce duplication",
        ],
    },
    "coverage": {
        "safe_to_auto_fix": False,
        "fix_summary": "Coverage dropped below threshold. Add tests for uncovered code paths, or file a Change Request to revise the threshold with justification.",
        "steps": [
            FixStep("Identify uncovered lines", "pytest --cov=. --cov-report=term-missing", risk_level="low"),
            FixStep("Add unit tests for critical uncovered paths (never mock away the logic)", risk_level="medium"),
            FixStep("If threshold is genuinely unreachable (Supabase-backed code), file CR to adjust", requires_cr=True, risk_level="low"),
        ],
        "guard_rail": "Enforce --cov-fail-under in pytest.ini once baseline is established. Add coverage tracking via Codecov.",
        "effort": "L",
        "cr_candidates": [
            "Establish realistic coverage baseline and enforce via pytest.ini --cov-fail-under",
        ],
    },
    "lint": {
        "safe_to_auto_fix": True,
        "fix_summary": "Fix lint errors. Auto-fixable issues can be resolved with ruff --fix / eslint --fix. Manual review needed for the rest.",
        "steps": [
            FixStep("Run auto-fix", "ruff check . --fix && ruff format .", risk_level="low"),
            FixStep("For TypeScript: tsc --noEmit to catch type errors", "npx tsc --noEmit", risk_level="low"),
            FixStep("For ESLint: eslint --fix on changed files only", "npx eslint --fix <files>", risk_level="low"),
            FixStep("Review remaining issues manually", risk_level="low"),
            FixStep("Run tests to confirm no behaviour change", risk_level="low"),
        ],
        "guard_rail": "Add ruff/eslint to pre-commit hooks so lint errors never reach CI.",
        "effort": "S",
        "cr_candidates": [
            "Reduce admin-dashboard ESLint --max-warnings from 600 to <100",
            "Add pre-commit hooks for ruff and ESLint",
        ],
    },
    "security": {
        "safe_to_auto_fix": False,
        "fix_summary": "Security finding requires immediate review. Never auto-fix security issues — understand the finding first.",
        "steps": [
            FixStep("Read the full CVE / finding description", risk_level="low"),
            FixStep("Check if the vulnerable code path is reachable in production", risk_level="low"),
            FixStep("For CVEs: update the vulnerable package to the fixed version", "pip install <pkg>==<fixed_ver>", risk_level="medium"),
            FixStep("For secrets: rotate the exposed credential IMMEDIATELY before fixing the code", requires_cr=True, risk_level="high"),
            FixStep("For Trivy CRITICAL: block deploy until resolved", risk_level="high"),
            FixStep("Document finding in reports/audits/", risk_level="low"),
        ],
        "guard_rail": "Enable Dependabot for automated CVE PRs. Add pip-audit and Trivy to all CI pipelines. Never commit secrets.",
        "effort": "M",
        "cr_candidates": [
            "Add .gitleaks.toml for enhanced pre-commit secret detection",
            "Enable Dependabot auto-merge for patch-level security updates",
        ],
    },
    "build": {
        "safe_to_auto_fix": False,
        "fix_summary": "Build failure — identify whether it is a code error, dependency issue, or environment issue.",
        "steps": [
            FixStep("Check if the error is in new code introduced in this PR", risk_level="low"),
            FixStep("For ImportError / ModuleNotFoundError: check requirements.txt / package.json", risk_level="low"),
            FixStep("For Docker build: validate Dockerfile changes", "docker build -f backend/Dockerfile .", risk_level="medium"),
            FixStep("For Next.js: run npm run build locally", "cd admin-dashboard && npm run build", risk_level="low"),
            FixStep("For Expo: run expo export locally", "cd rider-app && yarn build:web", risk_level="low"),
        ],
        "guard_rail": "Add build steps to local pre-push hook. Cache dependencies in CI to catch install regressions early.",
        "effort": "S",
        "cr_candidates": [],
    },
    "deploy": {
        "safe_to_auto_fix": False,
        "fix_summary": "Deployment failed. Check service logs, verify credentials, confirm infrastructure health.",
        "steps": [
            FixStep("Check Railway / Render / Vercel service logs for startup errors", risk_level="low"),
            FixStep("Verify deployment secrets are set and current (RAILWAY_TOKEN, etc.)", risk_level="medium"),
            FixStep("Confirm the build artefact from previous jobs succeeded", risk_level="low"),
            FixStep("Check health endpoint manually", "curl <API_URL>/health", risk_level="low"),
            FixStep("If infra issue: re-run the deployment job from GitHub Actions UI", risk_level="low"),
        ],
        "guard_rail": "Add retry logic to health checks (already present in smoke-test). Monitor Railway/Render status pages.",
        "effort": "S",
        "cr_candidates": [
            "Add deployment rollback automation on health check failure",
        ],
    },
    "dependency": {
        "safe_to_auto_fix": True,
        "fix_summary": "Package installation failed. Likely a version conflict, registry outage, or lock file mismatch.",
        "steps": [
            FixStep("Reproduce locally", "pip install -r requirements.txt  # or yarn install", risk_level="low"),
            FixStep("Check for conflicting version pins in requirements.txt / package.json", risk_level="low"),
            FixStep("Regenerate lock file if needed", "pip-compile requirements.in  # or yarn install --frozen-lockfile", risk_level="medium"),
            FixStep("If registry outage: retry CI (transient)", risk_level="low"),
        ],
        "guard_rail": "Enable Dependabot to keep dependencies up to date. Pin exact versions in requirements.txt.",
        "effort": "XS",
        "cr_candidates": [
            "Add requirements.txt pinning via pip-compile",
        ],
    },
    "infra": {
        "safe_to_auto_fix": False,
        "fix_summary": "CI infrastructure issue (runner, network, rate limit). Usually transient — retry first.",
        "steps": [
            FixStep("Re-run the failed workflow from GitHub Actions UI", risk_level="low"),
            FixStep("If persistent: check GitHub Status (githubstatus.com)", risk_level="low"),
            FixStep("If rate limited: check PAT token permissions and expiry", risk_level="medium"),
        ],
        "guard_rail": "Add workflow-level timeout and retry to critical jobs.",
        "effort": "XS",
        "cr_candidates": [],
    },
}


def _recommend(error: dict[str, Any], impact: dict[str, Any] | None) -> FixRecommendation:
    category = error.get("category", "infra")
    severity = error.get("severity", "P2")
    surface  = error.get("surface", "unknown")
    job      = error.get("job", "unknown")

    playbook = PLAYBOOKS.get(category, PLAYBOOKS["infra"])
    steps = [FixStep(**s) if isinstance(s, dict) else s for s in playbook["steps"]]

    requires_cr = any(s.requires_cr for s in steps) or not playbook["safe_to_auto_fix"]
    cr_reason = ""
    if requires_cr and category == "security":
        cr_reason = "Security findings always require human review and approval before any fix."
    elif requires_cr and category == "coverage":
        cr_reason = "Coverage threshold changes alter quality gates and require explicit approval."
    elif requires_cr:
        cr_reason = "Change touches application code — requires review to ensure no regression."

    return FixRecommendation(
        error_ref=f"{job}::{category}",
        category=category,
        severity=severity,
        surface=surface,
        is_safe_to_auto_fix=playbook["safe_to_auto_fix"],
        requires_change_request=requires_cr,
        fix_summary=playbook["fix_summary"],
        steps=steps,
        guard_rail_suggestion=playbook["guard_rail"],
        estimated_effort=playbook["effort"],
        change_request_reason=cr_reason,
    )


def _build_cr_list(recommendations: list[FixRecommendation]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    crs: list[dict[str, Any]] = []
    for rec in recommendations:
        playbook = PLAYBOOKS.get(rec.category, {})
        for candidate in playbook.get("cr_candidates", []):
            if candidate not in seen:
                seen.add(candidate)
                crs.append({
                    "title": candidate,
                    "category": rec.category,
                    "source_error": rec.error_ref,
                    "priority": rec.severity,
                    "status": "pending_approval",
                })
    return crs


def recommend(errors_data: dict[str, Any], impact_data: dict[str, Any]) -> dict[str, Any]:
    errors = errors_data.get("errors", [])
    assessments_by_ref: dict[str, Any] = {
        a["error_ref"]: a for a in impact_data.get("assessments", [])
    }

    recommendations = [
        _recommend(e, assessments_by_ref.get(f"{e['job']}::{e['category']}"))
        for e in errors
    ]

    change_requests = _build_cr_list(recommendations)

    # Determine immediate actions (P0/P1 that are safe)
    immediate_actions = [
        r for r in recommendations
        if r.severity in ("P0", "P1") and r.is_safe_to_auto_fix
    ]

    return {
        "total_recommendations": len(recommendations),
        "immediate_safe_fixes": len(immediate_actions),
        "change_requests_needed": len(change_requests),
        "recommendations": [asdict(r) for r in recommendations],
        "proposed_change_requests": change_requests,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CI fix recommendations")
    parser.add_argument("--errors",  required=True, help="Path to classified_errors.json")
    parser.add_argument("--impact",  required=True, help="Path to impact_report.json")
    parser.add_argument("--output",  required=True, help="Path to write fix_recommendations.json")
    args = parser.parse_args()

    errors_data = json.loads(Path(args.errors).read_text())
    impact_data = json.loads(Path(args.impact).read_text())
    result = recommend(errors_data, impact_data)
    Path(args.output).write_text(json.dumps(result, indent=2))

    print(f"Generated {result['total_recommendations']} fix recommendation(s)")
    print(f"Immediate safe fixes: {result['immediate_safe_fixes']}")
    print(f"Change requests needed: {result['change_requests_needed']}")


if __name__ == "__main__":
    main()
