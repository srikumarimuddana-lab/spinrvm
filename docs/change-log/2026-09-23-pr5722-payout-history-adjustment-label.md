# PR 5722: label refund adjustments in payout history

Driver payout history now identifies `clawback` rows as “Refund hold
adjustment.” The existing history row and amount remain visible for audit, with
no implication that the amount was transferred to the driver's bank.

Alternative considered: hiding the row would conceal a balance adjustment;
leaving it unlabeled makes a completed row look like cash paid. The explicit
type label clarifies its accounting role.

Blast radius is limited to rendering one payout type in the driver's history.
Dry-run scenario: a completed $5 clawback appears with the new adjustment
label, while regular payouts retain their existing display.

Rollback: revert the UI code. No persisted data changes. Validation: a focused
screen regression was added; no production APIs or financial writes are used.
