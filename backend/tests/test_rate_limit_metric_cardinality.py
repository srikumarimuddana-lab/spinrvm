"""The 429 metric's `path` label must be bounded.

`utils/metrics.py` keeps counters in a process-local dict with no eviction, so
every distinct label set is permanent. Labelling with `request.url.path` — which
embeds ride UUIDs — meant one permanent row per ride that ever hit a 429, growing
fastest during exactly the burst the metric exists to diagnose, and scanned once
a minute per replica by the capacity watchdog.

See `_metric_path_label` in utils/rate_limiter.py.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from utils.rate_limiter import _metric_path_label


class _Route:
    def __init__(self, path):
        self.path = path


def _request(*, url_path: str, route_template: str | None):
    scope = {
        "type": "http",
        "method": "POST",
        "path": url_path,
        "path_params": {},
        "headers": [],
        "client": ("198.51.100.1", 1234),
        "query_string": b"",
        "app": None,
    }
    if route_template is not None:
        scope["route"] = _Route(route_template)
    return Request(scope)


def test_uses_the_route_template_not_the_live_url():
    req = _request(
        url_path="/rides/8f14e45f-ceea-467a-9f8a-1c2d3e4f5a6b/cancel",
        route_template="/rides/{ride_id}/cancel",
    )
    assert _metric_path_label(req) == "/rides/{ride_id}/cancel"


def test_many_rides_collapse_to_one_label():
    """The whole point: N rides must produce 1 label, not N."""
    labels = {
        _metric_path_label(
            _request(url_path=f"/rides/ride-{i}/cancel", route_template="/rides/{ride_id}/cancel")
        )
        for i in range(1000)
    }
    assert labels == {"/rides/{ride_id}/cancel"}


def test_distinct_endpoints_stay_distinct():
    """Bounding cardinality must not collapse everything into one bucket — the
    runbook tells operators to read violations per endpoint."""
    a = _metric_path_label(_request(url_path="/rides/x/cancel", route_template="/rides/{ride_id}/cancel"))
    b = _metric_path_label(_request(url_path="/rides/x/emergency", route_template="/rides/{ride_id}/emergency"))
    assert a != b


@pytest.mark.parametrize(
    "url_path",
    [
        "/rides/../../etc/passwd",
        "/" + "A" * 4000,
        "/rides/%00%0a-injected",
    ],
)
def test_unmatched_routes_do_not_leak_attacker_controlled_labels(url_path):
    """A 404 has no matched route. Falling back to the raw path would reopen the
    same unbounded-cardinality hole, except the label author is now hostile —
    an attacker could mint unlimited label sets by varying the URL."""
    assert _metric_path_label(_request(url_path=url_path, route_template=None)) == "unmatched"


def test_missing_or_malformed_route_object_is_handled():
    req = _request(url_path="/rides/x", route_template=None)
    req.scope["route"] = object()  # no .path attribute
    assert _metric_path_label(req) == "unmatched"

    req2 = _request(url_path="/rides/x", route_template=None)
    req2.scope["route"] = _Route("")  # empty template
    assert _metric_path_label(req2) == "unmatched"
