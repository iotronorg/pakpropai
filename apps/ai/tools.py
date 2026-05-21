"""Tool functions for the RealTron AI agent."""
import logging

logger = logging.getLogger(__name__)

from ._tools_context import _ctx_user, _ctx_phone, set_context  # noqa: F401
from ._tools_search import search_properties                     # noqa: F401
from ._tools_financial import calculate_7e_tax, check_loan_eligibility  # noqa: F401
from ._tools_fraud import run_fraud_check                        # noqa: F401
from ._tools_deals import initiate_deal_lock                     # noqa: F401

# ─── Property Audit ──────────────────────────────────────────────────────────

def generate_property_audit(
    city: str,
    location: str,
    estimated_value_pkr: int,
    property_type: str = 'residential',
    area_marla: float = 0.0,
    owner_name: str = '',
    description: str = '',
) -> dict:
    """
    Generate a comprehensive property audit report with risk score, investment grade,
    market comparison, tax analysis, ROI projections, and role-specific insights.
    Call this when the user asks for 'property audit', 'audit report', 'property check',
    or wants a detailed analysis of a specific property.

    Args:
        city: City where the property is located e.g. 'Lahore', 'Karachi', 'Islamabad'
        location: Specific area e.g. 'DHA Phase 5', 'Bahria Town', 'Gulberg 3'
        estimated_value_pkr: Estimated property value in PKR (e.g. 15000000 for 1.5 crore)
        property_type: One of 'plot', 'house', 'apartment', 'commercial' (default: residential)
        area_marla: Size in Marla (0 if not known)
        owner_name: Owner name if known (optional)
        description: Any additional details, concerns, or context about the property
    """
    try:
        import os
        from django.conf import settings as django_settings
        from apps.audit.services import AuditEngine
        from apps.audit.pdf import generate_audit_pdf
        from apps.audit.models import PropertyAudit

        phone = _ctx_phone.get() or ''
        user  = _ctx_user.get()

        audit_data = AuditEngine.run(
            city=city,
            location=location,
            property_type=property_type,
            estimated_value_pkr=estimated_value_pkr,
            area_marla=area_marla if area_marla > 0 else None,
            owner_name=owner_name,
            description=description,
            phone=phone,
        )

        # Save to DB
        scores = audit_data['scores']
        audit  = PropertyAudit.objects.create(
            user=user,
            phone=phone,
            city=city,
            location=location,
            property_type=property_type,
            area_marla=area_marla if area_marla > 0 else None,
            estimated_value_pkr=estimated_value_pkr,
            owner_name=owner_name,
            description=description,
            risk_score=scores['risk_score'],
            investment_grade=scores['investment_grade'],
            liquidity_score=scores['liquidity_score'],
            audit_data=audit_data,
        )

        # Generate PDF
        media_root = django_settings.MEDIA_ROOT
        audits_dir = os.path.join(str(media_root), 'audits')
        os.makedirs(audits_dir, exist_ok=True)
        pdf_filename = f"audit_{audit.id}.pdf"
        pdf_path     = os.path.join(audits_dir, pdf_filename)

        pdf_url = None
        try:
            generate_audit_pdf(audit_data, pdf_path)
            audit.pdf_file = f"audits/{pdf_filename}"
            audit.save(update_fields=['pdf_file'])
            base_url = getattr(django_settings, 'BASE_URL', 'http://127.0.0.1:8000')
            pdf_url  = f"{base_url}/api/v1/audit/download/{audit.id}/"
        except Exception as pdf_exc:
            import logging as _log
            _log.getLogger(__name__).warning('Audit PDF generation failed for %s: %s', audit.id, pdf_exc)

        # Build WhatsApp summary
        ov  = audit_data['overview']
        fin = audit_data['financial_analysis']
        mkt = audit_data['market_analysis']
        rec = audit_data['recommendations']

        area_str  = f"{ov['area_marla']}M " if ov.get('area_marla') else ''
        value_str = f"PKR {estimated_value_pkr:,}"

        summary_lines = [
            f"🏠 *PROPERTY AUDIT REPORT*",
            f"📍 {area_str}{property_type.title()} — {location}, {city}",
            f"💰 Value: {value_str}",
            "",
            f"*Risk Score: {scores['risk_score']}/10 — {scores['risk_label']}*",
            f"*Investment Grade: {scores['investment_grade']} ({scores['investment_grade_label']})*",
            f"*Verdict: {scores['verdict']}* — _{scores['verdict_reason']}_",
            "",
            "📊 *MARKET ANALYSIS*",
            f"Price vs Market: {mkt['price_vs_market']}",
        ]

        if mkt.get('price_vs_market_pct') is not None:
            pct = mkt['price_vs_market_pct']
            summary_lines.append(f"Market Difference: {'+' if pct > 0 else ''}{pct:.1f}%")

        summary_lines += [
            f"Fair Value Range: PKR {mkt['estimated_fair_value_min']:,} – PKR {mkt['estimated_fair_value_max']:,}",
            "",
            "💰 *TAX OBLIGATIONS*",
        ]

        tax = fin['tax_table']
        if tax['7e_annual_filer'] > 0:
            summary_lines.append(f"7E Tax (filer): PKR {tax['7e_annual_filer']:,}/year")
        else:
            summary_lines.append("7E Tax: Exempt (below PKR 25M threshold)")
        summary_lines.append(f"WHT on sale (filer): PKR {tax['wht_filer']:,}")
        summary_lines.append(f"Stamp Duty: PKR {tax['stamp_duty_estimate']:,}")

        true_cost = fin['true_cost_buyer']['total']
        net_seller = fin['net_in_hand_seller']['net']
        summary_lines += [
            "",
            f"🏦 *FOR BUYER* — Total cost incl. fees: *PKR {true_cost:,}*",
            f"💼 *FOR SELLER* — Net in hand: *PKR {net_seller:,}*",
            f"📈 *5-YR PROJECTION* — PKR {fin['roi_projections']['5_year']['value']:,}",
            "",
            "⚠️ *TOP ACTIONS*",
        ]
        for i, action in enumerate(rec['top_3_actions'], 1):
            summary_lines.append(f"{i}. {action}")

        pdf_line = (
            f"📄 *Full PDF Report:* {pdf_url}"
            if pdf_url else
            "📄 *PDF report:* generation failed — all data available in your dashboard"
        )
        summary_lines += [
            "",
            pdf_line,
            "",
            "_Consult a registered property lawyer and CA for final decisions._",
        ]

        return {
            'success': True,
            'audit_id': audit.id,
            'whatsapp_summary': '\n'.join(summary_lines),
            'pdf_url': pdf_url,
            'risk_score': scores['risk_score'],
            'investment_grade': scores['investment_grade'],
            'verdict': scores['verdict'],
        }

    except Exception as exc:
        logger.error(f"generate_property_audit failed: {exc}", exc_info=True)
        return {
            'success': False,
            'error': 'Audit generation failed. Please try again.',
            'whatsapp_summary': 'Sorry, I could not generate the audit report right now. Please try again.',
        }


