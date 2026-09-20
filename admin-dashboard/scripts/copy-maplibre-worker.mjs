// MapLibre GL v6's tile-processing Web Worker is normally spawned by
// bundling an `import.meta.url`-derived module URL at build time -- the
// approach Webpack supports but Next.js 16's Turbopack does not, so the
// worker spawns and then never fetches a single tile (blank map canvas,
// controls/attribution only). See
// docs/change-log/2026-09-20-maplibre-gl-cve-upgrade-turbopack-worker-fix.md
// for the full investigation.
//
// The fix is MapLibre's own documented escape hatch: serve the worker as a
// same-origin static file and point the library at it explicitly via
// setWorkerUrl() (src/lib/map/maplibre-base.ts), instead of asking any
// bundler to resolve it automatically.
//
// MapLibre ships the worker as TWO files -- maplibre-gl-worker.mjs imports
// maplibre-gl-shared.mjs via a relative specifier. A plain file copy of both
// works for the browser's fetch of the worker's own top-level script (that
// fetch is unambiguously governed by the `worker-src` CSP directive, which
// this app already sets to `'self'`) -- but the worker's *nested* static
// `import` of the shared chunk is a separate, ambiguous case: per the CSP
// spec itself (see the open "import-src" proposal,
// https://github.com/w3c/webappsec-csp/issues/506, and "Clarify worker-src
// goals", https://github.com/w3c/webappsec-csp/issues/146), ES module
// import statements -- including ones nested inside a worker's own module
// graph -- are commonly treated as governed by `script-src`, not
// `worker-src`. This app's `script-src` (src/middleware.ts) is
// `'nonce-<per-request>' 'strict-dynamic' https:` with no `'self'` and no
// way to attach a nonce to a static `import` specifier, so that nested
// fetch could be silently CSP-blocked in a real browser -- reproducing the
// exact blank-map symptom this fix exists to solve, just via CSP instead of
// via Turbopack, undetectable by `next build` or by this repo's visual-
// regression suite (whose seeded monitoring-map baseline uses a source-less
// style that never dispatches a tile job to the worker in the first place).
//
// Rather than gamble on that spec ambiguity, this script uses esbuild
// (already present in node_modules transitively; declared here as an
// explicit devDependency so that stays guaranteed) to BUNDLE the worker
// entry point into one self-contained file with the shared chunk inlined --
// zero nested imports, so the browser only ever performs the one fetch that
// `worker-src 'self'` unambiguously covers. This also incidentally hardens
// against a future in-range maplibre-gl 6.x release restructuring how the
// shared code is chunked: esbuild resolves the entry file's actual import
// graph at copy time rather than relying on a hardcoded file list, so a
// renamed/added/removed *shared* file cannot silently produce a partial,
// broken copy the way a plain two-file copy could.
//
// Regenerated on every install/dev/build (git-ignored, not checked in) so
// it can never drift from whatever maplibre-gl version package.json
// actually resolves.
//
// Usage: node scripts/copy-maplibre-worker.mjs (run from admin-dashboard/)

import { existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as esbuild from "esbuild";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC_DIR = join(ROOT, "node_modules", "maplibre-gl", "dist");
const DEST_DIR = join(ROOT, "public", "maplibre");
const ENTRY = "maplibre-gl-worker.mjs";

function fail(message) {
  console.error(`✗ copy-maplibre-worker: ${message}`);
  process.exit(1);
}

if (!existsSync(SRC_DIR)) {
  fail(
    `${SRC_DIR} does not exist -- maplibre-gl isn't installed yet. ` +
      `Run npm install first (or this script is misconfigured as a ` +
      `pre-install hook instead of predev/prebuild).`
  );
}

const entryPath = join(SRC_DIR, ENTRY);
if (!existsSync(entryPath)) {
  fail(
    `${entryPath} not found. maplibre-gl's dist file layout changed -- ` +
      `re-check this script against the installed version's ` +
      `node_modules/maplibre-gl/dist/ contents before assuming the ` +
      `worker fix (setWorkerUrl in maplibre-base.ts) still applies.`
  );
}

mkdirSync(DEST_DIR, { recursive: true });

try {
  await esbuild.build({
    entryPoints: [entryPath],
    outfile: join(DEST_DIR, ENTRY),
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2020",
    minify: false,
    sourcemap: false,
    logLevel: "silent",
  });
} catch (err) {
  fail(`esbuild failed to bundle ${entryPath}: ${err.message}`);
}

console.log(
  `✓ copy-maplibre-worker: bundled ${ENTRY} (shared chunk inlined, no nested imports) to public/maplibre/`
);
