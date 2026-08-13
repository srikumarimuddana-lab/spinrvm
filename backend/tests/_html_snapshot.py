"""Golden-file snapshot assertions for rendered email bodies.

Addresses ACTION_ITEMS.md's N12: every existing email test (``test_email_layout.py``,
``test_receipt_shell_snapshot.py``, etc.) asserts individual substrings ("does the
brand colour appear", "is the GST line present") but nothing pins the **whole**
rendered document. A change that breaks the table structure, drops a closing tag,
or silently loses a style attribute the substring tests don't happen to check for
passes clean today. This module is the missing net: it diffs the full rendered
HTML/text against a committed golden file and fails loudly on any drift, expected
or not — a real content change is expected to touch its golden file in the same
diff, making the change visible in review rather than silent.

This does **not** catch actual rendering in a mail client (Gmail/Outlook/Apple
Mail quirks) — that gap is real and out of scope for a unit test; see N12's own
text. What it catches is *any* byte-level change to what we generate, which is
the layer we control and the layer most regressions (a broken merge, an
accidental style removal, a template edit that reflows content) actually happen
in.

Usage
-----
    from tests._html_snapshot import assert_snapshot

    def test_something():
        assert_snapshot("my_email_case", rendered_html)

Snapshots live in ``tests/snapshots/email/<name>.html`` (or ``.txt`` — the
extension is inferred from whether the content looks like markup). To
(re)generate them after a deliberate change, run once with the update flag set:

    SPINR_UPDATE_EMAIL_SNAPSHOTS=1 pytest tests/test_email_snapshots.py -q

Review the resulting diff in the changed ``tests/snapshots/email/*`` files like
any other code change before committing — a passing snapshot update is not
itself a review, it just makes deliberate changes reproducible instead of
hand-transcribed.
"""

from __future__ import annotations

import os
from pathlib import Path

_SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "email"
_UPDATE_ENV_VAR = "SPINR_UPDATE_EMAIL_SNAPSHOTS"


def _snapshot_path(name: str, content: str) -> Path:
    ext = ".html" if "<" in content and ">" in content else ".txt"
    return _SNAPSHOT_DIR / f"{name}{ext}"


def assert_snapshot(name: str, content: str) -> None:
    """Assert ``content`` matches the committed golden file for ``name``.

    - Missing golden file: writes it and passes. This is deliberate (a brand
      new snapshot has nothing to diff against yet) — the new file itself
      shows up in ``git status``/the PR diff, so it still gets reviewed once,
      same as any other new test fixture.
    - Existing golden file, content differs: fails with both the file path and
      an inline diff, unless ``SPINR_UPDATE_EMAIL_SNAPSHOTS=1`` is set, in
      which case it overwrites the file and passes — for deliberate updates
      only, never as a default CI behavior.
    """
    path = _snapshot_path(name, content)
    update = os.environ.get(_UPDATE_ENV_VAR) == "1"

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return

    expected = path.read_text(encoding="utf-8")
    if content == expected:
        return

    if update:
        path.write_text(content, encoding="utf-8")
        return

    diff = _unified_diff(expected, content, path.name)
    raise AssertionError(
        f"Email snapshot '{name}' drifted from {path.relative_to(_SNAPSHOT_DIR.parent.parent)}.\n"
        f"If this change is intentional, re-run with {_UPDATE_ENV_VAR}=1 to update the "
        f"golden file, then review the diff below (and the file's own diff) before committing:\n\n{diff}"
    )


def _unified_diff(expected: str, actual: str, filename: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"{filename} (golden)",
            tofile=f"{filename} (rendered)",
        )
    )
