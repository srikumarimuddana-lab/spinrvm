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

import asyncio
import logging
from typing import Any, Dict, Optional

try:
    from . import conversations, embeddings
    from .tools import ToolSpec, register
except ImportError:
    from ai import conversations, embeddings
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


# How many un-embedded FAQ rows one search may (re)embed + persist inline.
# Bounds first-call latency and write fan-out; remaining rows are embedded on
# subsequent searches until steady state (all embedded → query-only embed call).
_SEMANTIC_MAX_EMBED = 50
_NO_MATCH = {
    "results": [],
    "note": "No matching help-centre article — say so plainly and offer escalate_to_support.",
}


def _faq_view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"question": row.get("question"), "answer": row.get("answer"), "category": row.get("category")}


def _lexical_results(rows: list, query: str) -> list:
    q_tokens = _match_tokens(query)
    scored = []
    for row in rows:
        # Question matches count double — a query echoing the question is a
        # far stronger signal than the same words buried in an answer body.
        # _match_tokens folds synonyms so a reworded query still overlaps.
        question_overlap = len(q_tokens & _match_tokens(row.get("question", "")))
        body_overlap = len(q_tokens & _match_tokens(f"{row.get('answer', '')} {row.get('category', '')}"))
        score = question_overlap * 2 + body_overlap
        if score:
            scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_faq_view(r) for _, r in scored[:5]]


def _stored_vector(row: Dict[str, Any], model: str):
    vec = row.get("embedding")
    if isinstance(vec, list) and vec and row.get("embedding_model") == model:
        return vec
    return None


async def _persist_embedding(row: Dict[str, Any], vec: list, model: str) -> None:
    """Best-effort — a write failure must never fail the search."""
    try:
        await db_supabase.update_one("faqs", {"id": row["id"]}, {"embedding": vec, "embedding_model": model})
    except Exception:
        logger.error("ai faq embedding persist failed", exc_info=True)


def _schedule_persist(pairs: list, model: str) -> None:
    """Fire-and-forget the embedding writes so the user-facing tool path never
    blocks on Supabase — awaiting up to _SEMANTIC_MAX_EMBED writes inside the 5s
    tool budget risked a timeout (Codex). Ranking already has the vectors in
    memory, so persistence is pure background work."""

    async def _run() -> None:
        await asyncio.gather(*(_persist_embedding(r, v, model) for r, v in pairs))

    task = asyncio.create_task(_run())
    task.add_done_callback(lambda t: t.exception())  # swallow, avoid warnings


def _merge_results(primary: list, secondary: list) -> list:
    """Union two result lists, primary first, deduped by question, capped at 5."""
    seen, out = set(), []
    for r in (*primary, *secondary):
        key = r.get("question")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:5]


