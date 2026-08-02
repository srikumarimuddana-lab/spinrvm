"""External dependencies for the rides package (single dual-import block).

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

# ruff: noqa: F401

import asyncio
import json
import secrets
import time as _time_mod
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import generate_pickup_otp, get_current_user, get_current_user_allow_expired
    from ...features import (
        calculate_airport_fee,
        calculate_all_fees,
        notify_safety_team,
        send_push_notification,
    )
    from ...geo_utils import (
        calculate_distance,
        get_service_area_polygon,
        multi_leg_distance,
        point_in_polygon,
    )
    from ...models.ride_status import RideStatus
    from ...schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from ...services import DispatchService
    from ...services.dispatch_service import (
        dispatch_geo_bounds,
        filter_and_rank_drivers,
        rank_by_eta_with_acceptance,
    )
    from ...services.fare_service import build_fare_breakdown_lines, calculate_fare
    from ...settings_loader import get_app_settings
    from ...sms_service import send_sms
    from ...socket_manager import manager
    from ...utils.audit_logger import log_user_action
    from ...utils.background import spawn
    from ...utils.error_handling import (
        ErrorCode,
        RideNotFoundException,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from ...utils.error_keys import ErrorKeys
    from ...utils.idempotency import idempotent_endpoint
    from ...utils.maps_eta import batch_get_etas
    from ...utils.pii import first_name_only, geohash
    from ...utils.rate_limiter import (
        api_rate_limit,
        cancel_ride_limit,
        payment_action_limit,
        ride_action_limit,
        ride_message_limit,
        ride_rating_limit,
        ride_read_limit,
        ride_request_limit,
    )
    from ...validators import validate_ride_location
except ImportError:
    import db_supabase
    from dependencies import generate_pickup_otp, get_current_user, get_current_user_allow_expired
    from features import (
        calculate_airport_fee,
        calculate_all_fees,
        notify_safety_team,
        send_push_notification,
    )
    from geo_utils import calculate_distance, get_service_area_polygon, multi_leg_distance, point_in_polygon
    from models.ride_status import RideStatus  # noqa: F401
    from schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from services.dispatch_service import (
        DispatchService,
        dispatch_geo_bounds,
        filter_and_rank_drivers,
        rank_by_eta_with_acceptance,
    )
    from services.fare_service import build_fare_breakdown_lines, calculate_fare
    from settings_loader import get_app_settings
    from sms_service import send_sms
    from socket_manager import manager
    from utils.audit_logger import log_user_action
    from utils.background import spawn  # type: ignore
    from utils.error_handling import (
        ErrorCode,
        RideNotFoundException,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint
    from utils.maps_eta import batch_get_etas
    from utils.pii import first_name_only, geohash
    from utils.rate_limiter import (
        api_rate_limit,
        cancel_ride_limit,
        payment_action_limit,
        ride_action_limit,
        ride_message_limit,
        ride_rating_limit,
        ride_read_limit,
        ride_request_limit,
    )
    from validators import validate_ride_location


from ..fares import _fares_for_location_impl, get_fares_for_location

try:
    from ...utils.datetime_utils import parse_iso_utc
    from ...utils.earnings_snapshot import build_earnings_snapshot
    from ...utils.insurance_periods import record_period_transition
    from ...utils.live_activity import (
        EVENT_END,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from ...utils.metrics import inc as _metric_inc
    from ...utils.metrics import observe as _metric_observe
    from ...utils.metrics import timed as _metric_timed
    from ...utils.ride_code import generate_ride_code
except ImportError:
    from utils.datetime_utils import parse_iso_utc
    from utils.earnings_snapshot import build_earnings_snapshot  # noqa: F401
    from utils.insurance_periods import record_period_transition  # type: ignore[assignment]
    from utils.live_activity import (  # type: ignore
        EVENT_END,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.metrics import timed as _metric_timed  # type: ignore
    from utils.ride_code import generate_ride_code

try:
    from ...utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
except ImportError:
    from utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )

try:
    from ...services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore
except ImportError:
    from services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore

try:
    from ...core.config import settings as _settings
except ImportError:
    from core.config import settings as _settings  # noqa: F401 — dual-import pattern

try:
    from ...utils.offer_card_token import sign_offer_card_token
except ImportError:
    from utils.offer_card_token import sign_offer_card_token

try:
    from ...utils.safety_paging import page_on_call as page_sos_on_call
except ImportError:
    from utils.safety_paging import page_on_call as page_sos_on_call  # type: ignore

try:
    from ...services.cancellation_service import (
        calculate_cancellation_fee,
        calculate_scheduled_cancel_notice_fee,
        pay_driver_cancellation_fee,
    )
    from ...services.payment_service import (
        send_ride_receipt,
        settle_card,
        settle_corporate,
        settle_wallet,
    )
    from ...utils.stripe_charge import authorize_ride, cancel_authorization, charge_ancillary_fee, verify_authorization
except ImportError:
    from services.cancellation_service import (  # type: ignore
        calculate_cancellation_fee,
        calculate_scheduled_cancel_notice_fee,
        pay_driver_cancellation_fee,
    )
    from services.payment_service import send_ride_receipt, settle_card, settle_corporate, settle_wallet  # type: ignore
    from utils.stripe_charge import (  # type: ignore
        authorize_ride,
        cancel_authorization,
        charge_ancillary_fee,
        verify_authorization,
    )

db = db_supabase  # legacy alias

import re as _re

import httpx as _httpx  # noqa: E402 — late import to avoid circular at module load
