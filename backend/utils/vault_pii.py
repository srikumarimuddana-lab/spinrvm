"""
Generic Supabase Vault (pgsodium) encrypt/decrypt helpers for reversible PII
fields, parameterized by RPC name.

Mirrors backend/routes/drivers/_shared.py's _vault_encrypt/_vault_decrypt
exactly (same fail-closed/fail-open contract), generalized so a second table
(emergency_contacts, migration 357) doesn't duplicate the pattern. The driver
module keeps its own copy rather than importing this — surgical change, not
a refactor of already-shipped, tested code.
"""

import logging

from fastapi import HTTPException

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    import db_supabase  # type: ignore

logger = logging.getLogger(__name__)


async def vault_encrypt(rpc_name: str, value: str, hint: str = "") -> str:
    """Encrypt a PII string via a Supabase Vault RPC (e.g. encrypt_emergency_contact_pii).

    Fail-closed: any failure raises 503 rather than storing plaintext PII.
    """
    if not value:
        return value
    try:
        from supabase_client import supabase as _sb  # type: ignore[import]
    except ImportError as exc:
        logger.error(
            "vault_encrypt: supabase_client unavailable for %s (%s) — refusing to store plaintext",
            hint,
            rpc_name,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc
    if not _sb:
        logger.error(
            "vault_encrypt: Supabase client not initialised for %s (%s) — refusing to store plaintext",
            hint,
            rpc_name,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable")
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc(rpc_name, {"plaintext": value}).execute())
        if not res.data:
            logger.error(
                "vault_encrypt: RPC %s returned no data for %s — refusing to store plaintext",
                rpc_name,
                hint,
            )
            raise HTTPException(status_code=503, detail="Encryption service unavailable")
        return str(res.data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "vault_encrypt: RPC %s failed for %s — refusing to store plaintext",
            rpc_name,
            hint,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc


async def vault_decrypt(rpc_name: str, value: str, hint: str = "") -> str:
    """Decrypt a Vault-encrypted PII token via a Supabase RPC (e.g. decrypt_emergency_contact_pii).

    On failure, returns the raw token rather than raising — the encrypted
    token is not PII, so this degrades to unreadable data, not a leak.
    """
    if not value:
        return value
    try:
        from supabase_client import supabase as _sb  # type: ignore[import]
    except ImportError:
        return value
    if not _sb:
        return value
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc(rpc_name, {"secret_id": value}).execute())
        return str(res.data) if res.data else value
    except Exception:
        logger.error(
            "vault_decrypt: RPC %s failed for %s — returning raw token",
            rpc_name,
            hint,
            exc_info=True,
        )
        return value
