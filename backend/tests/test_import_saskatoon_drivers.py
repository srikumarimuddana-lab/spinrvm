"""Unit tests for scripts/import_saskatoon_drivers.py helper logic.

The script imports `supabase_client` (backend package, module-level client) at
import time; we stub it so the pure helpers are testable without credentials.
Only DB-free logic is covered here — date ambiguity detection and document
status validation — plus the constants the resume path relies on.
"""

import importlib.util
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_importer():
    mod_name = "import_saskatoon_drivers_under_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    # Stub the module-level Supabase client the script imports.
    if "supabase_client" not in sys.modules:
        stub = types.ModuleType("supabase_client")
        stub.supabase = None
        sys.modules["supabase_client"] = stub
    spec = importlib.util.spec_from_file_location(mod_name, REPO_ROOT / "scripts" / "import_saskatoon_drivers.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


class TestDateAmbiguity:
    def test_slash_date_with_two_valid_readings_is_ambiguous(self):
        imp = _load_importer()
        # 03/04/25 → Apr 3 day-first, Mar 4 month-first
        assert imp.date_is_ambiguous("03/04/25") is True

    def test_day_over_12_is_unambiguous(self):
        imp = _load_importer()
        # 25/04/25 → only day-first parses
        assert imp.date_is_ambiguous("25/04/25") is False

    def test_iso_and_sentinels_are_unambiguous(self):
        imp = _load_importer()
        assert imp.date_is_ambiguous("2025-04-03") is False
        assert imp.date_is_ambiguous("indefinite") is False
        assert imp.date_is_ambiguous("") is False

    def test_parse_date_still_returns_first_match(self):
        imp = _load_importer()
        parsed = imp.parse_date("03/04/25")
        assert parsed is not None
        # Day-first formats are tried before month-first for '/' dates.
        assert (parsed.day, parsed.month) == (3, 4)


class TestDocStatusValidation:
    def test_valid_statuses_match_backend_review_flow(self):
        imp = _load_importer()
        assert imp.VALID_DOC_STATUSES == {"pending", "approved", "rejected"}

    def test_import_source_constant_matches_metadata(self):
        imp = _load_importer()
        assert imp.IMPORT_SOURCE == "legacy_saskatoon_driver_import"
