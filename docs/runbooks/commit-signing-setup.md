# Runbook — Verified Commit Signing (SSH & GPG) for spinrvm

**Owner:** `infra` + whoever owns each dev/agent environment · **Cadence:** Once per
human contributor; once per durable automated/agent identity (CI bot, Claude
Code environment, etc.)

---

## Why commits show "Unverified" today

GitHub marks a commit "Verified" only when **both** of these hold:

1. The commit carries a cryptographic signature (GPG or SSH) that GitHub can
   validate against a public key registered on the **committer's own GitHub
   account**.
2. The committer email in the commit matches a **verified email** on that
   account (or, for SSH signing, just needs to match a key registered there —
   GitHub does not require the email to be verified for SSH-signed commits,
   only for GPG).

Ephemeral dev/agent environments (including Claude Code sessions in this
repo) commit as `Claude <noreply@anthropic.com>` per this repo's session
instructions, but have no signing key registered anywhere — so every commit
they make is correctly attributed (right name/email) but shows
**Unverified**. That is expected, not a bug, and is *not* fixed by editing
`git config` alone — a key must exist, be registered on the right GitHub
account, and be re-provisioned into every environment that commits as that
identity, since these containers are wiped on reclaim.

Two ways to get to "Verified," both documented below:

- **SSH signing** — simpler, reuses an SSH keypair, no GPG toolchain needed.
- **GPG signing** — the older/more common convention, needed if your org
  policy or a branch-protection rule specifically requires GPG.

Either is sufficient on its own. Pick one per identity — don't set both
`gpg.format ssh` and a GPG key on the same identity, the last `git config`
write wins and the other sits unused.

---

## Decision: whose identity is being signed?

Before doing anything, decide **who the signing key belongs to** — this
determines where the public key gets registered:

| Identity | Registers key under | Use when |
|---|---|---|
| A real human contributor | Their own GitHub account (Settings → SSH/GPG keys) | Normal day-to-day dev commits |
| A shared bot/automation account (e.g. `spinr-bot`) | The bot account's GitHub Settings | CI, scheduled jobs, agent sessions that should show as a distinct machine identity |
| Claude Code sessions committing as `noreply@anthropic.com` | **No standard path today** — see note below | Any Claude Code session in this repo |

**Important for the `Claude <noreply@anthropic.com>` identity specifically:**
`noreply@anthropic.com` is not a mailbox any human or bot logs into, so there
is no GitHub account to register a key under using the normal per-account
flow. Two real options if you want these to show Verified:

1. **Provision a dedicated `spinr-bot`-style GitHub account** (or GitHub App)
   with its own email, register a signing key on it, and change this repo's
   session instructions to commit as that identity instead of
   `noreply@anthropic.com`. This is the only way to get an actually-Verified
   badge without changing the git identity.
2. **Accept Unverified for agent-authored commits** and rely on the
   `Co-Authored-By` / `Claude-Session:` trailer already present in every
   commit message for attribution/audit instead of the GitHub badge. This is
   the status quo and requires no action.

Nothing below can make `noreply@anthropic.com` show Verified without picking
option 1 first (a real, key-bearing GitHub identity).

---

## Path A — SSH signing (recommended, simpler)

### 1. Generate a signing key (do this once per identity)
```bash
ssh-keygen -t ed25519 -C "<identity-email>" -f ~/.ssh/spinr_signing_key
# no passphrase needed for CI/agent use; a human should set one
```
This produces `~/.ssh/spinr_signing_key` (private) and
`~/.ssh/spinr_signing_key.pub` (public). **Never commit the private key
anywhere** — not to this repo, not to a gist, not to a Slack message.

### 2. Register the public key on GitHub
- GitHub → **Settings → SSH and GPG keys → New SSH key**
- **Key type: must be "Signing Key"**, not "Authentication Key" (GitHub
  lets you register the same key as both, but the dropdown must include
  Signing for commit verification to work)
