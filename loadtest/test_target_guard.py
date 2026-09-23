import ast
from pathlib import Path

import pytest

from loadtest.target_guard import (
    guard_http_client,
    guard_requests_session,
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


def test_requests_session_rejects_cross_origin_and_disables_redirects():
    class FakeSession:
        def request(self, method, url, **kwargs):
            return method, url, kwargs

    session = guard_requests_session(FakeSession(), STAGING)
    method, url, kwargs = session.request("POST", STAGING + "/auth/send-otp")
    assert (method, url) == ("POST", STAGING + "/auth/send-otp")
    assert kwargs["allow_redirects"] is False
    with pytest.raises(ValueError, match="cross-origin"):
        session.request("POST", "https://api.spinr.ca/auth/send-otp")


def test_websocket_client_is_forbidden_from_following_redirects():
    assert websocket_connection_options() == {"redirect_limit": 0}


def test_locust_entrypoints_guard_clients_before_login_and_disable_ws_redirects():
    tree = ast.parse((Path(__file__).parent / "locustfile.py").read_text())
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for class_name in ("RiderBot", "DriverBot"):
        on_start = next(
            node for node in classes[class_name].body
            if isinstance(node, ast.FunctionDef) and node.name == "on_start"
        )
        calls = [
            node for node in ast.walk(on_start)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        lines = {node.func.id: node.lineno for node in calls}
        assert lines["_configure_user"] < lines["_login"]

    configure = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_configure_user"
    )
    configure_calls = {
        node.func.id for node in ast.walk(configure)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"validate_api_target", "guard_http_client", "_load_token_cache", "validate_cached_target"} <= configure_calls

    ws_loop = next(
        node for node in classes["DriverBot"].body
        if isinstance(node, ast.FunctionDef) and node.name == "_ws_loop"
    )
    ws_call = next(
        node for node in ast.walk(ws_loop)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_connection"
    )
    assert any(
        keyword.arg is None
        and isinstance(keyword.value, ast.Call)
        and isinstance(keyword.value.func, ast.Name)
        and keyword.value.func.id == "websocket_connection_options"
        for keyword in ws_call.keywords
    )


@pytest.mark.parametrize(
    ("host", "cache_target", "allowed"),
    [
        (STAGING, STAGING, None),
        (STAGING, "https://other-staging.fly.dev", STAGING),
    ],
)
def test_locust_on_start_refuses_bad_target_or_cache_before_login(
    monkeypatch, host, cache_target, allowed
):
    tree = ast.parse((Path(__file__).parent / "locustfile.py").read_text())
    configure = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_configure_user"
    )
    rider_on_start = next(
        node for node in next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RiderBot"
        ).body
        if isinstance(node, ast.FunctionDef) and node.name == "on_start"
    )
    namespace = {
        "validate_api_target": validate_api_target,
        "guard_http_client": guard_http_client,
        "validate_cached_target": validate_cached_target,
        "_load_token_cache": lambda: {"base_url": cache_target},
        "_configure_user": None,
        "_login": lambda *_args: pytest.fail("login ran before target/cache guard"),
    }
    executable = ast.Module(body=[configure, rider_on_start], type_ignores=[])
    exec(compile(executable, "locustfile.py", "exec"), namespace)

    class FakeClient:
        def request(self, method, url, **kwargs):
            return method, url, kwargs

    class FakeUser:
        pass

    user = FakeUser()
    user.client = FakeClient()
    user.client.base_url = host
    user.host = host

    if allowed is None:
        monkeypatch.delenv("LOADTEST_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("LOADTEST_ALLOWED_ORIGINS", allowed)
    with pytest.raises(ValueError):
        namespace["on_start"](user)
