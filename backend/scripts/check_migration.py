#!/usr/bin/env python3
"""
check_migration.py — Spinr migration safety checker.

Runs the same checks as the GitHub Actions migration-check workflow so
developers can catch problems before pushing.

Usage:
    python backend/scripts/check_migration.py backend/migrations/64_foo.sql
    python backend/scripts/check_migration.py backend/migrations/*.sql
    python backend/scripts/check_migration.py  # checks all uncommitted .sql files

Exit codes:
    0  all hard checks pass (warnings may still be printed)
    1  one or more hard checks failed
"""

import re
import subprocess
import sys
from pathlib import Path

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

STATUS_OK = "✅"
STATUS_WARN = "⚠️ "
STATUS_FAIL = "❌"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = REPO_ROOT / "backend" / "migrations"


def seq_num(filename: str) -> int | None:
    """Return the leading integer prefix of a migration filename, or None."""
    m = re.match(r"^(\d+)_", Path(filename).name)
    return int(m.group(1)) if m else None


def existing_seq_numbers(exclude: list[str] | None = None) -> list[int]:
    """All sequence numbers already present in the migrations directory."""
    exclude_names = {Path(p).name for p in (exclude or [])}
    nums = []
    for f in MIGRATIONS_DIR.iterdir():
        if f.suffix == ".sql" and f.name not in exclude_names:
            n = seq_num(f.name)
            if n is not None:
                nums.append(n)
    return sorted(nums)


def file_is_committed(path: Path) -> bool:
    """Return True if the file exists in the most recent commit on main."""
    try:
        cmd = ["git", "cat-file", "-e", f"origin/main:{path.relative_to(REPO_ROOT)}"]  # noqa: S607
        result = subprocess.run(cmd, capture_output=True, cwd=REPO_ROOT)  # noqa: S603
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Individual checks (each returns a list of Result tuples)
# ---------------------------------------------------------------------------

Row = tuple[str, str, str, str]  # (filename, check, icon, message)


def check_naming(filename: str) -> Row:
    name = Path(filename).name
    if re.match(r"^\d{2,}_[a-z0-9_]+\.sql$", name):
        return (name, "naming", STATUS_OK, "matches NN_description.sql")
    return (
        name,
        "naming",
        STATUS_FAIL,
        f"'{name}' does not match ^\\d{{2,}}_[a-z0-9_]+\\.sql$",
    )


def check_sequence(filename: str, existing_nums: list[int]) -> Row:
    name = Path(filename).name
    file_num = seq_num(name)
    if file_num is None or not existing_nums:
        return (name, "sequence", STATUS_OK, "could not determine sequence — skipped")
    expected = max(existing_nums) + 1
    if file_num == expected:
        return (name, "sequence", STATUS_OK, f"prefix {file_num} is next after {max(existing_nums)}")
    if file_num < expected:
        return (
            name,
            "sequence",
            STATUS_WARN,
            f"prefix {file_num} collides with or precedes existing max {max(existing_nums)} (expected {expected})",
        )
    return (
        name,
        "sequence",
        STATUS_WARN,
        f"sequence gap — expected {expected}, got {file_num} (numbers missing between {max(existing_nums)} and {file_num})",
    )


def check_rls(filename: str, content: str) -> Row:
    name = Path(filename).name
    if not re.search(r"\bCREATE\s+TABLE\b", content, re.IGNORECASE):
        return (name, "RLS", STATUS_OK, "no CREATE TABLE — RLS check skipped")
    has_rls = bool(re.search(r"\bROW\s+LEVEL\s+SECURITY\b", content, re.IGNORECASE))
    has_policy = bool(re.search(r"\bCREATE\s+POLICY\b", content, re.IGNORECASE))
    if has_rls and has_policy:
        return (name, "RLS", STATUS_OK, "CREATE TABLE has ROW LEVEL SECURITY + CREATE POLICY")
    missing = []
    if not has_rls:
        missing.append("ROW LEVEL SECURITY")
    if not has_policy:
        missing.append("CREATE POLICY")
    return (
        name,
        "RLS",
        STATUS_FAIL,
        f"CREATE TABLE without {' and '.join(missing)}",
    )


def check_rollback(filename: str, content: str) -> Row:
    name = Path(filename).name
    if re.search(r"^--\s+[Rr]ollback:", content, re.MULTILINE):
        return (name, "rollback-comment", STATUS_OK, "rollback comment found")
    return (
        name,
        "rollback-comment",
        STATUS_FAIL,
        "missing '-- rollback:' or '-- Rollback:' comment",
    )


def check_dangerous(filename: str, content: str) -> Row:
    name = Path(filename).name
    dangerous = []
    if re.search(r"\bDROP\s+TABLE\b", content, re.IGNORECASE):
        dangerous.append("DROP TABLE")
    if re.search(r"\bTRUNCATE\b", content, re.IGNORECASE):
        dangerous.append("TRUNCATE")
    if re.search(r"\bALTER\s+TABLE\b.*\bDROP\s+COLUMN\b", content, re.IGNORECASE):
        dangerous.append("ALTER TABLE ... DROP COLUMN")
    if dangerous:
        return (
            name,
            "dangerous-ops",
            STATUS_WARN,
            f"contains: {', '.join(dangerous)} — verify intent",
        )
    return (name, "dangerous-ops", STATUS_OK, "no destructive operations")


