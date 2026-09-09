#!/usr/bin/env python3
"""Probe what the Play service account can actually do, per package.

Answers the question `eas submit` cannot: is this service account authorised
on THIS package, right now? Expo/fastlane collapse every authorisation problem
into one message ("the service account is missing the necessary permissions"),
which cannot distinguish "not granted", "granted but not propagated yet", and
"granted with the wrong permissions". This asks Google directly and prints its
own error text.

The probe is `edits.insert` — the first call any submission makes. An edit is a
transaction handle: it changes nothing unless committed, expires on its own,
and is deleted here regardless. Creating one is the narrowest possible test of
"can this account start a release on this package".

Usage:
    python3 scripts/check_play_permissions.py <key.json> <package> [package ...]

Exit codes: 0 = every package probed OK, 1 = at least one failed.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

SCOPE = "https://www.googleapis.com/auth/androidpublisher"
API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"

# Google returns these with an opaque 403; the mapping is what turns the probe
# into an actionable answer instead of another dead end.
HINTS = {
    401: "The key itself was rejected — wrong/revoked key, or the Google Play\n"
         "     Android Developer API is not enabled on its GCP project.",
    403: "Authenticated fine, but not authorised on THIS package. Either the\n"
         "     service account has no app-level grant for it, the grant lacks\n"
         "     'Create and edit draft releases' / 'Release to testing tracks',\n"
         "     or a recent grant has not propagated yet (usually minutes, but\n"
         "     Google documents up to 24h).",
    404: "Package not found for this account — check the package name, and that\n"
         "     the app lives in the developer account this key belongs to.",
}


def get_token(key_path: str) -> str:
    """Mint an access token from the service-account key."""
    try:
        from google.oauth2 import service_account  # noqa: PLC0415
        import google.auth.transport.requests  # noqa: PLC0415
    except ImportError:
        sys.exit("google-auth is required:  pip install google-auth")

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=[SCOPE]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _request(method: str, url: str, token: str):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Length", "0")
    return urllib.request.urlopen(req, timeout=30)


def probe(package: str, token: str) -> bool:
    """True when an edit could be created (and cleaned up) for this package."""
    print(f"\n=== {package} ===")
    try:
        with _request("POST", f"{API}/{package}/edits", token) as resp:
            edit_id = json.load(resp).get("id")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:  # Google nests the useful sentence; fall back to the raw body.
            detail = json.loads(body)["error"]["message"]
        except Exception:
            detail = body.strip()[:400]
        print(f"  CANNOT SUBMIT  (HTTP {e.code})")
        print(f"  Google says: {detail}")
        if e.code in HINTS:
            print(f"  ->   {HINTS[e.code]}")
        return False
    except urllib.error.URLError as e:
        print(f"  PROBE INCONCLUSIVE — could not reach Google: {e.reason}")
        return False

    print(f"  CAN SUBMIT  (created edit {edit_id})")
    # Best-effort cleanup. A leaked edit is harmless (it expires, and commits
    # nothing), so a failure here must not mask the successful probe above.
    try:
        _request("DELETE", f"{API}/{package}/edits/{edit_id}", token)
        print("  cleaned up the test edit")
    except Exception as e:  # noqa: BLE001
        print(f"  note: could not delete the test edit ({e}); it expires on its own")
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    key_path, packages = argv[1], argv[2:]

    with open(key_path, encoding="utf-8") as fh:
        key = json.load(fh)
    # Identifiers, not credential material — printing them is what lets a
    # reader match this run against Play Console's user list.
    print(f"service account: {key.get('client_email')}")
    print(f"gcp project:     {key.get('project_id')}")

    token = get_token(key_path)
    results = {pkg: probe(pkg, token) for pkg in packages}

    print("\n=== summary ===")
    for pkg, ok in results.items():
        print(f"  {'CAN SUBMIT ' if ok else 'BLOCKED    '} {pkg}")

    if all(results.values()):
        print("\nEvery package is submittable by this service account.")
        return 0
    if any(results.values()):
        print(
            "\nSome packages work and some do not, with ONE key — so the key is\n"
            "valid and the problem is per-app authorisation, not the credential."
        )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
