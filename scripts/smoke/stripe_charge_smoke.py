#!/usr/bin/env python3
"""
Stripe test-mode smoke harness for P0-5 Phase E.

Exercises the exact Stripe parameters that
backend/utils/stripe_charge.py::charge_ride() uses, against a real
Stripe test account. This is the scripted half of Phase E — the
rider-app walk-through in docs/scoping/P0-5_PHASE_E_RUNBOOK.md is the
other half.

What this DOES catch
--------------------
- Our PaymentIntent.create(off_session=True, confirm=True, ...) shape is
  accepted by Stripe (no 400s from the API)
- Each test card actually produces the outcome we expect
  (succeeded / requires_action / declined)
- The `idempotency_key` dedupe works: a second call with the same key
  returns the original PaymentIntent, not a second charge
- The CardError shape we map in charge_ride has the fields we read
  (.error.decline_code, .error.code, .error.message)

What this does NOT catch
------------------------
- Backend's actual handler behavior — that's in
  backend/tests/test_process_payment_card.py (mocked) and in the
  rider-app UI walk-through in the runbook (real)
- Webhook delivery + signature verification — runbook §4 covers that
- Native 3DS sheet rendering — rider-app / iOS / Android specific

Usage
-----

    pip install stripe  # or: pip install -r backend/requirements.txt

    # Smoke all three cards against Stripe test mode:
    export STRIPE_TEST_SECRET_KEY=sk_test_...
    python scripts/smoke/stripe_charge_smoke.py

    # Or pass --stripe-key explicitly:
    python scripts/smoke/stripe_charge_smoke.py \
        --stripe-key sk_test_... \
        --amount-cents 1850

Exit codes: 0 on all-pass, 1 on any failure. Safe to wire into CI later.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Optional


def _require_stripe():
    try:
        import stripe  # noqa: F401
    except ImportError:
        print("ERROR: the `stripe` package is required. Install it with:")
        print("    pip install stripe")
        print("or: pip install -r backend/requirements.txt")
        sys.exit(2)


# Stripe's official test cards.
# https://docs.stripe.com/testing#cards
TEST_CARDS = {
    "success": {
        "number": "4242424242424242",
        "label": "Visa — always succeeds",
        "expected_pi_status": "succeeded",
        "expected_raises_card_error": False,
    },
    "decline_generic": {
        "number": "4000000000000002",
        "label": "Generic decline",
        "expected_pi_status": None,  # raises CardError before returning a PI
        "expected_raises_card_error": True,
        "expected_decline_code": None,  # generic_decline, not insufficient_funds
    },
    "decline_insufficient_funds": {
        "number": "4000000000009995",
        "label": "Insufficient funds",
        "expected_pi_status": None,
        "expected_raises_card_error": True,
        "expected_decline_code": "insufficient_funds",
    },
    "requires_3ds": {
        "number": "4000002500003155",
        "label": "Requires 3DS authentication",
        "expected_pi_status": "requires_action",
        "expected_raises_card_error": False,
    },
}


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str


def _make_customer_and_payment_method(stripe, card_number: str) -> tuple[str, str]:
    """Create a one-off test customer + attach a payment method tokenized
    from the raw card. In production the rider-app tokenizes via Stripe
    React Native and never sends raw PAN — see manage-cards.tsx. For test
    mode the raw-card token path is the only programmatic way to pin a
    specific test card to a specific PM id."""
    customer = stripe.Customer.create(
        description=f"P0-5 smoke {uuid.uuid4().hex[:8]}",
        metadata={"source": "p0_5_stripe_charge_smoke"},
    )
    pm = stripe.PaymentMethod.create(
        type="card",
        card={
            "number": card_number,
            "exp_month": 12,
            "exp_year": 2035,
            "cvc": "123",
        },
    )
    stripe.PaymentMethod.attach(pm.id, customer=customer.id)
    stripe.Customer.modify(
        customer.id,
        invoice_settings={"default_payment_method": pm.id},
    )
    return customer.id, pm.id


def _delete_customer(stripe, customer_id: str) -> None:
    """Best-effort cleanup so the test Stripe account doesn't accumulate
    cruft. Safe to call even if Stripe already GC'd the customer."""
    try:
        stripe.Customer.delete(customer_id)
    except Exception as e:
        print(f"  (cleanup warning: {e})")


def _run_charge_case(
    stripe,
    case_name: str,
    card_number: str,
    amount_cents: int,
    ride_id: str,
    rider_id: str,
) -> tuple[bool, str, Optional[object]]:
    """Mirror charge_ride() exactly — same parameters, same idempotency
    key shape. Returns (ok, detail, intent_or_exception).
    """
    # Avoid name collision with Python's int-typed `amount_cents`.
    from stripe.error import CardError, StripeError  # type: ignore

    customer_id, pm_id = _make_customer_and_payment_method(stripe, card_number)

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="cad",
            customer=customer_id,
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            metadata={
                "ride_id": ride_id,
                "rider_id": rider_id,
                "source": "ride_completion_charge",
                "smoke_case": case_name,
            },
            idempotency_key=f"ride-charge-{ride_id}",
        )
        return True, f"pi={intent.id} status={intent.status}", intent
    except CardError as e:
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        return False, f"CardError decline_code={decline_code}", e
    except StripeError as e:
        return False, f"StripeError: {e}", e
    finally:
        _delete_customer(stripe, customer_id)


