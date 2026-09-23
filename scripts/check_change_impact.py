"""Require a substantive Change Impact Log for sensitive-surface diffs."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SENSITIVE_MARKERS = (
    "backend/routes/rides", "backend/services/dispatch_service",
    "backend/services/fare_service", "backend/routes/payments",
    "backend/routes/webhooks", "backend/routes/auth.py",
    "backend/routes/corporate", "backend/services/corporate",
    "backend/routes/wallet", "backend/routes/safety.py",
    "backend/utils/insurance_periods", "backend/services/payment_service",
    "backend/routes/drivers/ride_", "backend/routes/drivers/status",
    "backend/routes/drivers/payouts", "backend/migrations/",
    "backend/utils/payment_retry.py", "backend/utils/preauth_capture.py",
    "backend/utils/orphaned_hold_reconciler.py", "backend/utils/auto_payout.py",
    "backend/utils/corporate_autotopup.py", "backend/utils/redis_client.py",
)
FIELDS = {
    "Root cause": "root cause",
    "Risk & impact": "risk and impact",
    "User experience": "user experience",
    "Rollback plan": "rollback plan",
    "Verification performed": "verification performed",
}
_PLACEHOLDER = re.compile(
    r"^(?:\s*|[-*|`_\s]+|\[\s*[x ]?\s*\]|<[^>]+>|"
    r"(?:tbd|todo|fill\s+in|n/?a\s*\(.*\)|\.{3,}))$",
    re.IGNORECASE,
)
_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*#*\s*$")


def is_sensitive(paths: list[str]) -> bool:
    return any(marker in path for path in paths for marker in SENSITIVE_MARKERS)


def _plain(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([ xX])\]", "", text)
    return re.sub(r"[>*_#|]", " ", text).strip()


def _sections(text: str) -> dict[str, str]:
    matches = list(_HEADING.finditer(text))
    out = {}
    for index, match in enumerate(matches):
        title = re.sub(r"^\s*\d+[.)]?\s*", "", match.group(2)).lower().replace("-", " ")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out[title] = text[match.end():end]
    return out


def _field_content(sections: dict[str, str], expected: str) -> str:
    for title, content in sections.items():
        normalized = re.sub(r"&", " and ", title)
        normalized = re.sub(r"[^a-z0-9 ]", " ", normalized)
        normalized = " ".join(normalized.split())
        expected = re.sub(r"&", " and ", expected)
        wanted = " ".join(re.sub(r"[^a-z0-9 ]", " ", expected).split())
        if wanted in normalized:
            return content
    return ""


def _record(paths: list[str], text: str) -> set[str]:
    sections = _sections(text)
    for label, heading in FIELDS.items():
        content = _plain(_field_content(sections, heading))
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        lines = [line for line in lines if not _PLACEHOLDER.fullmatch(line)]
        # Drop the template's instructional prompts; they are not evidence that
        # the author supplied an actual risk/result.
        lines = [line for line in lines if not line.lower().startswith((
            "one or two sentences:", "why it happens", "what else reads/writes",
            "could this regress", "blast radius:", "who sees a difference",
            "is the change visible", "any copy/notification", "how to revert",
            "feature flag to flip off", "config/", "migration rollback",
            "automated tests run", "manual repro steps", "blast-radius grep",
            "reviewed against", "feature-flagged",
        ))]
        if not lines or len(" ".join(lines)) < 12:
            return set()
    file_section = _field_content(sections, "files modified")
    return {path for path in paths if path in file_section}


def check_change_impact(paths: list[str], records: list[str]) -> list[str]:
    touched = {path for path in paths if any(marker in path for marker in SENSITIVE_MARKERS)}
    covered = set().union(*(_record(touched, text) for text in records)) if records else set()
    return sorted(touched - covered)


def changed_paths(base: str, head: str) -> list[str]:
    merge_base = subprocess.run(
        ["git", "merge-base", base, head], check=True, capture_output=True, text=True
    ).stdout.strip()
    output = subprocess.run(
        ["git", "diff", "--name-only", merge_base, head], check=True, capture_output=True, text=True
    ).stdout
    return [path for path in output.splitlines() if path]


def main() -> int:
    import os

    paths = changed_paths(os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
    body = os.environ.get("PR_BODY") or ""
    candidates = [body] if body.strip() else []
    for path in paths:
        if path.startswith("docs/change-log/") and path.endswith(".md"):
            candidates.append(Path(path).read_text(encoding="utf-8"))
    uncovered = check_change_impact(paths, candidates)
    if uncovered:
        print("FAIL: sensitive-surface PR needs a substantive Change Impact Log.")
        print("Uncovered sensitive paths: " + ", ".join(uncovered))
        return 1
    if is_sensitive(paths):
        print("PASS: substantive Change Impact Log covers the sensitive diff.")
    else:
        print("PASS: no sensitive-surface files changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
