# Security audit note: refresh successor commitment (T11 X8)

Date: 2026-09-24. Status: **audit pending; flag must stay OFF.** PR #5748 ships the code dark.

## What it is

`POST /auth/refresh` may carry `proposed_refresh_token`: a 64-char base64url value the client
generates *before* sending the refresh. When enabled, the server stores sha256(proposed) as the
rotated successor. If the rotation response is lost and the client replays the parent with the
same proposal, the server recognises the committed successor and re-serves it instead of
treating the replay as theft.

- Flag: `settings.refresh_successor_commitment_enabled` (migration `463_refresh_successor_commitment.sql`),
  `boolean NOT NULL DEFAULT false`. An unreadable flag counts as off.
- Code: `utils/refresh_tokens.py` (`issue_refresh_token(raw=...)`, `classify_committed_replay`),
  `routes/auth.py` (`_accepted_refresh_proposal`, recover/dead branches, `_build_refresh_response`).
- Rider/driver only. `routes/admin/auth.py` is untouched by design (see `docs/known-forks.md`).

## Properties the auditor must confirm

1. **No new capability.** `recover` requires the parent raw token *and* the exact proposed raw
   token whose hash was committed. Holding both already means holding a live credential; the
   response re-serves that same successor (no new row, same `expires_at`).
2. **Constant-time match.** Successor hash vs sha256(proposed) uses `hmac.compare_digest`.
3. **Strict recover predicate.** Same `user_id`; audience rider/driver; successor not revoked and
   not expired; successor `token_version` equals the parent's; `refresh_token_generation_matches`
   against the current user; `_enforce_account_active` runs before any token is minted.
4. **Dead is a plain 401 (code 1003) with no cascade**; `no_match` falls through to today's path
   including the existing reuse cascade.
5. **Proposal shape.** Ignored unless it matches `^[A-Za-z0-9_-]{64}$` (same shape as a
   server-minted token, 384 bits when client RNG is sound).
6. **Collision handling.** A UNIQUE (`23505`) conflict on insert retries once with a
   server-generated token, so a proposal cannot collide into or probe another row.
7. **No token material in logs** (raw or hash) on any new path.
8. **DB errors are 503 (code 9005)**, never a false 401 (X7).

## Open questions for the audit

- Client RNG quality: the successor is client-generated. A weak mobile RNG weakens the successor.
  Consider requiring the platform CSPRNG in the mobile implementation and documenting it.
- A thief holding parent + proposal (e.g. full device compromise) is already equivalent to holding
  the successor; confirm this is acceptable versus the cascade's detection value.
- Metric `spinr_auth_refresh_recovered_total{audience}` should be alerted on unusual spikes.

## Enablement gate

Enable only after this audit passes (backend design A5). Never in PR #5748.
