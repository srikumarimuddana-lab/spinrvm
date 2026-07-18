#!/usr/bin/env python3
"""Guard: no test files inside an Expo Router `app/` tree.

Expo Router (SDK 55) generates `require.context(app, true, /.*/)`, which
EAGERLY bundles *every* module under `<app>/app/` into the production JS
bundle. Test files there get pulled in too; when they import Node built-ins
(`fs`, `path`) — as our source-inspection tests do — the iOS/Android EAS build
fails at the "Bundle JavaScript" phase with:

    Unable to resolve module fs from .../app/.../__tests__/X.test.ts
    Import stack: app (require.context)

The EAS summary only says "Unknown error"; the real cause is buried in the
phase log. Tests must live in the app-root `__tests__/` tree (outside `app/`),
where jest (preset `jest-expo`, default testMatch) still discovers them.

Run locally:  python3 scripts/check_mobile_test_placement.py
Exit code 0 = clean, 1 = offending file(s) found.
"""

from __future__ import annotations

import os
import re
import sys

# Expo Router mobile surfaces whose `app/` tree is eagerly bundled by
# require.context. Add new expo-router apps here; do NOT add admin-dashboard
# (Next.js App Router does not bundle test files the same way).
APP_ROOTS = ["driver-app/app", "rider-app/app"]

# Matches a `__tests__/` path segment OR a *.test.* / *.spec.* filename.
TEST_RE = re.compile(r"(^|/)__tests__(/|$)|\.(test|spec)\.[cm]?[jt]sx?$")


def find_offenders(repo_root: str) -> list[str]:
    offenders: list[str] = []
    for app_root in APP_ROOTS:
        abs_root = os.path.join(repo_root, app_root)
        if not os.path.isdir(abs_root):
            continue
        for dirpath, _dirs, files in os.walk(abs_root):
            for fname in files:
                rel = os.path.relpath(os.path.join(dirpath, fname), repo_root)
                rel = rel.replace(os.sep, "/")
                if TEST_RE.search(rel):
                    offenders.append(rel)
    return sorted(offenders)


def main() -> int:
    # Repo root = parent of this script's directory (scripts/).
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = find_offenders(repo_root)

    if offenders:
        print("FAIL: test files found under an Expo Router app/ tree:")
        for path in offenders:
            print(f"  {path}")
        print()
        print("Expo Router's require.context eagerly bundles EVERY module under")
        print("app/, so these are pulled into the production JS bundle. If they")
        print("import Node built-ins (fs/path) the iOS/Android EAS build fails at")
        print("the 'Bundle JavaScript' phase with 'Unable to resolve module fs'.")
        print()
        print("Move them OUT of app/ into the app-root __tests__/ tree, e.g.:")
        print("  driver-app/__tests__/...   rider-app/__tests__/...")
        print("Jest still discovers them there (preset jest-expo, default testMatch).")
        return 1

    print(f"PASS: no test files under app/ ({', '.join(APP_ROOTS)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
