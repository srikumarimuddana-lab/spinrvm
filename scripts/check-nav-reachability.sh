#!/usr/bin/env bash
#
# check-nav-reachability.sh
#
# Usage: scripts/check-nav-reachability.sh
#
# Mechanical, grep-based reachability check. For every screen file under
# rider-app/app/, driver-app/app/driver/, and
# admin-dashboard/src/app/dashboard/, this checks whether anything else in
# that surface (excluding the screen's own file and its test files)
# references its route via a router.push/router.replace/Link href call, or
# expo-router's object-form `router.push({ pathname: '/x', ... })` (or, for
# admin-dashboard, a sidebar/nav/command-palette href entry). A screen
# with zero such references is reported as "orphaned" — built and shipped,
# but unreachable from any in-app navigation. Exits non-zero if any are
# found, so it can be wired into CI later (not done by this script).
#
# This is a deliberate heuristic, not a route-graph/AST parser: a full
# parser would be more precise but is unjustified complexity for a
# periodic sanity check, not a hard merge gate (see CLAUDE.md's
# simplicity-first principle). Known limitations:
#   - only catches routes referenced as a literal quoted string on the
#     same line as router.push/router.replace/href= /href:/<Link — a route
#     built purely from a dynamic template (e.g. `${base}/${slug}`) can
#     read as orphaned even if it's reachable at runtime. Admin-dashboard
#     dynamic segments (folders named `[id]`) are handled by checking the
#     static path *prefix* up to the dynamic segment instead of the exact
#     route, which is intentionally looser (fewer false positives, some
#     loss of precision).
#   - a route mentioned only in a code comment or an unrelated string
#     (e.g. a mocked API path in a test fixture) will not be miscounted as
#     a reference, because we also exclude e2e/ fixture directories and
#     require a navigation keyword on the same line — but a comment that
#     happens to satisfy both could still slip through in principle.
#   - tab-bar screens (files under a `(tabs)/` group) are treated as
#     reachable by definition — the tab bar itself is their nav entry —
#     and are not checked.
#   - admin-dashboard's `page.tsx` files are the unit of "screen"; sibling
#     `loading.tsx`/`error.tsx`/`_components/` files are not screens.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ORPHANS=()

# A file is excluded from the "reference" search regardless of surface:
# it's a test file, an e2e fixture/spec, or a Next.js route-support file
# that is never itself a navigation reference.
is_excluded_ref_file() {
    local f="$1"
    case "$f" in
        *.test.ts|*.test.tsx|*.spec.ts|*.spec.tsx) return 0 ;;
        */__tests__/*|*/e2e/*) return 0 ;;
        *) return 1 ;;
    esac
}

# Search $surface_dir for a navigation reference to $route, excluding
# $screen_file (the screen's own file) and any test/e2e file. Prints
# matching lines (for visibility) and returns 0 if any reference is found.
find_reference() {
    local route="$1" surface_dir="$2" screen_file="$3"
    local raw
    raw=$(grep -rnE -- "['\"\`]${route}([^A-Za-z0-9_/-]|\$)" "$surface_dir" \
            --include='*.ts' --include='*.tsx' 2>/dev/null \
        | grep -E 'router\.(push|replace)|href[[:space:]]*[:=]|pathname[[:space:]]*:|<Link' || true)

    [[ -z "$raw" ]] && return 1

    local found=1
    while IFS= read -r line; do
        local file="${line%%:*}"
        [[ "$file" == "$screen_file" ]] && continue
        is_excluded_ref_file "$file" && continue
        found=0
    done <<< "$raw"
    return $found
}

check_screen() {
    local screen_file="$1" route="$2" surface_dir="$3"
    if ! find_reference "$route" "$surface_dir" "$screen_file"; then
        ORPHANS+=("$screen_file  (route: $route)")
    fi
}

echo "== rider-app (rider-app/app) =="
while IFS= read -r -d '' f; do
    rel="${f#"$REPO_ROOT"/}"
    case "$rel" in
        */\(tabs\)/*) continue ;;   # tab-bar entries: reachable by definition
        rider-app/app/_layout.tsx) continue ;;
        rider-app/app/index.tsx) continue ;;  # app entry point, not navigated to
    esac
    base="$(basename "$f" .tsx)"
    # Search the whole rider-app tree, not just app/ — nav calls to a
    # screen commonly live in components/ or hooks/, not only in other
    # screen files.
    check_screen "$rel" "/${base}" "rider-app"
done < <(find "$REPO_ROOT/rider-app/app" -maxdepth 1 -type f -name '*.tsx' -print0)

echo "== driver-app (driver-app/app/driver) =="
while IFS= read -r -d '' f; do
    rel="${f#"$REPO_ROOT"/}"
    case "$rel" in
        */\(tabs\)/*) continue ;;
        driver-app/app/driver/_layout.tsx) continue ;;
    esac
    base="$(basename "$f" .tsx)"
    check_screen "$rel" "/driver/${base}" "driver-app"
done < <(find "$REPO_ROOT/driver-app/app/driver" -maxdepth 1 -type f -name '*.tsx' -print0)

echo "== admin-dashboard (admin-dashboard/src/app/dashboard) =="
while IFS= read -r -d '' f; do
    rel="${f#"$REPO_ROOT"/}"
    route_path="${rel#admin-dashboard/src/app/}"
    route_path="${route_path%/page.tsx}"
    route="/${route_path}"
    # Dynamic segment (e.g. .../[id]/...): the literal folder name never
    # appears in a caller's quoted string, so check the static prefix up
    # to the dynamic segment instead of the exact route.
    if [[ "$route" == *"["* ]]; then
        route="${route%%\[*}"
    fi
    check_screen "$rel" "$route" "admin-dashboard/src"
done < <(find "$REPO_ROOT/admin-dashboard/src/app/dashboard" -type f -name 'page.tsx' -print0)

echo ""
if [[ ${#ORPHANS[@]} -eq 0 ]]; then
    echo "No orphaned screens found."
    exit 0
fi

echo "Orphaned screens (no in-app navigation reference found):"
for o in "${ORPHANS[@]}"; do
    echo "  - $o"
done
exit 1
