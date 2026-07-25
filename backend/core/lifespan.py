from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

try:
    from core.config import settings
    from db_supabase import run_sync
except ImportError:  # pragma: no cover - import style varies by entrypoint
    from ..core.config import settings  # type: ignore
    from ..db_supabase import run_sync  # type: ignore

from supabase_client import supabase


# Global database reference accessible via app state
async def init_database():
    """Initialize database connection and verify it is reachable.

    The supabase-py client is synchronous; we route the health-check probe
    through run_sync() to avoid blocking the event loop. In production,
    any failure raises — Uvicorn will refuse to serve traffic. In development
    we log a warning so local work without Supabase still boots.
    """
    if not supabase:
        msg = "Supabase client not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing)"
        if settings.ENV.lower() == "production":
            raise RuntimeError(msg)
        logger.warning(f"{msg} — continuing in {settings.ENV} mode")
        return None

    # PIPEDA data-residency check (Rider Phase E 22-2): Supabase must be in a
    # Canadian region (ca-central-1). The URL alone doesn't embed the region,
    # so we rely on the explicit SUPABASE_REGION env var. A missing or non-CA
    # value logs ERROR in production so this surfaces in SRE alerting.
    supabase_region = getattr(settings, "SUPABASE_REGION", "") or ""
    if settings.ENV.lower() == "production":
        if not supabase_region:
            logger.error(
                "PIPEDA 22-2: SUPABASE_REGION is not set. "
                "Set SUPABASE_REGION=ca-central-1 to confirm Canadian data residency."
            )
        elif supabase_region.lower() != "ca-central-1":
            logger.error(
                f"PIPEDA 22-2: SUPABASE_REGION={supabase_region!r} — expected 'ca-central-1'. "
                "Canadian data-residency compliance requires the Supabase project to be in ca-central-1."
            )
        else:
            logger.info(f"Supabase data residency confirmed: region={supabase_region}")
    else:
        logger.info(f"Supabase region: {supabase_region or 'unset (non-production)'}")

    # Active health check — one trivial read against a table that always exists.
    # The `users` table is part of the core schema; a failure here means either
    # the service role key is invalid, the DB is unreachable, or the schema
    # has not been applied.
    try:
        await run_sync(lambda: supabase.table("users").select("id").limit(1).execute())
        logger.info("Supabase connection verified")
    except Exception as e:
        logger.error(f"Supabase health check failed: {e}")
        if settings.ENV.lower() == "production":
            raise
        logger.warning(f"Continuing in {settings.ENV} mode despite health-check failure")

    return supabase


