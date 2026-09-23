"""Fail-closed target controls shared by load-test preparation and execution."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

PRODUCTION_API_HOSTS = frozenset(
    json.loads((Path(__file__).resolve().parents[1] / "config/production_api_hosts.json").read_text())
)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin(value: str) -> str:
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("load-test target origin contains whitespace or control characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (AttributeError, ValueError) as exc:
        raise ValueError("load-test target origin is malformed") from exc
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("load-test target must be an origin URL without credentials or paths")
    try:
        ipaddress.ip_address(host)
        valid_host = True
    except ValueError:
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        valid_host = bool(re.fullmatch(rf"(?:{label}\.)*{label}", host))
    if not valid_host or (port is not None and not 1 <= port <= 65535):
        raise ValueError("load-test target origin is malformed")
    if scheme == "http" and not _is_loopback(host):
        raise ValueError("non-loopback load-test targets must use HTTPS")
    default_port = 443 if scheme == "https" else 80
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != default_port:
        authority += f":{port}"
    return f"{scheme}://{authority}"


def _configured_origins(allowed_origins: str | None) -> set[str]:
    raw = allowed_origins if allowed_origins is not None else os.environ.get("LOADTEST_ALLOWED_ORIGINS", "")
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        raise ValueError("LOADTEST_ALLOWED_ORIGINS must list exact target origins")
    return {_origin(value) for value in values}


def validate_api_target(target: str, allowed_origins: str | None = None) -> str:
    """Require a target's normalized origin to be explicitly allowlisted."""
    origin = _origin(target)
    host = urlsplit(origin).hostname or ""
    if host in PRODUCTION_API_HOSTS:
        raise ValueError("production API origins are forbidden for load testing")
    if origin not in _configured_origins(allowed_origins):
        raise ValueError("load-test target origin is not in LOADTEST_ALLOWED_ORIGINS")
    return origin


def validate_cached_target(cache_target: str | None, current_target: str, allowed_origins: str | None = None) -> str:
    """Bind a pre-auth token cache to the exact origin it was created for."""
    current = validate_api_target(current_target, allowed_origins)
    if not cache_target:
        raise ValueError("pre-auth token cache has no base_url; regenerate it for this target")
    cached = validate_api_target(cache_target, allowed_origins)
    if cached != current:
        raise ValueError("pre-auth token cache base_url does not match the current load-test target")
    return current


def guard_http_client(client):
    """Reject absolute cross-origin calls and disable redirects on Locust HTTP."""
    request = client.request
    base_url = str(client.base_url)

    def no_redirects(method, url, **kwargs):
        parsed_url = urlsplit(str(url))
        if parsed_url.scheme or parsed_url.netloc:
            absolute_url = urljoin(base_url, str(url))
            absolute_parts = urlsplit(absolute_url)
            request_origin = _origin(f"{absolute_parts.scheme}://{absolute_parts.netloc}")
            base_parts = urlsplit(base_url)
            base_origin = _origin(f"{base_parts.scheme}://{base_parts.netloc}")
            if request_origin != base_origin:
                raise ValueError("cross-origin load-test request is forbidden")
        kwargs["allow_redirects"] = False
        return request(method, url, **kwargs)

    client.request = no_redirects
    return client


def guard_requests_session(session, base_url: str):
    """Reject cross-origin URLs and redirects for requests.Session callers."""
    request = session.request
    base_parts = urlsplit(base_url)
    base_origin = _origin(f"{base_parts.scheme}://{base_parts.netloc}")

    def no_redirects(method, url, *args, **kwargs):
        absolute_url = urljoin(base_url, str(url))
        target_parts = urlsplit(absolute_url)
        target_origin = _origin(f"{target_parts.scheme}://{target_parts.netloc}")
        if target_origin != base_origin:
            raise ValueError("cross-origin load-test request is forbidden")
        kwargs["allow_redirects"] = False
        return request(method, url, *args, **kwargs)

    session.request = no_redirects
    return session


def websocket_connection_options() -> dict[str, int]:
    """websocket-client follows redirects by default; zero forbids them."""
    return {"redirect_limit": 0}
