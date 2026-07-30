// Guards against a repeat of incident #310: src/middleware.ts was once
// accidentally renamed (to proxy.ts), and Next.js silently no-ops a missing
// middleware file at build time — every /dashboard/* route went unauthenticated
// until it was caught in a manual audit. This script fails CI loudly instead.
//
// Usage: node scripts/check-middleware.mjs (run from admin-dashboard/)

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const MIDDLEWARE_PATH = join(process.cwd(), "src", "middleware.ts");

function fail(message) {
  console.error(`✗ middleware guard: ${message}`);
  process.exit(1);
}

if (!existsSync(MIDDLEWARE_PATH)) {
  fail(
    `expected Next.js middleware at src/middleware.ts but it does not exist. ` +
      `Next.js only picks up middleware from this exact path/name at the ` +
      `project root of src/ — a rename or move silently disables all ` +
      `/dashboard/* auth enforcement (see incident #310).`
  );
}

const source = readFileSync(MIDDLEWARE_PATH, "utf8");

if (!/export\s+(async\s+)?function\s+middleware\s*\(/.test(source)) {
  fail(
    "src/middleware.ts exists but does not export a `middleware` function — " +
      "Next.js will not run it."
  );
}

if (!/export\s+const\s+config\s*=/.test(source)) {
  fail(
    "src/middleware.ts does not export a `config` object — without a matcher " +
      "Next.js falls back to running on every request, which may not be intended; " +
      "if that's deliberate, remove this check's expectation."
  );
}

if (!/matcher\s*:/.test(source)) {
  fail("src/middleware.ts config is missing a `matcher` — auth enforcement scope is undefined.");
}

console.log("✓ middleware guard: src/middleware.ts exists and exports middleware + matcher config");
