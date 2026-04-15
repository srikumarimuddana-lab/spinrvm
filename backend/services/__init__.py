"""
Service layer for Spinr backend.

Services contain business logic separated from HTTP/route concerns. Routes
should be thin controllers that:
  1. Parse and validate request input (Pydantic schemas)
  2. Authenticate / authorize (FastAPI dependencies)
  3. Delegate to a service
  4. Shape the response

Business logic (calculations, state machines, dispatch rules, etc.) lives here
so it can be unit-tested without spinning up FastAPI or the database.

See services/README.md for the pattern, conventions, and migration plan.
"""

from .dispatch_service import DispatchService  # noqa: F401
from .fare_service import FareService  # noqa: F401
