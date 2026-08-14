"""Shared company-settings string helpers (N16).

Extracted from two byte-identical private copies that lived independently in
``utils/company_details.py`` (transactional email) and
``utils/marketing_email.py`` (CASL marketing email) — same logic, same
docstring reasoning, duplicated rather than shared because the marketing
copy sits on a CASL consent-critical path and nobody wanted to risk an
unrelated change touching it.

This module changes nothing about either caller's *output* — it only gives
the identical logic one home. Both callers still pass their own
``settings`` dict through untouched; a settings value that was previously
coalesced/joined one way is coalesced/joined exactly the same way now.
Covered by ``tests/test_address_format.py`` for the shared logic itself,
plus each caller's own existing test suite (`test_company_details.py`,
`test_marketing_email.py`) for output-parity — both passed unmodified after
this extraction, proving no behavior changed.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def coalesce_setting(settings: Dict[str, Any], key: str) -> str:
    """Settings value as a clean string.

    The settings loader can surface a DB NULL as Python ``None`` (it
    overrides the schema default), so a bare ``.get()`` would render the
    string "None" in a footer.
    """
    return str(settings.get(key) or "").strip()


def address_lines(settings: Dict[str, Any]) -> Tuple[str, ...]:
    """The mailing address as display lines: street, then locality.

    ``company_address`` is a free-text field on the admin Settings page and
    often holds the whole address on its own; ``company_city`` /
    ``company_province`` / ``company_postal_code`` came later (migration
    192) and are not on that page, so they are usually blank. Keeping only
    the non-empty parts handles both shapes without producing blank lines.

    The email footer prints these one per line — the conventional receipt
    shape. :func:`postal_address` joins them for the contexts that need a
    single line (the PDF header, the plain-text identity line).
    """
    street = coalesce_setting(settings, "company_address")
    locality = " ".join(
        p
        for p in (
            coalesce_setting(settings, "company_city"),
            coalesce_setting(settings, "company_province"),
            coalesce_setting(settings, "company_postal_code"),
        )
        if p
    )
    return tuple(p for p in (street, locality) if p)


def postal_address(settings: Dict[str, Any]) -> str:
    """The same mailing address as one comma-joined line.

    Defined in terms of :func:`address_lines` so the two cannot disagree
    about which settings fields make up an address — the duplication this
    module was extracted to remove (N16).
    """
    return ", ".join(address_lines(settings))
