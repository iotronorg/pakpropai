"""
A person who withdrew the consent to share contact details stays in the CRM
without a phone number, and is not contacted through it (KI-074, owner decision 2026-11-24).
"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q


def withheld_q(lead='pk') -> Q:
    """Leads with an enquiry whose consent was withdrawn, and none with consent."""
    from apps.enquiries.models import Enquiry
    mine = Enquiry.objects.filter(lead=OuterRef(lead))
    return Q(Exists(mine.filter(consent_revoked_at__isnull=False))) & ~Q(
        Exists(mine.filter(consent_revoked_at__isnull=True)))


def contactable(leads):
    return leads.exclude(withheld_q())


def withheld(lead) -> bool:
    """Uses the ``contact_withheld`` annotation when the queryset has it."""
    if lead is None:
        return False
    if 'contact_withheld' in lead.__dict__:
        return lead.__dict__['contact_withheld']
    from .models import Lead
    return Lead.objects.filter(withheld_q(), pk=lead.pk).exists()


def phone(lead) -> str | None:
    return None if withheld(lead) else lead.user.phone


def phone_text(lead) -> str:
    return phone(lead) or 'number not shared'


def withheld_phones(leads) -> set[str]:
    """Phones, with and without '+', of people none of whose leads here may be contacted."""
    hidden = set(leads.filter(withheld_q()).values_list('user__phone', flat=True))
    if hidden:
        hidden -= set(leads.filter(user__phone__in=hidden).exclude(withheld_q()).values_list('user__phone', flat=True))
    return hidden | {p.lstrip('+') for p in hidden}


def hidden_session_phones(phones, organization_id) -> set[str]:
    """
    Of these WhatsApp session phones, those an organization's live-chat stream must
    not show: the person withdrew the consent on every lead they have there (KI-083).
    """
    from .models import Lead
    phones = {p for p in phones if p}
    if not phones or not organization_id:
        return set()
    spellings = phones | {p.lstrip('+') for p in phones} | {'+' + p.lstrip('+') for p in phones}
    return withheld_phones(Lead.objects.filter(organization_id=organization_id, user__phone__in=spellings)) & phones


def session_phone(phone: str | None, organization_id) -> str | None:
    return None if phone in hidden_session_phones([phone], organization_id) else phone
