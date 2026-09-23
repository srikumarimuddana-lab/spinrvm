# Keep licence edits available without eligibility dates

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | driver-app |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

Existing drivers can still save licence number/class when they have not entered DOB or licence issue date. If either eligibility date is supplied, both must pass the shared validation; only then are they included in the existing `PUT /drivers/me` payload. This keeps the feature-flag-off fleet profile flow intact while letting drivers add both eligibility dates together.

Blast radius is the existing driver profile licence editor only. No backend state transition, live DB write, or flag change. Verification: `git diff --check` passed; app Jest/production build are unavailable because driver-app dependencies are absent. No visual regression tooling is configured for mobile. Rollback by reverting the profile editor change.
