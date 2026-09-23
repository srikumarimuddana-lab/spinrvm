import pytest

from loadtest.target_guard import (
    guard_http_client,
    validate_api_target,
    validate_cached_target,
    websocket_connection_options,
)


STAGING = "https://spinr-backend-staging.fly.dev"


def test_api_target_requires_exact_positive_allowlist():
    assert validate_api_target(STAGING, STAGING) == STAGING
    assert validate_api_target("http://localhost:8000", "http://localhost:8000") == "http://localhost:8000"
    for target, allowed in [
        (STAGING, ""),
        (STAGING, "https://other-staging.fly.dev"),
        (STAGING + "/api", STAGING),
        (STAGING + "?x=1", STAGING),
        ("https://user:pass@staging.test", "https://staging.test"),
        ("http://staging.test", "http://staging.test"),
        ("https://staging.test:bad", "https://staging.test:bad"),
        ("https://staging.test\n.evil", "https://staging.test.evil"),
    ]:
        with pytest.raises(ValueError):
            validate_api_target(target, allowed)


def test_documented_staging_subdomain_can_be_explicitly_allowlisted():
    target = "https://staging-api.spinr.ca"
    assert validate_api_target(target, target) == target


@pytest.mark.parametrize(
    "target",
    [
        "https://api-spinr.spinr.ca",
        "https://api.spinr.ca",
        "https://spinr.ca",
        "https://spinr-backend-yyz.fly.dev",
    ],
)
def test_known_production_targets_are_denied_even_if_allowlisted(target):
    with pytest.raises(ValueError, match="production"):
        validate_api_target(target, target)


def test_cache_must_be_bound_to_the_current_validated_origin():
    assert validate_cached_target(STAGING, STAGING, STAGING) == STAGING
    with pytest.raises(ValueError):
        validate_cached_target(None, STAGING, STAGING)
    with pytest.raises(ValueError):
        validate_cached_target("https://other-staging.fly.dev", STAGING, STAGING)


def test_http_client_never_follows_redirects_even_if_caller_asks():
    class FakeClient:
        base_url = STAGING

        def request(self, method, url, **kwargs):
            return method, url, kwargs

    client = guard_http_client(FakeClient())
    _, _, kwargs = client.request("GET", STAGING, allow_redirects=True)
    assert kwargs["allow_redirects"] is False


def test_http_client_rejects_absolute_cross_origin_urls():
    class FakeClient:
        base_url = STAGING

        def request(self, method, url, **kwargs):
            return method, url, kwargs

    client = guard_http_client(FakeClient())
    with pytest.raises(ValueError, match="cross-origin"):
        client.request("GET", "https://api.spinr.ca/health")


def test_http_client_allows_relative_and_same_origin_urls():
    class FakeClient:
        base_url = STAGING

        def request(self, method, url, **kwargs):
            return method, url, kwargs

    client = guard_http_client(FakeClient())
    assert client.request("GET", "/health")[1] == "/health"
    assert client.request("GET", STAGING + "/health")[1] == STAGING + "/health"


def test_websocket_client_is_forbidden_from_following_redirects():
    assert websocket_connection_options() == {"redirect_limit": 0}
