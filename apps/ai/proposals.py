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
    return 'Please confirm you want to save this listing as a draft:', '\n'.join(lines)


def _submit_listing(a: dict):
    from apps.properties.listing_search import price_text, size_text
    from apps.properties.lifecycle import publish_problems

    from ._tools_moderation import own_draft
    user = _ctx_user.get()
    if user is None:
        raise Refused('The user must sign in or register on WhatsApp first.')
    p = own_draft(user, a.get('listing') or '')
    if p is None:
        raise Refused('The user has no such draft listing.')
    if problems := publish_problems(p):
        raise Refused('The draft is incomplete: ' + ', '.join(problems))
    a['listing'] = str(p.pk)
    lines = [f'📋 {p.title}', f'💰 {price_text(p)}', size_text(p), f'📷 {p.images.count()} photo(s)',
             'Our team checks it before it becomes public.']
    return 'Shall I send this listing for review?', '\n'.join(x for x in lines if x)


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


def _book_viewing(a: dict):
    from apps.enquiries import services as E
    from apps.properties.public_listings import public_text
    from apps.viewings import services as V

    from ._tools_viewings import parse_start, resolve
    user = _ctx_user.get()
    if user is None:
        raise Refused('The user must sign in or register on WhatsApp first.')
    listing = resolve(a['listing'])
    if listing is None:
        raise Refused('Listing not found or no longer available.')
    tz = V.listing_timezone(listing)
    try:
        E.check_allowed(user, listing)
        start = parse_start(a['start'], tz)
        slot = V.slot_at(listing, start, user)
    except E.EnquiryError as exc:
        raise Refused(str(exc)) from None
    except (ValueError, OverflowError):
        slot = None
    if slot is None:
        raise Refused('That time is not free. Call find_viewing_slots and offer its times.')
    a['listing'], a['start'] = str(listing.pk), slot.start.isoformat()
    a['note'] = (a.get('note') or '').strip()[:500]
    lines = [f'🏠 {public_text(listing, listing.title)} — {listing.location}, {listing.city} ({listing.ref_no})',
             f'📅 {V.local_text(slot.start, tz)}, {slot.minutes} minutes']
    if a['note']:
        lines.append(f"💬 {a['note']}")
    return f'Shall I book this viewing with {E.lister_name(listing)}?', '\n'.join(lines + [E.CONSENT_TEXT])


def _own_viewing(a: dict):
    """The viewing, pinned by id: "1" must not mean another viewing at the YES (KI-076)."""
    from ._tools_viewings import find_viewing
    user = _ctx_user.get()
    if user is None:
        raise Refused('The user must sign in or register on WhatsApp first.')
    appt = find_viewing(a.get('viewing'), user, _ctx_org.get())
    if appt is None:
        raise Refused('No such upcoming viewing. Call my_viewings and use one of its positions.')
    a['viewing'] = str(appt.pk)
    return user, appt


def _cancel_viewing(a: dict):
    from ._tools_viewings import viewing_text
    _, appt = _own_viewing(a)
    return 'Shall I cancel this viewing?', f'🏠 {viewing_text(appt)}\nThe agent will be told.'


def _reschedule_viewing(a: dict):
    from apps.enquiries.services import visible_listing
    from apps.viewings import services as V

    from ._tools_viewings import parse_start, viewing_text
    user, appt = _own_viewing(a)
    tz = V.zone(appt)
    p = visible_listing(appt.property_id, _ctx_org.get()) if appt.property_id else None
    try:
        start = parse_start(a.get('start'), tz)
        slot = V.slot_at(p, start, user, exclude=appt.pk) if p else None
    except (ValueError, OverflowError, TypeError):
        slot = None
    if slot is None or start == appt.scheduled_at:
        raise Refused('That time is not free. Call find_viewing_slots for this listing and offer its times.')
    a['start'] = slot.start.isoformat()
    return 'Shall I move this viewing?', '\n'.join([
        f'🏠 {viewing_text(appt)}', f'➡️ New time: {V.local_text(slot.start, tz)}, {slot.minutes} minutes',
        'The agent will be told.'])


