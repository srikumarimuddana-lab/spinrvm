import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.audit_logger import log_admin_action

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


_AUDIENCES = Literal["customers", "drivers", "particular_customer", "particular_driver", "all"]


class CloudMessageRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=500)
    audience: _AUDIENCES = "customers"
    type: str = "info"
    channels: List[Literal["push", "sms", "email"]] = ["push"]
    particular_ids: Optional[List[str]] = None
    scheduled_at: Optional[str] = None
    # When true the message is a CASL commercial electronic message: every
    # recipient is gated on express consent + suppression per channel, and the
    # marketing senders (footer + unsubscribe / STOP) are used. When false it
    # is an operational/service notice (no consent gate — e.g. safety, outage).
    is_marketing: bool = False
    # Optional service-area FILTER layered on the customers/drivers audience:
    # customers → riders with recent rides in this area; drivers → drivers
    # assigned to it. Ignored for particular_* / all. None = all areas.
    service_area_id: Optional[str] = None


def _target_app_for_audience(audience: str) -> str | None:
    """Map audience to the target_app param for send_push_notification."""
    if audience in ("drivers", "particular_driver"):
        return "driver"
    if audience in ("customers", "particular_customer"):
        return "rider"
    return None


async def _rider_ids_in_area(service_area_id: str) -> list:
    """Distinct rider ids with recent rides in a service area."""
    rides = await db.get_rows("rides", {"service_area_id": service_area_id}, columns="rider_id", limit=50000)
    return list({r.get("rider_id") for r in rides if r.get("rider_id")})


async def _driver_user_ids_in_area(service_area_id: str) -> list:
    """Distinct user ids of drivers assigned to a service area."""
    drivers = await db.get_rows("drivers", {"service_area_id": service_area_id}, columns="user_id", limit=10000)
    return list({d.get("user_id") for d in drivers if d.get("user_id")})


async def _resolve_recipients(
    audience: str,
    particular_ids: list,
    service_area_id: Optional[str],
    *,
    need_contact: bool,
) -> list:
    """Resolve an audience (optionally filtered by service area) to recipient rows.

    ``need_contact`` adds email+phone columns (required for the email/SMS
    channels); push only needs the id, so we avoid dragging contact PII when
    it isn't used. ``service_area_id`` narrows the customers/drivers audience to
    that area (riders via their rides, drivers via their assignment).
    """
    cols = "id,email,phone" if need_contact else "id"
    if audience in ("particular_customer", "particular_driver"):
        if not particular_ids:
            return []
        if need_contact:
            return await db.get_rows("users", {"id": {"$in": particular_ids}}, columns=cols, limit=10000)
        return [{"id": uid} for uid in particular_ids]
    if audience == "customers":
        if service_area_id:
            ids = await _rider_ids_in_area(service_area_id)
            if not ids:
                return []
            return await db.get_rows("users", {"id": {"$in": ids}, "is_rider": True}, columns=cols, limit=10000)
        return await db.get_rows("users", {"is_rider": True}, columns=cols, limit=10000)
    if audience == "drivers":
        if service_area_id:
            ids = await _driver_user_ids_in_area(service_area_id)
            if not ids:
                return []
            return await db.get_rows("users", {"id": {"$in": ids}}, columns=cols, limit=10000)
        return await db.get_rows("users", {"is_driver": True}, columns=cols, limit=10000)
    if audience == "all":
        return await db.get_rows("users", {}, columns=cols, limit=10000)
    return []


async def _count_audience(audience: str, particular_ids: list, service_area_id: Optional[str] = None) -> int:
    """Recipient count without materialising contact PII — used for scheduled
    sends. Honours the service-area filter for customers/drivers."""
    if audience in ("particular_customer", "particular_driver"):
        return len(particular_ids)
    if audience == "customers":
        if service_area_id:
            return len(await _resolve_recipients("customers", [], service_area_id, need_contact=False))
        return await db_supabase.count_documents("users", {"is_rider": True})
    if audience == "drivers":
        if service_area_id:
            return len(await _resolve_recipients("drivers", [], service_area_id, need_contact=False))
        return await db_supabase.count_documents("users", {"is_driver": True})
    if audience == "all":
        return await db_supabase.count_documents("users", {})
    return 0


