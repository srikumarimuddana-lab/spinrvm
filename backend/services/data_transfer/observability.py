"""Sentry + Prometheus instrumentation for the Data Transfer module.

Metric names follow CLAUDE.md's ``spinr_<domain>_<metric>_<unit>`` convention
(mirrors ``spinr_payment_settlement_total``/``spinr_fare_calc_duration_ms`` in
``routes/rides/payments.py``/``estimates.py``).

Ordinary ``logger.error(...)`` calls throughout this module are already
captured by Sentry automatically (``LoggingIntegration(event_level="ERROR")``
in ``server.py``) — but untagged. ``capture_failure`` below adds explicit
``domain``/``surface`` tags plus a structured context for the module's
highest-signal failure points (an export/import/SGI-generate that failed and,
for the backgrounded export path, has no request left to report to). Same
shape as the only other explicit-tagged capture in this codebase,
``utils/refresh_tokens.py``'s refresh-token-reuse alert — best-effort,
no-op when ``SENTRY_DSN`` is unset, never raises.
"""

import logging
from typing import Any, Optional

try:
    from ...utils import metrics
except ImportError:
    from utils import metrics  # type: ignore

logger = logging.getLogger(__name__)


def record_export_result(status: str, fmt: str, duration_ms: Optional[float] = None) -> None:
    metrics.inc("spinr_data_transfer_export_total", {"format": fmt, "status": status})
    if duration_ms is not None:
        metrics.observe("spinr_data_transfer_export_duration_ms", duration_ms, {"format": fmt})


def record_import_result(status: str) -> None:
    metrics.inc("spinr_data_transfer_import_total", {"status": status})


def record_sgi_form_result(form_type: str, status: str) -> None:
    metrics.inc("spinr_data_transfer_sgi_form_total", {"form_type": form_type, "status": status})


def capture_failure(message: str, alert: str, contexts: dict[str, Any]) -> None:
    """Explicit tagged Sentry event for a Data Transfer failure. Best-effort —
    never raises, and is a silent no-op if SENTRY_DSN isn't configured."""
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            message,
            level="error",
            tags={"spinr_alert": alert, "domain": "admin", "surface": "backend"},
            contexts={"data_transfer": contexts},
        )
    except Exception as e:
        logger.debug("data-transfer Sentry capture skipped: %s", e)