def _current_search():
    """The criteria of the user's last search, validated again."""
    from apps.preferences import alerts as A
    from apps.properties import criteria as C

    from ._tools_listings import _state
    if _ctx_user.get() is None:
        raise Refused('The user must sign in or register on WhatsApp first.')
    state = _state()
    try:
        return A.clean(dict((state.active_criteria or {}) if state else {}))
    except (C.CriteriaInvalid, C.LocationAmbiguous):
        raise Refused('There is no complete search yet. Search first (a place is needed).') from None


def _create_saved_search(a: dict):
    import json

    from apps.preferences import alerts as A
    from apps.preferences.models import SavedSearch
    crit = _current_search()
    a['frequency'] = (a.get('frequency') or 'daily').strip().lower()
    a['channel'] = (a.get('channel') or 'whatsapp').strip().lower()
    if a['frequency'] not in SavedSearch.Frequency.values or a['channel'] not in SavedSearch.Channel.values:
        raise Refused('Frequency is instant, daily or weekly; channel is whatsapp, email or in_app.')
    if a['channel'] == 'email' and not (_ctx_user.get().email or '').strip():
        raise Refused('The user has no email address. Offer whatsapp or in_app.')
    a['criteria'] = json.dumps(A.dump(crit), sort_keys=True)
    lines = [f'🔎 {A.describe(crit)}', f"🔔 {a['frequency'].title()}, by {a['channel'].replace('_', '-')}",
             *_criteria_lines(crit), 'You can stop these alerts at any time.']
    return 'Shall I alert you about new matching listings?', '\n'.join(lines)


def _criteria_lines(crit) -> list[str]:
    """Every condition, so the user sees exactly what is stored."""
    from apps.locations.models import Location
    d = crit.model_dump(mode='json', exclude_defaults=True, exclude={
        'market', 'missing_required', 'confidence', 'unsupported_requests', 'location'})
    out = []
    names = [*Location.objects.filter(pk__in=crit.location.location_ids).values_list('name', flat=True)]
    names += [crit.location.free_text] if crit.location.free_text else []
    names += ['map area'] if crit.location.geo else []
    if names:
        out.append(f"• location: {', '.join(names)}")
    for key, value in d.items():
        if isinstance(value, dict):
            value = ', '.join(f'{k} {v}' for k, v in value.items() if v not in (None, '', []))
        elif isinstance(value, list):
            value = ', '.join(map(str, value))
        out.append(f"• {key.replace('_', ' ')}: {value}")
    return out


def _remember_preferences(a: dict):
    import json

    from apps.preferences import services as P
    from apps.properties import criteria as C
    crit = _current_search()
    d = crit.model_dump(mode='json', exclude_defaults=True)
    if crit.amenities_required:                 # remembered as a wish, not a hard rule
        d['amenities_preferred'] = list(dict.fromkeys([*crit.amenities_required, *crit.amenities_preferred]))[:20]
    values = P.clean(crit.market, {k: d[k] for k in P.KEYS if k in d})
    if not values:
        raise Refused('There is nothing to remember yet.')
    a['criteria'] = json.dumps({'market': crit.market, **values}, sort_keys=True)
    shown = C.parse({'market': crit.market, **values})
    return 'Shall I remember these preferences?', '\n'.join([*_criteria_lines(shown), P.MEMORY_TEXT])


_BUILDERS = {'submit_listing': _submit_listing, 'create_saved_search': _create_saved_search, 'remember_preferences': _remember_preferences,
             'book_viewing': _book_viewing, 'cancel_viewing': _cancel_viewing, 'reschedule_viewing': _reschedule_viewing,
             'create_enquiry': _create_enquiry, 'list_property': _list_property, 'connect_to_agent': _connect_to_agent,
             'generate_property_audit': _generate_property_audit,
             'initiate_deal_lock': _initiate_deal_lock}


def describe(tool: str, args: dict) -> tuple[str, str]:
    """(question, summary). A tool without a builder cannot be proposed (fails closed)."""
    if tool not in _BUILDERS:
        raise Refused('This action cannot be confirmed yet.')
    return _BUILDERS[tool](args)
