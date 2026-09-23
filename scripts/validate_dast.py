"""Fail-closed checks for the ZAP staging target and generated report."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

PRODUCTION_HOSTS = {
    "api-spinr.spinr.ca", "api.spinr.ca", "spinr.ca", "www.spinr.ca",
    "spinr-backend-yyz.fly.dev",
}


def _origin(value: str) -> tuple[str, str, int]:
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("target URL contains whitespace or control characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port if parsed.port is not None else 443
    except ValueError as exc:
        raise ValueError("target URL is malformed") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("target must be a credential-free HTTPS origin")
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if not re.fullmatch(rf"(?:{label}\.)+{label}", host) or not 1 <= port <= 65535:
        raise ValueError("target hostname is malformed")
    return "https", host, port


def validate_target(target: str, allowed_origin: str) -> str:
    if not target or not allowed_origin:
        raise ValueError("STAGING_URL and STAGING_ALLOWED_ORIGIN are required")
    target_parts = _origin(target)
    if target_parts != _origin(allowed_origin):
        raise ValueError("STAGING_URL does not match the exact staging-origin allowlist")
    host, port = target_parts[1:]
    if host in PRODUCTION_HOSTS:
        raise ValueError("production origins are not valid DAST targets")
    return f"https://{host}" + (f":{port}" if port != 443 else "")


def validate_egress_attestation(target: str, allowed_origin: str, attestation: str) -> str:
    """Require an operator-provided egress origin matching the canonical target."""
    canonical = validate_target(target, allowed_origin)
    if not attestation or attestation != canonical:
        raise ValueError("DAST_EGRESS_ORIGIN_ATTESTATION must exactly match the canonical staging origin")
    return canonical


def validate_report(path: str | Path, target: str) -> None:
    expected = _origin(target)
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ZAP JSON report is missing or invalid") from exc
    sites = report.get("site") if isinstance(report, dict) else None
    if not isinstance(sites, list) or not sites:
        raise ValueError("ZAP report contains no scanned sites")
    all_match = all(
        isinstance(site, dict)
        and str(site.get("@host", "")).lower().rstrip(".") == expected[1]
        and str(site.get("@port", "")) == str(expected[2])
        and str(site.get("@ssl", "")).lower() == "true"
        for site in sites
    )
    if not all_match or not any(
        str(site.get("@host", "")).lower().rstrip(".") == expected[1]
        and str(site.get("@port", "")) == str(expected[2])
        and str(site.get("@ssl", "")).lower() == "true"
        for site in sites if isinstance(site, dict)
    ):
        raise ValueError("ZAP report sites must all match the exact allowlisted target")


def write_scope_context(target: str, path: str | Path) -> None:
    """Write a ZAP context restricted to the exact validated HTTPS origin."""
    _, host, port = _origin(target)
    authority = re.escape(host)
    if port == 443:
        authority += r"(?::443)?"
    else:
        authority += f":{port}"
    url_pattern = escape(rf"^https://{authority}/.*$")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <context>
    <name>Staging</name>
    <desc/>
    <inscope>true</inscope>
    <incregexes>{url_pattern}</incregexes>
  </context>
</configuration>
"""
    Path(path).write_text(xml, encoding="utf-8")


def main() -> int:
    try:
        target = os.environ.get("STAGING_URL", "")
        allowed_origin = os.environ.get("STAGING_ALLOWED_ORIGIN", "")
        origin = validate_egress_attestation(
            target, allowed_origin, os.environ.get("DAST_EGRESS_ORIGIN_ATTESTATION", "")
        )
        if len(sys.argv) > 1 and sys.argv[1] == "--write-scope-context":
            if len(sys.argv) != 3:
                raise ValueError("scope context output path is required")
            write_scope_context(target, sys.argv[2])
        elif len(sys.argv) > 1:
            validate_report(sys.argv[1], target)
    except ValueError as exc:
        print(f"DAST validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"DAST target/report validated for {origin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
