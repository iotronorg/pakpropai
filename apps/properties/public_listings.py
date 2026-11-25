"""
What an anonymous visitor may see of a listing (T-2031, GAP-002, ADR-035).

Fields are listed one by one: a new model field is never public by default.
"""
from __future__ import annotations

import re
import uuid

from django.db.models import Prefetch, Q
from django.utils.text import slugify

from .models import Property, PropertyImage

_EMAIL = re.compile(r'\S+@\S+\.\w+')
_PHONE = re.compile(r'(?<![\w.,])\+?\(?\d(?:[\s().\-]{0,2}\d){9,14}(?![\w,])')
_REF_AT_END = re.compile(r'(?:^|-)([a-z]{2,5}-[a-z0-9]{1,5}-\d{4}-\d{6,})$')


def redact_contacts(text: str) -> str:
    return _PHONE.sub('[contact hidden]', _EMAIL.sub('[contact hidden]', text or ''))


def public_text(p: Property, text: str) -> str:
    """Private people's phone numbers and emails are removed; an organization's own text is kept."""
    if p.listing_owner_type == Property.ListingOwnerType.ORGANIZATION:
        return text or ''
    return redact_contacts(text)


def safe_images() -> Prefetch:
    """Only images re-encoded without metadata (older uploads may carry GPS; T-1110)."""
    return Prefetch('images', PropertyImage.objects.exclude(sha256='').order_by('order', 'created_at'),
                    to_attr='public_images')


def slug(p: Property) -> str:
    """Readable and stable: the reference number at the end identifies the listing."""
    head = slugify(public_text(p, p.title))[:80].strip('-')
    tail = (p.ref_no or str(p.pk)).lower()
    return f'{head}-{tail}' if head else tail


def lookup(slug_or_id: str) -> Property | None:
    """A public listing by id, reference number or slug."""
    value = (slug_or_id or '').strip().lower()[:200]
    qs = (Property.objects.visible_to(None)
          .select_related('organization', 'location_ref', 'market').prefetch_related('amenities', safe_images()))
    try:
        return qs.filter(pk=uuid.UUID(value)).first()
    except ValueError:
        pass
    m = _REF_AT_END.search(value)
    if m:
        # A title may end in text that looks like part of the reference.
        ref = m.group(1)
        return qs.filter(ref_no__iexact=ref).first()
    try:
        return qs.filter(pk=uuid.UUID(value[-36:])).first()
    except ValueError:
        return None


def _url(f, request):
    return request.build_absolute_uri(f.url) if f else None


def image(i: PropertyImage, p: Property, request) -> dict:
    return {'url': _url(i.image, request), 'thumbnail_url': _url(i.thumbnail or i.image, request),
            'alt_text': public_text(p, i.alt_text or i.caption), 'width': i.width, 'height': i.height}


def card(p: Property, request) -> dict:
    from .listing_search import summary
    first = p.public_images[0] if p.public_images else None
    return {**summary(p), 'slug': slug(p), 'image': image(first, p, request) if first else None}


def _attributes(p: Property) -> dict:
    """Only keys the market's schema defines: free-form keys may hold anything."""
    fields = (Property.attribute_schema_for(p.market_id).get('fields') or {}) if p.market_id else {}
    return {k: v for k, v in (p.attributes or {}).items() if k in fields}


def _lister(p: Property) -> dict:
    org = p.organization if p.listing_owner_type == Property.ListingOwnerType.ORGANIZATION else None
    return {'type': p.listing_owner_type, 'name': org.name if org else None, 'slug': org.slug if org else None}


def detail(p: Property, request) -> dict:
    hidden = p.price_on_request
    return {
        **card(p, request),
        'description': public_text(p, p.description), 'status': p.status, 'country': p.country,
        'size_m2': float(p.size_m2) if p.size_m2 is not None else None,
        'plot_size_m2': float(p.plot_size_m2) if p.plot_size_m2 is not None else None,
        'floor': p.floor, 'total_floors': p.total_floors, 'year_built': p.year_built,
        'parking_spaces': p.parking_spaces, 'pets_allowed': p.pets_allowed,
        'furnishing': p.furnished_status, 'completion': p.construction_status,
        'installments': p.installment_available, 'price_on_request': hidden,
        'price_negotiable': p.price_negotiable, 'deposit': None if hidden else p.deposit,
        'service_charge': None if hidden else p.service_charge,
        'amenities': [{'key': a.key, 'labels': a.labels or {}} for a in p.amenities.all() if a.is_active],
        'attributes': _attributes(p), 'images': [image(i, p, request) for i in p.public_images],
        # The public point only (snapped for private or approximate listings).
        'latitude': float(p.geo_lat) if p.geo_lat is not None else None,
        'longitude': float(p.geo_lng) if p.geo_lng is not None else None,
        'location_precision': p.location_precision,
        'location_path': p.location_ref.path if p.location_ref and p.location_ref.is_active else None,
        'lister': _lister(p), 'published_at': p.published_at or p.created_at, 'updated_at': p.updated_at,
    }
