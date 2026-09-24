import pytest

from backend.core.background_loop_registry import (
    WORKER_WAVE1_LOOP_NAMES,
    resolve_worker_loop_allowlist,
    should_spawn_on_api,
    worker_loop_names,
)


def test_allowlist_selects_exactly_one_api_or_worker_owner():
    for selected_name in WORKER_WAVE1_LOOP_NAMES:
        selected = resolve_worker_loop_allowlist(selected_name)
        for loop_name in WORKER_WAVE1_LOOP_NAMES:
            api_owns = should_spawn_on_api(loop_name, "api", selected)
            worker_owns = loop_name in worker_loop_names(selected)
            assert int(api_owns) + int(worker_owns) == 1
            assert worker_owns is (loop_name == selected_name)


@pytest.mark.parametrize(
    "invalid",
    ("", " ", "unknown", "push_retry (30s),push_retry (30s)", "push_retry (30s),"),
)
def test_allowlist_rejects_empty_unknown_duplicates_and_empty_items(invalid):
    with pytest.raises(RuntimeError):
        resolve_worker_loop_allowlist(invalid)


def test_unset_allowlist_preserves_full_wave_and_all_role_behavior():
    assert resolve_worker_loop_allowlist() == WORKER_WAVE1_LOOP_NAMES
    assert all(should_spawn_on_api(name, "all", ()) for name in WORKER_WAVE1_LOOP_NAMES)


def test_driver_readiness_reconciler_runs_on_api_after_offer_expiry_reaper():
    from backend.core.background_loop_registry import LOOP_CATALOG, LOOP_PLACEMENT

    names = [name for name, _placement in LOOP_CATALOG]
    assert LOOP_PLACEMENT["driver_readiness_reconciler (20s)"] == "api"
    assert names.index("driver_readiness_reconciler (20s)") == names.index("offer_expiry_reaper (10s)") + 1
