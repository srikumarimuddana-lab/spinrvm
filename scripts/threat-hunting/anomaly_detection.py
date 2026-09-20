"""
Behavioral anomaly detection over auth and payment event streams: flags
OTP brute-force/credential-stuffing bursts and payment card-testing/retry
storms.

Phase 4b of the 2026-09-19 security automation roadmap
(docs/audit/2026-09-19-security-automation-roadmap.md) -- the "behavioral
anomaly flags" half of threat hunting, extending the same shape of check
spinr-fraud-auditor already applies to GPS-ping plausibility (implausible
speed between consecutive points) to two different domains: repeated auth
failures and repeated payment failures clustered in a short window.

Scaffolded against MOCKED data, same posture as Phase 3's
correlate_incident.py: there is no live event stream wired in here (that
would need this session's Sentry MCP connector or a direct Supabase query,
neither available), so this builds and tests the detection logic now
against synthetic fixtures shaped like the real event tables, ready to
point at a real query once a data source is authorized.

PIPEDA-safe BY INPUT SCHEMA, not by redaction after the fact (a stronger
design than Phase 3 needed to retrofit): every field this module accepts is
already an id, hash, or last-4 value, matching CLAUDE.md's Compliance
(PIPEDA) rules directly --
  - device_id / ip_hash: identifiers only, never a raw IP address
  - phone_last4: last 4 digits only, never a full phone number
  - card_fingerprint: Stripe's own non-reversible per-card token, never a PAN
`_assert_safe_event()` rejects an event carrying a field shaped like a raw
IP or a full phone number, so a future caller passing unscrubbed data fails
loudly instead of silently laundering PII through this tool.

Expected input shapes (each a JSON array of objects):

  auth_events.json:
    {"event_id": str, "timestamp": ISO8601 str, "outcome": "otp_failed" | "otp_success" | "login_failed",
     "device_id": str | None, "ip_hash": str | None, "phone_last4": str | None, "user_id": str | None}

  payment_events.json:
    {"event_id": str, "timestamp": ISO8601 str, "outcome": "declined" | "succeeded",
     "card_fingerprint": str | None, "user_id": str | None, "decline_reason": str | None,
     "device_id": str | None, "ip_hash": str | None}

Wiring in real data (once a source is authorized): replace the
`_load_json_safe(...)` calls in main() with a live query (e.g. a Sentry
search for auth/payment domain events, or a direct read of the relevant
Supabase tables via backend/db_supabase) mapped into the shapes above --
the detection functions themselves don't change.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_RAW_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_RAW_IPV6_RE = re.compile(r"^[0-9a-fA-F:]{2,45}$")  # loose on purpose -- see _looks_like_raw_ip


def _looks_like_raw_ip(value: str) -> bool:
    if _RAW_IPV4_RE.match(value):
        return True
    # IPv6 has no single reliable regex without a full parser; require at
    # least two "::"-or-colon-separated groups so a plain hex hash (no
    # colons) is never mistaken for one -- a spinr-fraud-auditor review
    # flagged raw IPv6 as an unguarded gap in an IPv4-only check.
    return value.count(":") >= 2 and bool(_RAW_IPV6_RE.match(value))


def _looks_like_full_phone(value: str) -> bool:
    """True if `value` is shaped like a full phone number, tolerating the
    punctuation a real phone string is normally formatted with
    ("306-555-1234", "(306) 555-1234", "+1 306 555 1234") -- a bare-digits-
    only check would miss all of those, which a spinr-fraud-auditor review
    flagged as a false-negative gap in the original regex."""
    digits_only = re.sub(r"[\s().+-]", "", value)
    return digits_only.isdigit() and 7 <= len(digits_only) <= 15


def _assert_safe_event(event: dict[str, Any]) -> None:
    """Raise if an event carries a field shaped like raw PII this module
    should never receive. Checks every identifier field this module
    accepts (not just ip_hash/phone_last4) -- the module's entire safety
    argument rests on this guard, per its own docstring, so it must cover
    every field, not just two of them (spinr-fraud-auditor review finding).
    A loud failure here is the point -- see the module docstring's
    PIPEDA-safe-by-schema section."""
    ip_hash = event.get("ip_hash")
    if isinstance(ip_hash, str) and _looks_like_raw_ip(ip_hash):
        raise ValueError(f"anomaly_detection: ip_hash looks like a raw IP address, not a hash: {ip_hash!r}")

    phone_last4 = event.get("phone_last4")
    if isinstance(phone_last4, str) and len(phone_last4) > 4 and _looks_like_full_phone(phone_last4):
        raise ValueError(f"anomaly_detection: phone_last4 looks like a full phone number, not last-4: {phone_last4!r}")

    # device_id / user_id / card_fingerprint are opaque identifiers by
    # contract -- none of them should ever look like a raw IP or a full
    # phone number either (e.g. a caller accidentally passing a phone
    # number as user_id).
    for field in ("device_id", "user_id", "card_fingerprint"):
        value = event.get(field)
        if not isinstance(value, str):
            continue
        if _looks_like_raw_ip(value):
            raise ValueError(f"anomaly_detection: {field} looks like a raw IP address: {value!r}")
        if _looks_like_full_phone(value):
            raise ValueError(f"anomaly_detection: {field} looks like a full phone number: {value!r}")


def _load_json_safe(path: Optional[str]) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_bursts_in_group(
    key_type: str,
    by_key: dict[str, list[dict[str, Any]]],
    window_seconds: int,
    distinct_target_threshold: int,
) -> list[dict[str, Any]]:
    anomalies = []
    window = timedelta(seconds=window_seconds)
    for key, key_events in by_key.items():
        key_events.sort(key=lambda e: _parse_ts(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
        for i, start_event in enumerate(key_events):
            start_ts = _parse_ts(start_event.get("timestamp"))
            if start_ts is None:
                continue
            in_window = [e for e in key_events[i:] if (_parse_ts(e.get("timestamp")) or start_ts) <= start_ts + window]
            targets = {e.get("phone_last4") or e.get("user_id") for e in in_window if e.get("phone_last4") or e.get("user_id")}
            if len(targets) >= distinct_target_threshold:
                anomalies.append(
                    {
                        "kind": "auth_burst",
                        "key_type": key_type,
                        "key": key,
                        "window_start": start_event.get("timestamp"),
                        "window_seconds": window_seconds,
                        "distinct_targets": len(targets),
                        "event_count": len(in_window),
                    }
                )
                break  # one flag per key is enough; don't re-flag overlapping sub-windows
    return anomalies


def detect_auth_bursts(
    events: list[dict[str, Any]],
    window_seconds: int = 300,
    distinct_target_threshold: int = 5,
) -> list[dict[str, Any]]:
    """Flag a device_id OR ip_hash (checked independently, not one falling
    back to the other) that hit `otp_failed`/`login_failed` against >=
    threshold DISTINCT phone_last4/user_id targets within a sliding window
    -- the credential-stuffing/OTP-brute-force shape (one actor trying many
    targets), not a single user just mistyping their own OTP a few times
    (which would be the SAME target repeatedly, not flagged here).

    device_id and ip_hash are tracked as two SEPARATE grouping dimensions,
    not "device_id if present else ip_hash": a spinr-fraud-auditor review
    found that falling back let an attacker evade detection by randomizing
    a client-supplied device_id every request while the server-derived
    ip_hash (harder to spoof) stayed constant -- each event would land in
    its own single-event device_id bucket and never accumulate. Grouping
    both independently means either stable signal can still catch the
    burst even if the other is being deliberately randomized.

    An event with NEITHER device_id nor ip_hash is not silently dropped --
    see its count in the returned "unattributed_failures" entry, if any.
    Reported for visibility rather than a burst verdict, since with no key
    to group by, no burst determination can be made for these.
    """
    for e in events:
        _assert_safe_event(e)

    failures = [e for e in events if e.get("outcome") in ("otp_failed", "login_failed")]

    by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_ip: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unattributed = 0
    for e in failures:
        device_id, ip_hash = e.get("device_id"), e.get("ip_hash")
        if device_id:
            by_device[device_id].append(e)
        if ip_hash:
            by_ip[ip_hash].append(e)
        if not device_id and not ip_hash:
            unattributed += 1

    anomalies = _find_bursts_in_group("device_id", by_device, window_seconds, distinct_target_threshold)
    anomalies += _find_bursts_in_group("ip_hash", by_ip, window_seconds, distinct_target_threshold)

    if unattributed:
        anomalies.append(
            {
                "kind": "unattributed_failures",
                "event_count": unattributed,
                "note": "auth failures with neither device_id nor ip_hash -- cannot be grouped into a burst determination",
            }
        )
    return anomalies


def detect_payment_retry_storms(
    events: list[dict[str, Any]],
    window_seconds: int = 600,
    failure_threshold: int = 5,
) -> list[dict[str, Any]]:
    """Flag a card_fingerprint with >= threshold `declined` outcomes within
    a sliding window -- the card-testing shape (many small/rapid charge
    attempts against one card, most declined, probing for a working
    card/CVV combination)."""
    for e in events:
        _assert_safe_event(e)

    declines = [e for e in events if e.get("outcome") == "declined"]
    by_card: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in declines:
        card = e.get("card_fingerprint")
        if card:
            by_card[card].append(e)

    anomalies = []
    for card, card_events in by_card.items():
        card_events.sort(key=lambda e: _parse_ts(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
        window = timedelta(seconds=window_seconds)
        for i, start_event in enumerate(card_events):
            start_ts = _parse_ts(start_event.get("timestamp"))
            if start_ts is None:
                continue
            in_window = [e for e in card_events[i:] if (_parse_ts(e.get("timestamp")) or start_ts) <= start_ts + window]
            if len(in_window) >= failure_threshold:
                anomalies.append(
                    {
                        "kind": "payment_retry_storm",
                        "card_fingerprint": card,
                        "window_start": start_event.get("timestamp"),
                        "window_seconds": window_seconds,
                        "decline_count": len(in_window),
                    }
                )
                break
    return anomalies


def detect_card_testing_rings(
    events: list[dict[str, Any]],
    window_seconds: int = 600,
    distinct_card_threshold: int = 5,
) -> list[dict[str, Any]]:
    """Flag a device_id or ip_hash that ran >= threshold DISTINCT
    card_fingerprints through `declined` attempts within a window --
    the professional card-testing-ring shape (one attacker cycling through
    many different stolen cards, each tried once or twice, never repeating
    a single card enough to trip detect_payment_retry_storms).

    A spinr-fraud-auditor review named this as the mirror-image blind spot
    of the single-card-many-declines check above: real card-testing rarely
    hammers one card repeatedly (that's the amateur/lucky-guess shape
    detect_payment_retry_storms catches) -- it spreads attempts across many
    stolen cards from one source. Keep both checks; they catch different
    attacker behavior, same as the auth module's per-key vs distinct-target
    split.
    """
    for e in events:
        _assert_safe_event(e)

    declines = [e for e in events if e.get("outcome") == "declined"]

    by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_ip: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in declines:
        device_id, ip_hash = e.get("device_id"), e.get("ip_hash")
        if device_id:
            by_device[device_id].append(e)
        if ip_hash:
            by_ip[ip_hash].append(e)

    def _find_ring(key_type: str, by_key: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        found = []
        window = timedelta(seconds=window_seconds)
        for key, key_events in by_key.items():
            key_events.sort(key=lambda e: _parse_ts(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
            for i, start_event in enumerate(key_events):
                start_ts = _parse_ts(start_event.get("timestamp"))
                if start_ts is None:
                    continue
                in_window = [e for e in key_events[i:] if (_parse_ts(e.get("timestamp")) or start_ts) <= start_ts + window]
                cards = {e.get("card_fingerprint") for e in in_window if e.get("card_fingerprint")}
                if len(cards) >= distinct_card_threshold:
                    found.append(
                        {
                            "kind": "card_testing_ring",
                            "key_type": key_type,
                            "key": key,
                            "window_start": start_event.get("timestamp"),
                            "window_seconds": window_seconds,
                            "distinct_cards": len(cards),
                            "event_count": len(in_window),
                        }
                    )
                    break
        return found

    return _find_ring("device_id", by_device) + _find_ring("ip_hash", by_ip)


def render_markdown(auth_anomalies: list[dict[str, Any]], payment_anomalies: list[dict[str, Any]], window_label: str) -> str:
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bursts = [a for a in auth_anomalies if a.get("kind") == "auth_burst"]
    unattributed = [a for a in auth_anomalies if a.get("kind") == "unattributed_failures"]
    storms = [a for a in payment_anomalies if a.get("kind") == "payment_retry_storm"]
    rings = [a for a in payment_anomalies if a.get("kind") == "card_testing_ring"]

    lines = [
        "# Behavioral Anomaly Report",
        "",
        f"**Generated:** {report_date}  ",
        f"**Window:** {window_label}  ",
        f"**Auth-burst anomalies:** {len(bursts)}  ",
        f"**Payment-retry-storm anomalies:** {len(storms)}  ",
        f"**Card-testing-ring anomalies:** {len(rings)}",
        "",
        "These are candidate signals for a human fraud/security review, not",
        "confirmed incidents -- a legitimate cause (a shared corporate NAT",
        "gateway's IP, a driver testing several of their own cards) can",
        "produce the same pattern. Verify before acting.",
        "",
    ]
    if bursts:
        lines.append("## Auth bursts (possible credential stuffing / OTP brute force)")
        lines.append("")
        for a in bursts:
            lines.append(
                f"- `{a['key_type']}={a['key']}` hit **{a['distinct_targets']} distinct targets** "
                f"in {a['window_seconds']}s starting `{a['window_start']}` ({a['event_count']} failed attempts)"
            )
        lines.append("")
    if unattributed:
        lines.append("## Unattributed auth failures")
        lines.append("")
        for a in unattributed:
            lines.append(f"- {a['event_count']} failed attempt(s) with neither device_id nor ip_hash -- {a['note']}")
        lines.append("")
    if storms:
        lines.append("## Payment retry storms (possible card testing -- one card, many declines)")
        lines.append("")
        for a in storms:
            lines.append(
                f"- `card_fingerprint={a['card_fingerprint']}` had **{a['decline_count']} declines** "
                f"in {a['window_seconds']}s starting `{a['window_start']}`"
            )
        lines.append("")
    if rings:
        lines.append("## Card-testing rings (possible card testing -- one source, many distinct cards)")
        lines.append("")
        for a in rings:
            lines.append(
                f"- `{a['key_type']}={a['key']}` ran **{a['distinct_cards']} distinct cards** through declined attempts "
                f"in {a['window_seconds']}s starting `{a['window_start']}` ({a['event_count']} attempts)"
            )
        lines.append("")
    if not bursts and not unattributed and not storms and not rings:
        lines.append("_No anomalies found in this window._\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect auth/payment behavioral anomalies")
    parser.add_argument("--auth-events", help="Path to auth_events.json")
    parser.add_argument("--payment-events", help="Path to payment_events.json")
    parser.add_argument("--window-label", default="(unspecified)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    auth_events = _load_json_safe(args.auth_events)
    payment_events = _load_json_safe(args.payment_events)

    auth_anomalies = detect_auth_bursts(auth_events)
    payment_anomalies = detect_payment_retry_storms(payment_events) + detect_card_testing_rings(payment_events)

    report = render_markdown(auth_anomalies, payment_anomalies, args.window_label)
    Path(args.output).write_text(report)
    print(f"Report written to {args.output}")
    print(f"Auth anomalies: {len(auth_anomalies)}, payment anomalies: {len(payment_anomalies)}")


if __name__ == "__main__":
    main()
