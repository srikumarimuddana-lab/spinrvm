"""External dependencies for the drivers package (single dual-import block).

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

# ruff: noqa: F401

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import stripe
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user, get_current_user, get_token_session_id
    from ...features import send_email, send_push_notification
    from ...geo_utils import calculate_distance, get_service_area_polygon
    from ...logging_utils import diag_logger
    from ...models.ride_status import RideStatus
    from ...schemas import Driver, RideRatingRequest
    from ...services.fare_service import recalculate_fare_for_distance
    from ...socket_manager import manager
    from ...utils.background import spawn
    from ...utils.breadcrumb_buffer import flush_driver_breadcrumbs
    from ...utils.breadcrumbs import invalidate_active_rides_cache
    from ...utils.datetime_utils import parse_iso_utc
    from ...utils.driver_code import generate_driver_code
    from ...utils.driver_online import intent_online
    from ...utils.driver_presence import (
        clear_presence,
        mark_present,
        present_driver_ids_checked,
        reset_miss_streak,
    )
    from ...utils.earnings_snapshot import build_earnings_snapshot, fare_share
    from ...utils.error_handling import (
        AccountDisabledException,
        DriverOfflineException,
        ErrorCode,
        RideStateError,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from ...utils.error_keys import ErrorKeys
    from ...utils.idempotency import idempotent_endpoint
    from ...utils.insurance_periods import record_period_transition
    from ...utils.live_activity import (
        EVENT_END,
        EVENT_START,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from ...utils.metrics import inc as _metric_inc
    from ...utils.metrics import observe as _metric_observe
    from ...utils.money import dollars_to_cents, to_decimal
    from ...utils.rate_limiter import (
        dsar_export_limit,
        heatmap_read_limit,
        location_update_limit,
        tax_doc_email_limit,
    )
    from ...utils.referral_terms import (
        paid_referee_earnings,
        paid_referral_earnings,
        resolve_referral_terms,
    )
    from ...utils.rider_emails import send_no_show_fee_email
    from ...utils.stripe_charge import cancel_authorization
    from ...utils.t4a_pdf import generate_t4a_pdf
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user, get_token_session_id
    from features import send_email, send_push_notification
    from geo_utils import calculate_distance, get_service_area_polygon
    from logging_utils import diag_logger
    from models.ride_status import RideStatus  # noqa: F401
    from schemas import Driver, RideRatingRequest
    from services.fare_service import recalculate_fare_for_distance
    from socket_manager import manager
    from utils.background import spawn  # type: ignore
    from utils.breadcrumb_buffer import flush_driver_breadcrumbs  # type: ignore
    from utils.breadcrumbs import invalidate_active_rides_cache  # type: ignore
    from utils.datetime_utils import parse_iso_utc
    from utils.driver_code import generate_driver_code  # type: ignore
    from utils.driver_online import intent_online  # type: ignore
    from utils.driver_presence import (
        clear_presence,
        mark_present,
        present_driver_ids_checked,
        reset_miss_streak,
    )
    from utils.earnings_snapshot import build_earnings_snapshot, fare_share
    from utils.error_handling import (
        AccountDisabledException,
        DriverOfflineException,
        ErrorCode,
        RideStateError,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint
    from utils.insurance_periods import record_period_transition  # type: ignore[assignment]
    from utils.live_activity import (  # type: ignore
        EVENT_END,
        EVENT_START,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.money import dollars_to_cents, to_decimal
    from utils.rate_limiter import (  # type: ignore
        dsar_export_limit,
        heatmap_read_limit,
        location_update_limit,
        tax_doc_email_limit,
    )
    from utils.referral_terms import (  # type: ignore
        paid_referee_earnings,
        paid_referral_earnings,
        resolve_referral_terms,
    )
    from utils.rider_emails import send_no_show_fee_email  # noqa: F401
    from utils.stripe_charge import cancel_authorization  # type: ignore
    from utils.t4a_pdf import generate_t4a_pdf  # noqa: F401 – used in download_t4a_pdf

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)


import uuid  # noqa: E402
