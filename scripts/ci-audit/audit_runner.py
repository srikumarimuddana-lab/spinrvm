"""
CI Error Audit — main orchestrator (local dev / manual run entry point).

Usage (local):
    python3 scripts/ci-audit/audit_runner.py \
        --run-id 12345678 \
        --repo srikumarimuddana-lab/spinrvm \
        --token ghp_... \
        [--surface backend] \
        [--severity-filter P2]

This is the same pipeline the CI workflow runs, but usable from a developer's machine.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT   = SCRIPTS_DIR.parent.parent


def _run(script: str, args: list[str], description: str) -> int:
    print(f"\n{'─' * 60}")
    print(f"▶  {description}")
    print(f"{'─' * 60}")
    cmd = [sys.executable, str(SCRIPTS_DIR / script)] + args
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode not in (0, 2):  # 2 = P0 warning, not a script error
        print(f"✗  {description} failed (exit {result.returncode})", file=sys.stderr)
    else:
        print(f"✓  {description} complete")
    return result.returncode


def run_full_audit(
    run_id: str,
    repo: str,
    token: str,
    surface: str = "all",
    severity_filter: str = "P1",
    output_dir: Path | None = None,
) -> Path:
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir   = output_dir or (REPO_ROOT / "reports" / "audits")
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path("/tmp") / f"ci-audit-{run_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    run_data_path     = tmp_dir / "run_data.json"
    errors_path       = tmp_dir / "classified_errors.json"
    impact_path       = tmp_dir / "impact_report.json"
    fixes_path        = tmp_dir / "fix_recommendations.json"
    report_tmp_path   = tmp_dir / "audit_report.md"
    crs_path          = tmp_dir / "change_requests.json"

    report_filename   = f"{date_str}-ci-failure-{run_id}.md"
    report_final_path = out_dir / report_filename
    report_logical    = f"reports/audits/{report_filename}"

    # ── Step 1: Fetch run data ─────────────────────────────────────────────
    rc = _run("fetch_run_data.py", [
        "--run-id", run_id,
        "--repo", repo,
        "--token", token,
        "--output", str(run_data_path),
    ], "Fetching workflow run data from GitHub API")
    if rc != 0:
        # Stub an empty run_data so downstream steps can still run
        run_data_path.write_text(json.dumps({"jobs": [], "run_id": run_id}))

    # ── Step 2: Classify errors ────────────────────────────────────────────
    rc = _run("error_classifier.py", [
        "--input",   str(run_data_path),
        "--output",  str(errors_path),
        "--surface", surface,
    ], "Classifying CI errors")

    if not errors_path.exists():
        errors_path.write_text(json.dumps({"errors": [], "top_severity": "P3", "total_errors": 0, "summary": "No errors", "severity_counts": {}}))

    # ── Step 3: Assess impact ──────────────────────────────────────────────
    _run("impact_assessor.py", [
        "--errors",    str(errors_path),
        "--repo-root", str(REPO_ROOT),
        "--output",    str(impact_path),
    ], "Assessing impact and blast radius")

    if not impact_path.exists():
        impact_path.write_text(json.dumps({"assessments": [], "top_blast_radius": "NONE", "affected_surfaces": [], "is_production_at_risk": False}))

    # ── Step 4: Generate fix recommendations ──────────────────────────────
    _run("fix_recommender.py", [
        "--errors", str(errors_path),
        "--impact", str(impact_path),
        "--output", str(fixes_path),
    ], "Generating fix recommendations")

    if not fixes_path.exists():
        fixes_path.write_text(json.dumps({"recommendations": [], "proposed_change_requests": []}))

    # ── Step 5: Generate audit report ─────────────────────────────────────
    errors_data  = json.loads(errors_path.read_text())
    branch       = json.loads(run_data_path.read_text()).get("head_branch", "unknown")
    commit       = json.loads(run_data_path.read_text()).get("head_sha", "unknown")
    workflow_name = json.loads(run_data_path.read_text()).get("workflow_name", "unknown")

    _run("report_generator.py", [
        "--errors",      str(errors_path),
        "--impact",      str(impact_path),
        "--fixes",       str(fixes_path),
        "--workflow",    workflow_name,
        "--run-id",      run_id,
        "--repo",        repo,
        "--branch",      branch,
        "--commit",      commit,
        "--output",      str(report_tmp_path),
        "--report-path", report_logical,
    ], "Generating audit report")

    # ── Step 6: Generate Change Requests ──────────────────────────────────
    _run("change_request_generator.py", [
        "--fixes",  str(fixes_path),
        "--output", str(crs_path),
    ], "Generating Change Request drafts")

    # ── Step 7: Save report to reports/audits/ ────────────────────────────
    if report_tmp_path.exists():
        report_final_path.write_text(report_tmp_path.read_text())
        print(f"\n✅ Audit report saved: {report_final_path}")
    else:
        print("\n⚠  Report file not generated — check errors above.", file=sys.stderr)

    # ── Summary ───────────────────────────────────────────────────────────
    top_sev   = errors_data.get("top_severity", "P3")
    total_err = errors_data.get("total_errors", 0)
    crs       = json.loads(crs_path.read_text()).get("change_requests", []) if crs_path.exists() else []

    print(f"\n{'═' * 60}")
    print(f"  AUDIT COMPLETE")
    print(f"{'═' * 60}")
    print(f"  Top severity : {top_sev}")
    print(f"  Total errors : {total_err}")
    print(f"  Change Reqs  : {len(crs)}")
    print(f"  Report       : {report_final_path}")
    print(f"{'═' * 60}")

    if crs:
        print("\n  Proposed Change Requests (pending approval):")
        for cr in crs:
            print(f"    {cr['cr_id']}: {cr['title']} [{cr['priority']}]")
        print(f"\n  File each CR via: .github/ISSUE_TEMPLATE/ci_change_request.yml")

    return report_final_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full CI Error Audit pipeline locally",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Audit a specific failed run
  python3 scripts/ci-audit/audit_runner.py \\
    --run-id 12345678 \\
    --repo srikumarimuddana-lab/spinrvm \\
    --token ghp_xxx

  # Audit only backend failures, report P2+
  python3 scripts/ci-audit/audit_runner.py \\
    --run-id 12345678 --repo srikumarimuddana-lab/spinrvm \\
    --token ghp_xxx --surface backend --severity-filter P2
        """,
    )
    parser.add_argument("--run-id",          required=True, help="GitHub Actions run ID")
    parser.add_argument("--repo",            required=True, help="GitHub repo (owner/name)")
    parser.add_argument("--token",           help="GitHub token (or set GITHUB_TOKEN env var)")
    parser.add_argument("--surface",         default="all", help="Surface filter")
    parser.add_argument("--severity-filter", default="P1", help="Minimum severity to report")
    parser.add_argument("--output-dir",      help="Directory to write the final report")
    args = parser.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("--token or GITHUB_TOKEN env var required")

    out_dir = Path(args.output_dir) if args.output_dir else None
    run_full_audit(
        run_id=args.run_id,
        repo=args.repo,
        token=token,
        surface=args.surface,
        severity_filter=args.severity_filter,
        output_dir=out_dir,
    )


if __name__ == "__main__":
    main()
