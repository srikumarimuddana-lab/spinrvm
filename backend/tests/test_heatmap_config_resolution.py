"""Resolution and clamping of per-service-area heatmap config.

Chain under test: per-area override → global app_settings → code default,
with an unconditional clamp at the end.

The clamp matters more than it looks. Both upstream sources are ordinary DB
rows, so a direct SQL edit, a migration, or a bulk script can put anything in
them without passing through the admin API's validation — and two of the
values are not cosmetic: ``k_floor`` is a PIPEDA k-anonymity control, and
``refresh_seconds`` multiplies across every online driver.
"""

import pytest

from utils.heatmap_config import (
    HEATMAP_SPEC,
    config_fingerprint,
    describe_overrides,
    resolve_heatmap_config,
)

pytestmark = [pytest.mark.unit]


# ── Defaults ────────────────────────────────────────────────────────────


def test_no_sources_yields_code_defaults():
    """An unconfigured install must reproduce the previously-hardcoded values."""
    cfg = resolve_heatmap_config()
    assert cfg["k_floor"] == 3
    assert cfg["cell_lat_deg"] == 0.004
    assert cfg["cell_lng_deg"] == 0.006
    assert cfg["decay_half_life_days"] == 3.0
    assert cfg["refresh_seconds"] == 90
    # Windows that were literals in the endpoint before this change.
    assert cfg["live_window_days"] == 7
    assert cfg["now_window_minutes"] == 10
    assert cfg["baseline_window_days"] == 28
    assert cfg["scheduled_lookahead_hours"] == 2
    assert cfg["forecast_hours_ahead"] == 6
    assert cfg["forecast_lookback_days"] == 28


def test_every_spec_key_is_always_present_and_typed():
    cfg = resolve_heatmap_config({"heatmap_config": {}}, {})
    assert set(cfg) == set(HEATMAP_SPEC)
    for name, spec in HEATMAP_SPEC.items():
        assert isinstance(cfg[name], int if spec.kind == "int" else float), name


# ── Precedence ──────────────────────────────────────────────────────────


def test_global_settings_override_defaults():
    cfg = resolve_heatmap_config(None, {"heatmap_k_floor": 7})
    assert cfg["k_floor"] == 7


def test_area_override_beats_global():
    cfg = resolve_heatmap_config(
        {"heatmap_config": {"k_floor": 12}},
        {"heatmap_k_floor": 7},
    )
    assert cfg["k_floor"] == 12


def test_unset_area_keys_still_inherit_the_global():
    """Overrides are sparse: setting one key must not freeze the others.

    This is why the column stores only overridden keys rather than a full
    snapshot — a snapshot would silently pin an area to whatever the global
    happened to be on the day someone opened the form.
    """
    cfg = resolve_heatmap_config(
        {"heatmap_config": {"k_floor": 12}},
        {"heatmap_k_floor": 7, "heatmap_refresh_seconds": 300},
    )
    assert cfg["k_floor"] == 12
    assert cfg["refresh_seconds"] == 300


def test_window_keys_are_area_and_default_only():
    """Windows have no global key — they are per-market by design."""
    cfg = resolve_heatmap_config(
        {"heatmap_config": {"baseline_window_days": 56}},
        {},
    )
    assert cfg["baseline_window_days"] == 56
    assert resolve_heatmap_config(None, {})["baseline_window_days"] == 28


# ── Clamping ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,value,expected",
    [
        # k_floor 0 or negative would disable the privacy floor entirely.
        ("k_floor", 0, 1),
        ("k_floor", -5, 1),
        ("k_floor", 999, 50),
        # refresh_seconds 1 would turn the whole online fleet into 1s pollers.
        ("refresh_seconds", 1, 30),
        ("refresh_seconds", 0, 30),
        ("refresh_seconds", 99999, 600),
        # cell size 0 divides by zero in the cell-key math.
        ("cell_lat_deg", 0, 0.0005),
        ("cell_lng_deg", -1, 0.0005),
        ("cell_lat_deg", 10, 0.05),
        ("decay_half_life_days", 0, 0.5),
        ("live_window_days", 0, 1),
        ("live_window_days", 3650, 30),
        ("now_window_minutes", 1, 5),
        ("baseline_window_days", 1, 7),
        ("scheduled_lookahead_hours", 0, 1),
        ("forecast_hours_ahead", 100, 24),
    ],
)
def test_out_of_range_area_override_is_clamped(key, value, expected):
    cfg = resolve_heatmap_config({"heatmap_config": {key: value}}, {})
    assert cfg[key] == expected