# ─── List Property ────────────────────────────────────────────────────────────

def list_property(
    city: str,
    location: str,
    area_marla: float,
    price: int,
    property_type: str,
    furnished: str = '',
    construction_status: str = '',
    description: str = '',
) -> dict:
    """
    Publish a new property listing on RealTron AI for buyers to discover.
    ONLY call this tool when you have collected ALL required information:
    city, location, area size (in marla), asking price (in PKR), and property type.
    If any required field is missing, ask the user for it first before calling this tool.

    Args:
        city: City where property is located (e.g., 'Lahore', 'Karachi')
        location: Specific area/society (e.g., 'DHA Phase 6', 'Gulberg 3', 'Bahria Town Block D')
        area_marla: Size in Marla. Convert Kanal to Marla (1 Kanal = 20 Marla).
        price: Asking price in Pakistani Rupees (e.g., 15000000 for PKR 1.5 crore)
        property_type: One of 'plot', 'residential', 'commercial'
        furnished: One of 'furnished', 'semi_furnished', 'unfurnished', or empty string
        construction_status: One of 'builder', 'ready', 'under_construction', or empty string
        description: Optional additional details or features
    """
    try:
        user = _ctx_user.get()

        from apps.properties.models import Property

        area_str = f"{area_marla}M " if area_marla else ""
        title = f"{area_str}{property_type.title()} — {location}, {city}"

        prop = Property.objects.create(
            owner=user,
            title=title,
            city=city,
            location=location,
            area_marla=area_marla if area_marla else None,
            price=price,
            property_type=property_type,
            furnished_status=furnished or None,
            construction_status=construction_status or None,
            description=description,
            legal_status=Property.LegalStatus.UNVERIFIED,
        )

        # Queue async AI scoring
        try:
            from apps.properties.tasks import score_property_task
            score_property_task.delay(str(prop.id))
        except Exception:
            pass

        # Capture lead
        try:
            from apps.leads.utils import upsert_lead
            if user:
                upsert_lead(user, 'sell', city_interest=city)
        except Exception:
            pass

        # Enter LISTING_PHOTOS state so the next WhatsApp image goes to this property
        phone = _ctx_phone.get()
        if phone:
            try:
                from apps.whatsapp.sessions import SessionManager
                SessionManager.update(phone, state='LISTING_PHOTOS', context={
                    'property_id': str(prop.id),
                    'photo_count': 0,
                })
            except Exception:
                pass

        return {
            'success':        True,
            'listing_id':     str(prop.id)[:8].upper(),
            'title':          title,
            'price_formatted': f"PKR {price:,}",
            'message': (
                'Property listed successfully. AI scoring is running in the background. '
                'Your listing is now visible to buyers.\n\n'
                'You can now send up to 5 photos of your property — just send them now. '
                'Type *done* when finished or to skip photos.'
            ),
        }
    except Exception as exc:
        logger.error(f"list_property tool failed: {exc}")
        return {'success': False, 'error': 'Failed to create listing. Please try again.'}


