#!/usr/bin/env python3
"""Bulk-import legacy Saskatoon drivers and document files (CLI wrapper).

The parsing/validation/commit logic lives in
``backend/services/driver_import_service.py`` so it can be shared with the admin
dashboard's HTTP import flow. This script is a thin CLI on top of it and
re-exports the service's public symbols for backwards compatibility (tests and
any callers that import from this module by path).

Dry-run first:
  python3 scripts/import_saskatoon_drivers.py \
    --drivers-csv bulk-import-saskatoon/drivers.csv \
    --documents-csv bulk-import-saskatoon/documents.csv \
    --files-root bulk-import-saskatoon \
    --service-area-name Saskatoon \
    --dry-run

Commit after the dry-run report is clean:
  python3 scripts/import_saskatoon_drivers.py ... --commit

The importer intentionally prints old_driver_id values, not raw PII. Keep CSVs
and reports in a secure location because the input files contain PIPEDA-covered
personal information.

Re-running after a partial failure is safe for drivers: rows whose driver was
already created by this importer (matched on legacy_import_metadata
old_driver_id + source) are skipped with a warning, and their documents are
deduplicated against existing driver_documents rows, so a crashed --commit can
be resumed by simply running --commit again. Caveat: the commit order is users
-> drivers -> files -> documents, so a failure between the users and drivers
inserts leaves user rows without drivers; those surface as "matching user ...
already exists" errors and need manual cleanup before resuming.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))


def _load_import_core():
    """Load the shared import core as a top-level module.

    The logic lives in ``backend/services/driver_import_service.py``. We load it
    by file path rather than ``from services.driver_import_service import ...``
    because ``backend/services/__init__.py`` eagerly imports the dispatch/fare
    services, which pull in FastAPI and the DB layer — the CLI only needs the
    (dependency-light) importer core. Loaded top-level, the module's dual-import
    falls back to ``from supabase_client import ...`` / ``from utils...``.
    """
    if "driver_import_service" in sys.modules:
        return sys.modules["driver_import_service"]
    path = REPO_ROOT / "backend" / "services" / "driver_import_service.py"
    spec = importlib.util.spec_from_file_location("driver_import_service", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["driver_import_service"] = module
    spec.loader.exec_module(module)
    return module


# Re-export the shared import core so existing callers/tests that load this
# script by path keep working (they read parse_date, date_is_ambiguous,
# VALID_DOC_STATUSES, IMPORT_SOURCE, etc. as module attributes).
_core = _load_import_core()
DATE_FORMATS = _core.DATE_FORMATS
DOCUMENT_REQUIREMENT_ALIASES = _core.DOCUMENT_REQUIREMENT_ALIASES
DRIVER_DATE_FIELDS = _core.DRIVER_DATE_FIELDS
FALSY = _core.FALSY
IMPORT_SOURCE = _core.IMPORT_SOURCE
REQUIRED_DRIVER_COLUMNS = _core.REQUIRED_DRIVER_COLUMNS
TRUTHY = _core.TRUTHY
VALID_DOC_STATUSES = _core.VALID_DOC_STATUSES
ImportErrorItem = _core.ImportErrorItem
ImportPlan = _core.ImportPlan
build_plan = _core.build_plan
canonical_requirement_key = _core.canonical_requirement_key
commit_plan = _core.commit_plan
date_is_ambiguous = _core.date_is_ambiguous
encrypt_pii = _core.encrypt_pii
get_service_area = _core.get_service_area
iso_date = _core.iso_date
normalize_header = _core.normalize_header
normalize_phone = _core.normalize_phone
parse_bool = _core.parse_bool
parse_date = _core.parse_date
print_report = _core.print_report
read_csv = _core.read_csv
regulatory_authority_defaults = _core.regulatory_authority_defaults
slug = _core.slug
split_name = _core.split_name
storage_signed_url = _core.storage_signed_url
supabase = _core.supabase
vehicle_type_map = _core.vehicle_type_map
work_auth_status = _core.work_auth_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk import legacy Saskatoon drivers and document files")
    parser.add_argument("--drivers-csv", type=Path, required=True)
    parser.add_argument("--documents-csv", type=Path, required=True)
    parser.add_argument("--files-root", type=Path, required=True)
    parser.add_argument("--service-area-id")
    parser.add_argument("--service-area-name", default="Saskatoon")
    parser.add_argument("--batch", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--commit", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if supabase is None:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured")
    drivers = read_csv(args.drivers_csv)
    docs = read_csv(args.documents_csv)
    service_area = get_service_area(args.service_area_id, args.service_area_name)
    plan = build_plan(drivers, docs, args.files_root, service_area, args.batch)
    print_report(plan, dry_run=args.dry_run)
    if plan.errors:
        return 2
    if args.commit:
        commit_plan(plan)
        print("Committed Saskatoon driver import successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