def check_append_only(filename: str) -> Row | None:
    """Return a FAIL row if the file already exists on main, else None."""
    path = REPO_ROOT / filename if not Path(filename).is_absolute() else Path(filename)
    name = path.name
    if file_is_committed(path):
        return (
            name,
            "append-only",
            STATUS_FAIL,
            "Append-only: never edit a merged migration",
        )
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_checks(paths: list[str]) -> int:
    """Run all checks on the given list of file paths. Return exit code."""
    rows: list[Row] = []
    errors: list[str] = []
    warnings: list[str] = []

    # Pre-compute existing sequence numbers excluding the files under check
    existing_nums = existing_seq_numbers(exclude=paths)

    for rel_path in paths:
        # Resolve to absolute: if given as relative, anchor to CWD first; if
        # that doesn't exist try anchoring to REPO_ROOT (useful when the script
        # is invoked from a different working directory).
        raw = Path(rel_path)
        if raw.is_absolute():
            path = raw
        elif raw.exists():
            path = raw.resolve()
        else:
            path = (REPO_ROOT / rel_path).resolve()
        filename = path.name

        # ── Append-only guard ─────────────────────────────────────────────────
        ao_row = check_append_only(rel_path)
        if ao_row:
            rows.append(ao_row)
            errors.append(f"{filename}: {ao_row[3]}")
            continue  # no further checks for edited-existing files

        # ── Naming ────────────────────────────────────────────────────────────
        row = check_naming(filename)
        rows.append(row)
        if row[2] == STATUS_FAIL:
            errors.append(f"{filename}: {row[3]}")

        # ── Sequence ──────────────────────────────────────────────────────────
        row = check_sequence(filename, existing_nums)
        rows.append(row)
        if row[2] == STATUS_WARN:
            warnings.append(f"{filename}: {row[3]}")

        # ── Read content ──────────────────────────────────────────────────────
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            rows.append((filename, "readable", STATUS_FAIL, "file not found"))
            errors.append(f"{filename}: file not found")
            continue

        # ── RLS ───────────────────────────────────────────────────────────────
        row = check_rls(filename, content)
        rows.append(row)
        if row[2] == STATUS_FAIL:
            errors.append(f"{filename}: {row[3]}")

        # ── Rollback comment ──────────────────────────────────────────────────
        row = check_rollback(filename, content)
        rows.append(row)
        if row[2] == STATUS_FAIL:
            errors.append(f"{filename}: {row[3]}")

        # ── Dangerous operations ──────────────────────────────────────────────
        row = check_dangerous(filename, content)
        rows.append(row)
        if row[2] == STATUS_WARN:
            warnings.append(f"{filename}: {row[3]}")

    # ── Print summary table ───────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  MIGRATION SAFETY CHECK SUMMARY")
    print("=" * 72)
    print(f"  {'File':<40} {'Check':<22} Result")
    print("-" * 72)
    for fname, check, icon, msg in rows:
        display = fname[:38] + ".." if len(fname) > 40 else fname
        print(f"  {display:<40} {check:<22} {icon}  {msg}")
    print("=" * 72)

    if warnings:
        print()
        print(f"{YELLOW}WARNINGS ({len(warnings)}):{RESET}")
        for w in warnings:
            print(f"  {STATUS_WARN} {w}")

    if errors:
        print()
        print(f"{RED}FAILURES ({len(errors)}):{RESET}")
        for e in errors:
            print(f"  {STATUS_FAIL} {e}")
        print()
        print(f"{RED}Fix the above before pushing.{RESET}")
        return 1

    print()
    print(f"{GREEN}All hard checks passed.{RESET}")
    if warnings:
        print(f"{YELLOW}Review warnings above before merging.{RESET}")
    return 0


def auto_detect_changed() -> list[str]:
    """
    When called with no arguments, detect .sql files that are staged/unstaged
    in the working tree compared to HEAD (useful for local pre-commit runs).
    """
    diff_cmd = ["git", "diff", "--name-only", "--diff-filter=ACDMRT", "HEAD", "--", "backend/migrations/*.sql"]  # noqa: S603 S607
    staged_cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACDMRT", "--", "backend/migrations/*.sql"]  # noqa: S603 S607
    result = subprocess.run(diff_cmd, capture_output=True, text=True, cwd=REPO_ROOT)  # noqa: S603
    staged = subprocess.run(staged_cmd, capture_output=True, text=True, cwd=REPO_ROOT)  # noqa: S603
    paths = set(result.stdout.splitlines()) | set(staged.stdout.splitlines())
    return sorted(str(REPO_ROOT / p) for p in paths if p)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = auto_detect_changed()
        if not targets:
            print("No changed migration files detected. Pass file paths explicitly or stage changes.")
            sys.exit(0)
        print(f"Auto-detected {len(targets)} changed migration file(s).")

    sys.exit(run_checks(targets))
