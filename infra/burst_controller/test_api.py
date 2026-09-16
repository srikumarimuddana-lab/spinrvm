import io
import http.client
import unittest
import urllib.error
from unittest.mock import patch
from api import Fly, RemoteError, request


class TransportTests(unittest.TestCase):
    @patch("api.OPENER.open")
    def test_http_errors_are_bounded_and_redacted(self, open_url):
        for status in (401, 409, 429, 500, 503):
            open_url.side_effect = urllib.error.HTTPError(
                "https://example/secret", status, "secret", {}, None
            )
            with self.assertRaisesRegex(RemoteError, f"^HTTP {status}$"):
                request("https://example", "FlyV1 secret")
            self.assertEqual(open_url.call_args.kwargs["timeout"], 5)

    @patch("api.OPENER.open")
    def test_malformed_oversized_and_timeout(self, open_url):
        for raw in (b"secret-not-json", b" " * 1_048_577):
            open_url.return_value = io.BytesIO(raw)
            with self.assertRaises(RemoteError) as error:
                request("https://example")
            self.assertNotIn("secret", str(error.exception))
        open_url.side_effect = TimeoutError("secret")
        with self.assertRaisesRegex(RemoteError, "Transport unavailable"):
            request("https://example")

    @patch("api.request")
    def test_fly_start_is_lease_fenced_and_id_escaped(self, send):
        Fly("app", "FlyV1 secret").start("a/b", "lease-nonce")
        self.assertEqual(
            send.call_args.args,
            (
                "https://api.machines.dev/v1/apps/app/machines/a%2Fb/start",
                "FlyV1 secret",
                "POST",
                None,
                "lease-nonce",
            ),
        )

    @patch("api.OPENER.open")
    def test_empty_success_is_supported(self, open_url):
        open_url.return_value = io.BytesIO(b"")
        self.assertEqual(request("https://example", method="POST"), {})

    @patch("api.OPENER.open")
    def test_bad_status_line_is_redacted(self, open_url):
        open_url.side_effect = http.client.BadStatusLine("secret upstream body")
        with self.assertRaises(RemoteError) as error:
            request("https://example")
        self.assertNotIn("secret", str(error.exception))

    @patch("api.request")
    def test_mutations_require_nonempty_nonce(self, send):
        for nonce in (None, "", 123):
            with self.assertRaises(RemoteError):
                Fly("app", "token").start("id", nonce)
        send.assert_not_called()

    def test_redirect_never_forwards_authentication(self):
        from api import NoRedirect

        with self.assertRaises(RemoteError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted")


if __name__ == "__main__":
    unittest.main()


class SlowBodyTests(unittest.TestCase):
    @patch("api.time.monotonic", side_effect=[0, 6])
    @patch("api.OPENER.open", return_value=io.BytesIO(b"{}"))
    def test_late_response_is_rejected(self, open_url, clock):
        with self.assertRaisesRegex(RemoteError, "deadline"):
            request("https://example")
