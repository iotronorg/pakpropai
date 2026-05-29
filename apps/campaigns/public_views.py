"""
Public (unauthenticated) endpoints for the viral referral loop.

GET  /public/verify/?ref=<code>   — click-tracking redirect
POST /public/referral/convert/    — lead creation on referral landing
GET  /public/platform-stats/      — anonymised aggregate trust stats
"""
from __future__ import annotations

import re
import logging

from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.campaigns.models import ReferralLink

logger = logging.getLogger(__name__)

_E164_RE = re.compile(r'^\+\d{7,15}$')
_STATS_CACHE_KEY = 'public_platform_stats'
_STATS_CACHE_TTL = 60  # seconds


class PublicVerifyThrottle(AnonRateThrottle):
    scope = 'public_verify'


class PublicConvertThrottle(AnonRateThrottle):
    scope = 'public_convert'


# ── Task 5: click-tracking redirect ──────────────────────────────────────────

class PublicVerifyRedirectView(APIView):
    """
    GET /public/verify/?ref=<code>

    Increments ReferralLink.clicks, resolves the org slug, and 302-redirects to
    the Next.js public landing page at /public/verify?org=<slug>&ref=<code>.
    Unknown or inactive codes redirect to the root landing.
    """
    authentication_classes = []
    permission_classes     = [AllowAny]
    throttle_classes       = [PublicVerifyThrottle]

    def get(self, request):
        from django.conf import settings
        ref   = request.query_params.get('ref', '').strip()[:40]
        base  = getattr(settings, 'FRONTEND_URL', 'https://realtron.ai').rstrip('/')

        if not ref:
            return HttpResponseRedirect(f"{base}/public/verify")

        rl = ReferralLink.objects.select_related('org').filter(code=ref).first()
        if not rl or not rl.org.is_active:
            return HttpResponseRedirect(f"{base}/public/verify")

        ReferralLink.objects.filter(pk=rl.pk).update(clicks=rl.clicks + 1)

        slug     = rl.org.slug
        redirect = f"{base}/public/verify?org={slug}&ref={ref}"
        return HttpResponseRedirect(redirect)


# ── Task 6: conversion endpoint ───────────────────────────────────────────────

class PublicReferralConvertView(APIView):
    """
    POST /public/referral/convert/

    Body: {ref, phone (E.164), source}

    Creates a stub Lead in the referral's org, increments ReferralLink.conversions,
    and returns {org_whatsapp_number} for the deep-link CTA.
    """
    authentication_classes = []
    permission_classes     = [AllowAny]
    throttle_classes       = [PublicConvertThrottle]

    def post(self, request):
        ref    = (request.data.get('ref')    or '').strip()[:40]
        phone  = (request.data.get('phone')  or '').strip()
        source = (request.data.get('source') or 'web').strip()[:20]

        if not ref:
            return Response({'detail': 'ref is required.'}, status=status.HTTP_400_BAD_REQUEST)

        if not phone or not _E164_RE.match(phone):
            return Response(
                {'detail': 'phone must be a valid E.164 number (e.g. +923001234567).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rl = ReferralLink.objects.select_related('org').filter(code=ref).first()
        if not rl or not rl.org.is_active:
            return Response({'detail': 'Invalid referral code.'}, status=status.HTTP_404_NOT_FOUND)

        org = rl.org

        try:
            lead = _create_stub_lead(phone, org, ref)
        except Exception:
            logger.warning('PublicReferralConvertView: stub lead creation failed', exc_info=True)
            lead = None

        if lead:
            ReferralLink.objects.filter(pk=rl.pk).update(conversions=rl.conversions + 1)

        wa_number = org.phone or ''
        return Response(
            {'org_whatsapp_number': wa_number, 'org_slug': org.slug},
            status=status.HTTP_201_CREATED if lead else status.HTTP_200_OK,
        )


def _create_stub_lead(phone: str, org, ref_code: str):
    from apps.users.models import User
    from apps.leads.models import Lead

    user, _ = User.objects.get_or_create(
        phone=phone,
        defaults={'role': 'client'},
    )
    lead, created = Lead.objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            'source':        Lead.Source.REFERRAL_VIRAL,
            'referral_code': ref_code,
            'status':        'new',
        },
    )
    if not created and not lead.referral_code:
        lead.referral_code = ref_code
        lead.save(update_fields=['referral_code'])
    return lead


# ── Task 8: public platform stats ─────────────────────────────────────────────

class PublicPlatformStatsView(APIView):
    """
    GET /public/platform-stats/

    Returns anonymised aggregate trust metrics.  Results are cached 60 s.
    """
    authentication_classes = []
    permission_classes     = [AllowAny]

    def get(self, request):
        data = cache.get(_STATS_CACHE_KEY)
        if data is None:
            data = _compute_stats()
            cache.set(_STATS_CACHE_KEY, data, _STATS_CACHE_TTL)
        return Response(data)


def _compute_stats() -> dict:
    from apps.audit.models import PropertyAudit
    from apps.compliance.models import SanctionScreeningResult
    from apps.leads.models import Lead
    from apps.organizations.models import Organization

    today = timezone.now().date()
    return {
        'total_verifications':  PropertyAudit.objects.count(),
        'scams_caught':         SanctionScreeningResult.objects.filter(
                                    status__in=['blocked', 'flagged']
                                ).count(),
        'active_orgs':          Organization.objects.filter(is_active=True).count(),
        'leads_generated_today': Lead.objects.filter(
                                    created_at__date=today
                                ).count(),
    }
