# 2026-09-14 — Claude Code plugins have never demonstrably activated in this session's environment

## Summary

`/tmp/claude-code.log` shows `Found 0 plugins (0 enabled, 0 disabled)` at **every checkpoint
from this session's first hour (2026-09-07 03:50 UTC) through its most recent** (2026-09-14
02:42 UTC), regardless of what `.claude/settings.json`'s `enabledPlugins` declared at the time —
one plugin (`feature-dev` alone), two (`+karpathy-skills`), four (`+pyright-lsp`,
`typescript-lsp`, `mcp-server-dev`), or the brief `Understand-Anything` window. This corrects the
root-cause framing in two prior write-ups (`docs/audit/2026-09-11-understand-anything-plugin-pilot.md`
and PR #5176's own commit message for the `karpathy-skills` removal), both of which diagnosed a
**third-party-marketplace-specific** bootstrap-sync failure. That diagnosis may still be true as
an independent, real phenomenon for those two repos specifically — but it was never the whole
picture, because the same "zero plugins active" state was already present *before* either
third-party marketplace was ever added, with only the first-party, Anthropic-published
`feature-dev` configured.

## Evidence

Representative log lines, oldest to newest (full grep available via
`grep -n "Found 0 plugins\|refreshActivePlugins" /tmp/claude-code.log`):

```
2026-09-07T03:50:50.600Z [DEBUG] Found 0 plugins (0 enabled, 0 disabled)
```
— this is this session's very first hour. At this point `.claude/settings.json` had not yet been
touched this session; whatever marketplace/plugin config existed on `main` at the time was
already showing zero.

```
2026-09-14T02:24:56.994Z [DEBUG] refreshActivePlugins: 0 enabled, 0 commands, 0 skills,
  30 agents, 0 hooks, 0 MCP, 0 LSP
```
— captured while `enabledPlugins` had only `feature-dev@claude-plugins-official@3ea32df`. The
"30 agents" figure is this repo's own `.claude/agents/*.md` files (the `spinr-*-reviewer` roster)
— a completely separate mechanism (plain files, always loaded) from marketplace plugins. Every
plugin-sourced category — commands, skills, hooks, MCP, LSP — reads zero.

```
2026-09-14T02:42:19.597Z [DEBUG] refreshActivePlugins: 0 enabled, 0 commands, 0 skills,
  30 agents, 0 hooks, 0 MCP, 0 LSP
```
— captured after `enabledPlugins` gained `pyright-lsp`, `typescript-lsp`, and `mcp-server-dev`
(all three from `anthropics/claude-plugins-official`, the same marketplace `feature-dev` uses).
The marketplace itself synced correctly this time — confirmed via
`/root/.claude/plugins/known_marketplaces.json` (`claude-plugins-official`, `lastUpdated`
matching this session's boot) and `/root/.claude/plugins/marketplaces/claude-plugins-official/
plugins/{pyright-lsp,typescript-lsp,mcp-server-dev}/` all present on disk — yet `0 LSP` and
`0 commands` still logged immediately after.

This rules out "the marketplace clone failed" as the explanation for this particular case: the
clone succeeded, the plugin directories exist, and the count was still zero.

## What this changes about the two prior incidents

- **`karpathy-skills`** (PR #5164 → removed PR #5176): diagnosed as a 403 at bootstrap sync
  because `forrestchang/andrej-karpathy-skills` sat outside this environment's GitHub-proxy
  allowlist. Plausible and possibly still true as one contributing factor — but `feature-dev`
  (the marketplace that supposedly "still worked") has no confirmed log evidence of ever
  actually activating either, in this environment, at any point this session.
- **`Understand-Anything`** (enabled, then rolled back 2026-09-11): diagnosed as the entry being
  silently dropped from the bootstrap reconcile list. Same caveat — `feature-dev` was claimed as
  the control case that "still worked" in that write-up's rollback rationale, but that claim was
  never actually verified against the log the way this entry now has been.

Neither prior write-up is being reverted or marked wrong outright — the specific mechanics they
describe (a 403, a silent reconcile-list drop) may be real and worth keeping as documented
history. What's being corrected is the implicit conclusion that a first-party,
`claude-plugins-official`-sourced plugin was a working control group. The log evidence says it
was never confirmed working, in this environment, this entire session.

## What this does NOT change

- `/spinr-feature`, `/plan`, `/spinr-swarm`, `/review`, and every other `spinr-*` slash command
  are plain `.claude/commands/*.md` files — a completely different, always-on mechanism unrelated
  to marketplace plugins. None of this affects them.
- The `spinr-*-reviewer` subagent roster (`.claude/agents/*.md`) is likewise unaffected — the
  "30 agents" in the log lines above are exactly this roster, loading normally regardless of
  plugin state.
- PR #5358 (`pyright-lsp`/`typescript-lsp`/`mcp-server-dev` enablement) is not being reverted.
  The change itself is harmless (additive, isolated, no new marketplace-trust surface) and may
  well work correctly in a different Claude Code execution context (local CLI, IDE integration)
  that this repo is also used from — this finding is specific to the `anthropic_cloud` /
  `web_claude_ai`-origin session type this investigation was run from, not a statement about
  Claude Code plugins in general.

## Open questions (not resolved here)

1. Is this a structural limitation of this specific environment/session kind (`environment_kind:
   "anthropic_cloud"`, `origin: "web_claude_ai"` per this session's own `get_session` metadata),
   or a configuration gap that's fixable?
2. Does a plugin ever actually activate in *any* Claude Code Remote / cloud session against this
   repo, or only in a local CLI / IDE session? Untested here — would need a session of a
   genuinely different `environment_kind`/`origin` to compare against, which this investigation
   did not have access to.
3. Is this specific to `spinrvm`'s configuration, or would a from-scratch repo with an identical
   `enabledPlugins` block show the same zero count in the same environment kind? Untested.

## Recommendation

Treat every Claude Code plugin currently declared in this repo's `.claude/settings.json`
(`feature-dev`, `pyright-lsp`, `typescript-lsp`, `mcp-server-dev`) as **unverified-working** in
any `anthropic_cloud`/`web_claude_ai`-origin session until one of the open questions above is
answered — e.g., by someone with access to a local Claude Code CLI session against this repo
confirming (or ruling out) the same zero-count behavior there. See `ACTION_ITEMS.md` C117.
