"""
Agent performance from what really happened (T-3010, FR-CRM-008, ADR-056).

Per agent of one organization, over a period of local days:

* enquiries: those whose lead is assigned to the agent, created in the period;
  first reply = the first outbound message in the thread, by anyone on the
  organization's side (the agent may be covered by a colleague);
* viewings: the agent's visits scheduled in the period, by outcome, and the
  visitors' feedback;
* leads: the organization's leads assigned to the agent, created in the period.

Times are wall-clock (no business hours). Attribution is the lead's current
agent. No contact details are returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median

from django.db.models import Count, OuterRef, Q, Subquery

MAX_DAYS = 366
DEFAULT_DAYS = 30
FAST = timedelta(hours=1)


class PeriodInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    first: date
    last: date               # inclusive
    tz: object

    @property
    def start(self) -> datetime:
        return datetime.combine(self.first, time.min, tzinfo=self.tz)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.last + timedelta(days=1), time.min, tzinfo=self.tz)


def period(org, first: str | None = None, last: str | None = None, today: date | None = None) -> Period:
    """Local dates (YYYY-MM-DD, inclusive). Default: the last 30 days up to today."""
    from django.utils import timezone

    from apps.markets.services import org_timezone
    tz = org_timezone(org)
    today = today or timezone.now().astimezone(tz).date()
    try:
        end = date.fromisoformat(last) if last else today
        begin = date.fromisoformat(first) if first else end - timedelta(days=DEFAULT_DAYS - 1)
    except ValueError:
        raise PeriodInvalid('Dates must be YYYY-MM-DD.') from None
    if begin > end:
        raise PeriodInvalid('"from" must not be after "to".')
    if (end - begin).days + 1 > MAX_DAYS:
        raise PeriodInvalid(f'At most {MAX_DAYS} days.')
    return Period(begin, end, tz)


def agents_of(org):
    """Agents who are active members of the organization (membership is canonical, ADR-013)."""
    from apps.agents.models import Agent
    from apps.organizations.models import OrganizationMembership
    members = OrganizationMembership.objects.filter(organization=org, is_active=True).exclude(
        role=OrganizationMembership.Role.VIEWER).values('user_id')
    return Agent.objects.filter(user_id__in=members).select_related('user').order_by('name', 'pk')


def _pct(part, whole):
    return round(part * 100 / whole, 1) if whole else None


def _enquiries(org, p: Period, agent_ids) -> dict:
    from apps.enquiries.models import Enquiry
    from apps.leads.models import ConversationMessage
    first_reply = ConversationMessage.objects.filter(
        enquiry=OuterRef('pk'), direction=ConversationMessage.Direction.OUTBOUND,
    ).order_by('created_at').values('created_at')[:1]
    rows = (Enquiry.objects.filter(organization=org, created_at__gte=p.start, created_at__lt=p.end,
                                   lead__assigned_agent_id__in=agent_ids)
            .annotate(replied_at=Subquery(first_reply))
            .values_list('lead__assigned_agent_id', 'created_at', 'replied_at'))
    out = {}
    for agent_id, created, replied in rows:
        out.setdefault(agent_id, []).append(replied - created if replied else None)
    return out


def _enquiry_stats(gaps: list) -> dict:
    answered = [g for g in gaps if g is not None]
    return {
        'received': len(gaps),
        'answered': len(answered),
        'unanswered': len(gaps) - len(answered),
        'median_first_reply_minutes': round(median(answered).total_seconds() / 60, 1) if answered else None,
        'answered_within_1h_pct': _pct(sum(g <= FAST for g in answered), len(gaps)),
    }


def _viewings(org, p: Period, agent_ids) -> dict:
    from apps.leads.models import Appointment
    from apps.viewings.models import ViewingFeedback
    S = Appointment.Status
    qs = Appointment.objects.filter(agent_id__in=agent_ids, lead__organization=org,
                                    scheduled_at__gte=p.start, scheduled_at__lt=p.end)
    counts = qs.values('agent_id').annotate(
        scheduled=Count('pk'),
        completed=Count('pk', filter=Q(status=S.COMPLETED)),
        no_show=Count('pk', filter=Q(status=S.NO_SHOW)),
        cancelled=Count('pk', filter=Q(status=S.CANCELLED)))
    out = {r.pop('agent_id'): r for r in counts}
    ratings = {}
    for agent_id, rating in ViewingFeedback.objects.filter(
            appointment__in=qs, side=ViewingFeedback.Side.USER).values_list('appointment__agent_id', 'rating'):
        ratings.setdefault(agent_id, []).append(rating)
    for agent_id, values in ratings.items():
        out.setdefault(agent_id, {}).update(
            feedback_count=len(values), feedback_avg=round(sum(values) / len(values), 2))
    return out


def _viewing_stats(row: dict | None) -> dict:
    row = row or {}
    held = row.get('completed', 0) + row.get('no_show', 0)
    return {
        'scheduled': row.get('scheduled', 0), 'completed': row.get('completed', 0),
        'no_show': row.get('no_show', 0), 'cancelled': row.get('cancelled', 0),
        'no_show_pct': _pct(row.get('no_show', 0), held),
        'feedback_count': row.get('feedback_count', 0), 'feedback_avg': row.get('feedback_avg'),
    }


def _leads(org, p: Period, agent_ids) -> dict:
    from apps.leads.models import Lead
    rows = Lead.objects.filter(organization=org, assigned_agent_id__in=agent_ids,
                               created_at__gte=p.start, created_at__lt=p.end).values('assigned_agent_id').annotate(
        assigned=Count('pk'),
        qualified=Count('pk', filter=Q(status=Lead.Status.QUALIFIED)),
        closed=Count('pk', filter=Q(routing_state=Lead.RoutingState.CLOSED)))
    return {r.pop('assigned_agent_id'): r for r in rows}


def _lead_stats(row: dict | None) -> dict:
    row = row or {}
    assigned = row.get('assigned', 0)
    return {'assigned': assigned, 'qualified': row.get('qualified', 0), 'closed': row.get('closed', 0),
            'conversion_pct': _pct(row.get('closed', 0), assigned)}


def report(org, p: Period, agents=None) -> dict:
    """``agents`` limits the rows (an agent sees only their own); totals cover the rows shown."""
    agents = list(agents if agents is not None else agents_of(org))
    ids = [a.pk for a in agents]
    enquiries, viewings, leads = _enquiries(org, p, ids), _viewings(org, p, ids), _leads(org, p, ids)
    rows = [{'agent_id': a.pk, 'name': a.display_name,
             'enquiries': _enquiry_stats(enquiries.get(a.pk, [])),
             'viewings': _viewing_stats(viewings.get(a.pk)),
             'leads': _lead_stats(leads.get(a.pk))} for a in agents]
    all_gaps = [g for gaps in enquiries.values() for g in gaps]
    sums = {k: sum(v.get(k, 0) for v in viewings.values()) for k in ('scheduled', 'completed', 'no_show', 'cancelled')}
    ratings = [(v['feedback_avg'], v['feedback_count']) for v in viewings.values() if v.get('feedback_count')]
    count = sum(n for _, n in ratings)
    sums.update(feedback_count=count,
                feedback_avg=round(sum(a * n for a, n in ratings) / count, 2) if count else None)
    lead_sums = {k: sum(v.get(k, 0) for v in leads.values()) for k in ('assigned', 'qualified', 'closed')}
    return {
        'organization': str(org.pk), 'from': p.first.isoformat(), 'to': p.last.isoformat(), 'timezone': p.tz.key,
        'agents': rows,
        'totals': {'enquiries': _enquiry_stats(all_gaps), 'viewings': _viewing_stats(sums),
                   'leads': _lead_stats(lead_sums)},
    }
