"""The SR-03/SR-11 money-file list and the pre-commit hook's copy of it must
agree.

Three places name "which files are money-handling code, for the money-safety
checks":

  1. `.claude/hooks/pre-commit` check 6's own file list (the arg to awk's
     `split(...)` in the MONEY_FLOAT_AWK program) — the local, blocking
     pre-commit gate.
  2. `.semgrep/spinr-rules.yml`'s `spinr-no-float-in-money` (SR-03) `include`
     list — the CI-blocking float-arithmetic gate.
  3. `.semgrep/spinr-rules.yml`'s `spinr-no-bare-sum-in-money` (SR-11)
     `include` list — the CI-blocking bare-sum() gate.

CLAUDE.md's "Money arithmetic" convention and check 6's own comments both
assert these three never disagree on what counts as a "money file" — nothing
previously checked that. This test parses each of the three lists straight
out of the source files (not a hardcoded copy of any of them, which would
just be a fourth place to drift) and asserts they are identical sets, so a
file added to — or dropped from — one list without the others fails CI
instead of silently widening or narrowing one gate relative to the other two.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRE_COMMIT_HOOK = _REPO_ROOT / ".claude" / "hooks" / "pre-commit"
_SEMGREP_RULES = _REPO_ROOT / ".semgrep" / "spinr-rules.yml"


def _hook_money_files() -> list[str]:
    """Parse the money-file list out of check 6's MONEY_FLOAT_AWK split()."""
    src = _PRE_COMMIT_HOOK.read_text(encoding="utf-8")
    m = re.search(r'split\("([^"]+)",\s*files,\s*" "\)', src)
    assert m, "could not locate the money-file list in .claude/hooks/pre-commit check 6"
    return m.group(1).split(" ")


def _semgrep_rule_include(rule_id: str) -> list[str]:
    """Parse a rule's paths.include list out of .semgrep/spinr-rules.yml."""
    rules = yaml.safe_load(_SEMGREP_RULES.read_text(encoding="utf-8"))["rules"]
    for rule in rules:
        if rule.get("id") == rule_id:
            include = rule.get("paths", {}).get("include")
            assert include, f"{rule_id} has no paths.include list"
            return include
    raise AssertionError(f"rule {rule_id} not found in .semgrep/spinr-rules.yml")


def test_hook_and_semgrep_money_file_lists_are_identical():
    hook_files = _hook_money_files()
    sr03_files = _semgrep_rule_include("spinr-no-float-in-money")
    sr11_files = _semgrep_rule_include("spinr-no-bare-sum-in-money")

    assert hook_files, "pre-commit hook money-file list is empty"
    assert len(hook_files) == len(set(hook_files)), "pre-commit hook money-file list has a duplicate entry"

    # Order-independent, set-based comparison — the three sources have
    # historically listed the same files in the same order, but what
    # actually matters is whether a file is gated by all three checks, not
    # the order it's written in.
    hook_set, sr03_set, sr11_set = set(hook_files), set(sr03_files), set(sr11_files)

    assert hook_set == sr03_set, (
        "pre-commit hook check 6 and SR-03 (spinr-no-float-in-money) money-file "
        f"lists have drifted.\n  hook only: {sorted(hook_set - sr03_set)}\n"
        f"  SR-03 only: {sorted(sr03_set - hook_set)}"
    )
    assert hook_set == sr11_set, (
        "pre-commit hook check 6 and SR-11 (spinr-no-bare-sum-in-money) money-file "
        f"lists have drifted.\n  hook only: {sorted(hook_set - sr11_set)}\n"
        f"  SR-11 only: {sorted(sr11_set - hook_set)}"
    )