async def _send_push_one(
    uid: str, title: str, description: str, target_app: str | None, is_marketing: bool, msg_type: str
) -> bool:
    push_data = {"type": msg_type}
    if is_marketing:
        try:
            from ...utils.marketing_push import send_marketing_push
        except ImportError:
            from utils.marketing_push import send_marketing_push  # type: ignore
        return await send_marketing_push(
            user_id=uid, title=title, body=description, data=push_data, target_app=target_app, log_id="cm"
        )
    try:
        from ...features import send_push_notification
    except ImportError:
        from features import send_push_notification  # type: ignore
    try:
        return bool(await send_push_notification(uid, title, description, data=push_data, target_app=target_app))
    except Exception:
        logger.error("cloud message: push send raised", exc_info=True)
        return False


async def _send_email_one(row: dict, title: str, description: str, is_marketing: bool) -> bool:
    to = (row.get("email") or "").strip()
    uid = row.get("id")
    if not to or not uid:
        return False
    # Marketing keeps its own body: send_marketing_email appends the CASL
    # footer (sender identity, physical address, unsubscribe) to whatever it is
    # given, and that footer is a legal requirement, not a style choice.
    html = f"<h2>{title}</h2><p>{description}</p>"
    if is_marketing:
        try:
            from ...utils.marketing_email import send_marketing_email
        except ImportError:
            from utils.marketing_email import send_marketing_email  # type: ignore
        return await send_marketing_email(to=to, user_id=uid, subject=title, html=html, text=description, log_id="cm")
    try:
        from ...utils.email_provider import send_transactional_email
    except ImportError:
        from utils.email_provider import send_transactional_email  # type: ignore
    # Transactional broadcasts get the shared shell. This also escapes the
    # admin-authored title and description, which the bare <h2>/<p> above did
    # not — a broadcast is the one place an admin's free text reaches every
    # rider or driver at once.
    try:
        from ...utils.email_layout import render_from_text
    except ImportError:
        from utils.email_layout import render_from_text  # type: ignore

    rendered = await render_from_text(heading=title, body=description)
    return await send_transactional_email(
        to=to,
        subject=title,
        html=rendered.html,
        text=rendered.text,
        email_type="broadcast",
        recipient_user_id=uid,
        log_id="cm",
    )


async def _send_sms_one(row: dict, title: str, description: str, is_marketing: bool, settings: dict) -> bool:
    to = (row.get("phone") or "").strip()
    uid = row.get("id")
    if not to or not uid:
        return False
    body = f"{title}\n{description}"
    if is_marketing:
        try:
            from ...utils.marketing_sms import send_marketing_sms
        except ImportError:
            from utils.marketing_sms import send_marketing_sms  # type: ignore
        return await send_marketing_sms(to=to, user_id=uid, message=body, log_id="cm")
    try:
        from ...sms_service import send_sms
    except ImportError:
        from sms_service import send_sms  # type: ignore
    res = await send_sms(
        to,
        body,
        twilio_sid=(settings.get("twilio_account_sid") or ""),
        twilio_token=(settings.get("twilio_auth_token") or ""),
        twilio_from=(settings.get("twilio_from_number") or ""),
    )
    return bool(res.get("success"))


async def _fan_out(
    message_id: str,
    recipients: list,
    *,
    title: str,
    description: str,
    channels: list,
    is_marketing: bool,
    target_app: str | None,
    msg_type: str,
) -> None:
    """Fan-out across every selected channel concurrently and persist stats.

    For marketing messages each channel's sender independently enforces the
    consent + suppression gate (services.marketing_consent), so an unconsented
    or suppressed recipient is silently skipped (counts as a non-success). The
    aggregate successful/failed_count across all channels is written back.

    NOTE: marketing gating does a per-recipient consent lookup. Broadcasts run
    in the background (not request-latency-critical); bulk-loading preferences
    is a future optimisation if list sizes grow large.
    """
    rows = [r for r in recipients if isinstance(r, dict) and r.get("id")]
    sem = asyncio.Semaphore(50)
    settings: dict = {}
    if "sms" in channels and not is_marketing:
        try:
            from ...settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        settings = await get_app_settings()

    async def _one(row: dict) -> bool:
        async with sem:
            ok = False
            if "push" in channels and await _send_push_one(
                row["id"], title, description, target_app, is_marketing, msg_type
            ):
                ok = True
            if "email" in channels and await _send_email_one(row, title, description, is_marketing):
                ok = True
            if "sms" in channels and await _send_sms_one(row, title, description, is_marketing, settings):
                ok = True
            return ok

    logger.info(f"Cloud message {message_id}: fan-out to {len(rows)} recipient(s) channels={channels}")
    results = await asyncio.gather(*[_one(r) for r in rows], return_exceptions=True)
    successful = sum(1 for r in results if r is True)
    failed_count = len(results) - successful
    logger.info(f"Cloud message {message_id} fan-out complete: success={successful} failed={failed_count}")

    try:
        await db_supabase.update_one(
            "cloud_messages",
            {"id": message_id},
            {"successful": successful, "failed_count": failed_count},
        )
    except Exception:
        logger.error(f"Failed to update cloud_message stats for {message_id}", exc_info=True)


