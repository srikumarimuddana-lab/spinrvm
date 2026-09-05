"""/deploy-info: deploy provenance + config-parity fingerprints (ACTION_ITEMS C5).

The standby-parity monitor diffs this endpoint across Fly and Railway. Two
properties must hold: it never leaks a config value, and it fails closed
without METRICS_AUTH_TOKEN in every environment.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


def _req(headers=None):
    r = MagicMock()
    r.headers = headers or {}
    r.query_params = {}
    return r


@pytest.mark.anyio
class TestDeployInfoAuth:
    async def test_unset_token_fails_closed_even_outside_production(self):
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value=""),
            patch.object(server.settings, "ENV", "development"),
            pytest.raises(HTTPException) as exc,
        ):
            await server.deploy_info(_req(headers={"authorization": "Bearer anything"}))
        assert exc.value.status_code == 503

    async def test_wrong_token_returns_401(self):
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value="secret"),
            pytest.raises(HTTPException) as exc,
        ):
            await server.deploy_info(_req(headers={"authorization": "Bearer wrong"}))
        assert exc.value.status_code == 401

    async def test_query_string_token_is_not_accepted(self):
        """Unlike /metrics, only the Authorization header is honoured."""
        from backend import server

        req = _req()
        req.query_params = {"token": "secret"}
        with (
            patch("backend.server._metrics_token", return_value="secret"),
            pytest.raises(HTTPException) as exc,
        ):
            await server.deploy_info(req)
        assert exc.value.status_code == 401


@pytest.mark.anyio
class TestDeployInfoBody:
    async def test_body_shape_and_no_raw_values(self):
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value="secret"),
            patch.object(server.settings, "JWT_SECRET", "x" * 48),
            patch.object(server.settings, "SUPABASE_URL", "https://abc.supabase.co"),
            patch.object(server.settings, "ADMIN_EMAIL", "ops@example.test"),
            patch.object(server.settings, "OTP_PEPPER", ""),
        ):
            body = await server.deploy_info(_req(headers={"authorization": "Bearer secret"}))

        assert set(body) == {"provider", "env", "build", "fingerprints"}
        fps = body["fingerprints"]
        assert set(fps) == set(server._PARITY_FIELDS)
        # Unset → null, not the HMAC of the empty string.
        assert fps["OTP_PEPPER"] is None
        # Set → fixed-width hex, never the value.
        assert len(fps["SUPABASE_URL"]) == server._PARITY_FINGERPRINT_HEX_CHARS
        serialized = json.dumps(body)
        assert "abc.supabase.co" not in serialized
        assert "ops@example.test" not in serialized
        assert "x" * 48 not in serialized

    def test_identical_config_gives_identical_fingerprints(self):
        from backend import server

        with patch.object(server.settings, "JWT_SECRET", "k" * 40):
            a = server._config_fingerprints()
            b = server._config_fingerprints()
        assert a == b

    def test_one_field_differing_changes_only_that_row(self):
        from backend import server

        with patch.object(server.settings, "JWT_SECRET", "k" * 40):
            with patch.object(server.settings, "ALLOWED_ORIGINS", "https://a.example"):
                a = server._config_fingerprints()
            with patch.object(server.settings, "ALLOWED_ORIGINS", "https://b.example"):
                b = server._config_fingerprints()
        assert a["ALLOWED_ORIGINS"] != b["ALLOWED_ORIGINS"]
        assert {k: v for k, v in a.items() if k != "ALLOWED_ORIGINS"} == {
            k: v for k, v in b.items() if k != "ALLOWED_ORIGINS"
        }

    def test_jwt_secret_mismatch_changes_every_set_row(self):
        """The HMAC key IS JWT_SECRET, so a mismatch is visible as all-rows-differ."""
        from backend import server

        with patch.object(server.settings, "JWT_SECRET", "a" * 40):
            a = server._config_fingerprints()
        with patch.object(server.settings, "JWT_SECRET", "b" * 40):
            b = server._config_fingerprints()
        for name in server._PARITY_FIELDS:
            if a[name] is None:
                assert b[name] is None
            else:
                assert a[name] != b[name], name

    def test_fingerprint_is_bound_to_field_name(self):
        """Same value under two names must not collide (name is part of the message)."""
        from backend import server

        with (
            patch.object(server.settings, "JWT_SECRET", "k" * 40),
            patch.object(server.settings, "REDIS_URL", "rediss://:pw@redis.spinr.ca:6379"),
            patch.object(server.settings, "WS_REDIS_URL", "rediss://:pw@redis.spinr.ca:6379"),
        ):
            fps = server._config_fingerprints()
        assert fps["REDIS_URL"] != fps["WS_REDIS_URL"]


class TestBuildInfo:
    def test_missing_file_returns_none(self, tmp_path):
        from utils import build_info

        build_info.load_build_info.cache_clear()
        with patch.object(build_info, "BUILD_INFO_PATH", tmp_path / "build_info.json"):
            assert build_info.load_build_info() is None
        build_info.load_build_info.cache_clear()

    def test_reads_known_keys_only(self, tmp_path):
        from utils import build_info

        f = tmp_path / "build_info.json"
        f.write_text(
            json.dumps(
                {
                    "sha": "abc123",
                    "ref": "refs/heads/main",
                    "built_at": "2026-09-04T20:36:01Z",
                    "provider": "railway",
                    "SUPABASE_SERVICE_ROLE_KEY": "must-not-leak",
                }
            )
        )
        build_info.load_build_info.cache_clear()
        with patch.object(build_info, "BUILD_INFO_PATH", f):
            info = build_info.load_build_info()
        build_info.load_build_info.cache_clear()
        assert info == {
            "sha": "abc123",
            "ref": "refs/heads/main",
            "built_at": "2026-09-04T20:36:01Z",
            "provider": "railway",
        }

    def test_committed_placeholder_reports_not_stamped(self):
        """The tracked backend/build_info.json has sha=null → None, not a dict of nulls."""
        from utils import build_info

        build_info.load_build_info.cache_clear()
        assert build_info.BUILD_INFO_PATH.exists(), "placeholder must stay tracked (railway up honours .gitignore)"
        assert build_info.load_build_info() is None
        build_info.load_build_info.cache_clear()

    def test_malformed_file_returns_none(self, tmp_path):
        from utils import build_info

        f = tmp_path / "build_info.json"
        f.write_text("{not json")
        build_info.load_build_info.cache_clear()
        with patch.object(build_info, "BUILD_INFO_PATH", f):
            assert build_info.load_build_info() is None
        build_info.load_build_info.cache_clear()

    def test_detect_provider(self, monkeypatch):
        from utils import build_info

        for var in ("FLY_APP_NAME", "FLY_MACHINE_ID", "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID"):
            monkeypatch.delenv(var, raising=False)
        assert build_info.detect_provider() == "unknown"
        monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
        assert build_info.detect_provider() == "railway"
        monkeypatch.setenv("FLY_APP_NAME", "spinr-backend-yyz")
        assert build_info.detect_provider() == "fly"
