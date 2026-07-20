"""Billed-vs-measured receipt labeling (fare-locked rides).

relabel_booked_distance_lines rewrites ONLY the served representation of the
frozen fare snapshot: when the GPS-measured distance diverged from the
booking-time estimate by more than 0.5 km, the ride-fare line label becomes
"Ride fare (X km booked)". Amounts are byte-identical, the stored snapshot is
never modified, and small divergences keep the original label.
"""

import copy

from backend.routes.rides._shared import relabel_booked_distance_lines


def _lines():
    return [
        {"label": "Ride fare (3.0 km)", "amount": "8.06", "type": "ride"},
        {"label": "Booking fee", "amount": "1.00", "type": "fee"},
        {"label": "GST (5.0%)", "amount": "0.51", "type": "tax"},
        {"label": "Tip", "amount": "2.00", "type": "tip"},
    ]


def _ride(planned=3.0, actual=9.9):
    return {"planned_distance_km": planned, "actual_distance_km": actual}


def test_divergent_ride_relabels_only_the_ride_line_with_booked_suffix():
    original = _lines()
    frozen = copy.deepcopy(original)

    served = relabel_booked_distance_lines(original, _ride(planned=3.0, actual=9.9))

    assert served[0]["label"] == "Ride fare (3.0 km booked)"
    assert [line["label"] for line in served[1:]] == [line["label"] for line in frozen[1:]]
    # Amounts byte-identical on every line.
    assert [line["amount"] for line in served] == [line["amount"] for line in frozen]
    # The input (the frozen snapshot's lines) is never mutated.
    assert original == frozen


def test_small_divergence_keeps_the_original_label():
    served = relabel_booked_distance_lines(_lines(), _ride(planned=3.0, actual=3.4))
    assert served[0]["label"] == "Ride fare (3.0 km)"


def test_missing_measured_or_planned_distance_is_a_noop():
    assert relabel_booked_distance_lines(_lines(), {"actual_distance_km": 9.9})[0]["label"] == "Ride fare (3.0 km)"
    assert relabel_booked_distance_lines(_lines(), {"planned_distance_km": 3.0})[0]["label"] == "Ride fare (3.0 km)"
    assert relabel_booked_distance_lines(_lines(), {})[0]["label"] == "Ride fare (3.0 km)"


def test_non_numeric_distances_are_a_noop():
    served = relabel_booked_distance_lines(_lines(), {"planned_distance_km": "n/a", "actual_distance_km": 9.9})
    assert served[0]["label"] == "Ride fare (3.0 km)"


def test_booked_label_uses_the_planned_distance_not_the_overwritten_column():
    # Under fare-lock complete_ride overwrites distance_km with the measured
    # value; the booked label must come from planned_distance_km.
    ride = {"planned_distance_km": 10.9, "actual_distance_km": 8.7, "distance_km": 8.7}
    served = relabel_booked_distance_lines(_lines(), ride)
    assert served[0]["label"] == "Ride fare (10.9 km booked)"
