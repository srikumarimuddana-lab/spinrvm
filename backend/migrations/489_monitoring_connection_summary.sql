-- 489: aggregate-only connection stats for the Grafana `grafana_monitor` role.
--
-- grafana_monitor (created out-of-band 2026-09-25 with a password, so not in
-- a migration -- see docs/change-log/2026-09-25-metrics-agent-supabase-redis-metrics.md)
-- deliberately lacks pg_read_all_stats: that role would expose every
-- session's query text, which can carry PII. Without it, pg_stat_activity
-- hides backend_type/state for other sessions, so a plain
-- `count(*) ... where backend_type = 'client backend'` from grafana_monitor
-- counts only its own session (verified: 1 vs. the real 7).
--
-- This SECURITY DEFINER function returns counts and ages only -- never
-- query text, client addresses, or application-supplied strings -- so the
-- monitor gets a correct picture without the query-text exposure.
--
-- Idempotent: safe to re-run (applied to production via the Supabase MCP
-- connector first, then again by run_migrations.py when this file lands),
-- and safe on an environment where grafana_monitor doesn't exist.

create schema if not exists monitoring;
revoke all on schema monitoring from public;

create or replace function monitoring.connection_summary()
returns table (
    role_name text,
    state text,
    connections bigint,
    oldest_xact_seconds numeric,
    waiting_on_lock bigint
)
language sql
stable
security definer
set search_path = pg_catalog, pg_temp
as $$
    select
        coalesce(a.usename::text, '(none)'),
        coalesce(a.state, '(unknown)'),
        count(*),
        round(max(extract(epoch from (now() - a.xact_start)))::numeric, 1),
        count(*) filter (where a.wait_event_type = 'Lock')
    from pg_stat_activity a
    where a.backend_type = 'client backend'
    group by 1, 2
$$;

revoke all on function monitoring.connection_summary() from public;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'grafana_monitor') then
        grant usage on schema monitoring to grafana_monitor;
        grant execute on function monitoring.connection_summary() to grafana_monitor;
    end if;
end
$$;

-- Rollback:
--   drop function if exists monitoring.connection_summary();
--   drop schema if exists monitoring;
