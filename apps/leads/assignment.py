"""
Team lead assignment rules (T-3040, FR-CRM-007, ADR-063).

An organization's ``AssignmentRules`` say how a lead is matched to one of its
agents; ``AgentRouting`` pauses one agent or gives them their own cap. Without
rows the matching is the one from before rules (best match, city required,
specialization preferred, no cap), so nothing changes until an organization
sets rules.

Who can get a lead: the organization's own active agents and active freelance
collaborators allowed to receive leads (``AgentOrgMembership``), minus paused
agents, agents at their cap and, with ``only_available``, busy or offline ones.
Then each match in turn (city, area, language, specialization) keeps the agents
that match: ``require`` leaves the lead in the queue when nobody does,
``prefer`` keeps everyone instead. A lead that says nothing about a match (no
city, no enquired listing, no conversation language, no intent) skips it.

The order is the strategy's: best match (the old score), fewest open leads, or
round robin (the agent after the last one given a lead, in joining order).
Open leads are the agent's assigned leads in the organization whose routing is
not closed and that are not blocked.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count, Q

from .models import AgentRouting, AssignmentRules, Lead

logger = logging.getLogger(__name__)

S, M = AssignmentRules.Strategy, AssignmentRules.Match
MATCHES = ('city', 'area', 'language', 'specialization')
MANAGERS = ('owner', 'org_admin', 'team_manager')

# Intent → specializations that fit it (empty: any agent).
INTENT_SPECIALIZATIONS = {
    'buy':    {'residential_buy', 'plots', 'new_projects', 'luxury'},
    'sell':   {'residential_buy', 'plots', 'new_projects', 'luxury'},
    'rent':   {'residential_rent'},
    'invest': {'new_projects', 'plots', 'luxury', 'commercial'},
    'loan':   {'residential_buy'},
    'tax':    set(),
}
# Conversation language code (apps.ai.lang) → how agents write it in ``Agent.languages``.
LANGUAGE_NAMES = {
    'en':    {'en', 'english'},
    'ur':    {'ur', 'urdu'},
    'roman': {'ur', 'urdu', 'roman urdu'},
    'ar':    {'ar', 'arabic'},
}


class AssignmentError(Exception):
    def __init__(self, message: str, field: str = 'detail'):
        super().__init__(message)
        self.field = field


@dataclass
class Candidate:
    agent: object
    open_leads: int = 0
    cap: int | None = None
    matched: list = field(default_factory=list)

    def explain(self, strategy: str) -> dict:
        return {'strategy': strategy, 'matched': list(self.matched), 'open_leads': self.open_leads,
                'cap': self.cap}


def _norm(value) -> str:
    return ' '.join(str(value or '').lower().replace(',', ' ').split())


# ── Rules ─────────────────────────────────────────────────────────────────────

def rules_for(org, *, lock: bool = False) -> AssignmentRules:
    """The organization's rules; unsaved defaults when it has none (or no organization)."""
    if org is None:
        return AssignmentRules()
    qs = AssignmentRules.objects.select_related('last_agent')
    if lock:
        qs = qs.select_for_update(of=('self',))
    return qs.filter(organization=org).first() or AssignmentRules(organization=org)


RULE_FIELDS = {'strategy': S.values, 'match_city': M.values, 'match_area': M.values,
               'match_language': M.values, 'match_specialization': M.values}


def _cap(value, name):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise AssignmentError('A whole number from 1 to 1000, or null.', name)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise AssignmentError('A whole number from 1 to 1000, or null.', name) from None
    if not 1 <= value <= 1000:
        raise AssignmentError('A whole number from 1 to 1000, or null.', name)
    return value


def _bool(value, name):
    if not isinstance(value, bool):
        raise AssignmentError('true or false.', name)
    return value


@transaction.atomic
def update_rules(org, data: dict, *, user=None) -> AssignmentRules:
    unknown = set(data) - set(RULE_FIELDS) - {'only_available', 'max_open_leads', 'organization'}
    if unknown:
        raise AssignmentError(f'Unknown field(s): {", ".join(sorted(unknown))}.')
    rules, _ = AssignmentRules.objects.select_for_update().get_or_create(organization=org)
    before = rules_dict(rules)
    for name, allowed in RULE_FIELDS.items():
        if name in data:
            if data[name] not in allowed:
                raise AssignmentError(f'One of {", ".join(allowed)}.', name)
            setattr(rules, name, data[name])
    if 'only_available' in data:
        rules.only_available = _bool(data['only_available'], 'only_available')
    if 'max_open_leads' in data:
        rules.max_open_leads = _cap(data['max_open_leads'], 'max_open_leads')
    if rules.strategy != S.ROUND_ROBIN:
        rules.last_agent = None
    rules.updated_by = user if getattr(user, 'is_authenticated', False) else None
    rules.save()
    after = rules_dict(rules)
    changed = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
    if changed:
        _audit('assignment.rules_changed', org, rules, user, {'changed': changed})
    return rules


