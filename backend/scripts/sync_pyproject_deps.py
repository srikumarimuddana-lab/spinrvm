"""Keep ``backend/pyproject.toml``'s dependency list identical to ``requirements.txt``.

Spinr's dependency source of truth is ``requirements.in`` -> ``requirements.txt``
(pip-compile; see ``.github/workflows/pip-compile-check.yml``). Production builds
from ``requirements.txt`` via ``backend/Dockerfile`` and nothing about that
changes.

``pyproject.toml`` exists only because FastAPI Cloud's deploy path requires one
(``fastapi deploy`` resolves the app from the directory holding
``pyproject.toml``). It is a *mirror*, not a second source of truth: it carries
the exact same pinned versions so the FastAPI Cloud dev tier runs byte-identical
dependencies to production. A dev environment that quietly runs different
package versions than production is worse than no dev environment, because it
produces false confidence.

Two modes::

    python -m backend.scripts.sync_pyproject_deps --write   # regenerate
    python -m backend.scripts.sync_pyproject_deps --check   # verify (CI)

``--check`` exits non-zero and prints the drift when the two disagree.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

BACKEND_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND_DIR / "requirements.txt"
PYPROJECT = BACKEND_DIR / "pyproject.toml"

_START = "# --- BEGIN GENERATED DEPENDENCIES (sync_pyproject_deps.py) ---"
_END = "# --- END GENERATED DEPENDENCIES ---"

# A pip-compile line is `name==version` at column 0; everything indented is a
# `# via ...` annotation, and `-e`/`--` lines are pip directives, not packages.
_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+(?:\[[A-Za-z0-9._,-]+\])?)==([^\s;]+)(.*)$")


def parse_requirements(text: str) -> list[str]:
    """Return ``name==version`` pins from a pip-compile output file, in order."""
    pins: list[str] = []
    for raw in text.splitlines():
        # Indented lines are pip-compile's "# via" annotations; skip them along
        # with comments, blanks, and pip directives (-r, -e, --hash, ...).
        if not raw or raw[0].isspace() or raw.lstrip().startswith(("#", "-")):
            continue
        match = _PIN_RE.match(raw.strip())
        if not match:
            continue
        name, version, trailer = match.groups()
        # Preserve an environment marker if pip-compile emitted one, since
        # dropping it would change what actually gets installed.
        marker = ""
        if ";" in trailer:
            marker = " ;" + trailer.split(";", 1)[1].split("#", 1)[0].rstrip()
        pins.append(f"{name}=={version}{marker}")
    return pins


def render_block(pins: list[str]) -> str:
    body = "\n".join(f'    "{pin}",' for pin in pins)
    return f"{_START}\n{body}\n    {_END}"


def extract_declared(pyproject_text: str) -> list[str]:
    data = tomllib.loads(pyproject_text)
    return list(data.get("project", {}).get("dependencies", []))


def write(pins: list[str]) -> None:
    text = PYPROJECT.read_text()
    if _START not in text or _END not in text:
        raise SystemExit(
            f"{PYPROJECT} is missing the generated-dependency markers.\nExpected to find {_START!r} and {_END!r}."
        )
    prefix, rest = text.split(_START, 1)
    _, suffix = rest.split(_END, 1)
    PYPROJECT.write_text(prefix + render_block(pins) + suffix)
    print(f"Wrote {len(pins)} pinned dependencies to {PYPROJECT.name}.")


def check(pins: list[str]) -> int:
    declared = extract_declared(PYPROJECT.read_text())
    # Compare as sets so pip-compile reordering alone is not a failure, but
    # report the difference in both directions so the fix is obvious.
    missing = sorted(set(pins) - set(declared))
    extra = sorted(set(declared) - set(pins))
    if not missing and not extra:
        print(f"pyproject.toml is in sync with requirements.txt ({len(pins)} pins).")
        return 0

    print("pyproject.toml has drifted from requirements.txt.\n")
    if missing:
        print("  In requirements.txt but NOT in pyproject.toml:")
        for pin in missing:
            print(f"    + {pin}")
    if extra:
        print("  In pyproject.toml but NOT in requirements.txt:")
        for pin in extra:
            print(f"    - {pin}")
    print(
        "\nRegenerate with:\n"
        "  python -m backend.scripts.sync_pyproject_deps --write\n\n"
        "Do NOT hand-edit pyproject.toml's dependency list — requirements.in ->\n"
        "requirements.txt (pip-compile) stays the source of truth. See this\n"
        "module's docstring and docs/runbooks/fastapi-cloud-dev.md."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="Regenerate pyproject.toml.")
    group.add_argument("--check", action="store_true", help="Verify sync; non-zero on drift.")
    args = parser.parse_args()

    if not REQUIREMENTS.exists():
        raise SystemExit(f"Missing {REQUIREMENTS}")
    pins = parse_requirements(REQUIREMENTS.read_text())
    if not pins:
        raise SystemExit(f"Parsed zero pins from {REQUIREMENTS} — refusing to continue.")

    if args.write:
        write(pins)
        return 0
    return check(pins)


if __name__ == "__main__":
    sys.exit(main())
