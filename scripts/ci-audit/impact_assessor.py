"""
Assess the impact / blast radius of classified CI errors.

Input:  classified_errors.json from error_classifier.py
Output: impact_report.json with affected features, user risk, and blast radius
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

# ─── Feature map: surface → feature domain ───────────────────────────────────

SURFACE_FEATURES: dict[str, list[str]] = {
    "backend": [
        "auth / OTP / JWT",
        "ride dispatch / state machine",
        "fare calculation",
        "payments / Stripe",
        "wallet (consumer + corporate)",
        "corporate billing layer",
        "driver document management",
        "notifications (FCM / Twilio)",
        "WebSocket real-time events",
        "admin dashboard API",
        "surge pricing engine",
        "scheduled rides",
    ],
    "rider-app": [
        "ride booking flow",
        "payment / wallet UI",
        "ride tracking map",
        "profile / account management",
        "ride history",
    ],
    "driver-app": [
        "ride acceptance / rejection",
        "navigation / directions",
        "earnings dashboard",
        "document upload",
        "availability toggle",
    ],
    "admin-dashboard": [
        "fleet operations",
        "driver management",
        "corporate accounts",
        "analytics / reporting",
        "settings management",
        "pricing configuration",
    ],
    "security": [
        "authentication security",
        "data privacy",
        "API security",
        "secret management",
        "supply chain integrity",
    ],
    "deploy": [
        "production availability",
        "API reachability",
        "mobile app updates",
    ],
}

# ─── User impact classification ───────────────────────────────────────────────

USER_IMPACT: dict[str, dict[str, Any]] = {
    "P0": {
        "user_facing": True,
        "scope": "All users",
        "severity_label": "CRITICAL",
        "description": "Production service degraded or broken. Immediate action required.",
        "response_sla": "< 1 hour",
    },
    "P1": {
        "user_facing": True,
        "scope": "Users on affected surface",
        "severity_label": "HIGH",
        "description": "Feature broken or PR blocked from merging. Fix in current sprint.",
        "response_sla": "< 4 hours",
    },
    "P2": {
        "user_facing": False,
        "scope": "Developer experience / CI pipeline",
        "severity_label": "MEDIUM",
        "description": "Non-blocking issue degrading CI signal. Fix in next sprint.",
        "response_sla": "< 1 week",
    },
    "P3": {
        "user_facing": False,
        "scope": "Code quality / technical debt",
        "severity_label": "LOW",
        "description": "Informational. No immediate user impact.",
        "response_sla": "Backlog",
    },
}

# ─── Category-to-feature impact mapping ──────────────────────────────────────

CATEGORY_IMPACT_NOTES: dict[str, str] = {
    "test":       "Confirms functional regression — the feature under test is not working as expected.",
    "coverage":   "Test coverage gap means undetected regressions may ship to production.",
    "lint":       "Code quality issue — does not directly break functionality but increases bug risk.",
    "security":   "Security posture degraded. May expose user data, allow auth bypass, or enable supply-chain attacks.",
    "build":      "Artefact not produced. Dependent deployments are blocked.",
    "deploy":     "Production service unavailable or update not delivered to users.",
    "dependency": "CI environment broken. No tests or builds can run until resolved.",
    "infra":      "CI infrastructure issue. Tests may produce false results.",
}


def _get_changed_files_from_git(repo_root: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, cwd=repo_root
        )
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        return []


def _map_files_to_surfaces(changed_files: list[str]) -> dict[str, list[str]]:
    surface_files: dict[str, list[str]] = {}
    prefix_map = {
        "backend/":         "backend",
        "rider-app/":       "rider-app",
        "driver-app/":      "driver-app",
        "admin-dashboard/": "admin-dashboard",
        "frontend/":        "frontend",
        "shared/":          "shared",
        ".github/":         "ci",
    }
    for filepath in changed_files:
        for prefix, surface in prefix_map.items():
            if filepath.startswith(prefix):
                surface_files.setdefault(surface, []).append(filepath)
                break
    return surface_files


def _assess_error(error: dict[str, Any], changed_files: list[str]) -> dict[str, Any]:
    category  = error.get("category", "unknown")
    severity  = error.get("severity", "P2")
    surface   = error.get("surface", "unknown")

    affected_features = SURFACE_FEATURES.get(surface, ["unknown"])
    user_impact       = USER_IMPACT.get(severity, USER_IMPACT["P3"])
    impact_note       = CATEGORY_IMPACT_NOTES.get(category, "Impact unknown.")

    # Identify changed files relevant to this surface
    surface_files = _map_files_to_surfaces(changed_files).get(surface, [])

    # Estimate blast radius: how many features in the surface are potentially affected
    if category in ("security", "deploy"):
        blast_radius = "FULL"
        blast_features = affected_features
    elif category == "build":
        blast_radius = "HIGH"
        blast_features = affected_features[:len(affected_features)//2 + 1]
    elif category == "test":
        blast_radius = "PARTIAL"
        blast_features = affected_features[:3]
    else:
        blast_radius = "LOW"
        blast_features = []

    return {
        "error_ref": f"{error['job']}::{error['category']}",
        "surface": surface,
        "category": category,
        "severity": severity,
        "blast_radius": blast_radius,
        "affected_features": blast_features,
        "user_impact": user_impact,
        "impact_note": impact_note,
        "changed_files_in_surface": surface_files[:10],
        "response_sla": user_impact["response_sla"],
    }


def assess(errors_data: dict[str, Any], repo_root: str) -> dict[str, Any]:
    errors = errors_data.get("errors", [])
    changed_files = _get_changed_files_from_git(repo_root)

    assessments = [_assess_error(e, changed_files) for e in errors]

    # Aggregate highest blast radius
    blast_order = {"FULL": 4, "HIGH": 3, "PARTIAL": 2, "LOW": 1, "NONE": 0}
    top_blast = max(
        (a["blast_radius"] for a in assessments),
        key=lambda b: blast_order.get(b, 0),
        default="NONE",
    )

    # Unique affected surfaces
    affected_surfaces = list({a["surface"] for a in assessments})

    # Unique affected features (deduplicated)
    all_features: list[str] = []
    seen: set[str] = set()
    for a in assessments:
        for f in a.get("affected_features", []):
            if f not in seen:
                seen.add(f)
                all_features.append(f)

    p0_count = sum(1 for e in errors if e.get("severity") == "P0")
    is_production_at_risk = p0_count > 0 or errors_data.get("top_severity") == "P0"

    return {
        "total_assessments": len(assessments),
        "top_blast_radius": top_blast,
        "affected_surfaces": affected_surfaces,
        "all_affected_features": all_features,
        "is_production_at_risk": is_production_at_risk,
        "p0_count": p0_count,
        "changed_files": changed_files,
        "assessments": assessments,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Assess CI error impact")
    parser.add_argument("--errors",    required=True, help="Path to classified_errors.json")
    parser.add_argument("--repo-root", required=True, help="Repo root for git context")
    parser.add_argument("--output",    required=True, help="Path to write impact_report.json")
    args = parser.parse_args()

    errors_data = json.loads(Path(args.errors).read_text())
    result = assess(errors_data, args.repo_root)
    Path(args.output).write_text(json.dumps(result, indent=2))

    print(f"Impact assessed: {result['total_assessments']} error(s)")
    print(f"Top blast radius: {result['top_blast_radius']}")
    print(f"Affected surfaces: {result['affected_surfaces']}")
    if result["is_production_at_risk"]:
        print("⚠  PRODUCTION IS AT RISK — P0 errors present")


if __name__ == "__main__":
    main()