def rules_dict(rules: AssignmentRules) -> dict:
    return {'strategy': rules.strategy, 'match_city': rules.match_city, 'match_area': rules.match_area,
            'match_language': rules.match_language, 'match_specialization': rules.match_specialization,
            'only_available': rules.only_available, 'max_open_leads': rules.max_open_leads}


# ── The team ──────────────────────────────────────────────────────────────────

def org_agents(org, *, active_only: bool = True):
    """Agents who can work the organization's leads: its own, and freelancers allowed to receive them."""
    from apps.agents.models import Agent, AgentOrgMembership
    freelance = AgentOrgMembership.objects.filter(
        organization=org, status=AgentOrgMembership.Status.ACTIVE, can_receive_leads=True,
    ).exclude(membership__is_active=False).values('agent_id')
    qs = Agent.objects.filter(Q(organization=org) | Q(id__in=freelance))
    return qs.filter(is_active=True) if active_only else qs


def agent_org_ids(agent) -> set:
    """Organizations whose leads ``agent`` may work (the ``org_agents`` rule, from the agent's side)."""
    from apps.agents.models import AgentOrgMembership
    ids = set(AgentOrgMembership.objects.filter(
        agent=agent, status=AgentOrgMembership.Status.ACTIVE, can_receive_leads=True,
    ).exclude(membership__is_active=False).values_list('organization_id', flat=True))
    if agent.organization_id:
        ids.add(agent.organization_id)
    return ids if agent.is_active else set()


def open_leads(agent_ids, org=None) -> dict:
    qs = Lead.objects.filter(assigned_agent_id__in=list(agent_ids)).exclude(
        routing_state=Lead.RoutingState.CLOSED).exclude(status=Lead.Status.BLOCKED_MALICIOUS)
    if org is not None:
        qs = qs.filter(organization=org)
    return dict(qs.order_by().values_list('assigned_agent_id').annotate(n=Count('pk')))


def routing_for(org, agent_ids) -> dict:
    if org is None:
        return {}
    return {r.agent_id: r for r in AgentRouting.objects.filter(organization=org, agent_id__in=list(agent_ids))}


def effective_cap(rules: AssignmentRules, routing: AgentRouting | None) -> int | None:
    if routing is not None and routing.max_open_leads:
        return routing.max_open_leads
    return rules.max_open_leads or None


@transaction.atomic
def update_routing(org, agent, data: dict, *, user=None) -> AgentRouting:
    unknown = set(data) - {'accepts_leads', 'max_open_leads', 'organization'}
    if unknown:
        raise AssignmentError(f'Unknown field(s): {", ".join(sorted(unknown))}.')
    routing, _ = AgentRouting.objects.select_for_update().get_or_create(organization=org, agent=agent)
    before = (routing.accepts_leads, routing.max_open_leads)
    if 'accepts_leads' in data:
        routing.accepts_leads = _bool(data['accepts_leads'], 'accepts_leads')
    if 'max_open_leads' in data:
        routing.max_open_leads = _cap(data['max_open_leads'], 'max_open_leads')
    routing.updated_by = user if getattr(user, 'is_authenticated', False) else None
    routing.save()
    if before != (routing.accepts_leads, routing.max_open_leads):
        _audit('assignment.agent_routing_changed', org, routing, user,
               {'agent_id': str(agent.pk), 'accepts_leads': routing.accepts_leads,
                'max_open_leads': routing.max_open_leads})
    return routing


def team(org) -> list[dict]:
    """The organization's agents with what routing sees: load, cap, pause, matching fields."""
    rules = rules_for(org)
    agents = list(org_agents(org, active_only=False).order_by('joined_at', 'pk'))
    ids = [a.pk for a in agents]
    loads, routing = open_leads(ids, org), routing_for(org, ids)
    out = []
    for a in agents:
        r = routing.get(a.pk)
        cap, load = effective_cap(rules, r), loads.get(a.pk, 0)
        accepts = r.accepts_leads if r is not None else True
        available = a.availability_status == a.AvailabilityStatus.AVAILABLE
        out.append({
            'agent_id': str(a.pk), 'name': a.name, 'employment_type': a.employment_type,
            'is_active': a.is_active, 'availability_status': a.availability_status,
            'cities': a.cities or [], 'areas': a.areas or [], 'languages': a.languages or [],
            'specializations': a.specializations or [],
            'accepts_leads': accepts, 'max_open_leads': r.max_open_leads if r is not None else None,
            'cap': cap, 'open_leads': load,
            'receives_leads': (a.is_active and accepts and (cap is None or load < cap)
                               and (available or not rules.only_available)),
        })
    return out


# ── What a lead asks for ──────────────────────────────────────────────────────

def lead_language(lead) -> str:
    """The language of the lead's latest assistant conversation ('' when unknown)."""
    from apps.conversations.models import Conversation
    qs = Conversation.objects.filter(user_id=lead.user_id).exclude(language='').order_by('-last_message_at')
    row = (qs.filter(organization_id=lead.organization_id).values_list('language', flat=True).first()
           if lead.organization_id else None)
    return row or qs.values_list('language', flat=True).first() or ''


