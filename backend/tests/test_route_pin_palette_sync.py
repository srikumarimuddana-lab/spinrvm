"""The snapshot PNG's marker colours must equal the app's.

`backend/utils/route_snapshot.py` mirrors `ROUTE_PIN_COLORS` from
`shared/constants/routeMapStyle.ts` by hand — Python cannot import the TS
module, and a generated constant would be a build step for three hex strings.
That leaves nothing to catch drift, which is precisely the gap that let the
receipt image and every in-app map disagree in the first place: the next person
to restyle a pin will edit the TS file only.

So this test reads the TS source and asserts the two agree. It fails loudly at
the moment of divergence rather than months later in a screenshot.
"""

import re
from pathlib import Path

import pytest

from backend.utils.route_snapshot import (
    _COMPLETION_HEX,
    _DROPOFF_HEX,
    _PICKUP_HEX,
)

_SHARED_SPEC = (
    Path(__file__).resolve().parents[2] / "shared" / "constants" / "routeMapStyle.ts"
)


def _shared_pin_colors() -> dict[str, str]:
    """Parse ROUTE_PIN_COLORS out of the shared TS spec, uppercase, no '#'."""
    src = _SHARED_SPEC.read_text(encoding="utf-8")
    block = re.search(
        r"export const ROUTE_PIN_COLORS\s*=\s*\{(.*?)\}\s*as const;", src, re.S
    )
    assert block, f"ROUTE_PIN_COLORS not found in {_SHARED_SPEC}"
    return {
        key: value.upper()
        for key, value in re.findall(
            r"(\w+)\s*:\s*'#([0-9a-fA-F]{6})'", block.group(1)
        )
    }


@pytest.mark.unit
def test_snapshot_marker_colors_match_the_shared_route_pin_palette():
    shared = _shared_pin_colors()
    assert shared, "parsed no colours — the TS spec's shape changed"
    assert _PICKUP_HEX.upper() == shared["pickup"]
    assert _DROPOFF_HEX.upper() == shared["dropoff"]
    assert _COMPLETION_HEX.upper() == shared["completion"]


@pytest.mark.unit
def test_osm_fallback_markers_use_the_same_palette():
    """The staticmap fallback writes the hexes inline, so check the source."""
    src = (
        Path(__file__).resolve().parents[1] / "utils" / "route_snapshot.py"
    ).read_text(encoding="utf-8")
    shared = _shared_pin_colors()
    for key in ("pickup", "dropoff", "completion"):
        assert (
            f'"#{shared[key].lower()}"' in src
        ), f"OSM fallback is missing the shared {key} colour #{shared[key].lower()}"