def _assert_case(case_key: str, spec: dict, amount_cents: int, stripe) -> CaseResult:
    """Run one test card and compare the outcome to expectations."""
    ride_id = f"smoke_{case_key}_{uuid.uuid4().hex[:8]}"
    rider_id = f"rider_smoke_{uuid.uuid4().hex[:6]}"

    print(f"\n[{case_key}] {spec['label']} (card {spec['number'][:4]}...{spec['number'][-4:]})")

    ok, detail, result = _run_charge_case(
        stripe, case_key, spec["number"], amount_cents, ride_id, rider_id
    )
    print(f"  → {detail}")

    # Now check the result matches expectations.
    if spec["expected_raises_card_error"]:
        if ok:
            return CaseResult(
                case_key, False,
                f"Expected CardError but Stripe returned PaymentIntent status={getattr(result, 'status', '?')}",
            )
        # CardError raised — check decline_code if specified
        expected_dc = spec.get("expected_decline_code")
        if expected_dc:
            err = getattr(result, "error", None)
            actual_dc = getattr(err, "decline_code", None) or getattr(err, "code", None)
            if actual_dc != expected_dc:
                return CaseResult(
                    case_key, False,
                    f"Expected decline_code={expected_dc!r}, got {actual_dc!r}",
                )
        return CaseResult(case_key, True, detail)

    # Did not expect CardError — require a PI with the right status
    if not ok:
        return CaseResult(
            case_key, False,
            f"Expected PI with status={spec['expected_pi_status']!r} but got exception: {detail}",
        )

    actual_status = getattr(result, "status", None)
    expected_status = spec["expected_pi_status"]
    if actual_status != expected_status:
        return CaseResult(
            case_key, False,
            f"Expected PI status={expected_status!r}, got {actual_status!r}",
        )

    # For requires_action, also verify client_secret is present — the
    # rider-app needs it for confirmPayment()
    if actual_status == "requires_action":
        cs = getattr(result, "client_secret", None)
        if not cs:
            return CaseResult(case_key, False, "PI requires_action but client_secret is missing")

    return CaseResult(case_key, True, detail)


def _assert_idempotency(stripe, amount_cents: int) -> CaseResult:
    """Two PaymentIntent.create calls with the same idempotency_key must
    return the same PI. Pins the double-charge safety net.
    """
    print("\n[idempotency] Same idempotency_key twice → same PI")
    ride_id = f"smoke_idempo_{uuid.uuid4().hex[:8]}"
    rider_id = f"rider_idempo_{uuid.uuid4().hex[:6]}"
    customer_id, pm_id = _make_customer_and_payment_method(stripe, TEST_CARDS["success"]["number"])

    try:
        kwargs = dict(
            amount=amount_cents,
            currency="cad",
            customer=customer_id,
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            metadata={"ride_id": ride_id, "rider_id": rider_id, "source": "ride_completion_charge"},
            idempotency_key=f"ride-charge-{ride_id}",
        )
        first = stripe.PaymentIntent.create(**kwargs)
        second = stripe.PaymentIntent.create(**kwargs)
        if first.id != second.id:
            return CaseResult(
                "idempotency", False,
                f"Same idempotency_key produced different PIs: {first.id} vs {second.id}",
            )
        print(f"  → Both calls returned pi={first.id}")
        return CaseResult("idempotency", True, f"both calls returned {first.id}")
    finally:
        _delete_customer(stripe, customer_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--stripe-key",
        default=os.environ.get("STRIPE_TEST_SECRET_KEY"),
        help="Stripe test-mode secret key (or set STRIPE_TEST_SECRET_KEY env var)",
    )
    parser.add_argument(
        "--amount-cents", type=int, default=1850,
        help="Charge amount in cents. Default 1850 (matches MOCK_RIDE.total_fare 18.50)",
    )
    parser.add_argument(
        "--skip", action="append", default=[],
        choices=list(TEST_CARDS.keys()) + ["idempotency"],
        help="Skip a specific case (repeatable). Useful if Stripe rate-limits.",
    )
    args = parser.parse_args()

    # Safety checks BEFORE any stripe dependency load — we want the live-key
    # guard to fire even in a misconfigured environment where the stripe
    # package isn't installed yet.
    if not args.stripe_key:
        print("ERROR: --stripe-key or STRIPE_TEST_SECRET_KEY env var required")
        return 2
    if not args.stripe_key.startswith("sk_test_"):
        print(f"ERROR: refusing to run against a non-test key (got {args.stripe_key[:8]}...).")
        print("       This script creates and deletes Stripe customers.")
        return 2

    # Defer the stripe import until after argparse + safety checks so --help
    # and the live-key guard work without the package installed.
    _require_stripe()
    import stripe

    stripe.api_key = args.stripe_key

    print("=" * 70)
    print("P0-5 Stripe Charge Smoke Harness")
    print(f"Stripe key: {args.stripe_key[:12]}... (test mode)")
    print(f"Amount: {args.amount_cents / 100:.2f} CAD")
    print("=" * 70)

    results: list[CaseResult] = []

    for case_key, spec in TEST_CARDS.items():
        if case_key in args.skip:
            print(f"\n[{case_key}] SKIPPED")
            continue
        try:
            results.append(_assert_case(case_key, spec, args.amount_cents, stripe))
        except Exception as e:
            results.append(CaseResult(case_key, False, f"harness error: {e}"))
        # Small spacing to avoid hammering the API
        time.sleep(0.2)

    if "idempotency" not in args.skip:
        try:
            results.append(_assert_idempotency(stripe, args.amount_cents))
        except Exception as e:
            results.append(CaseResult("idempotency", False, f"harness error: {e}"))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"  [{mark}] {r.name}: {r.detail}")
    print(f"\n{passed}/{len(results)} passed")
    if failed:
        print("\nFAILURES require investigation before closing P0-5.")
        print("See docs/scoping/P0-5_PHASE_E_RUNBOOK.md for the manual walk-through.")
        return 1
    print("\nAll checks passed. Continue with the rider-app UI walk-through in")
    print("docs/scoping/P0-5_PHASE_E_RUNBOOK.md to complete Phase E.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
