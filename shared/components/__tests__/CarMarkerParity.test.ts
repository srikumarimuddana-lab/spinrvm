import fs from 'fs';
import path from 'path';

/**
 * Mechanical parity guard for the CarMarker fork (roadmap R11,
 * docs/audit/ride-experience/ROADMAP.md; registry: docs/known-forks.md).
 *
 * driver-app/components/CarMarker.tsx is an intentional fork of this file —
 * driver-app needs course-up-camera props rider-app must not get by default.
 * Everything else is meant to stay identical. Five fixes have now needed
 * manual one-way porting (three tracked by ACTION_ITEMS.md C90, two more
 * found by the 2026-09-12 audit and fixed in this same PR) with nothing
 * catching a missed port — the same shape as the float()-on-NUMERIC bug
 * closed piecemeal five times across B28->B36 with no systemic fix.
 *
 * This test reads both files' `CarMarkerProps` interface as source text
 * (no component import/render — avoids mocking react-native-maps/expo-image
 * just to diff a type) and fails if the two prop surfaces diverge in any
 * way not explicitly declared below. It guards the PUBLIC PROP API
 * specifically — a deliberate, mechanically-diffable scope. It does not
 * (and cannot, via a Jest test in this stack) diff internal implementation
 * logic; an internal-only divergence like R1/R3's still needs a human
 * to notice, which is exactly why both files now carry a header comment
 * pointing at the other (R6) and at docs/known-forks.md.
 *
 * A failure here means one of two things happened, and BOTH require a
 * decision, not a reflexive edit to make the test pass:
 *   1. A new prop was added to one file and should be ported to the other
 *      (the common case — port it, or add a one-line reason to
 *      DRIVER_ONLY_PROPS below if it's legitimately driver-only).
 *   2. `SHARED_PROPS`/`DRIVER_ONLY_PROPS` below is stale relative to a
 *      deliberate, already-reviewed change — update the allowlist in the
 *      same PR as that change, with a comment saying why.
 */

const SHARED_PATH = path.join(__dirname, '../CarMarker.tsx');
const DRIVER_APP_PATH = path.join(__dirname, '../../../driver-app/components/CarMarker.tsx');

// Props both files must have, in the exact same optional/required shape.
// This is the component's shared contract — every prop the shared file
// declares must appear here, and every prop here must appear in BOTH files.
const COMMON_PROPS = [
  'coordinate',
  'heading?',
  'fixTimestampMs?',
  'fixFeed?',
  'size?',
  'zIndex?',
  'identifier?',
  'variant?',
  'imageUri?',
  'routeCoordinates?',
  'ring?',
  'onPositionChange?',
] as const;

// Props driver-app's fork is allowed to have that shared/ does not.
// Each entry needs a reason — this is a reviewed allowlist, not a dumping
// ground. Do not add to it without also updating this comment.
const DRIVER_ONLY_PROPS: Record<string, string> = {
  'onBearingChange?':
    'Course-up-camera bearing callback. Rider-app has no course-up camera; ' +
    'north-up is the correct rider-side convention (see ROADMAP.md R11 constraints).',
  'mapHeadingRef?':
    'Course-up-camera map-heading ref, paired with onBearingChange above.',
  'isOnline?':
    'Vestigial compat prop for the dashboard call site (see its own doc comment in ' +
    'driver-app/components/CarMarker.tsx) — tracked for removal in roadmap item R5, ' +
    'bundled into the next PR that already opens this file rather than removed standalone.',
};

/**
 * Extract the `interface CarMarkerProps { ... }` block and return its
 * top-level prop names (with a trailing `?` preserved for optional props),
 * using the same "closing brace at column 0" heuristic verified by hand
 * against both files' actual indentation style. Nested object types (e.g.
 * `coordinate: { latitude: number; ... }`) are indented and so never match
 * the column-0 anchor, which is what keeps this a top-level-only extraction.
 */
function extractPropNames(filePath: string): string[] {
  const text = fs.readFileSync(filePath, 'utf8');
  const lines = text.split('\n');
  const startIdx = lines.findIndex((l) => l.startsWith('interface CarMarkerProps {'));
  if (startIdx === -1) {
    throw new Error(`Could not find "interface CarMarkerProps {" in ${filePath}`);
  }
  const endIdx = lines.findIndex((l, i) => i > startIdx && /^\}/.test(l));
  if (endIdx === -1) {
    throw new Error(`Could not find the closing brace for CarMarkerProps in ${filePath}`);
  }
  const body = lines.slice(startIdx + 1, endIdx);
  const propNames: string[] = [];
  for (const line of body) {
    const match = /^\s{4}([a-zA-Z_][a-zA-Z0-9_]*\??):/.exec(line);
    if (match) {
      propNames.push(match[1]);
    }
  }
  return propNames;
}

describe('CarMarker fork parity (roadmap R11)', () => {
  it('both files exist where docs/known-forks.md says they do', () => {
    expect(fs.existsSync(SHARED_PATH)).toBe(true);
    expect(fs.existsSync(DRIVER_APP_PATH)).toBe(true);
  });

  it("shared/CarMarker.tsx's props are exactly COMMON_PROPS (no undeclared prop)", () => {
    const sharedProps = extractPropNames(SHARED_PATH);
    expect(new Set(sharedProps)).toEqual(new Set(COMMON_PROPS));
  });

  it("driver-app's fork has every COMMON_PROPS entry, plus only the declared DRIVER_ONLY_PROPS", () => {
    const driverProps = new Set(extractPropNames(DRIVER_APP_PATH));

    const missingFromDriver = COMMON_PROPS.filter((p) => !driverProps.has(p));
    expect(missingFromDriver).toEqual([]);

    const driverOnly = [...driverProps].filter((p) => !(COMMON_PROPS as readonly string[]).includes(p));
    expect(new Set(driverOnly)).toEqual(new Set(Object.keys(DRIVER_ONLY_PROPS)));
  });

  it('every declared DRIVER_ONLY_PROPS entry has a non-empty reason', () => {
    for (const [prop, reason] of Object.entries(DRIVER_ONLY_PROPS)) {
      expect(reason.trim().length).toBeGreaterThan(10);
      void prop;
    }
  });
});
