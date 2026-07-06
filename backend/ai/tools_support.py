"""FAQ, company-info and escalation tools for the AI assistant.

Available to both rider and driver audiences. escalate_to_support is the
assistant's only potential side effect, and it is OFF by default: it
returns an ``open_support`` _client_action the apps render as a "Contact
support" button. Only when app_settings.ai_escalation_creates_ticket is
enabled does it open a Zoho ticket (reusing the existing /support/escalate
integration), attaching the recent chat transcript so agents get context
without asking the user to repeat themselves. Safety topics always route
to 911/SOS language — never just a ticket.
"""

import logging
from typing import Any, Dict, Optional

try:
    from . import conversations
    from .tools import ToolSpec, register
except ImportError:
    from ai import conversations
    from ai.tools import ToolSpec, register

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
except ImportError:
    import db_supabase
    from settings_loader import get_app_settings

logger = logging.getLogger(__name__)

_BOTH = frozenset({"rider", "driver"})

ESCALATION_CATEGORIES = [
    "refund",
    "account",
    "lost_item",
    "complaint",
    "payment_issue",
    "safety",
    "other",
]

# Deep-link targets the apps know how to open (see rider-app routes).
_CATEGORY_LINKS = {
    "lost_item": "/lost-and-found",
}
_DEFAULT_LINK = "/support"


def _tokenize(text: str) -> set:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) > 2}


# Curated domain concept groups. A query and an FAQ that use *different* words
# for the same idea ("earnings" vs "payouts") still collide because each maps to
# the same concept token. Kept small and hand-verified — this is deliberately
# NOT a general thesaurus (which would blur distinct topics and hurt ranking).
# For truly novel phrasing, vector embeddings are the follow-up; this covers the
# common rewordings offline, with zero provider cost.
_SYNONYM_GROUPS: list[set] = [
    {"payout", "payouts", "payment", "payments", "paid", "pay", "earnings", "earning", "deposit", "deposited"},
    {"surge", "surges", "surging", "pricing", "multiplier", "peak"},
    {"cancel", "cancels", "cancelled", "canceled", "cancelling", "cancellation"},
    {"refund", "refunds", "refunded", "reimburse", "reimbursement"},
    {"coverage", "cover", "covers", "area", "areas", "zone", "zones", "serve", "serves", "service"},
    {"fare", "fares", "price", "prices", "cost", "costs", "charge", "charges", "rate", "rates"},
    {"wallet", "balance", "credit", "credits", "funds"},
    {"document", "documents", "license", "licence", "insurance", "registration", "abstract"},
    {"promo", "promos", "promotion", "promotions", "coupon", "discount", "discounts", "code", "codes"},
    {"tip", "tips", "tipping", "gratuity"},
]

_TERM_TO_CONCEPT: Dict[str, str] = {term: f"~c{idx}" for idx, group in enumerate(_SYNONYM_GROUPS) for term in group}


def _match_tokens(text: str) -> set:
    """Raw tokens plus a concept token for any domain term (with a trailing-'s'
    plural fallback). Applied identically to the query and to FAQ text so
    synonyms overlap. Concept tokens are namespaced (``~c#``) so they can never
    collide with a real word."""
    tokens = _tokenize(text)
    concepts = set()
    for t in tokens:
        concept = _TERM_TO_CONCEPT.get(t) or (_TERM_TO_CONCEPT.get(t[:-1]) if t.endswith("s") else None)
        if concept:
            concepts.add(concept)
    return tokens | concepts


async def search_faqs(user: Dict[str, Any], query: str) -> Dict[str, Any]:
    audience = user.get("ai_audience", "rider")
    rows = await db_supabase.get_rows(
        "faqs",
        {"is_active": True, "audience": {"$in": ["both", audience]}},
        limit=200,
    )
    q_tokens = _match_tokens(query)
    scored = []
    for row in rows or []:
        # Question matches count double — a query echoing the question is a
        # far stronger signal than the same words buried in an answer body.
        # _match_tokens folds synonyms so a reworded query still overlaps.
        question_overlap = len(q_tokens & _match_tokens(row.get("question", "")))
        body_overlap = len(q_tokens & _match_tokens(f"{row.get('answer', '')} {row.get('category', '')}"))
        score = question_overlap * 2 + body_overlap
        if score:
            scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return {
            "results": [],
            "note": "No matching help-centre article — say so plainly and offer escalate_to_support.",
        }
    return {
        "results": [
            {
                "question": r.get("question"),
                "answer": r.get("answer"),
                "category": r.get("category"),
            }
            for _, r in scored[:5]
        ]
    }


