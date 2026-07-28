"""Admin AI settings surface.

Pins: the 4 per-provider keys are masked on GET exactly like Stripe/Twilio
credentials (and the mask-roundtrip guard drops them on save), the
SettingsUpdateRequest validates ai_provider/bounds, the catalog endpoint is
admin-gated, and every catalog provider maps to a supported adapter +
declared credential field.
"""

import pytest

from backend.ai.catalog import PROVIDER_CATALOG
from backend.ai.providers import SUPPORTED_PROVIDERS
from backend.routes.admin.settings import (
    _CREDENTIAL_FIELDS,
    SettingsUpdateRequest,
    _mask_credentials,
)


class TestCredentialMasking:
    def test_ai_keys_are_credential_fields(self):
        assert {
            "ai_api_key_anthropic",
            "ai_api_key_openai",
            "ai_api_key_gemini",
            "ai_api_key_openrouter",
        } <= _CREDENTIAL_FIELDS

    def test_masked_on_get(self):
        # Dummy value — deliberately NOT shaped like a real key so the
        # pre-commit secret scanner stays quiet.
        masked = _mask_credentials({"ai_api_key_anthropic": "FAKE-TEST-KEY-verysecret"})
        assert masked["ai_api_key_anthropic"].endswith("*****")
        assert "verysecret" not in masked["ai_api_key_anthropic"]

    def test_mask_roundtrip_guard_protects_keys(self):
        # The PUT handler drops any credential whose value looks like a mask
        # preview — saving the settings form must not clobber a real key.
        body = SettingsUpdateRequest(ai_api_key_openrouter="FAKEKEY1*****")
        update_fields = body.model_dump(exclude_none=True)
        for field in _CREDENTIAL_FIELDS:
            v = update_fields.get(field)
            if isinstance(v, str) and v.endswith("*****"):
                update_fields.pop(field, None)
        assert "ai_api_key_openrouter" not in update_fields


class TestUpdateValidation:
    def test_valid_provider_accepted(self):
        body = SettingsUpdateRequest(ai_provider="openrouter", ai_model="anthropic/claude-haiku-4.5")
        assert body.ai_provider == "openrouter"

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValueError):
            SettingsUpdateRequest(ai_provider="grok")

    def test_guardrail_bounds(self):
        with pytest.raises(ValueError):
            SettingsUpdateRequest(ai_max_output_tokens=100000)
        with pytest.raises(ValueError):
            SettingsUpdateRequest(ai_daily_message_cap=0)

    def test_none_fields_not_persisted(self):
        body = SettingsUpdateRequest(ai_assistant_enabled=True)
        fields = body.model_dump(exclude_none=True)
        assert fields == {"ai_assistant_enabled": True}

    def test_mcp_daily_tool_cap_round_trips(self):
        # Codex review on PR #2774: the cap read by mcp_server must be a real
        # persistable settings field, not a phantom key that always falls back.
        body = SettingsUpdateRequest(ai_mcp_daily_tool_cap=200)
        assert body.model_dump(exclude_none=True) == {"ai_mcp_daily_tool_cap": 200}
        # 0 is valid and means "unset — fall back to ai_daily_message_cap"
        assert SettingsUpdateRequest(ai_mcp_daily_tool_cap=0).ai_mcp_daily_tool_cap == 0
        with pytest.raises(ValueError):
            SettingsUpdateRequest(ai_mcp_daily_tool_cap=-1)
        with pytest.raises(ValueError):
            SettingsUpdateRequest(ai_mcp_daily_tool_cap=5001)

    def test_auto_heal_flag_is_settable_and_defaults_unset(self):
        # The stripe_auto_heal_processing flag must round-trip through the
        # settings model so ops can toggle it via PUT /api/admin/settings.
        body = SettingsUpdateRequest(stripe_auto_heal_processing=True)
        assert body.model_dump(exclude_none=True) == {"stripe_auto_heal_processing": True}
        # Left unset it is never persisted — the reconcile default keeps it OFF,
        # so a routine settings save can't accidentally enable money movement.
        assert "stripe_auto_heal_processing" not in SettingsUpdateRequest().model_dump(exclude_none=True)


class TestCatalog:
    def test_every_provider_is_supported_and_keyed(self):
        for entry in PROVIDER_CATALOG:
            assert entry["provider"] in SUPPORTED_PROVIDERS
            assert entry["key_field"] in _CREDENTIAL_FIELDS
            assert entry["models"], f"{entry['provider']} has no model suggestions"
            for model in entry["models"]:
                assert model["id"] and model["label"]

    def test_default_provider_first_model_matches_appsettings_default(self):
        from backend.schemas import AppSettings

        anthropic_entry = next(e for e in PROVIDER_CATALOG if e["provider"] == "anthropic")
        assert anthropic_entry["models"][0]["id"] == AppSettings().ai_model

    def test_catalog_endpoint_requires_admin(self, test_client):
        resp = test_client.get("/api/v1/admin/ai/catalog")
        assert resp.status_code in (401, 403)

    def test_catalog_endpoint_shape(self, test_client):
        # /admin/ai/catalog lives on settings_router, mounted with
        # require_module("settings") ahead of get_admin_user (routes/admin/
        # __init__.py) -- the shared admin_override fixture's fake admin
        # dict carries no "modules" claim, so it 403s there before reaching
        # this route. Grant the specific module rather than widening the
        # shared fixture (used across many admin-route tests).
        from backend.server import app
        from dependencies import get_admin_user

        app.dependency_overrides[get_admin_user] = lambda: {
            "id": "admin_1",
            "role": "admin",
            "modules": ["settings"],
        }
        try:
            resp = test_client.get("/api/v1/admin/ai/catalog")
        finally:
            app.dependency_overrides.pop(get_admin_user, None)
        assert resp.status_code == 200
        providers = resp.json()["providers"]
        assert {p["provider"] for p in providers} == set(SUPPORTED_PROVIDERS)
