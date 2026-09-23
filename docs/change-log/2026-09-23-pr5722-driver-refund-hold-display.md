# PR 5722: show refund holds separately on the driver balance screen

The driver payout screen displays a “Refund Holds” amount when present, using
the additive `refund_holds_total` field from `/balance`. This lets drivers
reconcile why the cash-paid total and withdrawable balance differ after a
refund hold.

Alternatives considered: hiding the adjustment leaves an unexplained gap;
including it in Paid Out implies cash reached the bank. A separate conditional
line preserves the established display for drivers with no refund holds.

Blast radius is limited to the driver's aggregate balance card and its API
type. Dry-run scenarios: an absent or zero field renders no extra line; a
positive hold renders the amount separately from Pending and Paid Out.

Rollback: revert the UI and optional type field. No persisted data changes.
Validation: this screen change relies on the backend mocked regression; the
driver-app test runner was not run in this environment.
