# 2026-09-14 — pyright-lsp, typescript-lsp, mcp-server-dev: enablement decision

## What these are

All three ship from `anthropics/claude-plugins-official` — the marketplace already registered
in this repo (`.claude/settings.json`'s `extraKnownMarketplaces`) and proven working since
`feature-dev`'s enablement (PR #5164). **This is the key difference from the
`Understand-Anything` pilot** (`docs/audit/2026-09-11-understand-anything-plugin-pilot.md`,
rolled back after its third-party marketplace entry failed to sync): no new
`extraKnownMarketplaces` entry is added here, so none of that incident's bootstrap-sync risk
applies to this change.

- **`pyright-lsp`** / **`typescript-lsp`** — not skill/agent bundles. Each is just a
  `LICENSE`+`README`; the real mechanism is an `lspServers` entry in the marketplace's own
  `marketplace.json` (`{"command": "pyright-langserver"/"typescript-language-server", "args":
  ["--stdio"], "extensionToLanguage": {...}}`), giving Claude Code real language-server
  intelligence (go-to-definition, find-references, live type errors) for `.py`/`.pyi` and
  `.ts`/`.tsx`/`.js`/`.jsx`/`.mts`/`.cts`/`.mjs`/`.cjs` respectively, instead of grep-based
  navigation.
- **`mcp-server-dev`** — a real skill bundle (`skills/build-mcp-app`, `build-mcp-server`,
  `build-mcpb`) that interrogates a use case and guides MCP server design/deployment
  decisions (remote HTTP vs. MCPB vs. local stdio, tool-design patterns, auth).

## Why they're a fit for spinrvm

- `backend` is Python, `rider-app`/`driver-app`/`admin-dashboard`/`shared` are
  TypeScript/TSX — the only two languages this repo uses (per `CLAUDE.md`'s Project
  Overview), so both LSPs are a direct stack match with no wasted coverage.
- `mcp-server-dev` is relevant because this repo already ships its own MCP server
  (`backend/ai/mcp_server.py`, mounted via `StreamableHTTPSessionManager`) for the AI
  assistant's tool-calling surface.

## Verification performed before enabling

- **`pyright-lsp`**: confirmed the actual binary is already installed and working in this
  environment — `pyright-langserver` resolves to `/root/.local/bin/pyright-langserver`
  (pyright 1.1.408), verified by running `pyright --version` directly, not assumed. This
  should work immediately with no environment changes.
- **`typescript-lsp`**: confirmed `typescript-language-server` is genuinely **not** installed
  globally (`typescript-language-server --version` → command not found; only bare `typescript`
  6.0.2 is global via npm). `npx typescript-language-server` does auto-fetch and run it on
  demand (confirmed working, resolved to 6.0.0) — but the plugin's declared `lspServers`
  command is the bare binary name, not `npx`. Whether this "just works" depends on whether
  Claude Code's harness auto-installs a plugin's declared LSP binary on enable; that can't be
  tested from inside an already-running session (same fresh-session-only limitation as the
  marketplace-sync question below). **See "Open question" below.**
- **`mcp-server-dev`**: read the full skill content, including every `references/*.md` file
  under `build-mcp-server`. Grepped the entire skill tree for `lowlevel`/`list_tools`/
  `call_tool`/`FastMCP` — **zero references** to the low-level `mcp.server.lowlevel.Server`
  class that `backend/ai/mcp_server.py` actually uses. The skill's own Phase 4 framework
  table recommends exactly two options for a new Python MCP server: the TypeScript SDK, or
  **`fastmcp`** (jlowin's third-party PyPI package — explicitly *not* the frozen FastMCP 1.0
  bundled in the official `mcp` SDK, and not the low-level API this repo's existing server
  uses). **Correction to an earlier claim in this session**: this plugin does not contain
  version-migration guidance for the existing `mcp_server.py` bug found during PR #5253's CI
  investigation (`AttributeError: 'Server' object has no attribute 'list_tools'` — `mcp`
  SDK bumped past `1.28.1` upstream dropped that method from the low-level API). This plugin
  would inform a **rewrite** onto `fastmcp` as the fix, not a targeted patch — a materially
  larger, separately-scoped change, not something this enablement resolves by itself.

## Open question — typescript-language-server binary

If `/understand`-style commands or LSP-backed navigation fail specifically for TypeScript
files (while Python navigation via `pyright-lsp` works), the fix is adding
`npm install -g typescript-language-server` to a `SessionStart` hook in this file — not a
marketplace or plugin problem. Whoever is in the first fresh session after this merges should
check both LSPs actually respond (e.g. a go-to-definition or hover on a `.py` file and a `.ts`
file) and note the outcome below.

## Rollback plan

Remove the three added lines under `enabledPlugins`
(`pyright-lsp@claude-plugins-official@022b3c2`,
`typescript-lsp@claude-plugins-official@022b3c2`,
`mcp-server-dev@claude-plugins-official@022b3c2`). No `extraKnownMarketplaces` entry was
added, so there is nothing else to revert — this is strictly additive to an
already-registered, already-working marketplace.

## What was NOT verified

- Whether the Claude Code harness auto-installs `typescript-language-server` on plugin
  enable (the open question above).
- No end-to-end test of either LSP actually producing a hover/definition/diagnostic inside a
  real editing session — that requires a fresh session, which this authoring session cannot
  trigger and observe from the inside (plugin config resolves only at fresh session start).
- `mcp-server-dev`'s guidance has not been used to actually scope or attempt a fix for the
  known `backend/ai/mcp_server.py` bug — that remains a separate, unscoped follow-up.

## Outcome (fill in after the next fresh session)

_Pending — update this section once a fresh session confirms whether `pyright-lsp` and
`typescript-lsp` actually provide working LSP features, and whether the
`typescript-language-server` binary gap needed a `SessionStart` hook fix._