# ─── Connect to Agent ─────────────────────────────────────────────────────────

def connect_to_agent(
    city: str = '',
    intent: str = 'buy',
    budget_pkr: int = 0,
    property_type: str = '',
    specific_area: str = '',
) -> dict:
    """
    Match the user with a verified real estate agent and return the agent's contact details.
    Call this when the user says 'talk to agent', 'connect me with an agent',
    'I want to speak to someone', 'refer me to an agent', 'I need an agent',
    or when they are clearly ready to proceed with buying, selling, or renting.

    Args:
        city: City user is interested in e.g. 'Lahore', 'Karachi', 'Islamabad', 'Rawalpindi'
        intent: User's intent — 'buy', 'sell', 'rent', or 'invest'
        budget_pkr: User's budget in PKR (0 if not mentioned)
        property_type: 'plot', 'house', 'apartment', 'commercial' (optional)
        specific_area: Specific area/society they mentioned e.g. 'DHA Phase 5' (optional)
    """
    try:
        from apps.agents.models import Agent
        from django.utils import timezone
        from django.db.models import Q

        phone = _ctx_phone.get() or ''
        user  = _ctx_user.get()

        # Build queryset — verified + active + available agents only
        qs = Agent.objects.filter(
            is_active=True,
            is_verified=True,
            availability_status=Agent.AvailabilityStatus.AVAILABLE,
        )

        # City match — STRICT: if city was specified, only return agents for that city.
        # Never return an agent from a different city just because no local one exists.
        if city:
            city_qs = qs.filter(cities__icontains=city)
            if not city_qs.exists():
                city_qs = qs.filter(primary_city__icontains=city)
            agents = city_qs  # may be empty — handled below as "no agent" case
        else:
            agents = qs       # no city specified → any verified agent

        # Specialization preference (soft match — prefer but don't filter out)
        spec_map = {
            'buy':        ['residential_buy', 'plots', 'luxury', 'new_projects'],
            'sell':       ['residential_buy', 'plots', 'commercial', 'luxury'],
            'rent':       ['residential_rent', 'commercial'],
            'invest':     ['plots', 'new_projects', 'residential_buy', 'commercial'],
            'commercial': ['commercial', 'industrial'],
        }
        preferred_specs = spec_map.get(intent.lower(), [])

        # Load-balanced selection: featured first, then fewest recent leads (last 7 days),
        # then highest rating. This distributes leads evenly within the same tier.
        def _pick_agent(qs):
            from django.utils import timezone as _tz
            from datetime import timedelta
            from django.db.models import Count, Q as _Q
            week_ago = _tz.now() - timedelta(days=7)
            return (
                qs.annotate(
                    recent_leads=Count(
                        'assigned_leads',
                        filter=_Q(assigned_leads__created_at__gte=week_ago),
                    )
                )
                .order_by('-is_featured', 'recent_leads', '-rating')
                .first()
            )

        spec_agent = None
        for spec in preferred_specs:
            spec_qs = agents.filter(specializations__icontains=spec)
            if spec_qs.exists():
                spec_agent = _pick_agent(spec_qs)
                break

        agent = spec_agent or _pick_agent(agents)

        # No agents available for the requested city (or at all)
        if not agent:
            _capture_agent_request_lead(user, phone, city, intent, budget_pkr, specific_area)
            city_str = f"*{city}*" if city else "your area"
            no_agent_msg = (
                f"We don't have a verified agent registered for {city_str} yet.\n\n"
                "Your request has been noted. Our team will connect you with an "
                "authorized RealTron AI agent for your area shortly — we'll reach out "
                "to you on this WhatsApp number."
            )
            # INSTRUCTION FOR MODEL: return this message verbatim — do not add any agent details
            return {
                'found': False,
                'message': no_agent_msg,
                'whatsapp_summary': no_agent_msg,
                '_instruction': 'Return the whatsapp_summary above VERBATIM. Do NOT add any agent names, phone numbers, or contact details.',
            }

        # Update agent metrics
        Agent.objects.filter(pk=agent.pk).update(
            total_leads=agent.total_leads + 1,
            last_active_at=timezone.now(),
        )

        # Capture lead
        _capture_agent_request_lead(user, phone, city, intent, budget_pkr, specific_area,
                                    agent=agent)

        # Build WhatsApp agent card
        lines = [
            f"✅ *Agent Found!*",
            "",
        ]

        if agent.agent_type == Agent.AgentType.INDIVIDUAL:
            lines.append(f"👤 *{agent.name}*")
        else:
            lines.append(f"🏢 *{agent.name}*")

        if agent.company_name:
            lines.append(f"   {agent.company_name} ({agent.get_agent_type_display()})")

        if agent.designation:
            lines.append(f"   {agent.designation}")

        lines.append("")

        # Coverage
        cities_display = ', '.join(agent.cities[:3]) if agent.cities else agent.primary_city
        areas_display  = ', '.join(agent.areas[:4]) if agent.areas else ''
        if cities_display:
            coverage = f"📍 *Cities:* {cities_display}"
            if areas_display:
                coverage += f"\n   *Areas:* {areas_display}"
            lines.append(coverage)

        # Specializations
        if agent.specializations_str and agent.specializations_str != '—':
            lines.append(f"💼 *Specializes in:* {agent.specializations_str}")

        # Experience + rating
        exp_parts = []
        if agent.years_experience:
            exp_parts.append(f"{agent.years_experience} years experience")
        if float(agent.rating) > 0:
            exp_parts.append(f"⭐ {agent.rating}/5 rating")
        if agent.closed_deals:
            exp_parts.append(f"{agent.closed_deals} deals closed")
        if exp_parts:
            lines.append(f"📊 {' · '.join(exp_parts)}")

        if agent.license_number:
            lines.append(f"🪪 License: {agent.license_number}")

        lines.append("")
        lines.append(f"📞 *WhatsApp: {agent.contact_whatsapp}*")

        if agent.email:
            lines.append(f"📧 {agent.email}")
        if agent.website:
            lines.append(f"🌐 {agent.website}")
        if agent.office_address:
            lines.append(f"🏢 {agent.office_address}")

        if agent.instagram_handle:
            lines.append(f"📸 @{agent.instagram_handle}")

        lines += [
            "",
            "✅ *Verified by RealTron AI*",
            "",
            "_Feel free to contact them directly on WhatsApp. "
            "Mention RealTron AI when you reach out._",
        ]

        if agent.bio:
            lines += ["", f"_{agent.bio}_"]

        summary = '\n'.join(lines)

        # INSTRUCTION FOR MODEL: return whatsapp_summary VERBATIM — do not modify any details
        return {
            'found': True,
            'agent_id': agent.id,
            'agent_name': agent.name,
            'agent_whatsapp': agent.contact_whatsapp,
            'whatsapp_summary': summary,
            'message': summary,
            '_instruction': 'Return the whatsapp_summary field EXACTLY as shown. Do NOT change any names, numbers, or details.',
        }

    except Exception as exc:
        logger.error(f"connect_to_agent tool failed: {exc}", exc_info=True)
        return {
            'found': False,
            'error': str(exc),
            'whatsapp_summary': 'Sorry, I could not find an agent right now. Please try again.',
        }


def _capture_agent_request_lead(user, phone: str, city: str, intent: str,
                                 budget_pkr: int, specific_area: str,
                                 agent=None):
    try:
        from apps.leads.models import Lead
        intent_map = {
            'buy': Lead.Intent.BUY, 'sell': Lead.Intent.SELL,
            'rent': Lead.Intent.RENT, 'invest': Lead.Intent.INVEST,
        }
        lead_intent = intent_map.get(intent.lower(), Lead.Intent.BUY)
        signals = {'source': 'talk_to_agent', 'specific_area': specific_area}
        if agent:
            signals['matched_agent_id'] = agent.id
        if user:
            lead, _ = Lead.objects.update_or_create(
                user=user, intent=lead_intent,
                defaults={
                    'city_interest':  city,
                    'budget_max':     budget_pkr if budget_pkr > 0 else None,
                    'intent_signals': signals,
                    'score':          80,
                    'status':         Lead.Status.QUALIFIED,
                },
            )
            if agent and not lead.assigned_agent_id:
                lead.assigned_agent = agent
                lead.save(update_fields=['assigned_agent'])
    except Exception as exc:
        logger.error(f"_capture_agent_request_lead failed: {exc}")

