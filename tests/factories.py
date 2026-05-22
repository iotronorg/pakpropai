"""
Shared test factories for RealTron AI.

All make_* functions return the created object. Functions that create two models
together (e.g. make_developer, make_agent) return a tuple.

Phone-number uniqueness: pass explicit phone= when you need multiple users in one
test. The default phones are unique per process via _seq(), which is safe for
parallel test workers because each worker uses its own in-process counter.
"""
import itertools

from django.contrib.auth import get_user_model

from apps.agents.models import Agent
from apps.escrow.models import EscrowDeal
from apps.leads.models import Lead
from apps.organizations.models import Organization, OrganizationMembership
from apps.properties.models import Property
from apps.verification.models import DocumentScan, Verification

User = get_user_model()

_counter = itertools.count(1)


def _seq():
    return next(_counter)


# ── Users ─────────────────────────────────────────────────────────────────────

def make_user(phone=None, role='client', **kwargs):
    if phone is None:
        phone = f'+9230099{_seq():05d}'
    return User.objects.create_user(phone=phone, password='pw', role=role, **kwargs)


def make_client(phone=None, **kwargs):
    return make_user(phone=phone, role='client', **kwargs)


# ── Organizations ─────────────────────────────────────────────────────────────

def make_org(name='Test Org', **kwargs):
    return Organization.objects.create(name=name, **kwargs)


def make_membership(user, org, role=None, **kwargs):
    if role is None:
        role = OrganizationMembership.Role.AGENT
    membership, _ = OrganizationMembership.objects.get_or_create(
        user=user,
        organization=org,
        defaults={'role': role, 'is_active': True, **kwargs},
    )
    return membership


def make_developer(phone=None, org=None, org_name='Test Org'):
    """
    Create a developer user, an org (or link to the given one), and an OWNER membership.

    Returns (User, Organization).
    When org is provided, that org's admin_user is updated to the new user.
    """
    user = make_user(phone=phone, role='developer')
    if org is None:
        org = Organization.objects.create(name=org_name, admin_user=user)
    else:
        org.admin_user = user
        org.save(update_fields=['admin_user'])
    make_membership(user, org, role=OrganizationMembership.Role.OWNER)
    return user, org


def make_agent(phone=None, org=None, name=None, employment_type=None, **kwargs):
    """
    Create an agent user + Agent record (+ OrganizationMembership when org given).

    employment_type defaults to 'internal' when org is given, 'freelance' otherwise
    (the DB constraint requires internal agents to have an org).

    Returns (User, Agent).
    """
    user = make_user(phone=phone, role='agent')
    et = employment_type or ('internal' if org is not None else 'freelance')
    agent_kwargs = dict(
        user=user,
        organization=org,
        name=name or f'Agent {user.phone}',
        phone=user.phone,
        employment_type=et,
    )
    agent_kwargs.update(kwargs)
    agent = Agent.objects.create(**agent_kwargs)
    if org is not None:
        make_membership(user, org, role=OrganizationMembership.Role.AGENT)
    return user, agent


# ── Properties ────────────────────────────────────────────────────────────────

def make_property(
    org=None,
    owner=None,
    title='Test Property',
    city='Lahore',
    location='DHA Phase 5',
    property_type='residential',
    price=5_000_000,
    area_marla=5,
    **kwargs,
):
    """
    Create a Property, inferring listing_owner_type from the supplied owner/org.

      org=<Organization>  → listing_owner_type='organization'
      owner=<User>        → listing_owner_type='client'
      neither             → listing_owner_type='platform'

    Pass listing_owner_type explicitly to override the inference.
    """
    if 'listing_owner_type' not in kwargs:
        if org is not None:
            kwargs['listing_owner_type'] = 'organization'
        elif owner is not None:
            kwargs['listing_owner_type'] = 'client'
        else:
            kwargs['listing_owner_type'] = 'platform'

    if org is not None:
        kwargs.setdefault('organization', org)
    if owner is not None:
        kwargs['owner'] = owner

    return Property.objects.create(
        title=title,
        city=city,
        location=location,
        property_type=property_type,
        price=price,
        area_marla=area_marla,
        **kwargs,
    )


# ── Leads ─────────────────────────────────────────────────────────────────────

def make_lead(user, org=None, **kwargs):
    return Lead.objects.create(user=user, organization=org, **kwargs)


# ── Deals ─────────────────────────────────────────────────────────────────────

def make_deal(buyer, prop, agent=None, token_amount=25_000, **kwargs):
    return EscrowDeal.objects.create(
        buyer=buyer,
        property=prop,
        agent=agent,
        token_amount=token_amount,
        status=EscrowDeal.Status.INITIATED,
        **kwargs,
    )


# ── Document scans ────────────────────────────────────────────────────────────

def make_scan(prop, requester):
    """Create a Verification + DocumentScan pair. Returns (DocumentScan, Verification)."""
    verif = Verification.objects.create(property=prop, requested_by=requester)
    scan  = DocumentScan.objects.create(verification=verif)
    return scan, verif
