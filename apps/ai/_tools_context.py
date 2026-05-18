import contextvars

_ctx_user  = contextvars.ContextVar('agent_user',  default=None)
_ctx_phone = contextvars.ContextVar('agent_phone', default=None)
_ctx_org   = contextvars.ContextVar('agent_org',   default=None)


def set_context(user, phone: str, org=None):
    _ctx_user.set(user)
    _ctx_phone.set(phone)
    _ctx_org.set(org)