- Paste the contents of `spinr_signing_key.pub`

This step requires access to the target account's GitHub login — it cannot
be done via the repo-scoped GitHub App/MCP tooling available to an agent
session, only via the account owner's own browser session or `gh auth`
session.

### 3. Configure git to sign with it
```bash
git config user.signingkey ~/.ssh/spinr_signing_key.pub
git config gpg.format ssh
git config commit.gpgsign true
```
Scope these with `--global` for a human's laptop, or write them into
`~/.gitconfig` at container-provisioning time for a CI/agent environment
(see "Durability" below).

### 4. Verify
```bash
git commit --allow-empty -m "test: verify SSH commit signing"
git log --show-signature -1
```
Push it and check the commit on GitHub shows the green "Verified" badge,
then delete the test commit/branch.

---

## Path B — GPG signing

### 1. Generate a GPG key
```bash
gpg --full-generate-key
# choose: (9) ECC (sign only) or RSA 4096
# Real name: match the git commit author name exactly
# Email: match the git commit author email exactly (must be a GitHub-verified email)
# No expiration, or a long one — expired keys stop verifying past commits too
```

### 2. Get the key ID and export the public key
```bash
gpg --list-secret-keys --keyid-format=long
# note the key ID after "sec   ed25519/<KEY_ID>"

gpg --armor --export <KEY_ID>
```

### 3. Register the public key on GitHub
- GitHub → **Settings → SSH and GPG keys → New GPG key**
- Paste the armored public key block from step 2

Same caveat as Path A step 2 — requires the account owner's own GitHub
login, not repo-scoped tooling.

### 4. Configure git to sign with it
```bash
git config user.signingkey <KEY_ID>
git config gpg.format openpgp   # default, only needed if gpg.format was previously set to ssh
git config commit.gpgsign true
```

### 5. Verify
```bash
git commit --allow-empty -m "test: verify GPG commit signing"
git log --show-signature -1
```
Confirm the "Verified" badge on GitHub, then delete the test commit/branch.

---

## Durability across ephemeral environments

A key generated inside a Claude Code / CI container is lost when that
container is reclaimed unless it is deliberately persisted. Two supported
approaches:

1. **Secret injection at environment startup** — store the private signing
   key (SSH or GPG) as an encrypted secret in whatever mechanism provisions
   the environment (repo/org secrets, environment variables surfaced only at
   container boot, etc.), and have a startup script write it to disk and run
   the `git config` commands from Path A/B step 3 before any commit happens.
   This is the only approach that scales to "every future session commits as
   the same signed identity."
2. **One key per environment, accepted as throwaway** — generate and
   register a key inside a single session, accept it will need to be
   re-registered (or the old one revoked and a new one added) each time a
   fresh environment needs to sign. Fine for a one-off "make this specific
   PR's commits verified" ask; does not scale, and leaves stale keys
   registered on the identity's GitHub account that should be periodically
   pruned (Settings → SSH and GPG keys → Delete on the unused ones).

Whichever approach is chosen, **never** write the private key into a file
this repo tracks, a commit message, an issue/PR body, or a log line — all of
those are exactly the exposure this runbook is trying to avoid by using a
signing key in the first place.

---

## What this runbook does NOT cover

- Revoking a compromised signing key — see GitHub's own key-revocation flow
  (Settings → SSH and GPG keys → Revoke/Delete); if a private key is
  suspected leaked, treat it as a credential-compromise incident per
  `docs/incident-response.md`, not a routine rotation.
- Branch-protection rules that *require* verified/signed commits before
  merge — that is a separate repo-admin setting (Settings → Branches →
  branch protection rule → "Require signed commits") and is out of scope
  here; enabling it before every contributing identity has a working signing
  setup will block merges.

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-08-03 | Initial runbook — SSH + GPG signing setup, ephemeral-environment durability notes | Claude Code (session request) |