async def _semantic_results(rows: list, query: str, settings: Dict[str, Any]):
    """Rank FAQs by cosine similarity. Returns (results, complete) or None when
    embeddings are unavailable (caller uses lexical). ``complete`` is False when
    some rows had no vector this pass (cold rollout beyond the embed cap) — the
    caller then merges lexical matches so an un-embedded but keyword-matching FAQ
    is not dropped. An empty results list means nothing cleared the floor."""
    model = embeddings.embedding_model_for(settings)
    if not model or not rows:
        return None

    to_embed = [r for r in rows if _stored_vector(r, model) is None][:_SEMANTIC_MAX_EMBED]
    vectors = await embeddings.embed_texts([query] + [r.get("question", "") for r in to_embed], settings)
    if vectors is None:
        return None

    q_vec, new_vecs = vectors[0], vectors[1:]
    fresh = {r["id"]: v for r, v in zip(to_embed, new_vecs, strict=True)}
    if fresh:
        _schedule_persist([(r, fresh[r["id"]]) for r in to_embed], model)  # deferred, not awaited

    # Explicit None check — a configured floor of 0 (inspect all matches) must
    # not be silently replaced by the default via truthiness.
    raw_floor = settings.get("ai_faq_semantic_min_score")
    min_score = 0.30 if raw_floor in (None, "") else float(raw_floor)
    scored, covered = [], 0
    for row in rows:
        vec = fresh.get(row.get("id")) or _stored_vector(row, model)
        if vec is None:
            continue  # not embedded this pass — lexical merge covers it
        covered += 1
        sim = embeddings.cosine(q_vec, vec)
        if sim >= min_score:
            scored.append((sim, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_faq_view(r) for _, r in scored[:5]], covered == len(rows)


async def _current_area_scope(user: Dict[str, Any], audience: str) -> set:
    """Service-area ids the user's current location belongs to — the resolved
    area plus its ancestors. Prefers the device location sent with the turn; for
    drivers falls back to their assigned area. Empty → only global FAQs show."""
    area_id = None
    loc = user.get("_client_location") or {}
    lat, lng = loc.get("lat"), loc.get("lng")
    if lat is not None and lng is not None:
        try:
            try:
                from ..routes.fares import resolve_service_area_for_point
            except ImportError:
                from routes.fares import resolve_service_area_for_point
            area = await resolve_service_area_for_point(float(lat), float(lng))
            if area:
                area_id = area.get("id")
        except Exception:
            logger.error("ai faq service-area resolve failed", exc_info=True)
    if area_id is None and audience == "driver":
        try:
            driver = await db_supabase.get_driver_by_user_id_cached(user["id"])
            if driver:
                area_id = driver.get("service_area_id")
        except Exception:
            logger.error("ai faq driver-area lookup failed", exc_info=True)
    try:
        try:
            from ..routes.fares import resolve_area_scope
        except ImportError:
            from routes.fares import resolve_area_scope
        return await resolve_area_scope(area_id)
    except Exception:
        logger.error("ai faq area-scope lookup failed", exc_info=True)
        return {area_id} if area_id else set()


def _in_area_scope(row_area_ids, scope: set) -> bool:
    """Global FAQs (no areas) always match; an area-tagged FAQ matches when it
    shares an area with the user's scope (current area or an ancestor)."""
    if not row_area_ids:
        return True
    return bool(set(row_area_ids) & scope)


async def search_faqs(user: Dict[str, Any], query: str) -> Dict[str, Any]:
    audience = user.get("ai_audience", "rider")
    settings = await get_app_settings()
    semantic = bool(settings.get("ai_faq_semantic_enabled"))
    # Only pull the (large JSONB) embedding vector when the semantic path will
    # actually read it — lexical/off searches select display columns only.
    columns = (
        "id,question,answer,category,audience,service_area_ids,embedding,embedding_model"
        if semantic
        else "id,question,answer,category,audience,service_area_ids"
    )
    rows = (
        await db_supabase.get_rows(
            "faqs",
            {"is_active": True, "audience": {"$in": ["both", audience]}},
            limit=200,
            columns=columns,
        )
        or []
    )
    # Once any area-scoped FAQ exists for this audience, the answer to a given
    # question depends on the asker's location — so this turn must NOT be
    # replayed cross-user by the (audience, question) response cache. Flag it
    # for the orchestrator. (Checked before the scope filter, on the full
    # audience corpus, so a no-location user's global answer can't be cached
    # and then wrongly served to a rider whose area has a specific FAQ.)
    location_scoped = any(r.get("service_area_ids") for r in rows)

    # Location scope: keep global FAQs (no service area) plus those tagged for
    # the user's current area or any of its ancestor areas. Unknown → global only.
    scope = await _current_area_scope(user, audience)
    rows = [r for r in rows if _in_area_scope(r.get("service_area_ids"), scope)]

    results = None
    if semantic:
        sem = await _semantic_results(rows, query, settings)
        if sem is not None:
            sem_results, complete = sem
            # When not every row was embedded yet, merge keyword matches so a
            # relevant un-embedded FAQ can't be omitted by a semantic-only pass.
            results = sem_results if complete else _merge_results(sem_results, _lexical_results(rows, query))
    # None (embeddings unavailable) or empty (nothing above floor) → lexical.
    if not results:
        results = _lexical_results(rows, query)
    out = dict(_NO_MATCH) if not results else {"results": results[:5]}
    if location_scoped:
        # Meta flag, popped by the orchestrator — never serialized to the model.
        out["_no_cache"] = True
    return out


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
        # Write-capable (can open a real support ticket with the chat
        # transcript when ai_escalation_creates_ticket is on) — the /mcp
        # surface is a READ-ONLY contract, so this stays chat-only.
        mcp_exposed=False,
    )
)
