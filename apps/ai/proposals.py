"""
What the user is asked to confirm, per tool (T-2012). Every argument that has an
effect must appear in the summary: these are the words the user consents to.
"""
from __future__ import annotations

from apps.markets.pk import CURRENCY as PK_CURRENCY

from ._tools_context import _ctx_org, _ctx_user


class Refused(Exception):
    """The action cannot be proposed; the message goes back to the model."""


def _list_property(a: dict):
    if _ctx_user.get() is None:
        raise Refused('Please register on WhatsApp first so the listing can be linked to you.')
    size = f"{a['area_marla']}M " if a.get('area_marla') else ''
    lines = [f"📋 {size}{a['property_type'].title()} — {a['location']}, {a['city']}",
             f"💰 {PK_CURRENCY} {int(a['price'] or 0):,}"]
    extras = [str(a[k]).replace('_', ' ') for k in ('furnished', 'construction_status') if a.get(k)]
    if extras:
        lines.append(f"🏷️ {', '.join(extras)}")
    if a.get('description'):
        lines.append(f"📝 {a['description']}")
    return 'Please confirm you want to publish this listing:', '\n'.join(lines)


def _connect_to_agent(a: dict):
    place = ', '.join(p for p in (a.get('specific_area'), a.get('city')) if p)
    lines = [f"🤝 {(a.get('intent') or 'buy').title()} enquiry" + (f' in {place}' if place else '')]
    if a.get('property_type'):
        lines.append(f"🏠 {a['property_type']}")
    if a.get('budget_pkr'):
        lines.append(f"💰 Budget: {PK_CURRENCY} {a['budget_pkr']:,}")
    lines.append('Your phone number will be shared with the matched agent.')
    return 'Shall I connect you with a verified agent?', '\n'.join(lines)


def _generate_property_audit(a: dict):
    kind = a.get('property_type') or 'residential'
    lines = [f"📊 Audit report for {kind} — {a['location']}, {a['city']}",
             f"💰 Value you provided: {PK_CURRENCY} {int(a['estimated_value_pkr'] or 0):,}"]
    if a.get('area_marla'):
        lines.append(f"📐 {a['area_marla']} marla")
    if a.get('owner_name'):
        lines.append(f"👤 Owner: {a['owner_name']}")
    lines.append('Indicative estimate only — not a valuation.')
    return 'Shall I generate this property audit report?', '\n'.join(lines)


def _initiate_deal_lock(a: dict):
    from django.core.exceptions import ValidationError

    from apps.config.services import SystemConfigService
    from apps.properties.models import Property

    from ._tools_deals import _resolve_currency
    if not SystemConfigService.get_features().get('feature_deal_lock', False):
        raise Refused('Reserving a property with a token payment is not available. '
                      'I can connect you with an agent to discuss next steps.')
    try:
        prop = Property.objects.get(id=a['property_id'], is_active=True)
    except (Property.DoesNotExist, ValidationError, ValueError):
        raise Refused('Property not found. Please search again and use the exact property ID.') from None
    lines = [f'🔒 {prop.title} — {prop.location}, {prop.city}',
             f"💰 Token: {_resolve_currency(_ctx_org.get())} {a['token_amount']:,}"]
    if a.get('payment_method'):
        lines.append(f"💳 {a['payment_method']}")
    return 'Please confirm you want to reserve this property with a token payment:', '\n'.join(lines)


def _create_enquiry(a: dict):
    from apps.enquiries import services as E
    from apps.properties.listing_search import find

    from ._tools_listings import _state
    user, org = _ctx_user.get(), _ctx_org.get()
    if user is None:
        raise Refused('The user must sign in or register on WhatsApp first.')
    a['message'] = (a.get('message') or '').strip()[:2000]
    if not a['message']:
        raise Refused('Ask the user what they want to say to the lister.')
    state = _state()
    hit = find(a['listing'], org, (state.last_results or []) if state else [])
    listing = E.visible_listing(hit.pk, org) if hit else None
    if listing is None:
        raise Refused('Listing not found or no longer available.')
    try:
        E.check_allowed(user, listing)
    except E.EnquiryError as exc:
        raise Refused(str(exc)) from None
    a['listing'] = str(listing.pk)        # "2" must not mean another listing later
    from apps.properties.public_listings import public_text
    lines = [f'🏠 {public_text(listing, listing.title)} — {listing.location}, {listing.city} ({listing.ref_no})',
             f"💬 {a['message']}", E.CONSENT_TEXT]
    return f'Shall I send this enquiry to {E.lister_name(listing)}?', '\n'.join(lines)


_BUILDERS = {'create_enquiry': _create_enquiry, 'list_property': _list_property, 'connect_to_agent': _connect_to_agent,
             'generate_property_audit': _generate_property_audit,
             'initiate_deal_lock': _initiate_deal_lock}


def describe(tool: str, args: dict) -> tuple[str, str]:
    """(question, summary). A tool without a builder cannot be proposed (fails closed)."""
    if tool not in _BUILDERS:
        raise Refused('This action cannot be confirmed yet.')
    return _BUILDERS[tool](args)