# ---------- Cloud Messaging ----------


@router.post("/cloud-messaging/send")
async def admin_send_cloud_message(
    payload: CloudMessageRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    admin: dict = Depends(get_admin_user),
):
    """Send or schedule a cloud message to users/drivers.

    Immediate sends return 202 Accepted — notifications are fanned out in a
    background task via asyncio.gather + Semaphore(50) so the event loop is
    never blocked by thousands of sequential push-notification awaits.
    Final successful/failed_count figures are written back to the DB record
    once the background task completes.
    """
    title = payload.title
    description = payload.description
    audience = payload.audience
    msg_type = payload.type
    channels = payload.channels
    particular_ids = payload.particular_ids or []
    scheduled_at = payload.scheduled_at
    is_marketing = payload.is_marketing
    # Service-area filter only applies to the customers/drivers audiences.
    service_area_id = payload.service_area_id if audience in ("customers", "drivers") else None

    is_scheduled = bool(scheduled_at)
    status = "scheduled" if is_scheduled else "sent"

    # Email and SMS need the contact columns; push only needs the id.
    need_contact = any(c in channels for c in ("email", "sms"))
    target_app = _target_app_for_audience(audience)

    # Resolve the recipient list only for an immediate send; a scheduled send
    # is materialised at dispatch time, so we only need its size now.
    recipients: list = []
    if not is_scheduled:
        recipients = await _resolve_recipients(audience, particular_ids, service_area_id, need_contact=need_contact)
        total_recipients = len(recipients)
    else:
        total_recipients = await _count_audience(audience, particular_ids, service_area_id)

    doc = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "audience": audience,
        "type": msg_type,
        "channel": channels[0],
        "channels": channels,
        "is_marketing": is_marketing,
        "service_area_id": service_area_id,
        "particular_id": particular_ids[0] if particular_ids else None,
        "particular_ids": particular_ids,
        "status": status,
        "scheduled_at": scheduled_at,
        "sent_at": datetime.now(timezone.utc).isoformat() if not is_scheduled else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_recipients": total_recipients,
        "successful": 0,
        "failed_count": 0,
    }

    try:
        await db_supabase.insert_one("cloud_messages", doc)
    except Exception as e:
        logger.error(f"Failed to insert cloud message: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save message. The cloud_messages table may not exist yet. Please run migration 06_cloud_messaging.sql.",
        ) from e

    if not is_scheduled:
        background_tasks.add_task(
            _fan_out,
            doc["id"],
            recipients,
            title=title,
            description=description,
            channels=channels,
            is_marketing=is_marketing,
            target_app=target_app,
            msg_type=msg_type,
        )
        response.status_code = 202

    await log_admin_action(
        admin,
        "cloud_message_sent" if not is_scheduled else "cloud_message_scheduled",
        "cloud_messages",
        doc["id"],
        {"audience": audience, "channels": channels, "total_recipients": total_recipients},
    )
    return {"success": True, "message": doc}


@router.get("/cloud-messaging/audience-preview")
async def admin_cloud_message_audience_preview(
    audience: _AUDIENCES = Query("customers"),
    service_area_id: Optional[str] = Query(None),
):
    """Estimate reach before sending: the audience size (honouring the optional
    service-area filter) plus, for marketing, the number of users opted in on
    each channel.

    The opt-in figures are the GLOBAL opted-in pool (not intersected with the
    audience) — a guidance number. Exact per-recipient consent filtering still
    happens at send time, so the delivered count can be lower.
    """
    area = service_area_id if audience in ("customers", "drivers") else None
    recipients = await _resolve_recipients(audience, [], area, need_contact=False)
    out: Dict[str, Any] = {"audience": audience, "audience_total": len(recipients)}
    try:
        out["email_opted_in"] = await db_supabase.count_documents(
            "marketing_preferences", {"email_opt_in": True}, id_column="user_id"
        )
        out["sms_opted_in"] = await db_supabase.count_documents(
            "marketing_preferences", {"sms_opt_in": True}, id_column="user_id"
        )
        out["push_opted_in"] = await db_supabase.count_documents(
            "marketing_preferences", {"push_opt_in": True}, id_column="user_id"
        )
    except Exception:
        logger.error("audience-preview consent counts failed", exc_info=True)
        out["email_opted_in"] = out["sms_opted_in"] = out["push_opted_in"] = None
    return out


