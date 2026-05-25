import io
import logging
import os
from datetime import date, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_reports():
    """
    Runs on the 1st of each month. Generates a branded PDF report for each
    active organization covering the previous calendar month.
    """
    from apps.organizations.models import Organization
    from .models import MonthlyReport
    from .generator import generate_monthly_report_content

    today                = date.today()
    first_of_this_month  = today.replace(day=1)
    last_month_end       = first_of_this_month - timedelta(days=1)
    last_month_start     = last_month_end.replace(day=1)

    start_dt    = timezone.make_aware(
        timezone.datetime(last_month_start.year, last_month_start.month, 1)
    )
    end_dt      = timezone.make_aware(
        timezone.datetime(first_of_this_month.year, first_of_this_month.month, 1)
    )
    month_label = last_month_start.strftime('%B %Y')

    orgs      = Organization.objects.filter(is_active=True)
    succeeded = 0
    failed    = 0

    for org in orgs:
        if MonthlyReport.objects.filter(organization=org, period_start=last_month_start).exists():
            logger.info(f"generate_monthly_reports: skipping {org} — report already exists for {month_label}")
            continue

        report = MonthlyReport.objects.create(
            organization=org,
            period_start=last_month_start,
            period_end=last_month_end,
            status=MonthlyReport.Status.GENERATING,
        )
        try:
            content   = generate_monthly_report_content(org, start_dt, end_dt)
            pdf_bytes = _build_monthly_pdf(org, content, month_label)
            pdf_url   = _upload_monthly_pdf(report, pdf_bytes)

            report.content    = content
            report.pdf_url    = pdf_url
            report.status     = MonthlyReport.Status.READY
            report.ready_at   = timezone.now()
            report.save(update_fields=['content', 'pdf_url', 'status', 'ready_at'])
            succeeded += 1
            logger.info(f"generate_monthly_reports: {org} — {month_label} ready")
        except Exception as exc:
            logger.warning(f"generate_monthly_reports: {org} — {month_label} failed: {exc}")
            report.status = MonthlyReport.Status.FAILED
            report.save(update_fields=['status'])
            failed += 1

    logger.info(
        f"generate_monthly_reports: {month_label} complete — "
        f"succeeded={succeeded}, failed={failed}"
    )
    return {'month': month_label, 'succeeded': succeeded, 'failed': failed}


