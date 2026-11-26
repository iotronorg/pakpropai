"""
Copy the old WhatsApp log into the message log (T-3100b, ADR-064 phase B).

For each ``WhatsAppMessage`` not yet in the message log (same Meta id in the same
session): an old CRM mirror of it in the same organization (no session yet) is
completed in place (session, organization, type, media), so its lead and id stay;
otherwise a new row is written with the message's time and no lead (the old log
had none). A mirror filed under another organization's lead (KI-073) is left as
it is and stays hidden by the view. Rows without a Meta id cannot be matched and
are left (reported). Idempotent; safe to stop and run again.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from .models import ConversationMessage as CM


def pending():
    from apps.whatsapp.models import WhatsAppMessage
    copied = CM.objects.filter(session_id=OuterRef('session_id'), wa_message_id=OuterRef('wa_message_id'))
    return WhatsAppMessage.objects.exclude(wa_message_id='').exclude(Exists(copied))


def unmatched() -> int:
    from apps.whatsapp.models import WhatsAppMessage
    return WhatsAppMessage.objects.filter(wa_message_id='').count()


def _mirror(w):
    org = w.session.organization_id
    mine = CM.objects.filter(wa_message_id=w.wa_message_id, channel=CM.Channel.WHATSAPP, session__isnull=True)
    return mine.filter(Q(lead__organization_id=org) if org else Q(lead__organization__isnull=True)).first()


def run_batch(size: int = 500) -> dict:
    done = {'linked': 0, 'copied': 0}
    with transaction.atomic():
        rows = list(pending().select_related('session').order_by('created_at', 'pk')[:size])
        for w in rows:
            fields = dict(session_id=w.session_id, organization_id=w.session.organization_id, msg_type=w.msg_type,
                          media_id=w.media_id, media_url=w.media_url)
            mirror = _mirror(w)
            if mirror is not None:
                CM.objects.filter(pk=mirror.pk).update(**fields)
                done['linked'] += 1
            else:
                row = CM.objects.create(direction=w.direction, channel=CM.Channel.WHATSAPP, body=w.body,
                                        wa_message_id=w.wa_message_id, **fields)
                CM.objects.filter(pk=row.pk).update(created_at=w.created_at)
                done['copied'] += 1
    done['batch'] = len(rows)
    return done
