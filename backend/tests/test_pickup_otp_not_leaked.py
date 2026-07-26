"""P0 safety: the rider's pickup OTP must never reach the driver client.

serialize_doc() is an identity function, so a raw `select("*")` ride row
returned to the driver (active ride, ride history, ride completion) would
otherwise carry `pickup_otp` — letting a driver pass verify-pickup-otp without
the rider present and defeating the right-car / right-passenger handshake.
"""

from __future__ import annotations


class TestSerializeRideForDriver:
    def test_strips_pickup_otp(self):
        from backend.routes.drivers._shared import serialize_ride_for_driver

        ride = {"id": "r1", "status": "driver_arrived", "pickup_otp": "4821", "fare": "12.50"}
        out = serialize_ride_for_driver(ride)
        assert "pickup_otp" not in out
        assert out["id"] == "r1"
        assert out["fare"] == "12.50"

    def test_does_not_mutate_source_row(self):
        """The same ride dict is reused for state transitions in-request — the
        strip must return a copy, not delete the key from the source."""
        from backend.routes.drivers._shared import serialize_ride_for_driver

        ride = {"id": "r1", "pickup_otp": "4821"}
        serialize_ride_for_driver(ride)
        assert ride["pickup_otp"] == "4821"

    def test_ride_without_otp_passes_through(self):
        from backend.routes.drivers._shared import serialize_ride_for_driver

        ride = {"id": "r1", "status": "searching"}
        assert serialize_ride_for_driver(ride) == {"id": "r1", "status": "searching"}

    def test_non_dict_falls_back_to_serialize_doc(self):
        from backend.routes.drivers._shared import serialize_ride_for_driver

        assert serialize_ride_for_driver(None) is None
        assert serialize_ride_for_driver([{"id": "r1"}]) == [{"id": "r1"}]

    def test_secret_field_set_includes_pickup_otp(self):
        from backend.routes.drivers._shared import _DRIVER_RIDE_SECRET_FIELDS

        assert "pickup_otp" in _DRIVER_RIDE_SECRET_FIELDS