async def cleanup_database(db):
    """Cleanup database connections on shutdown."""
    try:
        # Add any cleanup logic here if needed
        logger.info("Database cleanup completed")
    except Exception as e:
        logger.error(f"Database cleanup error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events"""
    # Size the event-loop DEFAULT ThreadPoolExecutor. IMPORTANT: this does NOT
    # size DB capacity. Every Supabase call goes through run_sync() →
    # run_in_executor(_DB_EXECUTOR, ...), a DEDICATED pool sized by
    # DB_THREAD_POOL_SIZE (repositories/_base.py) — that is the single source of
    # truth for DB throughput. The default executor here is used only by the few
    # non-DB blocking offloads that pass None to run_in_executor (currently the
    # ride-snapshot PNG render + Supabase Storage upload in routes/drivers.py).
    # Those are low-frequency (per ride completion), so a modest pool suffices;
    # the previous 64 here (mislabelled as DB sizing) left ~48 idle threads
    # wasting stack budget on a 1 GB VM. Override with BACKEND_EXECUTOR_WORKERS.
    import asyncio as _asyncio_lifespan
    import os as _os
    from concurrent.futures import ThreadPoolExecutor as _Executor

    executor_size = int(_os.environ.get("BACKEND_EXECUTOR_WORKERS", "16"))
    loop = _asyncio_lifespan.get_event_loop()
    loop.set_default_executor(_Executor(max_workers=executor_size, thread_name_prefix="spinr-misc"))
    logger.info(
        f"Default (non-DB) executor sized to {executor_size} workers; "
        f"DB capacity is the separate _DB_EXECUTOR (DB_THREAD_POOL_SIZE)"
    )

    # Initialize database
    logger.info("Initializing database connection...")
    try:
        db = await init_database()
        app.state.db = db
        logger.info("Database initialized and attached to app state")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Warn operators if Redis is absent in production. Without Redis, OTP
    # lockout state and per-user rate-limit counters are in-process only and
    # are lost on every restart — brute-force protection degrades silently.
    if settings.ENV.lower() == "production" and not any(
        [settings.REDIS_URL, settings.RATE_LIMIT_REDIS_URL, settings.WS_REDIS_URL]
    ):
        logger.error(
            "No Redis URL configured in production. OTP lockout and rate-limit "
            "state are stored in-process and reset on every restart. "
            "Set REDIS_URL (or RATE_LIMIT_REDIS_URL + WS_REDIS_URL) before launch."
        )

    # Configure Stripe SDK globals (timeout, retries, API version pin)
    try:
        from utils.stripe_config import configure_stripe

        configure_stripe()
    except Exception as e:
        logger.error(f"Failed to configure Stripe SDK: {e}", exc_info=True)
        if settings.ENV.lower() == "production":
            raise

    # Start background tasks
    import asyncio

    # Track task handles so we can cancel them cleanly on shutdown.
    background_tasks: list[asyncio.Task] = []

    async def _restartable(name: str, coro_factory):
        """Wrap a background loop so an uncaught crash auto-restarts after 5s."""
        while True:
            try:
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    f"Background task {name!r} crashed — restarting in 5s",
                    exc_info=True,
                )
                await asyncio.sleep(5)

    def _spawn(name: str, coro_factory):
        try:
            task = asyncio.create_task(_restartable(name, coro_factory), name=name)
            background_tasks.append(task)
            logger.info(f"Started background task: {name}")
        except Exception as e:
            logger.error(f"Failed to start background task {name}: {e}", exc_info=True)

    # G5: Subscription expiry warning — checks every 6h for subscriptions
    # expiring within 24h and sends push notifications.
    try:
        from routes.drivers import check_expiring_subscriptions

        _spawn("subscription_expiry (6h)", check_expiring_subscriptions)
    except Exception as e:
        logger.error(f"Failed to import subscription expiry checker: {e}", exc_info=True)

    # Automated surge pricing — recalculates demand/supply ratio every 2 min
    # and updates service_areas.surge_multiplier for auto-managed areas.
    try:
        from utils.surge_engine import surge_recalculation_loop

        _spawn("surge_engine (2min)", surge_recalculation_loop)
    except Exception as e:
        logger.error(f"Failed to import surge pricing engine: {e}", exc_info=True)

    # Scheduled ride dispatcher — checks every 60s for rides due for dispatch
    # and sends 10-minute reminder notifications.
    try:
        from utils.scheduled_rides import scheduled_ride_dispatcher_loop

        _spawn("scheduled_dispatcher (60s)", scheduled_ride_dispatcher_loop)
    except Exception as e:
        logger.error(f"Failed to import scheduled ride dispatcher: {e}", exc_info=True)

    # Payment retry — retries failed Stripe payments every 5 minutes
    try:
        from utils.payment_retry import payment_retry_loop

        _spawn("payment_retry (5min)", payment_retry_loop)
    except Exception as e:
        logger.error(f"Failed to import payment retry service: {e}", exc_info=True)

    # Pre-auth capture sweeper — captures booking-time card holds whose tip
    # window has elapsed, so a hold never lapses uncaptured. Every 5 minutes.
    try:
        from utils.preauth_capture import preauth_capture_loop

        _spawn("preauth_capture (5min)", preauth_capture_loop)
    except Exception as e:
        logger.error(f"Failed to import pre-auth capture sweeper: {e}", exc_info=True)

    # Referral reward payouts — pays referrer/referee rewards once a referee
    # hits the ride threshold. Idempotent via referral_payouts UNIQUE claim.
    # Reward amounts are admin-controlled per service area; a per-area reward of
    # 0 means that side is simply not paid (no global on/off switch).
    try:
        from utils.referral_payout import referral_payout_loop

        _spawn("referral_payout (5min)", referral_payout_loop)
    except Exception as e:
        logger.error(f"Failed to import referral payout loop: {e}", exc_info=True)

    # Driver claim reaper — releases drivers claimed by dispatch whose offer
    # insert never landed (crash/restart), recovering orphaned is_available
    # flags so supply isn't silently eroded. Every 60 seconds.
    try:
        from utils.driver_claim_reaper import driver_claim_reaper_loop

        _spawn("driver_claim_reaper (60s)", driver_claim_reaper_loop)
    except Exception as e:
        logger.error(f"Failed to import driver claim reaper: {e}", exc_info=True)

    # Document expiry alerts — notifies drivers about expiring docs every 12h
    try:
        from utils.document_expiry import document_expiry_loop

        _spawn("document_expiry (12h)", document_expiry_loop)
    except Exception as e:
        logger.error(f"Failed to import document expiry checker: {e}", exc_info=True)

    # Driver onboarding reminders — daily 08:00 local-time pushes for drivers
    # who registered from the driver app but still need vehicle info or docs.
    # Idempotent per driver/reminder/local-date via DB claim log.
    try:
        from utils.driver_onboarding_reminders import driver_onboarding_reminder_loop

        _spawn("driver_onboarding_reminders (15min)", driver_onboarding_reminder_loop)
    except Exception as e:
        logger.error(f"Failed to import driver onboarding reminder loop: {e}", exc_info=True)

    # Corporate wallet auto-top-up — kicks off off-session Stripe charges
    # every 10 minutes for wallets that have dropped below their threshold.
    try:
        from utils.corporate_autotopup import corporate_autotopup_loop

        _spawn("corporate_autotopup (10min)", corporate_autotopup_loop)
    except Exception as e:
        logger.error(f"Failed to import corporate autotopup loop: {e}", exc_info=True)

    # Corporate wallet low-balance email — for accounts with auto-topup OFF,
    # sends a reminder once every 12h while the balance stays below threshold.
    try:
        from utils.corporate_low_balance import corporate_low_balance_loop

        _spawn("corporate_low_balance (1h)", corporate_low_balance_loop)
    except Exception as e:
        logger.error(f"Failed to import corporate low-balance loop: {e}", exc_info=True)

    # Monthly allowance reset — rolls fixed_recurring periods forward and
    # zeroes `used` for non-rollover employee allowances once per hour.
    try:
        from utils.allowance_reset import allowance_reset_loop

        _spawn("allowance_reset (1h)", allowance_reset_loop)
    except Exception as e:
        logger.error(f"Failed to import allowance reset loop: {e}", exc_info=True)

    # Versioned actual-route finalizer — claims one pending route every 15s,
    # retaining observed GPS gaps and never touching fare settlement.
    try:
        from utils.route_finalizer import route_finalizer_loop

        _spawn("route_finalizer (15s)", route_finalizer_loop)
    except Exception as e:
        logger.error(f"Failed to import route finalizer loop: {e}", exc_info=True)

    # GPS capture-gap monitor — records timestamp-only, idempotent audit
    # events for active trips that stop reporting location. It never changes a
    # ride lifecycle or fare and never logs raw coordinates.
    try:
        from utils.route_gap_monitor import route_gap_monitor_loop

        _spawn("route_gap_monitor (15s)", route_gap_monitor_loop)
    except Exception as e:
        logger.error(f"Failed to import route gap monitor loop: {e}", exc_info=True)

    # Presence sweeper REMOVED — Uber/Lyft-style presence model.
    # We no longer flip drivers.is_online=False from a background loop.
    # `is_online` is now pure driver intent (only the driver or an admin
    # writes it); reachability is the Redis presence key. Composing the
    # two at read time prevents the dual-source-of-truth drift class that
    # let a transient WS gap / unconfigured Redis silently corrupt the
    # persistent flag. See backend/utils/driver_online.py for the
    # effective-online helper used by readers.

    # Stale intent reconciler — every 15 min, flips is_online=False for
    # drivers whose app has been unreachable for hours (default 4h, durable
    # drivers.updated_at signal, Redis-healthy gate + presence double-check).
    # Closes the insurance-period audit gap left by force-killed apps without
    # repeating the retired presence_sweeper's 30s-scale mass-flip failure.
    try:
        from utils.stale_intent_reconciler import stale_intent_reconciler_loop

        _spawn("stale_intent_reconciler (15min)", stale_intent_reconciler_loop)
    except Exception as e:
        logger.error(f"Failed to import stale intent reconciler: {e}", exc_info=True)

    # Safety check-in — every 30s: sends a push to riders whose trip has been
    # in_progress for ≥ 20 minutes.  If the rider does not respond within 90s,
    # an open safety incident is created for the trust-and-safety team.
    try:
        from utils.safety_checkin_loop import safety_checkin_loop

        _spawn("safety_checkin (30s)", safety_checkin_loop)
    except Exception as e:
        logger.warning(f"Failed to import safety checkin loop: {e}")

    # PII retention purge — daily SECURITY DEFINER call to anonymize
    # ride GPS at 3y, hard-delete rides at 7y, delete location history
    # / chat / stripe events at 90d, delete expired refresh tokens after
    # a 30d grace period. Closes audit B-P1-6 (Saskatchewan Transportation
    # Act + PIPEDA). The Postgres function is naturally idempotent; the
    # Redis leader lock inside the loop is belt-and-braces.
    try:
        from utils.retention_purge import retention_purge_loop

        _spawn("retention_purge (24h)", retention_purge_loop)
    except Exception as e:
        logger.error(f"Failed to import retention purge loop: {e}", exc_info=True)

    # Data-export object purge — hourly deletion of DSAR export ZIPs from the
    # private data-exports Storage bucket after their 7-day signed-link TTL
    # lapses (PIPEDA data minimization). Idempotent per object; replay-safe.
    try:
        from utils.data_export_purge import data_export_purge_loop

        _spawn("data_export_purge (1h)", data_export_purge_loop)
    except Exception as e:
        logger.error(f"Failed to import data export purge loop: {e}", exc_info=True)

    # Daily Stripe ↔ DB ↔ wallet reconciliation — 20-2 (PCI-DSS, SOC2 CC9.1,
    # CRA). Polls every 60 s, runs the actual reconciliation once per day at
    # 02:00 UTC. Alerts finance on discrepancies > $0.01.
    try:
        from utils.reconciliation import reconciliation_loop

        _spawn("reconciliation (daily 02:00 UTC)", reconciliation_loop)
    except Exception as e:
        logger.warning(f"Failed to import reconciliation loop: {e}")

    # Stripe ↔ DB daily reconciliation — runs at 02:00 UTC, one replica
    # via Redis leader lock. Flags paid rides with no matching Stripe
    # PaymentIntent, amount mismatches, and orphaned Stripe charges.
    # Discrepancies logged at ERROR so they reach Sentry + audit_logs.
    try:
        from utils.stripe_reconcile import stripe_reconcile_loop

        _spawn("stripe_reconcile (24h)", stripe_reconcile_loop)
    except Exception as e:
        logger.warning(f"Failed to import Stripe reconciliation loop: {e}")

    # Distance reconciliation — daily 04:00 UTC, one replica via Redis leader
    # lock. Compares each completed ride's quoted vs measured distance; opens a
    # per-ride integrity event on outliers and logs at ERROR (→ Sentry) on a
    # systematic aggregate bias — the class of bug the haversine fare defect was.
    try:
        from utils.distance_reconciliation import distance_reconciliation_loop

        _spawn("distance_reconciliation (daily 04:00 UTC)", distance_reconciliation_loop)
    except Exception as e:
        logger.error(f"Failed to import distance reconciliation loop: {e}", exc_info=True)

    # Period-1 (deadhead) distance finalizer — drains completed online-no-ride
    # accumulators into the append-only driver_period_distances audit (period=1)
    # once a driver leaves Period 1. Off unless period1_distance_tracking_enabled;
    # single replica via Redis leader lock, claim-before-write for replay safety.
    try:
        from utils.period1_distance_finalizer import period1_distance_finalizer_loop

        _spawn("period1_distance_finalizer (5min)", period1_distance_finalizer_loop)
    except Exception as e:
        logger.error(f"Failed to import period1 distance finalizer loop: {e}", exc_info=True)

    # T4A annual issuance — runs on the last day of February each year at
    # 08:00 UTC. Identifies drivers with ≥ $500 prior-year earnings, sends
    # each a push notification that their T4A slip is available, and logs
    # the batch to audit_logs (CRA regulatory requirement, P4-7).
    try:
        from utils.t4a_annual_job import t4a_annual_job_loop

        _spawn("t4a_annual_job (yearly Feb 28)", t4a_annual_job_loop)
    except Exception as e:
        logger.error(f"Failed to import T4A annual job loop: {e}", exc_info=True)

    # Stuck ride sweeper — cancels rides that have been in 'searching' for
    # more than 5 minutes. Recovers rides whose in-process asyncio timeout
    # was lost due to a pod restart. Atomic claim pattern ensures only one
    # replica acts on each ride.
    try:
        from utils.stuck_ride_sweeper import stuck_ride_sweeper_loop

        _spawn("stuck_ride_sweeper (60s)", stuck_ride_sweeper_loop)
    except Exception as e:
        logger.error(f"Failed to import stuck ride sweeper: {e}", exc_info=True)

    # Durable offer-expiry reaper — restart-safe backstop for offer timeouts.
    # In-process asyncio offer/search timers are lost on a pod restart; this loop
    # finds pending ride_offers past their persisted expires_at (migration 224)
    # and runs the same idempotent process_expired_offer, then re-dispatches.
    # Replay-safe: the per-offer atomic claim gates side-effects across replicas.
    try:
        from utils.offer_expiry_reaper import offer_expiry_reaper_loop

        _spawn("offer_expiry_reaper (10s)", offer_expiry_reaper_loop)
    except Exception as e:
        logger.error(f"Failed to import offer expiry reaper: {e}", exc_info=True)

    # Auto-reactivation of expired temporary rider suspensions — flips status
    # back to active once suspended_until passes so the admin list isn't stale.
    # Atomic conditional update keeps it replay-safe across replicas.
    try:
        from utils.suspension_reactivation import suspension_reactivation_loop

        _spawn("suspension_reactivation (10min)", suspension_reactivation_loop)
    except Exception as e:
        logger.error(f"Failed to import suspension reactivation loop: {e}", exc_info=True)

    # Push notification retry loop — re-attempts FCM/Expo deliveries for
    # dispatch and safety priority pushes that failed on first attempt.
    # Uses exponential back-off (60 s × 2^attempt) up to _MAX_ATTEMPTS=5.
    try:
        from utils.push_retry import push_retry_loop

        _spawn("push_retry (30s)", push_retry_loop)
    except Exception as e:
        logger.error(f"Failed to import push retry loop: {e}", exc_info=True)

    # Zoho Desk mirror sync — upserts recent tickets into zoho_desk_tickets so
    # the Help Desk serves lists/dashboards/trends from our DB (saves Zoho API
    # credits). No-op when the integration is disabled. Replay-safe (upsert).
    try:
        from utils.zoho_desk_sync import zoho_desk_sync_loop

        _spawn("zoho_desk_sync (10min)", zoho_desk_sync_loop)
    except Exception as e:
        logger.error(f"Failed to import Zoho Desk sync loop: {e}", exc_info=True)

    # Loop watchdog — scans heartbeats every 5 minutes and posts a
    # Slack-compatible alert when any loop has gone stale.  No-op when
    # ALERT_WEBHOOK_URL is unset.
    _WATCHDOG_LOOP_NAMES = list(
        [
            "subscription_expiry (6h)",
            "surge_engine (2min)",
            "scheduled_dispatcher (60s)",
            "payment_retry (5min)",
            "document_expiry (12h)",
            "driver_onboarding_reminders (15min)",
            "stale_intent_reconciler (15min)",
            "corporate_autotopup (10min)",
            "corporate_low_balance (1h)",
            "allowance_reset (1h)",
            "retention_purge (24h)",
            "data_export_purge (1h)",
            "stripe_reconcile (24h)",
            "t4a_annual_job (yearly Feb 28)",
            "stuck_ride_sweeper (60s)",
            "offer_expiry_reaper (10s)",
            "push_retry (30s)",
        ]
    )

    async def _loop_watchdog():
        import asyncio as _asyncio

        try:
            from utils.loop_alert import check_and_alert
            from utils.loop_monitor import record_heartbeat
        except ImportError:
            from utils.loop_alert import check_and_alert  # type: ignore
            from utils.loop_monitor import record_heartbeat  # type: ignore

        while True:
            try:
                await check_and_alert(
                    registered_names=_WATCHDOG_LOOP_NAMES,
                    webhook_url=settings.ALERT_WEBHOOK_URL,
                )
                record_heartbeat("loop_watchdog (5min)")
            except Exception:
                logger.error("loop_watchdog tick failed", exc_info=True)
            await _asyncio.sleep(300)

    _spawn("loop_watchdog (5min)", _loop_watchdog)

    app.state.background_tasks = background_tasks

    # WebSocket pub/sub (audit P0-B3): before this, socket sends were
    # in-process only, so on >1 replica the driver and the rider
    # regularly ended up on different containers and dispatch events
    # silently disappeared. Starting the pub/sub attaches a Redis
    # subscriber to the shared ConnectionManager; every outbound send
    # now fans out across replicas. In dev (no Redis URL configured)
    # this is a no-op and the manager stays in local-only mode.
    try:
        from socket_manager import manager as ws_manager
        from utils.ws_pubsub import pubsub as ws_pubsub
        from utils.ws_pubsub import resolve_ws_redis_url

        ws_redis_url = resolve_ws_redis_url(settings.WS_REDIS_URL, settings.RATE_LIMIT_REDIS_URL)
        ws_started = await ws_pubsub.start(ws_manager, ws_redis_url)
        app.state.ws_pubsub = ws_pubsub

        # Connectivity diagnosis — probe every Redis URL (PING + pub/sub
        # round-trip) and print a password-free banner so operators can tell
        # "down" from "up but pub/sub broken" from "wrong URL kind" straight
        # from the Fly logs. Run it in the BACKGROUND: diagnose_redis awaits a
        # pub/sub round-trip serially across three URLs, so a diagnostic-only
        # Redis stall would otherwise add tens of seconds to boot and risk
        # tripping the deploy health check. Best-effort; never blocks boot.
        app.state.redis_diagnosis = None

        async def _run_redis_diagnosis() -> None:
            try:
                from utils.redis_diag import diagnose_redis, log_diagnosis

                _diag = await asyncio.wait_for(
                    diagnose_redis(
                        {
                            "REDIS_URL": settings.REDIS_URL,
                            "RATE_LIMIT_REDIS_URL": settings.RATE_LIMIT_REDIS_URL,
                            "WS_REDIS_URL (effective)": ws_redis_url,
                        }
                    ),
                    timeout=20.0,
                )
                log_diagnosis(_diag)
                app.state.redis_diagnosis = _diag
            except Exception as _diag_err:
                logger.warning(f"Redis diagnosis failed: {_diag_err}")

        # Keep a reference so the task isn't garbage-collected mid-flight.
        app.state.redis_diag_task = asyncio.create_task(_run_redis_diagnosis(), name="redis_startup_diagnosis")

        if not ws_started and settings.ENV.lower() == "production":
            # Production without distributed WS is a correctness
            # hazard, but not a boot-blocker — a single-machine prod
            # deploy is still coherent. Log at WARNING so the operator
            # sees it in the boot logs.
            logger.warning(
                "WS pub/sub did NOT start — WebSocket fan-out will be "
                "limited to the current machine. Set WS_REDIS_URL (or "
                "RATE_LIMIT_REDIS_URL, which will be reused) to enable "
                "cross-machine delivery."
            )
    except Exception as e:
        logger.error(f"Failed to start WS pub/sub: {e}", exc_info=True)

    # Start the MCP streamable-HTTP session manager if /mcp was mounted
    # (no-op when the mcp SDK is absent). Never a boot-blocker — the AI chat
    # path runs tools in-process and does not depend on this.
    try:
        try:
            from ai.mcp_server import start_mcp
        except ImportError:
            from ..ai.mcp_server import start_mcp
        await start_mcp()
    except Exception as e:
        logger.error(f"Failed to start MCP session manager: {e}", exc_info=True)

    # Perform startup checks
    logger.info(f"Spinr API startup complete ({len(background_tasks)} background tasks running)")

    yield

    # Cleanup on shutdown — cancel background tasks and await them.
    logger.info("Shutting down Spinr API...")
    try:
        try:
            from ai.mcp_server import stop_mcp
        except ImportError:
            from ..ai.mcp_server import stop_mcp
        await stop_mcp()
    except Exception as e:
        logger.warning(f"Error stopping MCP session manager: {e}")
    # Stop WS pub/sub FIRST so in-flight publishes don't race against
    # a half-torn-down Redis client during the last ~millisecond of
    # shutdown (and so its consumer task isn't left as an orphan when
    # the event loop stops).
    try:
        ws_pubsub_ref = getattr(app.state, "ws_pubsub", None)
        if ws_pubsub_ref is not None:
            await ws_pubsub_ref.stop()
    except Exception as e:
        logger.warning(f"Error stopping WS pub/sub: {e}")

    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
        logger.info(f"Cancelled {len(background_tasks)} background tasks")

    # Cleanup database
    if hasattr(app.state, "db") and app.state.db:
        await cleanup_database(app.state.db)

    logger.info("Spinr API shutdown complete")
