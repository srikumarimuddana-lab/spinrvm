#!/usr/bin/env python3
"""
Spinr database migration runner (OPS-002).

Applies SQL migration files in alphanumeric order, skipping any that have
already been recorded in the schema_migrations tracking table.

Usage:
    python backend/scripts/migrate.py [--dry-run]

Environment variables required:
    SUPABASE_URL              — e.g. https://xxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY — Supabase service role key (not anon key)

Optional:
    MIGRATIONS_DIR — path to migration files (default: backend/migrations)
"""

import argparse
import glob
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate")


def load_dotenv():
    """Load local .env variables if not already set in environment."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


def get_db_connection():
    """
    Return a psycopg2 connection to Supabase Postgres.

    Supabase exposes a direct Postgres connection at:
        postgresql://postgres:[SERVICE_ROLE_KEY]@db.[PROJECT_REF].supabase.co:5432/postgres
    We derive the host from SUPABASE_URL.
    """
    try:
        import psycopg2  # type: ignore
    except ImportError:
        logger.error("psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not supabase_url or not service_role_key:
        logger.error(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.\n"
            "  export SUPABASE_URL=https://xxxx.supabase.co\n"
            "  export SUPABASE_SERVICE_ROLE_KEY=eyJh..."
        )
        sys.exit(1)

    # Extract project ref from URL (https://xxxx.supabase.co → xxxx)
    try:
        project_ref = supabase_url.split("//")[1].split(".")[0]
    except IndexError:
        logger.error(f"Could not parse project ref from SUPABASE_URL: {supabase_url}")
        sys.exit(1)

    host = f"db.{project_ref}.supabase.co"
    dsn = f"host={host} port=5432 dbname=postgres user=postgres password={service_role_key} sslmode=require"
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        sys.exit(1)


def get_migration_files(migrations_dir: Path) -> list:
    """Return sorted list of .sql files in the migrations directory."""
    pattern = str(migrations_dir / "*.sql")
    files = sorted(glob.glob(pattern))
    return files


def get_applied_versions(conn) -> set:
    """Return set of migration versions already recorded in schema_migrations."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version;")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        # Table may not exist yet (first run before 00_schema_migrations_table.sql)
        logger.warning(f"Could not read schema_migrations ({e}). Assuming none applied.")
        conn.rollback()
        return set()


def apply_migration(conn, version: str, sql: str, dry_run: bool) -> bool:
    """Execute a single migration. Returns True on success.

    Migrations containing CREATE INDEX CONCURRENTLY (or DROP INDEX CONCURRENTLY)
    must run outside a transaction block. For those, we temporarily switch the
    connection to autocommit, execute each statement individually, then record
    the version. All other migrations run inside a single transaction.
    """
    if dry_run:
        logger.info(f"  [DRY-RUN] Would apply: {version}")
        return True

    needs_autocommit = "CONCURRENTLY" in sql.upper()

    if needs_autocommit:
        return _apply_migration_autocommit(conn, version, sql)

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING;", (version,)
            )
        conn.commit()
        logger.info(f"  ✅  Applied: {version}")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"  ❌  Failed: {version} — {e}")
        return False


def _apply_migration_autocommit(conn, version: str, sql: str) -> bool:
    """Run a migration that contains CONCURRENTLY statements.

    psycopg2 requires autocommit=True for any statement that cannot run inside
    a transaction block (CREATE/DROP INDEX CONCURRENTLY, VACUUM, CLUSTER, etc.).
    We execute each semicolon-delimited statement individually so that the
    schema_migrations INSERT can follow without being inside the same implicit
    transaction that CONCURRENTLY would reject.
    """
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Split on semicolons; skip blank/comment-only chunks.
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            for stmt in statements:
                if stmt.upper().startswith("--") or not stmt:
                    continue
                cur.execute(stmt)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING;", (version,)
            )
        logger.info(f"  ✅  Applied (autocommit): {version}")
        return True
    except Exception as e:
        logger.error(f"  ❌  Failed: {version} — {e}")
        return False
    finally:
        conn.autocommit = False


def main():
    parser = argparse.ArgumentParser(description="Spinr database migration runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be applied without executing anything",
    )
    args = parser.parse_args()

    # Resolve migrations directory
    script_dir = Path(__file__).parent
    default_migrations_dir = script_dir.parent / "migrations"
    migrations_dir = Path(os.environ.get("MIGRATIONS_DIR", default_migrations_dir))

    if not migrations_dir.is_dir():
        logger.error(f"Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    files = get_migration_files(migrations_dir)
    if not files:
        logger.info("No migration files found.")
        return

    logger.info(f"Found {len(files)} migration file(s) in {migrations_dir}")

    if args.dry_run:
        logger.info("DRY-RUN mode — no changes will be made.\n")

    conn = get_db_connection()
    applied = get_applied_versions(conn)

    pending = [(Path(f).name, f) for f in files if Path(f).name not in applied]

    if not pending:
        logger.info("All migrations are already applied. Nothing to do.")
        conn.close()
        return

    logger.info(f"{len(pending)} migration(s) to apply:")
    failed = 0
    for version, filepath in pending:
        sql = Path(filepath).read_text(encoding="utf-8")
        ok = apply_migration(conn, version, sql, args.dry_run)
        if not ok:
            failed += 1
            # Stop on first failure — later migrations may depend on earlier ones
            logger.error("Stopping due to migration failure.")
            break

    conn.close()

    if failed:
        sys.exit(1)

    if args.dry_run:
        logger.info(f"\nDRY-RUN complete. {len(pending)} migration(s) would be applied.")
    else:
        applied_count = len(pending) - failed
        logger.info(f"\nDone. {applied_count}/{len(pending)} migration(s) applied successfully.")


if __name__ == "__main__":
    main()
