#!/usr/bin/env bash
# PreToolUse hook — surfaces a domain-specific checklist reminder whenever
# Claude is about to write or edit a file in one of the money/dispatch/
# safety-sensitive domains this repo has a dedicated spinr-* subagent for.
# Mirrors pre-migration-write.sh's pattern exactly: read the JSON payload on
# stdin, match the file path, print a stderr reminder, never block (exit 0
# always) — the point is a nudge at write time, not a gate.
#
# Rationale: the spinr-* subagents (.claude/agents/*.md) only get used if
# a session remembers to invoke them. Migrations already had this nudge
# (pre-migration-write.sh); this extends the same pattern to the other
# domains with a dedicated subagent + slash command, per the same
# CLAUDE.md "run PROACTIVELY" convention.

set -euo pipefail

INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null || true)
[ -z "$FILE_PATH" ] && exit 0

case "$FILE_PATH" in
  */backend/services/fare_*.py|backend/services/fare_*.py| \
  */backend/routes/payments.py|backend/routes/payments.py| \
  */backend/routes/wallet.py|backend/routes/wallet.py| \
  */backend/routes/fares.py|backend/routes/fares.py| \
  */backend/routes/tips.py|backend/routes/tips.py| \
  */backend/routes/payouts.py|backend/routes/payouts.py| \
  */backend/utils/payment_retry.py|backend/utils/payment_retry.py| \
  */backend/utils/stripe_charge.py|backend/utils/stripe_charge.py)
    cat >&2 <<'EOF'
💰 Money-touching file about to be written/edited.
   Checklist (from CLAUDE.md + .claude/context/domain-payments.md):
     • Decimal only — _d()/_round()/_f() helpers, never float() or round()
     • Integer cents at the Stripe API boundary
     • Every webhook handler calls claim_stripe_event(event_id) first
     • Receipt line items stay separate — never bundled into "service fee"
     • Refunds always via Stripe API, never a direct wallet UPDATE
   Run /fare-audit before committing.
EOF
    ;;
  */backend/utils/surge_engine.py|backend/utils/surge_engine.py| \
  */backend/routes/admin/*surge*|backend/routes/admin/*surge*)
    cat >&2 <<'EOF'
📈 Surge-pricing file about to be written/edited.
   Checklist (from CLAUDE.md + .claude/context/domain-payments.md):
     • SURGE_CAP = 2.5 is the auto ceiling — never raise without an ADR + business/legal sign-off
     • Surge multiplies distance + time only — never base fare, booking fee, or airport fee
     • Surge locked at estimate time — never retroactive, never on corporate/scheduled rides
     • Admin manual override > 2.5x requires a documented justification field
   Run /surge-check before committing.
EOF
    ;;
  */backend/routes/corporate*.py|backend/routes/corporate*.py| \
  */backend/services/corporate_*.py|backend/services/corporate_*.py| \
  */backend/routes/rides/booking.py|backend/routes/rides/booking.py)
    cat >&2 <<'EOF'
🏢 Corporate billing file about to be written/edited.
   Checklist (from CLAUDE.md + .claude/context/domain-corporate.md):
     • Every wallet delta goes through corporate_wallet_apply_delta — never a direct UPDATE
     • Payment source priority: rider wallet → corporate allowance → master wallet → rider card
     • routes/rides/booking.py has TWO independent corporate paths (company_allowance,
       work_profile) — a fix to one has shipped without the other before; check both
     • Corporate-paid rides never get surge
   Run /corporate-check before committing.
EOF
    ;;
  */backend/services/dispatch_service.py|backend/services/dispatch_service.py| \
  */backend/routes/rides.py|backend/routes/rides.py| \
  */backend/socket_manager.py|backend/socket_manager.py| \
  */backend/utils/ws_pubsub.py|backend/utils/ws_pubsub.py| \
  */backend/utils/scheduled_rides.py|backend/utils/scheduled_rides.py)
    cat >&2 <<'EOF'
🚗 Dispatch / ride state-machine file about to be written/edited.
   Checklist (from CLAUDE.md + .claude/context/domain-dispatch.md):
     • Every status write guarded by _require_ride_in_state()
     • Acceptance update filters {'status': 'searching'} — atomic race guard
     • Every state change emits a WS event to both rider and driver (if assigned)
     • is_available ⇒ is_online must hold — never the inverse
     • Never re-offer to a driver who already declined this search cycle
     • Re-check driver is still online at acceptance time, not just at offer time
   Run /dispatch-check before committing.
EOF
    ;;
  */backend/routes/safety.py|backend/routes/safety.py| \
  */backend/routes/sos.py|backend/routes/sos.py| \
  */backend/services/insurance_*.py|backend/services/insurance_*.py| \
  */backend/utils/emergency_*.py|backend/utils/emergency_*.py| \
  */backend/routes/drivers.py|backend/routes/drivers.py)
    cat >&2 <<'EOF'
🚨 Safety / SOS / insurance-period file about to be written/edited.
   Checklist (from CLAUDE.md + .claude/context/domain-safety.md):
     • SOS never auto-dials 911 — one-tap only, never gated behind auth refresh
     • driver_insurance_periods rows are append-only — never UPDATE/DELETE a period value
     • Period 2 starts at driver_assigned, not driver_accepted
     • Period 3 requires a non-null ride_id
     • Document expiry blocks go_online — re-checked every call, not just at onboarding
   Run /security-check and /insurance-check before committing.
EOF
    ;;
  *) exit 0 ;;
esac

exit 0
