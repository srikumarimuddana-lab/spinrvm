"""Entry point for FastAPI Cloud's app discovery.

FastAPI's standard discovery looks for ``main.py`` / ``app.py`` / ``api.py``.
Spinr's application object lives in ``server.py`` (``app`` at module scope,
built by the app factory there), which discovery does not find. Rather than
rename ``server.py`` — it is referenced by ``python -m backend.server``,
``backend/Dockerfile``'s uvicorn command, ``railway.json``'s start command,
and a great deal of documentation — this module re-exports it.

This file is *only* an alias. It must never grow logic of its own: anything
added here would run on the FastAPI Cloud dev tier and nowhere else, which is
precisely the environment divergence the dev tier exists to avoid. See
``docs/runbooks/fastapi-cloud-dev.md``.

Production is unaffected — the Dockerfile and Railway start command still
target ``server:app`` directly.
"""

# Dual-import pattern (CLAUDE.md, "Critical Conventions"): supports both
# `python -m backend.main` from the repo root and a bare `main:app` import
# when `backend/` itself is the working directory, which is how the FastAPI
# Cloud app directory is configured.
try:
    from .server import app
except ImportError:
    from server import app

__all__ = ["app"]