async def get_company_info(user: Dict[str, Any]) -> Dict[str, Any]:
    settings = await get_app_settings()
    return {
        "name": settings.get("company_name", "Spinr") or "Spinr",
        "address": settings.get("company_address", "") or "",
        "phone": settings.get("company_phone", "") or "",
        "email": settings.get("company_email", "") or "",
        "website": settings.get("company_website", "") or "",
    }


# Transcript bounds for escalation tickets: enough for an agent to catch up,
# small enough to keep tickets readable.
_TRANSCRIPT_MAX_MESSAGES = 10
_TRANSCRIPT_MAX_MESSAGE_CHARS = 300


async def _recent_transcript(conversation_id: Optional[str]) -> Optional[str]:
    """Compact 'User:/Assistant:' transcript of the current conversation for
    the support ticket. Messages are already PII-scrubbed at ingest. Returns
    None when unavailable — a transcript must never block an escalation."""
    if not conversation_id:
        return None
    try:
        history = await conversations.load_history(conversation_id, _TRANSCRIPT_MAX_MESSAGES)
    except Exception:
        logger.error("ai escalation transcript load failed", exc_info=True)
        return None
    lines = []
    for m in history:
        label = "Assistant" if m.get("role") == "assistant" else "User"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{label}: {content[:_TRANSCRIPT_MAX_MESSAGE_CHARS]}")
    return "\n".join(lines) or None


async def escalate_to_support(user: Dict[str, Any], reason: str, category: str) -> Dict[str, Any]:
    link = _CATEGORY_LINKS.get(category, _DEFAULT_LINK)
    result: Dict[str, Any] = {
        "action": "open_support",
        "category": category,
        "link": link,
        "message": "I've prepared a handoff to our human support team.",
        # Lifted into an SSE `action` frame by the orchestrator; the apps and
        # the admin console render it as the "Contact support" card.
        "_client_action": {"type": "open_support", "category": category, "link": link},
    }
    if category == "safety":
        # The assistant is never an emergency channel (see CLAUDE.md).
        result["message"] = (
            "If anyone is in danger, call 911 now or use the SOS button in the app. "
            "For non-urgent safety concerns our support team will follow up."
        )

    settings = await get_app_settings()
    if settings.get("ai_escalation_creates_ticket"):
        try:
            from ..services.zoho_desk_integration import create_support_ticket
        except ImportError:
            from services.zoho_desk_integration import create_support_ticket
        try:
            transcript = await _recent_transcript(user.get("_conversation_id"))
            ticket = await create_support_ticket(
                user=user, message=f"[AI escalation:{category}] {reason}", transcript=transcript
            )
            result["ticket_number"] = ticket.get("ticketNumber")
            result["message"] += " A support ticket has been opened; we'll follow up by email."
        except Exception:
            # Zoho outage must not strand the rider — the deep link still works.
            logger.error("ai escalation ticket failed", exc_info=True, extra={"user_id": user.get("id")})
    return result


register(
    ToolSpec(
        name="search_faqs",
        description=(
            "Call this for how-the-app-works and policy questions — scheduling rides, "
            "cancellation fees, splitting fares, accessibility, service animals, payments "
            "setup. Returns matching help-centre articles; answer ONLY from them and say "
            "so if nothing matches."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Keywords from the rider's question.",
                }
            },
            "required": ["query"],
        },
        handler=search_faqs,
        audiences=_BOTH,
    )
)

register(
    ToolSpec(
        name="get_company_info",
        description=(
            "Call this when the user asks how to contact Spinr, the support phone/email, or where the company is based."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_company_info,
        audiences=_BOTH,
    )
)

register(
    ToolSpec(
        name="escalate_to_support",
        description=(
            "Call this when the user needs a human: refunds, account suspensions, lost "
            "items, disputes, complaints, payment problems you cannot explain from data, "
            "or anything outside your scope. Returns a handoff the app renders as a "
            "'Contact support' button — tell the user to tap it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "maxLength": 300,
                    "description": "One-sentence summary of what the user needs.",
                },
                "category": {
                    "type": "string",
                    "enum": ESCALATION_CATEGORIES,
                    "description": "Best-fit category for routing.",
                },
            },
            "required": ["reason", "category"],
        },
        handler=escalate_to_support,
        audiences=_BOTH,
    )
)
