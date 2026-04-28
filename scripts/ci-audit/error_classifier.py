"""
Classify CI failure signals from a GitHub Actions run into structured errors.

Input:  JSON produced by fetch_run_data.py  (jobs + step logs)
Output: classified_errors.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ─── Classification rules ────────────────────────────────────────────────────

SURFACE_JOBS: dict[str, list[str]] = {
    "backend":          ["backend-test", "backend-check"],
    "rider-app":        ["rider-app-test", "rider-app-check"],
    "driver-app":       ["driver-app-test", "driver-app-check"],
    "admin-dashboard":  ["admin-test"],
    "frontend":         ["frontend-test"],
    "security":         ["security-scan", "docker-image-scan"],
    "deploy":           ["deploy-backend", "deploy-frontend", "deploy-admin"],
    "e2e":              ["e2e-test"],
    "mobile":           ["mobile-build", "build-mobile-test", "ota-update-test"],
    "infra":            ["smoke-test"],
}

CATEGORY_RULES: list[tuple[str, str, str]] = [
    # (category, pattern_in_log, description_template)
    # ── Test failures ─────────────────────────────────────────────────────
    ("test",     r"FAILED\s+tests/",               "pytest test failure"),
    ("test",     r"FAILED\s+backend/tests/",        "pytest test failure (backend prefix)"),
    ("test",     r"= \d+ failed",                   "pytest: N tests failed"),
    ("test",     r"= \d+ error",                    "pytest: collection/fixture errors"),
    ("test",     r"ERROR\s+collecting",              "pytest collection error"),
    ("test",     r"ERROR\s+tests/",                 "pytest fixture/setup error"),
    ("test",     r"AssertionError",                 "assertion error in test"),
    ("test",     r"FAIL\s+.*\.(test|spec)\.[tj]sx?", "Jest/Vitest test failure"),
    ("test",     r"●\s+.*\s+›",                     "Jest describe block failure"),
    ("test",     r"TimeoutError:",                  "test timeout"),
    ("test",     r"Error: expect\(",                "expect assertion failure"),
    ("test",     r"short test summary info",         "pytest summary (failures present)"),
    ("test",     r"Tests\s+\d+\s+failed",           "Vitest/Jest failure summary"),
    ("coverage", r"FAIL\s+Required test coverage",  "coverage threshold not met"),
    ("coverage", r"Coverage\s+.*below\s+threshold", "coverage threshold not met"),
    # ── Lint / type errors ────────────────────────────────────────────────
    ("lint",     r"Found \d+ error[s]?\.",            "ruff lint errors"),
    ("lint",     r"error:\s+\w+\s+\[E\d+\]",       "ruff error code"),
    ("lint",     r"ESLint:\s+\d+ error",            "ESLint errors"),
    ("lint",     r"TypeScript error",               "TypeScript type error"),
    ("lint",     r"error TS\d+:",                   "TypeScript compiler error"),
    ("lint",     r"Parsing error:",                 "ESLint parse error"),
    # ── Security ──────────────────────────────────────────────────────────
    ("security", r"CVE-\d{4}-\d+",                 "CVE vulnerability found"),
    ("security", r"VULNERABILITY\s+FOUND",          "security vulnerability"),
    ("security", r"Found verified secret",          "trufflehog verified secret"),
    ("security", r"Found unverified result",        "trufflehog unverified secret"),
    ("security", r"CRITICAL.*vulnerability",        "Trivy CRITICAL vulnerability"),
    ("security", r"HIGH.*vulnerability",            "Trivy HIGH vulnerability"),
    ("security", r"EXPO_PUBLIC_.*SECRET",           "exposed secret in public env var"),
    # ── Build failures ────────────────────────────────────────────────────
    ("build",    r"ModuleNotFoundError:\s+No module named", "Python module not found"),
    ("build",    r"ImportError",                    "Python import error"),
    ("build",    r"SyntaxError",                    "syntax error"),
    ("build",    r"Cannot find module",             "Node module not found"),
    ("build",    r"Failed to compile",              "Next.js/Expo compile error"),
    ("build",    r"Build failed",                   "build failure"),
    ("build",    r"ERROR in.*\.(ts|tsx|js)",        "TypeScript/webpack build error"),
    ("build",    r"docker.*build.*failed",          "Docker build failure"),
    # ── Deploy failures ───────────────────────────────────────────────────
    ("deploy",   r"Deploy failed",                  "deployment failure"),
    ("deploy",   r"Railway.*error",                 "Railway deploy error"),
    ("deploy",   r"Render.*failed",                 "Render deploy failure"),
    ("deploy",   r"Vercel.*failed",                 "Vercel deploy failure"),
    ("deploy",   r"EAS.*build.*failed",             "EAS mobile build failure"),
    ("deploy",   r"Health check.*failed",           "post-deploy health check failure"),
    ("deploy",   r"curl.*failed",                   "smoke test curl failure"),
    # ── Dependency / environment ──────────────────────────────────────────
    ("dependency", r"pip install.*error",           "pip install failure"),
    ("dependency", r"yarn.*ERR!",                   "yarn install failure"),
    ("dependency", r"npm ERR!",                     "npm install failure"),
    ("dependency", r"ResolutionError",              "dependency resolution error"),
    # ── Infrastructure ────────────────────────────────────────────────────
    ("infra",    r"runner.*unavailable",            "GitHub Actions runner issue"),
    ("infra",    r"Connection timed out",           "network timeout in CI"),
    ("infra",    r"rate limit exceeded",            "GitHub API rate limit"),
]

SEVERITY_MAP: dict[str, str] = {
    "security":   "P0",
    "deploy":     "P0",
    "build":      "P1",
    "test":       "P1",
    "coverage":   "P2",
    "lint":       "P2",
    "dependency": "P1",
    "infra":      "P2",
}

SEVERITY_OVERRIDE: list[tuple[str, str]] = [
    # (pattern, override_severity)
    (r"CVE-.*CRITICAL",    "P0"),
    (r"Found verified secret", "P0"),
    (r"Health check.*failed", "P0"),
    (r"coverage.*below.*\b[0-9]\b",  "P3"),  # very low thresholds
]


@dataclass
class ClassifiedError:
    job: str
    step: str
    category: str
    severity: str
    description: str
    log_excerpt: str
    surface: str
    patterns_matched: list[str] = field(default_factory=list)
    raw_message: str = ""


def _job_to_surface(job_name: str) -> str:
    for surface, jobs in SURFACE_JOBS.items():
        if any(j in job_name.lower() for j in jobs):
            return surface
    return "unknown"


def _classify_log_chunk(job_name: str, step_name: str, log_text: str) -> list[ClassifiedError]:
    errors: list[ClassifiedError] = []
    surface = _job_to_surface(job_name)
    seen_categories: set[str] = set()

    for category, pattern, description in CATEGORY_RULES:
        matches = re.findall(pattern, log_text, re.IGNORECASE | re.MULTILINE)
        if not matches:
            continue

        # Deduplicate same category from same job
        key = f"{job_name}::{category}"
        if key in seen_categories:
            continue
        seen_categories.add(key)

        # Extract a meaningful excerpt (up to 3 matching lines)
        lines = log_text.split("\n")
        excerpt_lines = [
            ln.strip() for ln in lines
            if re.search(pattern, ln, re.IGNORECASE)
        ][:3]
        excerpt = "\n".join(excerpt_lines)

        severity = SEVERITY_MAP.get(category, "P2")
        # Check overrides against the matched excerpt only, not the full log,
        # so a CVE elsewhere in the job doesn't contaminate unrelated errors.
        for override_pattern, override_sev in SEVERITY_OVERRIDE:
            if re.search(override_pattern, excerpt, re.IGNORECASE):
                severity = override_sev
                break

        errors.append(ClassifiedError(
            job=job_name,
            step=step_name,
            category=category,
            severity=severity,
            description=description,
            log_excerpt=excerpt[:500],
            surface=surface,
            patterns_matched=[pattern],
            raw_message=matches[0] if isinstance(matches[0], str) else str(matches[0]),
        ))

    return errors


def classify(run_data: dict[str, Any], surface_filter: str = "all") -> dict[str, Any]:
    all_errors: list[ClassifiedError] = []

    for job in run_data.get("jobs", []):
        job_name: str = job.get("name", "unknown")
        job_status: str = job.get("conclusion", "")

        if job_status not in ("failure", "cancelled"):
            continue

        # Prefer the job-level log (fetched once per job by fetch_run_data.py).
        # Fall back to per-step logs for backwards compatibility.
        job_log: str = job.get("log", "")
        if job_log:
            errors = _classify_log_chunk(job_name, "job log", job_log)
            all_errors.extend(errors)
        else:
            for step in job.get("steps", []):
                step_name: str = step.get("name", "unknown")
                log_text: str = step.get("log", "")
                if not log_text:
                    continue
                errors = _classify_log_chunk(job_name, step_name, log_text)
                all_errors.extend(errors)

    # Apply surface filter
    if surface_filter != "all":
        all_errors = [e for e in all_errors if e.surface == surface_filter or e.surface == "unknown"]

    # Build severity summary
    severity_counts: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for e in all_errors:
        severity_counts[e.severity] = severity_counts.get(e.severity, 0) + 1

    # Determine highest severity present
    top_severity = "P3"
    for sev in ("P0", "P1", "P2"):
        if severity_counts.get(sev, 0) > 0:
            top_severity = sev
            break

    summary_parts = []
    for sev, count in severity_counts.items():
        if count:
            summary_parts.append(f"{count}×{sev}")
    summary = f"{len(all_errors)} error(s): {', '.join(summary_parts)}" if all_errors else "No errors found"

    return {
        "total_errors": len(all_errors),
        "top_severity": top_severity,
        "severity_counts": severity_counts,
        "summary": summary,
        "errors": [asdict(e) for e in all_errors],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify CI errors from run data")
    parser.add_argument("--input",   required=True, help="Path to run_data.json")
    parser.add_argument("--output",  required=True, help="Path to write classified_errors.json")
    parser.add_argument("--surface", default="all",  help="Surface filter (default: all)")
    args = parser.parse_args()

    run_data = json.loads(Path(args.input).read_text())
    result = classify(run_data, args.surface)
    Path(args.output).write_text(json.dumps(result, indent=2))

    print(f"Classified {result['total_errors']} error(s). Top severity: {result['top_severity']}")
    print(f"Summary: {result['summary']}")

    # Exit non-zero if P0 found (lets workflow step fail loudly)
    if result["top_severity"] == "P0":
        sys.exit(2)


if __name__ == "__main__":
    main()
