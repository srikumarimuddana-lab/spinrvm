import pytest

from backend.scripts import seed_loadtest_bots as seed


STAGING_REF = "abcdefghijklmnopqrst"
STAGING_URL = f"https://{STAGING_REF}.supabase.co"


def env(**overrides):
    values = {
        "ENV": "staging",
        "SUPABASE_URL": STAGING_URL,
        "SUPABASE_SERVICE_ROLE_KEY": "test-key",
        "EXPECTED_SUPABASE_PROJECT_REF": STAGING_REF,
    }
    values.update(overrides)
    return values


def test_requires_independently_configured_expected_project_ref():
    with pytest.raises(ValueError, match="EXPECTED_SUPABASE_PROJECT_REF"):
        seed._validate_target_environment(env(EXPECTED_SUPABASE_PROJECT_REF=""))


def test_import_does_not_initialize_supabase_client():
    assert seed.db_supabase is None


def test_keeps_environment_allowlist_before_any_target_is_accepted():
    with pytest.raises(ValueError, match="ENV"):
        seed._validate_target_environment(env(ENV="production"))
    with pytest.raises(ValueError, match="ENV"):
        seed._validate_target_environment(env(ENV=" staging "))


def test_requires_exact_project_ref_match_even_in_staging():
    with pytest.raises(ValueError, match="does not match"):
        seed._validate_target_environment(env(EXPECTED_SUPABASE_PROJECT_REF="zyxwvutsrqponmlkjihg"))


def test_rejects_known_production_project_even_when_it_is_expected():
    production_ref = "soavhtdhefowwvforzwb"
    with pytest.raises(ValueError, match="production project"):
        seed._validate_target_environment(
            env(
                SUPABASE_URL=f"https://{production_ref}.supabase.co",
                EXPECTED_SUPABASE_PROJECT_REF=production_ref,
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "http://abcdefghijklmnopqrst.supabase.co",
        "https://abcdefghijklmnopqrst.supabase.co.evil.test",
        "https://user:pass@abcdefghijklmnopqrst.supabase.co",
        "https://abcdefghijklmnopqrst.supabase.co/path",
        "https://abcdefghijklmnopqrst.supabase.co?query=1",
        "https://abcdefghijklmnopqrst.supabase.co?",
        "https://abcdefghijklmnopqrst.supabase.co#",
        " https://abcdefghijklmnopqrst.supabase.co",
        "https://abcdefghijklmnopqrst.supabase.co\t.evil.test",
        "https://abcdefghijklmnopqrst.supabase.co\n.evil.test",
        "https://db.abcdefghijklmnopqrst.supabase.co",
    ],
)
def test_rejects_malformed_or_noncanonical_supabase_urls(url):
    with pytest.raises(ValueError):
        seed._validate_target_environment(env(SUPABASE_URL=url))


def test_staging_target_guard_returns_only_after_exact_match():
    assert seed._validate_target_environment(env()) == STAGING_REF
    with pytest.raises(ValueError, match="whitespace"):
        seed._validate_target_environment(env(EXPECTED_SUPABASE_PROJECT_REF=f" {STAGING_REF}"))


@pytest.mark.parametrize(
    "bad_env",
    [
        env(EXPECTED_SUPABASE_PROJECT_REF=""),
        env(EXPECTED_SUPABASE_PROJECT_REF="zyxwvutsrqponmlkjihg"),
        env(SUPABASE_URL="not-a-url"),
        env(ENV="production"),
        env(SUPABASE_URL="https://soavhtdhefowwvforzwb.supabase.co", EXPECTED_SUPABASE_PROJECT_REF="soavhtdhefowwvforzwb"),
    ],
)
def test_main_rejects_target_before_database_client_or_writes(monkeypatch, bad_env):
    import sys
    from unittest.mock import AsyncMock, patch

    for key in (
        "ENV",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "EXPECTED_SUPABASE_PROJECT_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in bad_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["seed_loadtest_bots.py"])
    monkeypatch.setattr(seed, "load_dotenv", lambda: None)

    with patch.object(seed, "_initialize_db_supabase") as initialize_client, patch.object(
        seed, "_ensure_vehicle_type_and_area", new=AsyncMock()
    ) as seed_area:
        with pytest.raises(SystemExit):
            import asyncio

            asyncio.run(seed.main())
    initialize_client.assert_not_called()
    seed_area.assert_not_awaited()
