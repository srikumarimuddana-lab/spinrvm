"""Google encoded-polyline decoding.

Extracted from ``routes/rides/_shared.py`` (R7,
docs/audit/ride-experience/ROADMAP.md) so ``routes/maps_proxy.py``'s new
Directions proxy endpoint can decode a route without importing a private
helper out of an unrelated route package. ``routes/rides/_shared.py``
re-exports the same name for its own existing callers — this is pure code
motion, no behaviour change.
"""


def decode_polyline(encoded: str) -> list:
    """Decode a Google encoded polyline string to [[lat, lng], ...] list."""
    coords: list = []
    index = 0
    lat = 0
    lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("Truncated encoded polyline at index %d" % index)
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 32:
                    break
            value = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += value
            else:
                lat += value
        coords.append([lat / 1e5, lng / 1e5])
    return coords
