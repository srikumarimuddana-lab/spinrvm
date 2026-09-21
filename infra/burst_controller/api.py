"""Bounded HTTP transport. Exceptions never include credentials or response bodies."""

import json
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request


class RemoteError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RemoteError("Unexpected redirect")


OPENER = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))


def request(url, token=None, method="GET", body=None, nonce=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    if nonce:
        headers["fly-machine-lease-nonce"] = nonce
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    deadline = time.monotonic() + 5
    try:
        # Socket inactivity timeout plus elapsed checks while reading the body.
        # DNS/header receipt is not a hard wall deadline: callers MUST fence
        # subsequent mutations against lease expiry after each network call.
        with OPENER.open(req, timeout=5) as response:
            raw = bytearray()
            while len(raw) <= 1_048_576:
                chunk = response.read1(min(65536, 1_048_577 - len(raw)))
                if time.monotonic() > deadline:
                    raise RemoteError("Response deadline exceeded")
                if not chunk:
                    break
                raw.extend(chunk)
        if len(raw) > 1_048_576:
            raise RemoteError("Response exceeds size limit")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raise RemoteError(f"HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RemoteError("Transport unavailable or timed out") from None
    except http.client.HTTPException:
        raise RemoteError("Invalid HTTP response") from None
    except (ValueError, UnicodeError):
        raise RemoteError("Invalid JSON response") from None


class Fly:
    def __init__(self, app, token):
        self.base = (
            "https://api.machines.dev/v1/apps/"
            + urllib.parse.quote(app, safe="")
            + "/machines"
        )
        self.token = token

    def call(self, suffix="", method="GET", body=None, nonce=None):
        return request(self.base + suffix, self.token, method, body, nonce)

    @staticmethod
    def path(machine_id):
        return "/" + urllib.parse.quote(machine_id, safe="")

    def machines(self):
        return self.call()

    def journal(self, anchor):
        metadata = self.call(self.path(anchor) + "/metadata")
        return json.loads(metadata.get("spinr_burst_journal", "{}"))

    def save(self, anchor, state, nonce):
        self.require_nonce(nonce)
        return self.call(
            self.path(anchor) + "/metadata/spinr_burst_journal",
            "POST",
            {"value": json.dumps(state)},
            nonce,
        )

    def lease(self, machine_id):
        return self.call(
            self.path(machine_id) + "/lease",
            "POST",
            {"ttl": 60, "description": "spinr burst controller"},
        )["data"]

    def release(self, machine_id, nonce):
        self.require_nonce(nonce)
        self.call(self.path(machine_id) + "/lease", "DELETE", nonce=nonce)

    def start(self, machine_id, nonce):
        self.require_nonce(nonce)
        self.call(self.path(machine_id) + "/start", "POST", nonce=nonce)

    @staticmethod
    def require_nonce(nonce):
        if not isinstance(nonce, str) or not nonce:
            raise RemoteError("Missing lease nonce")
