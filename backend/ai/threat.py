"""AI threat detection (Layer 7 — visibility).

Two jobs:
1. scan_message(text) — cheap, offline regex heuristics that flag likely
   prompt-injection / jailbreak / impersonation / data-exfiltration attempts in
   a user message. It DETECTS, it does not block: the tool layer
   (backend/ai/tools.py) already prevents the actual harm (a hijacked model
   still can't act for another user or run destructive actions), and blocking on
   heuristics would false-positive on legitimate questions. Flagging gives the
   security team visibility without breaking real users.
2. record_security_event(...) — fire-and-forget append to ai_security_events so
   suspicious activity is queryable (admin console) and alertable (metric).

PIPEDA-safe: only the matched SIGNAL TAGS and ids are recorded — never the
user's message text (which may contain PII).
"""

import logging
import re
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
    from ..utils.metrics import inc as _metric_inc
except ImportError:  # pragma: no cover — top-level run
    import db_supabase
    from utils.metrics import inc as _metric_inc

logger = logging.getLogger(__name__)

# category -> (severity, [patterns]). Kept deliberately tight to limit false
# positives; the tool layer is the real control, this is the tripwire.
_CRITICAL = {"impersonation", "data_exfiltration"}

_PATTERNS: Dict[str, List[re.Pattern]] = {
    # "ignore previous instructions", "reveal your system prompt", …
    "prompt_injection": [
        re.compile(r"\bignore\s+(all\s+|the\s+|your\s+)?(previous|prior|above)\s+(instruction|prompt|rule)", re.I),
        re.compile(r"\bdisregard\s+(your\s+|the\s+)?(instruction|rule|system\s+prompt|guardrail)", re.I),
        re.compile(
            r"\b(reveal|show|print|repeat|expose)\s+(me\s+)?(your\s+)?(system\s+)?(prompt|instruction|rule)", re.I
        ),
        re.compile(r"\bwhat\s+(is|are)\s+your\s+(system\s+)?(prompt|instruction|rule)", re.I),
    ],
    # "you are now", "developer mode", "act as admin", "jailbreak" …
    "role_override": [
        re.compile(r"\byou\s+are\s+now\b", re.I),
        re.compile(r"\b(developer|debug|god|admin)\s+mode\b", re.I),
        re.compile(r"\bact\s+as\s+(a\s+|an\s+)?(developer|admin|administrator|system|dba|root)\b", re.I),
        re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.I),
        re.compile(r"\bjailbreak\b|\bDAN\b", re.I),
    ],
    # attempts to act as / read another user
    "impersonation": [
        re.compile(r"\b(as|for)\s+(user|rider|driver|customer|account)\s+[#\w-]+", re.I),
        re.compile(r"\bon\s+behalf\s+of\s+(user|another|someone)", re.I),
        re.compile(r"\b(log\s*in|sign\s*in|switch)\s+as\b", re.I),
        re.compile(r"\b(another|other|someone\s+else'?s)\s+(user|rider|driver|account|ride|wallet|trip)", re.I),
    ],
    # bulk / cross-tenant data pulls, SQLi-ish
    "data_exfiltration": [
        re.compile(r"\b(list|show|dump|give\s+me|get)\s+all\s+(users|riders|drivers|rides|accounts|customers)", re.I),
        re.compile(r"\beveryone'?s\s+(data|rides|info|phone|email|wallet)", re.I),
        re.compile(r"\ball\s+(users|riders|drivers)'?\s+(data|phone|email|address)", re.I),
        re.compile(r"\b(select\s+.*\s+from|drop\s+table|union\s+select|;\s*--)", re.I),
    ],
    # fishing for secrets/keys
    "secrets": [
        re.compile(r"\b(api\s*key|access\s*token|secret\s*key|service\s*role|env(ironment)?\s+variable)", re.I),
        re.compile(r"\breveal\b.*\b(key|secret|token|password|credential)", re.I),
    ],
}


def scan_message(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return ``{"signals": [...], "severity": "critical|high"}`` when a message
    trips one or more heuristics, else None. Never returns the message text."""
    if not text:
        return None
    hits = [cat for cat, pats in _PATTERNS.items() if any(p.search(text) for p in pats)]
    if not hits:
        return None
    severity = "critical" if any(c in _CRITICAL for c in hits) else "high"
    return {"signals": sorted(hits), "severity": severity}


def record_security_event(
    *,
    user_id: Optional[str],
    audience: Optional[str],
    conversation_id: Optional[str],
    event_type: str,
    severity: str,
    signals: List[str],
    source: str,
) -> None:
    """Fire-and-forget append to ai_security_events + metric + warning log.
    PII-safe (tags/ids only). Never raises."""
    import asyncio

    _metric_inc("spinr_ai_security_events_total", {"type": event_type, "severity": severity})
    logger.warning(
        "ai security event: %s (%s) via %s signals=%s",
        event_type,
        severity,
        source,
        signals,
        extra={"user_id": user_id, "conversation_id": conversation_id},
    )
    record = {
        "user_id": user_id,
        "audience": audience,
        "conversation_id": conversation_id,
        "event_type": event_type,
        "severity": severity,
        "signals": signals,
        "source": source,
    }

    async def _run() -> None:
        try:
            await db_supabase.insert_one("ai_security_events", record)
        except Exception:
            logger.error("ai security-event write failed", exc_info=True)

    try:
        task = asyncio.create_task(_run())
        task.add_done_callback(lambda t: t.exception())
    except RuntimeError:  # no running loop (sync context) — best-effort
        pass
