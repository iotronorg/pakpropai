"""
Role × endpoint permission matrix (T-6030, MVP_SCOPE §6 criterion 12).

``endpoints()`` lists every DRF endpoint under ``/api/`` and its methods, including views hidden
from the OpenAPI schema (webhooks), with a concrete path that resolves to the same view.
``audience(view_cls)`` derives who may call it from the declared permission classes:

    staff      platform admins only (``role == 'admin'``, optionally a platform role)
    org_admin  owner/org_admin of the active organization, or a platform admin
    dashboard  agent/developer/admin accounts (not consumers)
    moderator  moderation staff
    api_key    organization API keys (no user session)
    signed_in  any signed-in account; finer checks happen in the view
    public     no authentication (must be on the allow-list in tests/test_endpoint_matrix.py)

``tests/test_endpoint_matrix.py`` checks the real HTTP behaviour against this, and
``manage.py permission_matrix`` prints it for the pentest scope.
"""
from __future__ import annotations

import itertools
import re
import uuid
from dataclasses import dataclass

from django.urls import Resolver404, resolve
from rest_framework import permissions as drf
from rest_framework.permissions import OperandHolder
from rest_framework.schemas.generators import EndpointEnumerator, is_api_view

PLACEHOLDER_UUID = str(uuid.UUID(int=0x5EC0))
_CANDIDATES = (PLACEHOLDER_UUID, '1', 'x1', 'ZZ')   # ZZ: a two-letter code shape, no real market
_PARAM = re.compile(r'\{([^}]+)\}')

AUDIENCE_ORDER = ('staff', 'moderator', 'org_admin', 'dashboard', 'api_key', 'signed_in', 'public')


@dataclass(frozen=True)
class Endpoint:
    path: str          # concrete path, e.g. /api/v1/leads/00000000-…/
    template: str      # /api/v1/leads/{id}/
    method: str
    view: type
    audience: str
    permissions: str   # human-readable list


class _AllViews(EndpointEnumerator):
    def should_include_endpoint(self, path, callback):
        # Format-suffix copies (``.json``) and the routers' ``{format}`` roots repeat other routes.
        return is_api_view(callback) and '{format}' not in path


def _classes():
    """Permission classes grouped by audience (identity, so look-alike names cannot slip in)."""
    from apps.core import permissions as core
    groups = {
        'staff': [core.IsAdminUser, core.IsSuperAdmin, core.IsOpsAdmin, core.IsAIAdmin, core.IsComplianceAdmin,
                  core.IsBillingAdmin, core.IsSupportAdmin, drf.IsAdminUser],
        'org_admin': [core.IsAdminOrOrgAdmin, core.IsOrgAdmin, core.IsOrgAdminMembership, core.IsOrgOwner],
        'dashboard': [core.IsAgentOrAdmin],
        'moderator': [],
        'api_key': [],
    }
    from apps.audit.views import IsAdmin as AuditAdmin
    from apps.config.views import IsAdmin as ConfigAdmin
    from apps.escrow.views import IsAdmin as EscrowAdmin, IsDashboardUser as EscrowDashboard
    from apps.inventory.views import _IsOrgAdminOrAdmin
    from apps.leads.views import IsDashboardUser as LeadsDashboard
    from apps.moderation.views import IsModerator
    from apps.organizations.external_api import HasApiKeyScope
    from apps.verification.views import IsAdmin as VerificationAdmin
    from apps.whatsapp.views import _IsDashboard
    groups['staff'] += [AuditAdmin, ConfigAdmin, EscrowAdmin, VerificationAdmin]
    groups['org_admin'] += [_IsOrgAdminOrAdmin]
    groups['dashboard'] += [EscrowDashboard, LeadsDashboard, _IsDashboard]
    groups['moderator'] += [IsModerator]
    groups['api_key'] += [HasApiKeyScope]
    return groups


def _name(perm) -> str:
    if isinstance(perm, OperandHolder):
        op = {'OR': '|', 'AND': '&'}.get(perm.operator_class.__name__, perm.operator_class.__name__)
        return f'({_name(perm.op1_class)} {op} {_name(perm.op2_class)})'
    return getattr(perm, '__name__', type(perm).__name__)


def _audience_of(perm, groups) -> str | None:
    """Audience a single permission class (or ``A | B`` / ``A & B``) restricts to, if any."""
    if isinstance(perm, OperandHolder):
        a, b = _audience_of(perm.op1_class, groups), _audience_of(perm.op2_class, groups)
        if perm.operator_class.__name__ == 'OR':
            # The wider side decides; an unrestricted side opens the whole expression.
            if a is None or b is None:
                return None
            return max(a, b, key=AUDIENCE_ORDER.index)
        return min((x for x in (a, b) if x), key=AUDIENCE_ORDER.index, default=None)
    for name, classes in groups.items():
        if any(perm is c or (isinstance(perm, type) and issubclass(perm, c)) for c in classes):
            return name
    if perm in (drf.IsAuthenticated,) or (isinstance(perm, type) and issubclass(perm, drf.IsAuthenticated)):
        return 'signed_in'
    return None


def audience(view_cls, groups=None) -> str:
    groups = groups or _classes()
    perms = list(getattr(view_cls, 'permission_classes', ()) or ())
    found = [a for a in (_audience_of(p, groups) for p in perms) if a]
    if not found:
        return 'public'
    return min(found, key=AUDIENCE_ORDER.index)   # AND of the list: the narrowest wins


def describe(view_cls) -> str:
    return ', '.join(_name(p) for p in (getattr(view_cls, 'permission_classes', ()) or ())) or '(none)'


def _concrete(template: str, callback) -> str | None:
    names = _PARAM.findall(template)
    if not names:
        return template
    for combo in itertools.product(_CANDIDATES, repeat=len(names)):
        values = iter(combo)
        path = _PARAM.sub(lambda _m: next(values), template)
        try:
            if resolve(path).func is callback:
                return path
        except Resolver404:
            continue
    return None


def endpoints(prefix: str = '/api/') -> list[Endpoint]:
    groups = _classes()
    out = []
    for template, method, callback in _AllViews().get_api_endpoints():
        if not template.startswith(prefix) or method in ('OPTIONS', 'HEAD'):
            continue
        path = _concrete(template, callback)
        if path is None:
            continue
        view = callback.cls
        out.append(Endpoint(path, template, method, view, audience(view, groups), describe(view)))
    return sorted(out, key=lambda e: (e.template, e.method))


def unresolved(prefix: str = '/api/') -> list[str]:
    """Templates for which no placeholder resolves back to the view (so the matrix skips them)."""
    return sorted({t for t, m, cb in _AllViews().get_api_endpoints()
                   if t.startswith(prefix) and _concrete(t, cb) is None})
