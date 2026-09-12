"""utils/polyline.py — extracted from routes/rides/_shared.py for R7
(docs/audit/ride-experience/ROADMAP.md) so routes/maps_proxy.py's new
Directions proxy endpoint can decode a route without importing a private
helper from an unrelated route package.
"""

import pytest

from backend.routes.rides._shared import _decode_polyline
from backend.utils.polyline import decode_polyline

pytestmark = pytest.mark.unit


def test_decodes_a_known_google_example():
    # Google's own documented example: "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    # decodes to [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)].
    result = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert result == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]


def test_empty_string_decodes_to_empty_list():
    assert decode_polyline("") == []


def test_raises_on_truncated_input():
    with pytest.raises(ValueError):
        decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`")[:-1]
    with pytest.raises(ValueError):
        decode_polyline("_")


def test_routes_rides_shared_reexports_the_same_function():
    """routes/rides/_shared.py's own callers must see identical behavior
    after the extraction — this is pure code motion, not a rewrite."""
    encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert _decode_polyline(encoded) == decode_polyline(encoded)
