from contextvars import ContextVar

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_user_id_var: ContextVar[str] = ContextVar("user_id", default="")
_domain_var: ContextVar[str] = ContextVar("domain", default="backend")


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_context(request_id: str, user_id: str = "", domain: str = "backend") -> None:
    _request_id_var.set(request_id)
    _user_id_var.set(user_id)
    _domain_var.set(domain)


def get_log_context() -> dict:
    return {
        "request_id": _request_id_var.get(),
        "user_id": _user_id_var.get(),
        "domain": _domain_var.get(),
    }
