"""Super-admin Sentry Issues viewer (read + resolve).

A thin, stateless proxy over the Sentry Web API. The admin dashboard uses it
to let a super-admin see live issues across every Spinr surface (backend /
rider-app / driver-app / admin), drill into an issue's latest stacktrace, and
mark an issue resolved ("close" it). Nothing is stored server-side: every
request hits Sentry live, and the dashboard's Refresh button simply re-calls
these endpoints, so the data is always the current Sentry state.

Config lives in ``core/config.py`` (SENTRY_API_TOKEN / SENTRY_ORG_SLUG /
SENTRY_API_BASE_URL / SENTRY_PROJECT_* / SENTRY_PROJECT_ALL). When it's missing
the read endpoints report ``configured: False`` (via /config) rather than
pretending; the list/detail/resolve endpoints raise 503 so a misconfiguration
surfaces loudly instead of silently returning nothing.

Two ways to tell the surfaces apart, chosen by config (see ``_targets``):
  * PROJECT MODE — SENTRY_PROJECT_* give each surface its own Sentry project;
    an issue's surface is implied by which project returned it.
  * TAG MODE — SENTRY_PROJECT_ALL names one project every surface reports into,
    and each leg of the fan-out narrows by the `surface` tag the SDKs set at
    init. An extra `!has:surface` leg keeps untagged events visible instead of
    dropping them. Per-surface slugs win when both are configured, so adding
    SENTRY_PROJECT_ALL cannot change an existing project-mode deployment.

Auth: mounted with ``Depends(require_super_admin)`` in routes/admin/__init__.py
AND re-declared on every handler here, the same belt-and-braces posture as
routes/admin/stripe_payout_sync.py — so the gate travels with the handler if
the router is ever remounted.

PIPEDA: issue titles and stacktraces can contain scrubbed-but-still-sensitive
strings, so this module never logs issue bodies — only issue ids, project
slugs, counts, and upstream HTTP status codes. Event tags are relayed through
an explicit allowlist (``_TAG_ALLOWLIST``) rather than verbatim: each surface's
Sentry SDK scrubs PII at capture, but the SDKs also auto-attach tags like
``url`` / ``server_name`` / ``user`` that are outside that scrubbing contract,
and this viewer must not be the thing that widens it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from ...core.config import settings
    from ...dependencies import require_super_admin
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import default_limiter as limiter
except ImportError:
    from core.config import settings  # type: ignore
    from dependencies import require_super_admin  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore
    from utils.rate_limiter import default_limiter as limiter  # type: ignore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentry", tags=["Sentry"])

# Ordered surface -> Settings attribute holding that surface's Sentry project
# slug. Order controls the display order of the surface tabs in the dashboard.
_SURFACE_SETTING = (
    ("backend", "SENTRY_PROJECT_BACKEND"),
    ("rider-app", "SENTRY_PROJECT_RIDER"),
    ("driver-app", "SENTRY_PROJECT_DRIVER"),
    ("admin", "SENTRY_PROJECT_ADMIN"),
)

# The single source of truth for issue statuses, used by BOTH the list filter
# (`is:<status>` in the Sentry search) and the status-update endpoint. "resolved"
# is the "close" action the dashboard exposes; "ignored" (mute) and "unresolved"
# (reopen) round out the set, so the frontend can reopen a mistake. Deliberately
# no "muted" alias: it is not accepted by the update endpoint, so allowing it on
# the list side only would mean a value the UI can filter by but never set —
# and a rejected `is:muted` search surfaces as an opaque 502 from upstream.
_ALLOWED_STATUSES: Tuple[str, ...] = ("unresolved", "resolved", "ignored")
_STATUSES_HELP = ", ".join(_ALLOWED_STATUSES)

# Event tags relayed to the dashboard. ALLOWLIST, not a denylist: Sentry's SDKs
# auto-attach tags beyond what our `beforeSend` scrubbers cover (`url`,
# `server_name`, `transaction`, browser/RN user context), and those can carry
# addresses, hostnames, and identifiers that must never reach an admin screen
# under PIPEDA. Everything here is either an environment descriptor or an id
# that Spinr's own Sentry tag conventions (see CLAUDE.md → Observability) allow.
# Add to this list deliberately; never swap it for "relay everything".
_TAG_ALLOWLIST = frozenset(
    {
        "environment",
        "release",
        "level",
        "logger",
        "handled",
        "mechanism",
        "runtime",
        "runtime.name",
        "server_version",
        # Spinr's own conventions — ids only, never PII.
        "domain",
        "surface",
        "ride_id",
        "driver_id",
        "rider_id",
    }
)

# Per-project list cap. Sentry's project-issues endpoint returns one page; we
# don't paginate here because the viewer is a triage surface, not an archive —
# the newest `limit` issues per surface is what an operator scans. The cap is
# surfaced to the client (`truncated` hint) so a full page never reads as
# "that's everything".
_MAX_LIMIT = 100
_DEFAULT_LIMIT = 25

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Sentry's project-issues endpoint accepts ONLY these for `statsPeriod`, where it
# sizes the per-issue stats sparkline: "can be one of '24h', '14d', and ''".
# Anything else is a hard 400. The dashboard offers 24h/7d/14d/30d/90d, so three
# of its five options used to fail — and because the 400 was mapped to a generic
# 502, the UI reported "Sentry API failed for every configured surface", which
# reads as an outage rather than an unsupported parameter.
_SENTRY_STATS_PERIODS = ("", "24h", "14d")

# Look-back windows accepted from the client. Sentry's relative-time suffixes:
# m(inutes) h(ours) d(ays) w(eeks).
_PERIOD_RE = re.compile(r"^\d{1,3}[mhdw]$")

# Rate limits. This surface fans one request out to N Sentry projects, and the
# dashboard re-fetches on every filter change — an abandoned tab in a reload
# loop could otherwise burn the org's Sentry API quota for everyone. Reads are
# generous enough for real triage; the mutation is tighter.
_READ_RATE_LIMIT = "60/minute"
_WRITE_RATE_LIMIT = "20/minute"


def _per_surface_projects() -> Dict[str, str]:
    """{surface: project_slug} for every surface given its OWN project slug."""
    out: Dict[str, str] = {}
    for surface, attr in _SURFACE_SETTING:
        slug = getattr(settings, attr, None)
        if slug:
            out[surface] = slug
    return out


def _shared_project() -> Optional[str]:
    """The single project every surface reports into, or None (see TAG MODE)."""
    slug = (settings.SENTRY_PROJECT_ALL or "").strip()
    return slug or None


def _tag_mode() -> bool:
    """True when surfaces are told apart by the `surface` tag, not by project.

    Per-surface slugs win: if anyone has wired even one SENTRY_PROJECT_*, that
    is the more specific configuration and tag mode stays off. This makes
    SENTRY_PROJECT_ALL purely additive — setting it can never change the
    behaviour of an existing project-mode deployment.
    """
    return bool(_shared_project()) and not _per_surface_projects()


def _surface_projects() -> Dict[str, str]:
    """{surface: project_slug} for every surface the viewer can serve.

    In tag mode every surface maps to the same shared project; they are
    separated at query time by a `surface:<name>` search term instead.
    """
    shared = _shared_project()
    if shared and not _per_surface_projects():
        return {surface: shared for surface, _ in _SURFACE_SETTING}
    return _per_surface_projects()


def _is_configured() -> bool:
    return bool(settings.SENTRY_API_TOKEN and settings.SENTRY_ORG_SLUG and _surface_projects())


def _require_configured() -> None:
    if not _is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Sentry API is not configured. Set SENTRY_API_TOKEN, "
                "SENTRY_ORG_SLUG, and either SENTRY_PROJECT_ALL (one project "
                "for every surface) or at least one SENTRY_PROJECT_* in the "
                "backend environment."
            ),
        )


# Surface label for issues whose events carry no `surface` tag. Tag mode asks
# for these explicitly (`!has:surface`) instead of letting them fall out of the
# result: an error-triage screen that silently drops a whole class of events is
# worse than one that shows them labelled "unknown".
_UNKNOWN_SURFACE = "unknown"


class _Target(NamedTuple):
    """One leg of the fan-out: which project to ask, and what to call the answer.

    ``term`` is an extra Sentry search term ANDed onto the status query. It is
    None in project mode (the project itself identifies the surface) and
    ``surface:<name>`` / ``!has:surface`` in tag mode.
    """

    surface: str
    project: str
    term: Optional[str]


def _targets(surface: Optional[str]) -> List[_Target]:
    """Build the fan-out plan for a list request.

    Project mode issues one request per configured project and infers each
    issue's surface from which project answered it. Tag mode issues one request
    per surface against the single shared project, narrowed by `surface:<name>`
    — so the label is something we asked for rather than something we guessed.

    Raises 400 for a surface the current configuration cannot serve, rather
    than silently returning an empty list.
    """
    shared = _shared_project()
    if shared and not _per_surface_projects():
        known = [s for s, _ in _SURFACE_SETTING]
        if surface is not None:
            # "unknown" is a real, selectable bucket in tag mode, not an error.
            if surface == _UNKNOWN_SURFACE:
                return [_Target(_UNKNOWN_SURFACE, shared, "!has:surface")]
            if surface not in known:
                raise HTTPException(status_code=400, detail=f"Surface '{surface}' is not configured")
            return [_Target(surface, shared, f"surface:{surface}")]
        return [_Target(s, shared, f"surface:{s}") for s in known] + [_Target(_UNKNOWN_SURFACE, shared, "!has:surface")]

    # Project mode. Deliberately via _surface_projects(), which is identical to
    # _per_surface_projects() on this branch (tag mode was ruled out above) and
    # keeps one seam for callers and tests that stub the surface map.
    projects = _surface_projects()
    if surface is not None:
        if surface not in projects:
            raise HTTPException(status_code=400, detail=f"Surface '{surface}' is not configured")
        return [_Target(surface, projects[surface], None)]
    return [_Target(s, slug, None) for s, slug in projects.items()]


def _period_params(stats_period: str) -> Tuple[str, Optional[str]]:
    """Split a requested look-back into (statsPeriod, extra search term).

    `statsPeriod` only sizes the stats sparkline and rejects anything outside
    ``_SENTRY_STATS_PERIODS``. The real look-back filter is a `lastSeen:-<window>`
    search term, which takes arbitrary windows ("lastSeen:-2d returns issues last
    seen within the past two days"). So clamp the sparkline to a value Sentry
    accepts and let the search do the filtering.

    The alternative — restricting the dashboard to 24h/14d — would drop 30d/90d
    triage, which is exactly the window where slow-burn issues become visible.
    """
    period = (stats_period or "").strip()
    if not period:
        return "", None
    if not _PERIOD_RE.match(period):
        # Reject here with a clear 400 rather than forwarding garbage and
        # surfacing Sentry's rejection as a 502 that looks like an outage.
        raise HTTPException(
            status_code=400,
            detail="stats_period must look like 24h, 7d, 30d, or 12w",
        )
    stats = period if period in _SENTRY_STATS_PERIODS else "14d"
    return stats, f"lastSeen:-{period}"


def _base_url() -> str:
    return (settings.SENTRY_API_BASE_URL or "https://sentry.io").rstrip("/")


def _validate_issue_id(issue_id: str) -> str:
    """Reject anything that is not a plain Sentry issue id.

    ``issue_id`` is interpolated straight into the Sentry API path, so a value
    carrying ``/``, ``..``, ``?`` or ``#`` would reshape the request into a
    different Sentry endpoint. Sentry issue ids are decimal integers, so the
    check is exact rather than a sanitiser — nothing is stripped and silently
    accepted.
    """
    candidate = (issue_id or "").strip()
    if not candidate.isdigit():
        raise HTTPException(status_code=400, detail="Invalid Sentry issue id")
    return candidate


async def _sentry_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
) -> httpx.Response:
    """Issue one authenticated Sentry API call. Raises HTTPException (never a
    bare httpx error) so callers get a clean upstream-failure response and the
    dashboard can retry. Never logs the response body (may hold scrubbed PII)."""
    url = f"{_base_url()}/api/0{path}"
    headers = {"Authorization": f"Bearer {settings.SENTRY_API_TOKEN}"}
    try:
        resp = await client.request(method, url, params=params, json=json, headers=headers)
    except httpx.HTTPError as exc:
        # PII-safe: log the path + exception type only, never issue content.
        logger.error("[sentry] %s %s failed: %s", method, path, type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Sentry API request failed: {type(exc).__name__}") from exc

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Sentry issue or project not found")
    if resp.status_code in (401, 403):
        logger.error("[sentry] %s %s -> %s (check token scopes)", method, path, resp.status_code)
        raise HTTPException(
            status_code=502,
            detail="Sentry rejected the API token (needs event:read + event:write). Check SENTRY_API_TOKEN scopes.",
        )
    if resp.status_code == 400:
        # A 400 here is Sentry rejecting OUR request shape (an unsupported
        # statsPeriod, an unparseable search term), not an outage. Its body is a
        # parameter-validation message rather than issue content, so relaying a
        # truncated `detail` is PII-safe and is the difference between a
        # diagnosable error and an opaque "Sentry API failed for every surface".
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("error") or "")[:200]
        except ValueError:
            detail = ""
        logger.error("[sentry] %s %s -> 400 %s", method, path, detail or "(no detail)")
        raise HTTPException(
            status_code=502,
            detail=f"Sentry rejected the request: {detail}" if detail else "Sentry API returned HTTP 400",
        )
    if resp.status_code >= 400:
        logger.error("[sentry] %s %s -> %s", method, path, resp.status_code)
        raise HTTPException(status_code=502, detail=f"Sentry API returned HTTP {resp.status_code}")
    return resp


def _shape_issue(raw: Dict[str, Any], surface: str, project_slug: str) -> Dict[str, Any]:
    """Normalise a Sentry issue object into the dashboard's SentryIssue shape.

    Only the fields the triage view needs — no raw Sentry envelope. `count`
    comes back from Sentry as a string; coerce so the frontend can sort/sum.
    """
    metadata = raw.get("metadata") or {}
    try:
        count = int(raw.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {
        "id": str(raw.get("id")) if raw.get("id") is not None else None,
        "short_id": raw.get("shortId"),
        "title": raw.get("title"),
        "culprit": raw.get("culprit"),
        # `level` is the event severity (fatal/error/warning/info/debug);
        # `priority` is Sentry's newer high/medium/low triage field (may be
        # absent on older orgs). The dashboard shows priority when present and
        # falls back to level.
        "level": raw.get("level"),
        "priority": raw.get("priority"),
        "status": raw.get("status"),
        "substatus": raw.get("substatus"),
        "count": count,
        "user_count": raw.get("userCount"),
        "first_seen": raw.get("firstSeen"),
        "last_seen": raw.get("lastSeen"),
        "permalink": raw.get("permalink"),
        "type": metadata.get("type"),
        "value": metadata.get("value"),
        "surface": surface,
        "project": project_slug,
    }


def _extract_exceptions(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull the exception chain + stack frames out of a Sentry event.

    Sentry events carry an ``entries`` list; the ``exception`` entry holds
    ``data.values`` (one per chained exception), each with a ``stacktrace`` of
    frames. We flatten to a compact shape the dashboard renders directly.
    """
    exceptions: List[Dict[str, Any]] = []
    for entry in event.get("entries") or []:
        if entry.get("type") != "exception":
            continue
        for exc in (entry.get("data") or {}).get("values") or []:
            frames_out: List[Dict[str, Any]] = []
            stacktrace = exc.get("stacktrace") or {}
            for frame in stacktrace.get("frames") or []:
                # Sentry gives frame context as a list of [lineno, source] pairs.
                # Only match when the frame actually has a line number: minified
                # JS frames without sourcemaps carry lineNo=None, and a context
                # pair whose lineno is also None would otherwise "match" and
                # attach an unrelated source line to the frame.
                line_no = frame.get("lineNo")
                context_line = None
                if line_no is not None:
                    for pair in frame.get("context") or []:
                        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == line_no:
                            context_line = pair[1]
                            break
                frames_out.append(
                    {
                        "filename": frame.get("filename") or frame.get("absPath"),
                        "function": frame.get("function"),
                        "module": frame.get("module"),
                        "lineno": line_no,
                        "colno": frame.get("colNo"),
                        "in_app": frame.get("inApp"),
                        "context_line": context_line,
                    }
                )
            exceptions.append(
                {
                    "type": exc.get("type"),
                    "value": exc.get("value"),
                    "module": exc.get("module"),
                    # Sentry orders frames oldest-first (the crashing frame is
                    # last). Reverse so the dashboard shows the crash site on top.
                    "frames": list(reversed(frames_out)),
                }
            )
    return exceptions


def _filter_tags(raw_tags: Any) -> List[Dict[str, str]]:
    """Keep only allowlisted event tags (see ``_TAG_ALLOWLIST``).

    Anything not explicitly allowed is dropped rather than redacted-in-place —
    a `url=<redacted>` chip would tell the operator nothing and still invite
    someone to "just widen it a bit" later.
    """
    out: List[Dict[str, str]] = []
    for tag in raw_tags or []:
        if not isinstance(tag, dict):
            continue
        key = tag.get("key")
        if not isinstance(key, str) or key not in _TAG_ALLOWLIST:
            continue
        value = tag.get("value")
        out.append({"key": key, "value": "" if value is None else str(value)})
    return out


def _sentry_config() -> Dict[str, Any]:
    """Implementation behind ``GET /config`` — see the route."""
    projects = _surface_projects()
    return {
        "configured": _is_configured(),
        "org": settings.SENTRY_ORG_SLUG,
        "base_url": _base_url(),
        # "tag" = one shared project, surfaces separated by the `surface` tag;
        # "project" = one Sentry project per surface. Advisory for the UI —
        # every other field means the same thing in both modes.
        "mode": "tag" if _tag_mode() else "project",
        "surfaces": [
            {"surface": surface, "project": projects.get(surface), "enabled": surface in projects}
            for surface, _ in _SURFACE_SETTING
        ],
    }


@router.get("/config")
@limiter.limit(_READ_RATE_LIMIT)
async def get_sentry_config(
    request: Request,
    current_admin: dict = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Report whether the Sentry viewer is usable and which surfaces are wired.

    The dashboard calls this first: if ``configured`` is False it renders a
    setup hint instead of firing the issue queries. Never returns the token.
    """
    return _sentry_config()


async def _fetch_project_issues(
    client: httpx.AsyncClient,
    surface: str,
    project_slug: str,
    *,
    query: str,
    stats_period: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """List issues for one project (project-scoped endpoint takes the slug
    directly, avoiding a project-id lookup). Sorted newest-seen first by Sentry."""
    org = settings.SENTRY_ORG_SLUG
    resp = await _sentry_request(
        client,
        "GET",
        f"/projects/{org}/{project_slug}/issues/",
        params={"query": query, "statsPeriod": stats_period, "limit": limit, "sort": "date"},
    )
    rows = resp.json()
    if not isinstance(rows, list):
        return []
    return [_shape_issue(r, surface, project_slug) for r in rows]


def _error_detail(exc: BaseException) -> str:
    """One-line, PII-free description of a per-surface failure for the client."""
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return f"Sentry API request failed: {type(exc).__name__}"


async def _list_issues(
    *,
    surface: Optional[str],
    status: str,
    query: Optional[str],
    stats_period: str,
    limit: int,
) -> Dict[str, Any]:
    """Implementation behind ``GET /issues`` — see the route for the contract.

    Kept separate from the decorated route so unit tests can drive it without
    constructing a ``Request`` for the rate limiter.
    """
    _require_configured()

    targets = _targets(surface)

    status = (status or "unresolved").strip()
    if status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {_STATUSES_HELP}")
    # The look-back is applied as a search term, not via statsPeriod — see
    # _period_params for why. `stats_param` is only the sparkline window.
    stats_param, period_term = _period_params(stats_period)

    # Compose the Sentry search: `is:<status>` plus the look-back plus any extra
    # caller terms.
    base_query = f"is:{status}"
    if period_term:
        base_query = f"{base_query} {period_term}"
    if query:
        base_query = f"{base_query} {query.strip()}"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # return_exceptions=True is load-bearing, not defensive style. Without
        # it the first failing leg propagates immediately, the `async with`
        # closes the client out from under its still-in-flight siblings, and
        # one typo'd SENTRY_PROJECT_* slug blanks the entire multi-surface
        # triage view. Degrade per surface instead: report what worked and say
        # loudly which surface failed and why.
        results = await asyncio.gather(
            *(
                _fetch_project_issues(
                    client,
                    t.surface,
                    t.project,
                    query=f"{base_query} {t.term}" if t.term else base_query,
                    stats_period=stats_param,
                    limit=limit,
                )
                for t in targets
            ),
            return_exceptions=True,
        )

    issues: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    truncated = False
    for target, result in zip(targets, results, strict=True):
        if isinstance(result, BaseException):
            logger.error(
                "[sentry] listing project %s (%s) failed: %s",
                target.project,
                target.surface,
                _error_detail(result),
            )
            errors.append({"surface": target.surface, "project": target.project, "detail": _error_detail(result)})
            continue
        issues.extend(result)
        # A leg that returned a full page may have more behind it — flag so a
        # full list never silently reads as "everything".
        if len(result) >= limit:
            truncated = True

    # In tag mode every leg queries the SAME project, so one issue can match two
    # legs if its events carry more than one `surface` value (same error
    # signature raised on two surfaces). Sentry groups by signature, not by tag,
    # so that is a real possibility rather than a theoretical one. Keep the
    # first match — legs run in _SURFACE_SETTING order, so the label is stable
    # across refreshes rather than dependent on which request returned first.
    deduped: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for issue in issues:
        issue_id = issue.get("id")
        if issue_id is not None:
            if issue_id in seen_ids:
                continue
            seen_ids.add(issue_id)
        deduped.append(issue)
    issues = deduped

    # Every configured surface failed: there is nothing to show and pretending
    # otherwise ("0 issues, all clear") would be the worst possible outcome on
    # an error-triage screen. Surface it as an upstream failure.
    if errors and not issues and len(errors) == len(targets):
        raise HTTPException(
            status_code=502,
            detail="Sentry API failed for every configured surface: "
            + "; ".join(f"{e['surface']}: {e['detail']}" for e in errors),
        )

    # Merge across legs newest-seen first so the combined view stays useful.
    issues.sort(key=lambda i: i.get("last_seen") or "", reverse=True)

    return {
        "issues": issues,
        "count": len(issues),
        "surfaces": [t.surface for t in targets],
        # Surfaces whose fetch failed. The dashboard warns on a non-empty list
        # so a partial view is never mistaken for a complete one.
        "errors": errors,
        "partial": bool(errors),
        "status": status,
        "stats_period": stats_period,
        "per_project_limit": limit,
        "truncated": truncated,
    }


@router.get("/issues")
@limiter.limit(_READ_RATE_LIMIT)
async def list_sentry_issues(
    request: Request,
    surface: Optional[str] = Query(None, description="Filter to one surface; omit for all configured surfaces"),
    status: str = Query("unresolved", description=f"Sentry issue status filter: {_STATUSES_HELP}"),
    query: Optional[str] = Query(None, description="Extra Sentry search terms appended to the status filter"),
    stats_period: str = Query("14d", description="Look-back window (e.g. 24h, 14d, 90d)"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    current_admin: dict = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Live issues across the configured surfaces, tagged with which app each
    came from. No caching — every call is a fresh Sentry read, so the
    dashboard's Refresh is just another call with no stale state to clear.

    A surface whose Sentry read fails is reported in ``errors`` and the rest
    still render; only an all-surfaces failure is a 502."""
    return await _list_issues(
        surface=surface,
        status=status,
        query=query,
        stats_period=stats_period,
        limit=limit,
    )


def _resolve_surface(raw_issue: Dict[str, Any]) -> Tuple[str, str]:
    """Map an issue's Sentry project slug to a Spinr surface, or reject it.

    The org token is org-scoped, so ``/organizations/{org}/issues/{id}/`` will
    happily return an issue from a project that has nothing to do with Spinr.
    This viewer is defined by SENTRY_PROJECT_* / SENTRY_PROJECT_ALL — an id
    outside that set is out of scope, and resolving it to ``surface: "unknown"``
    would silently make this a read/write console for the entire Sentry
    organization.

    In tag mode the project no longer identifies the surface (every surface
    shares one project), so the scope check still runs against that project but
    the surface comes back as "unknown". ``_get_issue`` upgrades it from the
    latest event's `surface` tag, which is the only authoritative source here;
    the issues endpoint does not return per-issue tag values.
    """
    project_slug = (raw_issue.get("project") or {}).get("slug") or ""

    shared = _shared_project()
    if shared and not _per_surface_projects():
        if project_slug != shared:
            logger.warning("[sentry] issue in unconfigured project %r requested", project_slug)
            raise HTTPException(
                status_code=404,
                detail="Sentry issue is not in a configured Spinr project",
            )
        return _UNKNOWN_SURFACE, project_slug

    slug_to_surface = {slug: surf for surf, slug in _surface_projects().items()}
    surface = slug_to_surface.get(project_slug)
    if surface is None:
        logger.warning("[sentry] issue in unconfigured project %r requested", project_slug)
        raise HTTPException(
            status_code=404,
            detail="Sentry issue is not in a configured Spinr project",
        )
    return surface, project_slug


async def _get_issue(issue_id: str) -> Dict[str, Any]:
    """Implementation behind ``GET /issues/{issue_id}`` — see the route."""
    _require_configured()
    issue_id = _validate_issue_id(issue_id)
    org = settings.SENTRY_ORG_SLUG

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # The issue metadata and its latest event are independent reads.
        # return_exceptions=True so a failure in one does not close the client
        # under the other while it is still in flight; the error is re-raised
        # below with the client already cleanly torn down.
        issue_resp, event_resp = await asyncio.gather(
            _sentry_request(client, "GET", f"/organizations/{org}/issues/{issue_id}/"),
            _sentry_request(client, "GET", f"/organizations/{org}/issues/{issue_id}/events/latest/"),
            return_exceptions=True,
        )

    # The issue read is required; a missing latest event is not fatal (an issue
    # whose events have aged out still has metadata worth showing).
    if isinstance(issue_resp, BaseException):
        raise issue_resp

    raw_issue = issue_resp.json()
    surface, project_slug = _resolve_surface(raw_issue)

    shaped = _shape_issue(raw_issue, surface, project_slug)
    if isinstance(event_resp, BaseException):
        logger.error("[sentry] latest event for issue %s failed: %s", issue_id, _error_detail(event_resp))
        shaped["exceptions"] = []
        shaped["event_id"] = None
        shaped["event_timestamp"] = None
        shaped["tags"] = []
        shaped["event_error"] = _error_detail(event_resp)
        return shaped

    event = event_resp.json()
    shaped["exceptions"] = _extract_exceptions(event)
    shaped["event_id"] = event.get("id")
    shaped["event_timestamp"] = event.get("dateCreated") or event.get("dateReceived")
    # Event tags pass through _TAG_ALLOWLIST, not straight through: the SDKs
    # auto-attach `url` / `server_name` / user context that our beforeSend
    # scrubbers do not cover, and none of it belongs on an admin screen.
    shaped["tags"] = _filter_tags(event.get("tags"))
    # Tag mode: the project could not tell us the surface, but the event can.
    # Only trust a value we actually recognise — an arbitrary tag string would
    # reach the dashboard's surface badge unvalidated.
    if shaped.get("surface") == _UNKNOWN_SURFACE:
        known = {s for s, _ in _SURFACE_SETTING}
        for tag in shaped["tags"]:
            if tag["key"] == "surface" and tag["value"] in known:
                shaped["surface"] = tag["value"]
                break
    shaped["event_error"] = None
    return shaped


@router.get("/issues/{issue_id}")
@limiter.limit(_READ_RATE_LIMIT)
async def get_sentry_issue(
    request: Request,
    issue_id: str,
    current_admin: dict = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Full detail for one issue, including the latest event's exception
    stacktrace, so the operator can diagnose without leaving the dashboard.

    404s for an issue outside the configured SENTRY_PROJECT_* set — this is a
    Spinr viewer, not an org-wide Sentry console."""
    return await _get_issue(issue_id)


class UpdateIssueStatusRequest(BaseModel):
    status: str = Field(..., description="resolved (close) | ignored (mute) | unresolved (reopen)")


async def _update_issue_status(issue_id: str, new_status: str, current_admin: dict) -> Dict[str, Any]:
    """Implementation behind ``POST /issues/{issue_id}/status`` — see the route."""
    _require_configured()
    issue_id = _validate_issue_id(issue_id)
    new_status = (new_status or "").strip()
    if new_status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {_STATUSES_HELP}")

    org = settings.SENTRY_ORG_SLUG
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # Confirm the issue is in a configured Spinr project before mutating
        # it. Without this the org-scoped token would let a super-admin resolve
        # issues belonging to any other project in the organization.
        issue_resp = await _sentry_request(client, "GET", f"/organizations/{org}/issues/{issue_id}/")
        surface, project_slug = _resolve_surface(issue_resp.json())

        resp = await _sentry_request(
            client,
            "PUT",
            f"/organizations/{org}/issues/{issue_id}/",
            json={"status": new_status},
        )

    updated = resp.json() if resp.content else {}
    # Audit trail: who changed what to what. PII-safe — issue id, project slug
    # and status only, never issue content. Written to audit_logs (not just the
    # app log) because this is an admin action on production data; the helper
    # swallows its own failures so it can never fail the mutation itself.
    await log_admin_action(
        current_admin,
        "sentry_issue_status_change",
        "sentry_issue",
        issue_id,
        {"status": new_status, "surface": surface, "project": project_slug},
    )
    logger.info(
        "[sentry] issue %s -> %s by admin %s",
        issue_id,
        new_status,
        current_admin.get("id"),
    )
    return {
        "id": issue_id,
        "status": updated.get("status", new_status),
        "status_details": updated.get("statusDetails") or {},
    }


@router.post("/issues/{issue_id}/status")
@limiter.limit(_WRITE_RATE_LIMIT)
async def update_sentry_issue_status(
    request: Request,
    issue_id: str,
    body: UpdateIssueStatusRequest,
    current_admin: dict = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Change an issue's status. The dashboard's "Close" action sends
    ``resolved``; reopen/mute reuse the same endpoint. Requires the token to
    hold event:write. Writes an ``audit_logs`` row."""
    return await _update_issue_status(issue_id, body.status, current_admin)