def _build_monthly_pdf(org, content: dict, month_label: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )

    BRAND_BLUE = HexColor('#1B4F72')
    GREY       = HexColor('#F2F3F4')

    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                               leftMargin=inch, rightMargin=inch,
                               topMargin=inch, bottomMargin=inch)
    styles = getSampleStyleSheet()
    H1     = ParagraphStyle('H1', parent=styles['Heading1'],
                            textColor=BRAND_BLUE, fontSize=18, spaceAfter=6)
    H2     = ParagraphStyle('H2', parent=styles['Heading2'],
                            textColor=BRAND_BLUE, fontSize=13, spaceAfter=4)
    BODY   = styles['BodyText']
    SMALL  = ParagraphStyle('Small', parent=BODY, fontSize=8,
                            textColor=HexColor('#666666'))

    story = [
        Paragraph('RealTron AI', ParagraphStyle('Brand', parent=H1, fontSize=22)),
        Paragraph(f'Monthly Report — {month_label}', H1),
        HRFlowable(width='100%', color=BRAND_BLUE, thickness=1.5),
        Spacer(1, 0.15 * inch),
        Paragraph(f'Organization: {org.name}', BODY),
        Paragraph(f'Period: {month_label}', BODY),
        Spacer(1, 0.25 * inch),
    ]

    # Leads
    leads = content.get('leads', {})
    story += [
        Paragraph('Leads', H2),
        _monthly_kv_table([
            ('Total New Leads',  leads.get('total', 0)),
            ('Qualified',        leads.get('qualified', 0)),
            ('Conversion Rate',  f"{leads.get('conversion_rate', 0)}%"),
            ('Avg Score',        leads.get('avg_score', 0)),
        ]),
        Spacer(1, 0.2 * inch),
    ]

    # Top Agents
    top_agents = content.get('top_agents', [])
    if top_agents:
        story.append(Paragraph('Top Agents', H2))
        rows = [['Agent', 'Closed Deals', 'Rating']]
        rows += [[a['name'], a['closed_deals'], a['rating']] for a in top_agents]
        t = Table(rows, colWidths=['50%', '25%', '25%'])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_BLUE),
            ('TEXTCOLOR',  (0, 0), (-1, 0), HexColor('#FFFFFF')),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('GRID',       (0, 0), (-1, -1), 0.5, HexColor('#BDC3C7')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [None, GREY]),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story += [t, Spacer(1, 0.2 * inch)]

    # Deals
    deals = content.get('deals', {})
    story += [
        Paragraph('Deal Locks', H2),
        _monthly_kv_table([
            ('Total',     deals.get('total', 0)),
            ('Completed', deals.get('completed', 0)),
            ('Expired',   deals.get('expired', 0)),
            ('Disputed',  deals.get('disputed', 0)),
        ]),
        Spacer(1, 0.2 * inch),
    ]

    # Properties
    props = content.get('properties', {})
    story += [
        Paragraph('Property Listings', H2),
        _monthly_kv_table([
            ('New Listings', props.get('new_listings', 0)),
            ('Avg AI Score', props.get('avg_ai_score', 0)),
        ]),
        Spacer(1, 0.3 * inch),
    ]

    story += [
        HRFlowable(width='100%', color=HexColor('#BDC3C7'), thickness=0.5),
        Paragraph(
            'This report is generated by RealTron AI for informational purposes only.',
            SMALL,
        ),
    ]

    doc.build(story)
    return buf.getvalue()


