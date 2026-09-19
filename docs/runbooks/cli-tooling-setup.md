# CLI tooling setup — where to run what

This exists because the same question keeps coming up: *which computer/app am I
supposed to be typing this command into?* Read this section once, then use the
per-tool sections below as a lookup whenever a new tool comes up.

## The one rule that resolves almost all confusion

**Every command in this document runs in a terminal on your own computer —
not in a Claude conversation of any kind.** Concretely:

| Surface | What it is | Run CLI setup/auth commands here? |
|---|---|---|
| **Your own computer's terminal** (PowerShell, Terminal.app, etc.) | A real, persistent machine that's yours | **Yes — this is always the answer** for `fly auth login`, `firebase login`, `eas login`, `gh auth login`, `stripe login`, `supabase login`, etc. |
| **This cloud session** (Claude Code on the web / "Claude Code Remote" — where this conversation is happening) | An ephemeral, disposable container tied to the GitHub repo. Resets when the session ends. | No, for anything that logs into *your* personal Fly/Firebase/Stripe/etc. account. A login here doesn't persist, and browser-based OAuth logins often don't work cleanly through this sandbox's network proxy anyway. This surface is for editing code and running the repo's own build/test commands, not for setting up your personal tool access. |
| **Claude Desktop app or claude.ai web chat** (plain chat, not Claude Code) | A chat interface with no terminal at all | No — there is no command line here to type into, period. If you're looking at a plain chat window, none of this applies to that window. |
| **Local Claude Code CLI** (optional — Claude Code installed on your own computer) | A Claude session running inside your own terminal, on your own machine | This is where the *benefit* shows up: once you've logged into a tool on your machine (below), a local Claude Code session can use that same login, because it's the same terminal/account. This is a separate, optional thing from the cloud session above. |

So: whenever a walkthrough below says "run X" — open PowerShell (or your terminal) on your own laptop and type it there. Nowhere else.

## Prerequisites (needed before almost everything else)

**Node.js + npm** — most of these CLIs install via `npm install -g`.
- Check: `node -v` and `npm -v`
- Install (Windows): `winget install OpenJS.NodeJS.LTS`, or download from nodejs.org
- Install (Mac): `brew install node`

**git** — needed to work with this repo at all.
- Check: `git --version`
- Install (Windows): `winget install --id Git.Git`, or download from git-scm.com
- Install (Mac): `brew install git`

## Per-tool quick reference

| Tool | What it's for in Spinr | Check installed | Install | First login | Verify it worked |
|---|---|---|---|---|---|
| **Fly.io CLI** | Backend production deploys/secrets (`spinr-backend-yyz`) | `fly version` | Windows: `pwsh -c "irm https://fly.io/install.ps1 \| iex"` · Mac/Linux: `curl -L https://fly.io/install.sh \| sh` | `fly auth login` (opens browser) | `fly auth whoami` |
| **GitHub CLI (`gh`)** | PR/issue work from your own machine (separate from this session's built-in GitHub access) | `gh --version` | Windows: `winget install --id GitHub.cli` · Mac: `brew install gh` | `gh auth login` | `gh auth status` |
| **Firebase CLI (`firebase-tools`)** | App Check config, SHA fingerprints, Firebase project inspection | `firebase --version` | `npm install -g firebase-tools` | `firebase login` | `firebase projects:list` |
| **Expo / EAS CLI** | Mobile builds for rider-app/driver-app | `eas --version` | `npm install -g eas-cli` | `eas login` | `eas whoami` |
| **Supabase CLI** | Local dev DB, migrations, project inspection | `supabase --version` (or `npx supabase --version` if not installed globally — Supabase blocks plain `npm install -g supabase`) | Windows: `scoop bucket add supabase https://github.com/supabase/scoop-bucket.git` then `scoop install supabase` · Mac: `brew install supabase/tap/supabase` | `supabase login` | `supabase projects list` |
| **Vercel CLI** | admin-dashboard deploys | `vercel --version` | `npm install -g vercel` | `vercel login` | `vercel whoami` |
| **Stripe CLI** | Local webhook testing, Stripe account inspection | `stripe --version` | Windows: `scoop install stripe` (or download the `.exe` from Stripe's GitHub releases) · Mac: `brew install stripe/stripe-cli/stripe` | `stripe login` (opens browser, pairs CLI to your Stripe account) | `stripe config --list` |

These are the standard, currently-documented install paths for each tool as of general knowledge — if one fails (installers do change over time), that's a signal to check the tool's own official install page rather than force it.

## Access scoping — read before logging into anything production-facing

Per this project's own access philosophy: log into the **narrowest** account/scope a task actually needs, not your broadest admin account, and never share a login session across projects. Concretely for Spinr:
- **Fly/Stripe/Vercel/Supabase**: if the tool supports a team or project switch, confirm you're scoped to the Spinr project specifically before running anything that reads or writes production data — several of these CLIs default to "all projects/teams you can see" once logged in.
- **Stripe**: this repo's own `.mcp.json` states outright — only ever authenticate against a **test-mode** Stripe account for AI-assisted work, never the live Spinr account.
- Don't leave a production-capable CLI session logged in on a shared or temporary machine.

## FAQ

**"Do I need to do this in every new Claude conversation?"**
No. Once you've logged into a tool on your own computer, that login persists on your machine (usually until the token expires or you explicitly log out) — it has nothing to do with any particular Claude conversation.

**"I'm inside this cloud session and a command isn't found — is that a bug?"**
No — it means the tool genuinely isn't installed in this disposable container, which is expected for anything that needs a personal account login. That's the signal to switch to your own computer and follow the table above.
