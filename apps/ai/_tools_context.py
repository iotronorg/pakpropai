import contextvars
import uuid

_ctx_user    = contextvars.ContextVar('agent_user',    default=None)
_ctx_phone   = contextvars.ContextVar('agent_phone',   default=None)
_ctx_org     = contextvars.ContextVar('agent_org',     default=None)
# One id per inbound user message: lets side-effect tools tell "proposed this
# turn" from "confirmed in a later turn" (apps/ai/confirmation.py).
_ctx_turn    = contextvars.ContextVar('agent_turn',    default='')
_ctx_message = contextvars.ContextVar('agent_message', default='')
# Args hash of the one confirmed call allowed to run (apps/ai/confirmation.py only).
_ctx_grant = contextvars.ContextVar('agent_grant', default='')
# False when the reply is not shown to the user (copilot drafts, voice, evals).
_ctx_interactive = contextvars.ContextVar('agent_interactive', default=True)

# The Conversation being handled (T-2020); set by AIServiceManager.process / confirmation only.
_ctx_conversation = contextvars.ContextVar('agent_conversation', default=None)


def set_context(user, phone: str, org=None, message: str = '', interactive: bool = True):
    _ctx_user.set(user)
    _ctx_phone.set(phone)
    _ctx_org.set(org)
    _ctx_turn.set(uuid.uuid4().hex)
    _ctx_message.set(message or '')
    _ctx_grant.set('')
    _ctx_interactive.set(interactive)