def _monthly_kv_table(rows: list):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Table, TableStyle
    GREY = HexColor('#F2F3F4')
    data = [[k, str(v)] for k, v in rows]
    t = Table(data, colWidths=['45%', '55%'])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), GREY),
        ('FONTNAME',   (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('GRID',       (0, 0), (-1, -1), 0.5, HexColor('#BDC3C7')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [None, GREY]),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t


def _upload_monthly_pdf(report, pdf_bytes: bytes) -> str:
    import cloudinary.uploader
    result = cloudinary.uploader.upload(
        pdf_bytes,
        resource_type='raw',
        public_id=f'monthly_reports/{report.organization_id}/{report.period_start}',
        format='pdf',
        overwrite=True,
    )
    return result['secure_url']


@shared_task
def generate_report_task(report_id: str):
    """
    Async Celery task: generates report content + PDF, saves to media/reports/,
    sets report.status=READY and report.file_url on success.
    """
    from .models import Report

    try:
        report = Report.objects.select_related('user', 'property').get(id=report_id)
    except Report.DoesNotExist:
        logger.error(f"generate_report_task: report {report_id} not found")
        return

    report.status = Report.Status.GENERATING
    report.save(update_fields=['status'])

    try:
        content = _build_content(report)
        pdf_bytes = _build_pdf(report, content)
        file_url = _save_pdf(report, pdf_bytes)

        report.content  = content
        report.file_url = file_url
        report.status   = Report.Status.READY
        report.ready_at = timezone.now()
        report.save(update_fields=['content', 'file_url', 'status', 'ready_at'])

        _notify_user(report)
        logger.info(f"Report {report_id} ({report.report_type}) ready.")

    except Exception as exc:
        logger.exception(f"generate_report_task failed for {report_id}: {exc}")
        report.status = Report.Status.FAILED
        report.save(update_fields=['status'])


# ─── Content builders ─────────────────────────────────────────────────────────

def _build_content(report) -> dict:
    t = report.report_type
    if t == 'property_analysis':
        return _content_property_analysis(report)
    if t == 'tax_advisory':
        return _content_tax_advisory(report)
    if t == 'loan_eligibility':
        return _content_loan_eligibility(report)
    if t == 'fraud_check':
        return _content_fraud_check(report)
    raise ValueError(f"Unknown report type: {t}")


def _content_property_analysis(report) -> dict:
    from apps.audit.services import PropertyAuditEngine
    prop = report.property
    if not prop:
        return {'error': 'No property linked to this report.'}
    engine = PropertyAuditEngine(prop)
    return engine.run()


def _resolve_org_country(report) -> str:
    """Return the ISO 3166-1 alpha-2 country for this report, or '' if unknown."""
    try:
        if report.property and report.property.organization:
            return report.property.organization.country or ''
    except Exception:
        pass
    try:
        return report.user.owned_organization.country or ''
    except Exception:
        return ''


def _content_tax_advisory(report) -> dict:
    country = _resolve_org_country(report)
    if country != 'PK':
        return {
            'supported': False,
            'country':   country or 'unknown',
            'message': (
                'Detailed tax advisory is currently available for Pakistan (PK) only. '
                'Support for additional markets is on the roadmap. '
                'Please consult a local tax professional for your jurisdiction.'
            ),
        }

    prop  = report.property
    meta  = report.content.get('input', {})  # caller may pre-populate input params

    property_value = meta.get('property_value') or (prop.price if prop else 0) or 0
    ownership_type = meta.get('ownership_type', 'filer')
    holding_years  = meta.get('holding_years', 1)

    # Pakistan FBR — Section 7E deemed income tax (annual)
    exemption_7e = 25_000_000  # PKR 25M threshold
    taxable_7e   = max(0, property_value - exemption_7e)
    rate_7e      = 0.01 if ownership_type == 'filer' else 0.02
    tax_7e       = int(taxable_7e * rate_7e)

    # Pakistan FBR — Capital Gains Tax schedule
    if holding_years < 1:
        cgt_rate = 0.15
    elif holding_years < 2:
        cgt_rate = 0.12
    elif holding_years < 3:
        cgt_rate = 0.10
    elif holding_years < 4:
        cgt_rate = 0.075
    elif holding_years < 6:
        cgt_rate = 0.05
    else:
        cgt_rate = 0.0
    cgt_amount = int(property_value * cgt_rate)

    # Pakistan FBR — WHT on purchase (buyer)
    wht_rate   = 0.01 if ownership_type == 'filer' else 0.02
    wht_amount = int(property_value * wht_rate)

    return {
        'supported':       True,
        'country':         'PK',
        'property_value':  property_value,
        'ownership_type':  ownership_type,
        'holding_years':   holding_years,
        'section_7e': {
            'taxable_value': taxable_7e,
            'rate_pct':      rate_7e * 100,
            'annual_tax':    tax_7e,
            'note':          'Annual deemed income tax on immovable property over PKR 25M (FBR Section 7E)',
        },
        'capital_gains_tax': {
            'rate_pct': cgt_rate * 100,
            'amount':   cgt_amount,
            'note':     f'CGT applies on gain at {holding_years}-year holding period (FBR schedule)',
        },
        'withholding_tax': {
            'rate_pct': wht_rate * 100,
            'amount':   wht_amount,
            'note':     'WHT payable by buyer at time of purchase (FBR)',
        },
        'total_estimated_liability': tax_7e + wht_amount,
    }


def _content_loan_eligibility(report) -> dict:
    meta           = report.content.get('input', {})
    monthly_income = meta.get('monthly_income', 0)
    property_value = meta.get('property_value') or (report.property.price if report.property else 0) or 0
    existing_emis  = meta.get('existing_emis', 0)

    # Apna Ghar / standard bank rules
    max_emi_ratio  = 0.40  # 40% of net income
    max_monthly_emi = max(0, monthly_income * max_emi_ratio - existing_emis)
    # 20-year term @ 18% pa = factor
    rate_monthly = 0.18 / 12
    n = 240  # months
    factor = rate_monthly * (1 + rate_monthly) ** n / ((1 + rate_monthly) ** n - 1) if rate_monthly else 1/n
    max_loan_amount = int(max_monthly_emi / factor) if factor > 0 else 0
    min_down_payment = int(property_value * 0.20)
    loan_needed = max(0, property_value - min_down_payment)
    eligible = max_loan_amount >= loan_needed

    return {
        'monthly_income':    monthly_income,
        'existing_emis':     existing_emis,
        'property_value':    property_value,
        'max_monthly_emi':   int(max_monthly_emi),
        'max_loan_amount':   max_loan_amount,
        'min_down_payment':  min_down_payment,
        'loan_needed':       loan_needed,
        'eligible':          eligible,
        'note': (
            'Eligible for bank financing.'
            if eligible
            else 'Monthly income may be insufficient for this property value at current rates.'
        ),
        'apna_ghar': {
            'subsidy_rate': '5% for first-time buyers under PKR 5M',
            'max_loan':     5_000_000,
            'term_years':   20,
        },
    }


def _content_fraud_check(report) -> dict:
    prop = report.property
    if not prop:
        return {'error': 'No property linked for fraud check.'}

    from apps.verification.models import Verification
    from apps.verification.services import FraudCheckService

    verifications = Verification.objects.filter(property=prop).order_by('-created_at')[:1]
    latest_v = verifications.first()

    # Blacklist check on owner phone
    owner_phone = prop.owner.phone if prop.owner else ''
    blacklisted = FraudCheckService.is_blacklisted(owner_phone) if owner_phone else False

    return {
        'property_id':     str(prop.id),
        'property_title':  prop.title,
        'legal_status':    prop.legal_status,
        'risk_level':      prop.risk_level,
        'ai_score':        prop.ai_score,
        'owner_blacklisted': blacklisted,
        'latest_verification': {
            'status':       latest_v.status if latest_v else None,
            'signal_score': latest_v.signal_score if latest_v else None,
            'fraud_flags':  latest_v.fraud_flags if latest_v else [],
        } if latest_v else None,
        'risk_summary': _fraud_risk_summary(prop, latest_v, blacklisted),
    }


def _fraud_risk_summary(prop, verification, blacklisted: bool) -> str:
    issues = []
    if blacklisted:
        issues.append('Owner phone is blacklisted')
    if prop.legal_status in ('disputed', 'unverified'):
        issues.append(f'Legal status is {prop.legal_status}')
    if prop.risk_level == 'high':
        issues.append('AI risk level is HIGH')
    if verification and verification.fraud_flags:
        issues.append(f"{len(verification.fraud_flags)} fraud flag(s) on verification")
    if not issues:
        return 'No significant fraud risk indicators detected.'
    return 'Risk indicators found: ' + '; '.join(issues) + '.'


# ─── PDF builder ──────────────────────────────────────────────────────────────

def _build_pdf(report, content: dict) -> bytes:
    """Build a simple but professional PDF for any report type."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )

    BRAND_BLUE = HexColor('#1B4F72')
    GREEN      = HexColor('#27AE60')
    GREY       = HexColor('#F2F3F4')

    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                               leftMargin=inch, rightMargin=inch,
                               topMargin=inch, bottomMargin=inch)
    styles = getSampleStyleSheet()
    H1     = ParagraphStyle('H1', parent=styles['Heading1'],
                            textColor=BRAND_BLUE, fontSize=18, spaceAfter=6)
    H2     = ParagraphStyle('H2', parent=styles['Heading2'],
                            textColor=BRAND_BLUE, fontSize=13, spaceAfter=4)
    BODY   = styles['BodyText']
    SMALL  = ParagraphStyle('Small', parent=BODY, fontSize=8, textColor=HexColor('#666666'))

    title_map = {
        'property_analysis': 'Property Analysis Report',
        'tax_advisory':      'Tax Advisory Report',
        'loan_eligibility':  'Loan Eligibility Report',
        'fraud_check':       'Fraud Check Report',
    }

    story = [
        Paragraph('RealTron AI', ParagraphStyle('Brand', parent=H1, fontSize=22)),
        Paragraph(title_map.get(report.report_type, 'Report'), H1),
        HRFlowable(width='100%', color=BRAND_BLUE, thickness=1.5),
        Spacer(1, 0.15 * inch),
        Paragraph(f"Prepared for: {report.user.phone}", BODY),
        Paragraph(f"Date: {date.today().strftime('%d %B %Y')}", BODY),
        Spacer(1, 0.25 * inch),
    ]

    if report.property:
        story += [
            Paragraph('Property', H2),
            _kv_table([
                ('Title',    report.property.title),
                ('City',     report.property.city),
                ('Location', report.property.location),
                ('Type',     report.property.property_type),
                ('Price',    f"{report.property.currency} {report.property.price:,}" if report.property.price else '—'),
            ]),
            Spacer(1, 0.2 * inch),
        ]

    # Flatten content dict into readable sections
    story += _content_to_story(content, H2, BODY)

    story += [
        Spacer(1, 0.3 * inch),
        HRFlowable(width='100%', color=HexColor('#BDC3C7'), thickness=0.5),
        Paragraph(
            'This report is generated by RealTron AI for informational purposes only. '
            'It does not constitute legal or financial advice.',
            SMALL,
        ),
    ]

    doc.build(story)
    return buf.getvalue()


def _kv_table(rows: list):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Table, TableStyle
    GREY = HexColor('#F2F3F4')
    data = [[k, str(v)] for k, v in rows]
    t = Table(data, colWidths=['35%', '65%'])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), GREY),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#BDC3C7')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [None, GREY]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t


def _content_to_story(content: dict, H2, BODY) -> list:
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor

    story = []
    for key, val in content.items():
        if key in ('error',):
            story.append(Paragraph(f"⚠ {val}", BODY))
            continue
        if isinstance(val, dict):
            story.append(Paragraph(key.replace('_', ' ').title(), H2))
            rows = [(k.replace('_', ' ').title(), str(v)) for k, v in val.items()]
            story.append(_kv_table(rows))
            story.append(Spacer(1, 0.15 * inch))
        elif isinstance(val, list):
            story.append(Paragraph(key.replace('_', ' ').title(), H2))
            for item in val:
                story.append(Paragraph(f"• {item}", BODY))
            story.append(Spacer(1, 0.1 * inch))
        else:
            label = key.replace('_', ' ').title()
            story.append(Paragraph(f"<b>{label}:</b> {val}", BODY))
    return story


# ─── File storage ─────────────────────────────────────────────────────────────

def _save_pdf(report, pdf_bytes: bytes) -> str:
    """Upload PDF bytes to Cloudinary and return the CDN URL."""
    import cloudinary.uploader
    result = cloudinary.uploader.upload(
        pdf_bytes,
        resource_type='raw',
        public_id=f'reports/{report.id}',
        format='pdf',
        overwrite=True,
    )
    return result['secure_url']


# ─── Notification ─────────────────────────────────────────────────────────────

def _notify_user(report):
    try:
        title_map = {
            'property_analysis': 'Property Analysis',
            'tax_advisory':      'Tax Advisory',
            'loan_eligibility':  'Loan Eligibility',
            'fraud_check':       'Fraud Check',
        }
        label = title_map.get(report.report_type, 'Report')
        from apps.notifications.services import notify_user
        notify_user(
            report.user,
            title=f"{label} Report Ready",
            message=(
                f"✅ *Your {label} Report is Ready*\n\n"
                f"Download your report from the link below:\n"
                f"{report.file_url}"
            ),
            event_type='report_ready',
        )
    except Exception as exc:
        logger.warning(f"Failed to notify user {report.user.phone} for report {report.id}: {exc}")
