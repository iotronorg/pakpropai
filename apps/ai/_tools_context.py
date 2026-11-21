import contextvars
import uuid

_ctx_user    = contextvars.ContextVar('agent_user',    default=None)
_ctx_phone   = contextvars.ContextVar('agent_phone',   default=None)
_ctx_org     = contextvars.ContextVar('agent_org',     default=None)
# One id per inbound user message: lets side-effect tools tell "proposed this
# turn" from "confirmed in a later turn" (apps/ai/confirmation.py).
_ctx_turn    = contextvars.ContextVar('agent_turn',    default='')
_ctx_message = contextvars.ContextVar('agent_message', default='')
# Set only by deterministic Python handlers acting on the user's literal request.
_ctx_explicit_request = contextvars.ContextVar('agent_explicit_request', default=False)


def set_context(user, phone: str, org=None, message: str = ''):
    _ctx_user.set(user)
    _ctx_phone.set(phone)
    _ctx_org.set(org)
    _ctx_turn.set(uuid.uuid4().hex)
    _ctx_message.set(message or '')
    _ctx_explicit_request.set(False)
