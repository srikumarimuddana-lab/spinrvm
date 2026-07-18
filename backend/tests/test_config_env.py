"""Environment-model tests for Settings (ADR-008).

Covers ENV/DEPLOY_STAGE validation, the is_production / is_production_like /
is_canary properties, and the extension of the production secret guards to
staging. Pure Settings construction — no fixtures or DB needed.
"""

import pytest

try:
    from backend.core.config import Settings
except ImportError:
    from core.config import Settings

# A configuration that must satisfy every production-like guard.
# Dummy test values only — dict-literal keys keep the pre-commit secret
# scanner from matching kwarg-style assignments.
STRONG = {
    "JWT_SECRET": "x" * 48,
    "ADMIN_PASSWORD": "unit-test-dummy-credential-42",
    "FIREBASE_DRIVER_APP_ID": "1:1:android:driver",
    "FIREBASE_RIDER_APP_ID": "1:1:android:rider",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
    "SUPABASE_REGION": "ca-central-1",
}

WEAK = {"JWT_SECRET": "short", "ADMIN_PASSWORD": "admin123"}


def make(**overrides):
    kwargs = {"_env_file": None, **overrides}
    return Settings(**kwargs)


class TestEnvValidation:
    @pytest.mark.parametrize("env", ["prod", "Production ", "stage", "qa", ""])
    def test_unknown_env_refuses_to_boot(self, env):
        with pytest.raises(ValueError, match="not a recognised environment"):
            make(ENV=env, **STRONG)

    @pytest.mark.parametrize("env", ["development", "test", "staging", "production"])
    def test_known_envs_accepted(self, env):
        assert make(ENV=env, **STRONG).ENV == env

    def test_env_normalised_to_lowercase(self):
        assert make(ENV="STAGING", **STRONG).ENV == "staging"

    def test_unknown_deploy_stage_refused(self):
        with pytest.raises(ValueError, match="DEPLOY_STAGE"):
            make(ENV="production", DEPLOY_STAGE="blue", **STRONG)

    def test_deploy_stage_default_stable(self):
        assert make(ENV="development", **WEAK).DEPLOY_STAGE == "stable"


class TestProperties:
    @pytest.mark.parametrize(
        "env,stage,is_prod,prod_like,canary",
        [
            ("development", "stable", False, False, False),
            ("test", "stable", False, False, False),
            ("staging", "stable", False, True, False),
            ("production", "stable", True, True, False),
            ("production", "canary", True, True, True),
            # canary stage outside production is not canary
            ("staging", "canary", False, True, False),
        ],
    )
    def test_truth_table(self, env, stage, is_prod, prod_like, canary):
        s = make(ENV=env, DEPLOY_STAGE=stage, **STRONG)
        assert s.is_production is is_prod
        assert s.is_production_like is prod_like
        assert s.is_canary is canary

    def test_background_loops_default_enabled(self):
        assert make(ENV="development", **WEAK).BACKGROUND_LOOPS_ENABLED is True

    def test_release_sha_default_empty(self):
        assert make(ENV="development", **WEAK).RELEASE_SHA == ""


class TestStagingGuards:
    """ADR-008: staging must fail the same startup guards production does."""

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_short_jwt_secret_refused(self, env):
        cfg = {**STRONG, "JWT_SECRET": "short"}
        with pytest.raises(ValueError, match="JWT_SECRET"):
            make(ENV=env, **cfg)

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_weak_admin_password_refused(self, env):
        cfg = {**STRONG, "ADMIN_PASSWORD": "admin123"}
        with pytest.raises(ValueError, match="known-weak"):
            make(ENV=env, **cfg)

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_non_canadian_region_refused(self, env):
        cfg = {**STRONG, "SUPABASE_REGION": "us-east-1"}
        with pytest.raises(ValueError, match="not a Canadian"):
            make(ENV=env, **cfg)

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_missing_firebase_app_ids_refused(self, env):
        cfg = {**STRONG, "FIREBASE_DRIVER_APP_ID": ""}
        with pytest.raises(ValueError, match="FIREBASE_DRIVER_APP_ID"):
            make(ENV=env, **cfg)

    def test_staging_strong_config_boots(self):
        s = make(ENV="staging", **STRONG)
        assert s.is_production_like and not s.is_production

    @pytest.mark.parametrize("env", ["development", "test"])
    def test_dev_and_test_skip_guards(self, env):
        # weak values still boot outside production-like envs
        assert make(ENV=env, **WEAK).ENV == env
