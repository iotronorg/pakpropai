import contextvars

_ctx_user = contextvars.ContextVar('agent_user', default=None)
_ctx_phone = contextvars.ContextVar('agent_phone', default=None)


def set_context(user, phone: str):
    _ctx_user.set(user)
    _ctx_phone.set(phone)