def lead_areas(lead) -> list[str]:
    """Locations of the listings the lead enquired about, newest first."""
    rows = (lead.enquiries.filter(listing__isnull=False).order_by('-created_at')
            .values_list('listing__location', flat=True)[:5])
    return [n for n in (_norm(r) for r in rows) if n]


def _city_ok(agent, city):
    names = [_norm(c) for c in (agent.cities or [])] + [_norm(agent.primary_city)]
    return any(n and (n == city or city in n) for n in names)


def _area_ok(agent, areas):
    mine = [n for n in (_norm(a) for a in (agent.areas or [])) if len(n) >= 3]
    return any(a in loc or loc in a for a in mine for loc in areas)


def _language_ok(agent, names):
    return bool(names & {_norm(x) for x in (agent.languages or [])})


def _spec_ok(agent, specs):
    return any(s in (agent.specializations or []) for s in specs)


# ── Ranking and assigning ─────────────────────────────────────────────────────

def _best_score(c: Candidate, city: str) -> float:
    a = c.agent
    s = float(a.rating or 0) * 4 + (6 if a.is_featured else 0) + (4 if a.is_verified else 0)
    s += 2 if city and _norm(a.primary_city) == city else 0
    return s - c.open_leads * 0.5


def rank(lead, rules: AssignmentRules | None = None) -> list[Candidate]:
    """Agents who may get ``lead``, best first under the organization's rules."""
    from apps.agents.models import Agent
    org = lead.organization if lead.organization_id else None
    rules = rules if rules is not None else rules_for(org)
    agents = list((org_agents(org) if org is not None else Agent.objects.filter(is_active=True))
                  .order_by('joined_at', 'pk'))
    ids = [a.pk for a in agents]
    loads, routing = open_leads(ids, org), routing_for(org, ids)

    cands = []
    for a in agents:
        r = routing.get(a.pk)
        if r is not None and not r.accepts_leads:
            continue
        if rules.only_available and a.availability_status != a.AvailabilityStatus.AVAILABLE:
            continue
        c = Candidate(a, loads.get(a.pk, 0), effective_cap(rules, r))
        if c.cap is not None and c.open_leads >= c.cap:
            continue
        cands.append(c)

    city = _norm(lead.city_interest)
    checks = {
        'city': (city, lambda a: _city_ok(a, city)),
        'area': None, 'language': None,
        'specialization': (INTENT_SPECIALIZATIONS.get(lead.intent or '') or None,
                           lambda a: _spec_ok(a, INTENT_SPECIALIZATIONS[lead.intent])),
    }
    for name in MATCHES:
        mode = getattr(rules, f'match_{name}')
        if mode == M.OFF or not cands:
            continue
        if name == 'area':
            areas = lead_areas(lead)
            checks['area'] = (areas, lambda a, areas=areas: _area_ok(a, areas))
        elif name == 'language':
            names = LANGUAGE_NAMES.get(lead_language(lead), set())
            checks['language'] = (names, lambda a, names=names: _language_ok(a, names))
        wanted, ok = checks[name]
        if not wanted:
            continue
        hits = [c for c in cands if ok(c.agent)]
        for c in hits:
            c.matched.append(name)
        if hits or mode == M.REQUIRE:
            cands = hits

    if rules.strategy == S.LEAST_LOADED:
        cands.sort(key=lambda c: (c.open_leads, -_best_score(c, city)))
    elif rules.strategy == S.ROUND_ROBIN:
        last = rules.last_agent if rules.last_agent_id else None
        if last is not None:
            key = (last.joined_at, last.pk)
            cands = ([c for c in cands if (c.agent.joined_at, c.agent.pk) > key]
                     + [c for c in cands if (c.agent.joined_at, c.agent.pk) <= key])
    else:
        cands.sort(key=lambda c: _best_score(c, city), reverse=True)
    for c in cands:
        c.agent.assignment = c.explain(rules.strategy)
    return cands


def auto_assign(lead, *, actor=None, reassign: bool = False):
    """Give ``lead`` to the first agent the rules pick; ``None`` when nobody fits (it stays queued)."""
    from . import services
    with transaction.atomic():
        lead = (Lead.objects.select_for_update(of=('self',)).select_related('organization', 'assigned_agent')
                .get(pk=lead.pk))
        if lead.assigned_agent_id and not reassign:
            return None
        org = lead.organization if lead.organization_id else None
        rules = rules_for(org, lock=True)          # serialises round robin within the organization
        picks = [a for a in services.suggest_agents_for_lead(lead, limit=2, rules=rules)
                 if a.pk != lead.assigned_agent_id][:1]
        if not picks:
            return None
        agent = picks[0]
        services.assign_agent_to_lead(lead, agent, actor=actor, auto=True,
                                      reason=getattr(agent, 'assignment', None))
        if not rules._state.adding and rules.strategy == S.ROUND_ROBIN:
            AssignmentRules.objects.filter(pk=rules.pk).update(last_agent=agent)
    return agent


def _audit(action, org, target, user, meta):
    from apps.core.audit import safe_record
    safe_record(action, category='organization', actor=user, organization=org, target=target, meta=meta)
