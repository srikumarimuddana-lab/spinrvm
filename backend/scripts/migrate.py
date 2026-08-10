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
    PG_CONNECTION_STRING / DATABASE_URL — full Postgres DSN. When set, it is used
        verbatim and the direct db.<ref>.supabase.co host is NOT derived. That
        direct host is IPv6-only on current Supabase projects and fails to resolve
        on many IPv4-only networks; point this at the Session pooler instead
        (user postgres.<ref> @ aws-N-<region>.pooler.supabase.com:5432). Takes
        precedence over SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.
    MIGRATIONS_DIR — path to migration files (default: backend/migrations)
"""

import argparse
import glob
import logging
import os
import re
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


_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")

# Matches CREATE/DROP INDEX ... CONCURRENTLY and REINDEX ... CONCURRENTLY —
# the statement forms that require running outside a transaction block.
_CONCURRENTLY_STMT_RE = re.compile(
    r"\b(CREATE\s+(UNIQUE\s+)?INDEX|DROP\s+INDEX|REINDEX)\b.*\bCONCURRENTLY\b",
    re.IGNORECASE | re.DOTALL,
)


def _tokenize_sql(sql: str):
    """Yield (kind, text) chunks covering the whole input, in order.

    kind is one of:
      - "code": ordinary SQL text that may legally contain a top-level
        statement-separating semicolon.
      - "comment": a `-- ...` line comment or `/* ... */` block comment.
      - "literal": a `'...'` string (with `''`-escaped quotes), a `"..."`
        quoted identifier, or a `$tag$...$tag$` dollar-quoted body (standard
        Postgres dollar-quoting used for function bodies; tag may be empty,
        i.e. `$$...$$`, or named, e.g. `$func$...$func$`).

    Semicolons inside "comment" or "literal" chunks are never statement
    separators — only ones inside "code" chunks are.
    """
    i = 0
    n = len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            end = n if j == -1 else j + 1
            yield ("comment", sql[i:end])
            i = end
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
            yield ("comment", sql[i:end])
            i = end
            continue
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j : j + 2] == "''":
                    j += 2
                    continue
                if sql[j] == "'":
                    j += 1
                    break
                j += 1
            else:
                j = n
            yield ("literal", sql[i:j])
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n and sql[j] != '"':
                j += 1
            j = min(j + 1, n)
            yield ("literal", sql[i:j])
            i = j
            continue
        if ch == "$":
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                tag = m.group(0)
                close = sql.find(tag, m.end())
                end = n if close == -1 else close + len(tag)
                yield ("literal", sql[i:end])
                i = end
                continue
        j = i
        while j < n and sql[j] not in "-/'\"$":
            j += 1
        if j == i:
            j += 1
        yield ("code", sql[i:j])
        i = j


def split_sql_statements(sql: str) -> list:
    """Split a SQL script into top-level, semicolon-delimited statements.

    Unlike a naive `sql.split(";")`, this ignores semicolons that appear
    inside `--`/`/* */` comments and inside `'...'` / `"..."` / `$tag$...$tag$`
    literals, so a stray semicolon in a prose comment (or inside a `$$`-quoted
    function body) does not shred the migration into broken fragments.

    Each returned statement retains any leading comment lines that preceded
    it (callers that need the bare executable SQL should strip those, see
    `_strip_leading_comments`).
    """
    statements = []
    buf = []
    for kind, text in _tokenize_sql(sql):
        if kind != "code" or ";" not in text:
            buf.append(text)
            continue
        start = 0
        while True:
            idx = text.find(";", start)
            if idx == -1:
                buf.append(text[start:])
                break
            buf.append(text[start:idx])
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            start = idx + 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _strip_leading_comments(stmt: str) -> str:
    """Drop leading comments (`--` line or `/* ... */` block) and blank lines
    so only executable SQL remains. Uses the same comment-aware tokenizer as
    the splitter, so a `/* ... */` header comment strips just like a `--` one
    — a naive line-based strip only handled the latter."""
    tokens = list(_tokenize_sql(stmt))
    i = 0
    while i < len(tokens):
        kind, text = tokens[i]
        if kind == "comment" or (kind == "code" and not text.strip()):
            i += 1
            continue
        break
    return "".join(text for _, text in tokens[i:]).strip()


def _statement_needs_autocommit(stmt: str) -> bool:
    """True if `stmt` is a CREATE/DROP INDEX or REINDEX ... CONCURRENTLY.

    Only "code" tokens are inspected — a `CONCURRENTLY` mention inside a
    comment (e.g. a rollback-instructions block) does not count, so a
    migration is routed to the non-transactional autocommit path only when
    it actually contains a statement that requires it.
    """
    code_only = "".join(text for kind, text in _tokenize_sql(stmt) if kind == "code")
    return bool(_CONCURRENTLY_STMT_RE.search(code_only))


def apply_migration(conn, version: str, sql: str, dry_run: bool) -> bool:
    """Execute a single migration. Returns True on success.

    Migrations containing CREATE INDEX CONCURRENTLY (or DROP INDEX/REINDEX
    CONCURRENTLY) must run outside a transaction block. For those, we
    temporarily switch the connection to autocommit, execute each statement
    individually, then record the version. All other migrations run inside a
    single transaction.
    """
    if dry_run:
        logger.info(f"  [DRY-RUN] Would apply: {version}")
        return True

    statements = split_sql_statements(sql)
    needs_autocommit = any(_statement_needs_autocommit(stmt) for stmt in statements)

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
    We execute each top-level statement individually — split with
    `split_sql_statements` (comment/literal aware, not a naive `split(";")`)
    so that a mid-comment semicolon or a `$$`-quoted function body cannot
    shred a statement — so that the schema_migrations INSERT can follow
    without being inside the same implicit transaction that CONCURRENTLY
    would reject.
    """
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in split_sql_statements(sql):
                executable = _strip_leading_comments(stmt)
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
