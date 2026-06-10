"""Provider adapters for the AI assistant.

Each adapter normalizes one vendor SDK to the canonical types in base.py;
the provider-agnostic tool loop lives in backend/ai/orchestrator.py. The
get_adapter() factory (added with the full adapter set) selects the active
provider/model from app_settings at request time.
"""
