#!/usr/bin/env python3
"""
Spinr database migration runner (OPS-002).

Applies SQL migration files in alphanumeric order, skipping any that have
already been recorded in the schema_migrations tracking table.

Usage:
    python backend/scripts/migrate.py [--dry-run] [--env ENV] [--yes]

    --env labels the run (development|test|staging|production) and is printed
        in the logs; --env production additionally requires typing the word
        "production" at an interactive prompt unless --yes is passed
        (CI passes --yes; its human gate is the GitHub Environment approval).
        The flag does NOT select the database — connection info comes from the
        environment variables below, so export the right DSN for the tier.

Environment variables required:
    SUPABASE_URL              — e.g. https://xxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY — Supabase service role key (not anon key)

Optional:
    PG_CONNECTION_STRING / DATABASE_URL — full Postgres DSN. When set, it is used
        verbatim and the direct db.<ref>.supabase.co host is NOT derived. That
        direct host is IPv6-only on current Supabase projects and fails to resolve
        on many IPv4-only networks; point this at the Session pooler instead
        (user postgres.<ref> @ aws-N-<region>.pooler.supabase.com:5432). Takes
        precedence over SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.
    MIGRATIONS_DIR — path to migration files (default: backend/migrations)
    EXPECTED_PROJECT_REF — when set, the run refuses to proceed unless the
        Supabase project ref appears in the DSN/SUPABASE_URL. Cheap guard
        against pointing a production migration at the wrong database
        (CI sets this per GitHub Environment, ADR-008).
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

    # Direct DSN override. When PG_CONNECTION_STRING (or DATABASE_URL) is set we
    # use it verbatim and skip deriving the `db.<ref>.supabase.co` host — that
    # host is IPv6-only on current Supabase projects and fails to resolve on many
    # IPv4-only networks. Point this at the Session pooler
    # (user postgres.<ref> @ aws-N-<region>.pooler.supabase.com:5432) for IPv4.
    # The value contains a password, so it is never logged.
    pg_dsn = os.environ.get("PG_CONNECTION_STRING") or os.environ.get("DATABASE_URL")
    if pg_dsn:
        try:
            conn = psycopg2.connect(pg_dsn)
            conn.autocommit = False
            logger.info("Connected via PG_CONNECTION_STRING/DATABASE_URL override.")
            return conn
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            sys.exit(1)

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


def _inject_session_variables(conn) -> None:
    """Set PostgreSQL session variables that migrations can read via current_setting().

    Currently injects:
      app.supabase_url — full value of SUPABASE_URL env var, e.g. https://xxxx.supabase.co
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.supabase_url = %s", (supabase_url,))
        conn.commit()
        logger.debug("Injected app.supabase_url session variable")
    except Exception as exc:
        logger.warning("Could not inject session variables: %s", exc)
        conn.rollback()


def get_migration_files(migrations_dir: Path) -> list:
    """Return .sql files in numeric-prefix order (matches run_migrations.py).

    Lexicographic sort mis-orders unpadded prefixes ("224_…" before "48_…"),
    which breaks fresh-environment runs when a later migration ALTERs a table
    created by a two-digit one.
    """
    try:
        from .run_migrations import migration_sort_key
    except ImportError:
        from run_migrations import migration_sort_key  # type: ignore

    pattern = str(migrations_dir / "*.sql")
    files = sorted(glob.glob(pattern), key=lambda f: migration_sort_key(os.path.basename(f)))
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
            # Split on semicolons, then strip leading `--` comment lines from
            # each chunk BEFORE deciding whether to skip it. The previous
            # `stmt.startswith("--")` check threw away any chunk whose first
            # line was a comment — which is every migration that opens with
            # the conventional header/rollback comment block — silently
            # skipping the CREATE INDEX CONCURRENTLY it was written to run.
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            for stmt in statements:
                lines = stmt.splitlines()
                while lines and (not lines[0].strip() or lines[0].strip().startswith("--")):
                    lines.pop(0)
                executable = "\n".join(lines).strip()
                if not executable:
                    continue
                cur.execute(executable)
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


def _check_expected_project_ref() -> None:
    """Refuse to run when EXPECTED_PROJECT_REF doesn't match the target DB.

    The ref (the xxxx in https://xxxx.supabase.co) must appear in whichever
    connection source will be used. Unset = no check (backward compatible).
    """
    expected = os.environ.get("EXPECTED_PROJECT_REF", "").strip()
    if not expected:
        return
    target = (
        os.environ.get("PG_CONNECTION_STRING")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_URL", "")
    )
    if expected not in target:
        logger.error(
            f"EXPECTED_PROJECT_REF={expected!r} does not appear in the configured "
            "connection target — refusing to migrate what looks like the wrong "
            "database. Fix the DSN or unset EXPECTED_PROJECT_REF."
        )
        sys.exit(1)


def _confirm_production(env: str, assume_yes: bool, dry_run: bool) -> None:
    """Interactive guard for --env production (ADR-008).

    CI passes --yes (its human gate is the GitHub Environment approval).
    A human on a TTY must type the literal word "production"; a non-TTY
    run without --yes is refused outright.
    """
    if env != "production" or assume_yes or dry_run:
        return
    if not sys.stdin.isatty():
        logger.error("--env production requires --yes when stdin is not a TTY.")
        sys.exit(1)
    answer = input("Type 'production' to confirm migrating the PRODUCTION database: ")
    if answer.strip() != "production":
        logger.error("Confirmation did not match — aborting.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Spinr database migration runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be applied without executing anything",
    )
    parser.add_argument(
        "--env",
        choices=["development", "test", "staging", "production"],
        help="Label the run with its target tier; production prompts unless --yes. "
        "Connection is still selected by env vars (PG_CONNECTION_STRING etc.).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive production confirmation (for CI)",
    )
    args = parser.parse_args()

    if args.env:
        logger.info(f"Migration target tier: {args.env}")
    else:
        logger.warning("No --env given — consider labelling the run (see --help).")
    _confirm_production(args.env, args.yes, args.dry_run)
    load_dotenv()
    _check_expected_project_ref()

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
    _inject_session_variables(conn)
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