def test_out_of_range_global_is_clamped_too():
    """A hostile global value must not escape by skipping the area layer."""
    cfg = resolve_heatmap_config(None, {"heatmap_refresh_seconds": 1, "heatmap_k_floor": 0})
    assert cfg["refresh_seconds"] == 30
    assert cfg["k_floor"] == 1


# ── Malformed input ─────────────────────────────────────────────────────


@pytest.mark.parametrize("junk", ["abc", None, {}, [], "", float("nan")])
def test_unusable_area_value_falls_through_to_the_next_source(junk):
    """Garbage must fall through the chain, never raise — this runs per poll."""
    cfg = resolve_heatmap_config(
        {"heatmap_config": {"k_floor": junk}},
        {"heatmap_k_floor": 9},
    )
    assert cfg["k_floor"] == 9


def test_unusable_value_in_both_sources_lands_on_the_default():
    cfg = resolve_heatmap_config(
        {"heatmap_config": {"k_floor": "abc"}},
        {"heatmap_k_floor": None},
    )
    assert cfg["k_floor"] == 3


@pytest.mark.parametrize("bad_column", ["not json", "[]", "null", 42, [], None])
def test_malformed_config_column_is_ignored(bad_column):
    """A non-object column must degrade to inheritance, not an exception."""
    cfg = resolve_heatmap_config({"heatmap_config": bad_column}, {"heatmap_k_floor": 5})
    assert cfg["k_floor"] == 5


def test_jsonb_returned_as_a_json_string_is_still_honoured():
    """Some drivers hand JSONB back as text; both shapes must work."""
    cfg = resolve_heatmap_config({"heatmap_config": '{"k_floor": 11}'}, {})
    assert cfg["k_floor"] == 11


def test_unknown_keys_in_the_column_are_ignored():
    cfg = resolve_heatmap_config({"heatmap_config": {"nonsense": 1, "k_floor": 4}}, {})
    assert cfg["k_floor"] == 4
    assert "nonsense" not in cfg


# ── Cache fingerprint ───────────────────────────────────────────────────


def test_fingerprint_is_stable_for_identical_config():
    a = resolve_heatmap_config({"heatmap_config": {"k_floor": 5}}, {})
    b = resolve_heatmap_config({"heatmap_config": {"k_floor": 5}}, {})
    assert config_fingerprint(a) == config_fingerprint(b)


def test_fingerprint_changes_when_any_key_changes():
    """The cache key embeds this, so a tuning change must invalidate it.

    Without this, tightening an area's k-anonymity floor would keep serving
    cells built under the looser floor until the TTL lapsed.
    """
    base = resolve_heatmap_config(None, {})
    for key, spec in HEATMAP_SPEC.items():
        bumped = resolve_heatmap_config({"heatmap_config": {key: spec.default + 1}}, {})
        if bumped[key] == base[key]:
            continue  # default already at its ceiling
        assert config_fingerprint(bumped) != config_fingerprint(base), key


def test_fingerprint_ignores_key_ordering():
    a = {k: v for k, v in sorted(resolve_heatmap_config().items())}
    b = {k: v for k, v in sorted(resolve_heatmap_config().items(), reverse=True)}
    assert config_fingerprint(a) == config_fingerprint(b)


# ── Override introspection (admin UI) ───────────────────────────────────


def test_describe_overrides_returns_only_explicit_keys():
    """The UI must distinguish 'inherits' from 'set to the same number'.

    They differ the moment the global changes.
    """
    out = describe_overrides({"heatmap_config": {"k_floor": 5}})
    assert out == {"k_floor": 5}


def test_describe_overrides_clamps_and_drops_junk():
    out = describe_overrides({"heatmap_config": {"k_floor": 0, "refresh_seconds": "abc", "bogus": 1}})
    assert out == {"k_floor": 1}


def test_describe_overrides_on_a_clean_area_is_empty():
    assert describe_overrides({"heatmap_config": {}}) == {}
    assert describe_overrides(None) == {}