@router.get("/cloud-messaging")
async def admin_get_cloud_messages(
    status: Optional[str] = None,
    audience: Optional[str] = None,
    limit: int = Query(100),
    offset: int = Query(0),
):
    """Get cloud messages with optional filters."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if audience:
        filters["audience"] = audience

    try:
        messages = await db_supabase.get_rows(
            "cloud_messages",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.error("cloud_messages table missing or query failed", exc_info=True)
        return []
    return messages


@router.get("/cloud-messaging/stats")
async def admin_get_cloud_message_stats():
    """Get cloud messaging statistics."""
    try:
        all_messages = await db_supabase.get_rows("cloud_messages", {}, limit=10000)
    except Exception:
        logger.error("cloud_messages table missing or query failed", exc_info=True)
        all_messages = []

    total = len(all_messages)
    total_sent = sum(1 for m in all_messages if m.get("status") == "sent")
    total_scheduled = sum(1 for m in all_messages if m.get("status") == "scheduled")
    total_failed = sum(1 for m in all_messages if m.get("status") == "failed")
    total_reached = sum(m.get("successful", 0) for m in all_messages)
    total_recipients = sum(m.get("total_recipients", 0) for m in all_messages)
    success_rate = round((total_reached / total_recipients * 100), 1) if total_recipients > 0 else 0

    return {
        "total_messages": total,
        "total_sent": total_sent,
        "total_scheduled": total_scheduled,
        "total_failed": total_failed,
        "total_recipients_reached": total_reached,
        "success_rate": success_rate,
    }


class ManualSuppressionRequest(BaseModel):
    channel: Literal["email", "sms"]
    target: str = Field(..., min_length=1, max_length=320)
    reason: Literal["manual", "bounce", "complaint", "unsubscribe"] = "manual"


@router.get("/marketing/suppressions")
async def admin_list_marketing_suppressions(
    channel: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
):
    """List the marketing suppression list (the 'do-not-market' list).

    PIPEDA: the target (email/phone) is intrinsic to a suppression record — an
    operator must see WHO is suppressed to manage the list — so it is returned
    here, behind the admin auth + the notifications module gate.
    """
    filters: Dict[str, Any] = {}
    if channel:
        filters["channel"] = channel
    try:
        rows = await db_supabase.get_rows(
            "marketing_suppressions", filters, order="created_at", desc=True, limit=limit, offset=offset
        )
    except Exception:
        logger.error("marketing_suppressions query failed", exc_info=True)
        return []
    return rows


@router.post("/marketing/suppressions")
async def admin_add_marketing_suppression(payload: ManualSuppressionRequest, admin: dict = Depends(get_admin_user)):
    """Manually add an address/number to the marketing suppression list."""
    try:
        from ...services import marketing_consent
    except ImportError:
        from services import marketing_consent  # type: ignore
    added = await marketing_consent.add_marketing_suppression(
        payload.channel, payload.target, reason=payload.reason, source="admin"
    )
    await log_admin_action(
        admin,
        "marketing_suppression_added",
        "marketing_suppressions",
        payload.target,
        {"channel": payload.channel, "reason": payload.reason},
    )
    return {"success": True, "added": added}


@router.delete("/marketing/suppressions/{suppression_id}")
async def admin_delete_marketing_suppression(suppression_id: str, admin: dict = Depends(get_admin_user)):
    """Remove a suppression (re-allow marketing to this target).

    Use with care: lifting a bounce/complaint suppression re-enables marketing
    to an address that previously bounced or complained. The deletion is
    audited via the standard admin audit trail.
    """
    existing = await db_supabase.find_one("marketing_suppressions", {"id": suppression_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Suppression not found")
    await db_supabase.delete_many("marketing_suppressions", {"id": suppression_id})
    await log_admin_action(
        admin,
        "marketing_suppression_deleted",
        "marketing_suppressions",
        suppression_id,
        {"target": existing.get("target"), "channel": existing.get("channel")},
    )
    return {"success": True}


@router.delete("/cloud-messaging/{message_id}")
async def admin_delete_cloud_message(message_id: str, admin: dict = Depends(get_admin_user)):
    """Cancel/delete a scheduled cloud message."""
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("cloud_messages", {"id": message_id}, limit=1)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Message not found")

    if existing.get("status") == "sent":
        raise HTTPException(status_code=400, detail="Cannot delete a sent message")

    await db_supabase.update_one("cloud_messages", {"id": message_id}, {"status": "cancelled"})
    await log_admin_action(admin, "cloud_message_cancelled", "cloud_messages", message_id, {})
    return {"message": "Message cancelled"}
