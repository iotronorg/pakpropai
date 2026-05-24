"""
PDF generation for Property Audit reports.
Uses ReportLab to produce a multi-page branded PDF.
"""
import io
import os
from dataclasses import dataclass
from datetime import date

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    PageBreak,
    KeepTogether,
)

@dataclass
class OrgBrandContext:
    org_name: str
    brand_color: str = '#1B4F72'
    logo_url: str | None = None
    measurement_system: str = 'pk_traditional'


# ─── Area display helper ──────────────────────────────────────────────────────

_SQFT_PER_SQM  = 10.7639
_MARLA_PER_SQM = 1 / 25.2929


def _format_area_display(area_sqm: float | None, measurement_system: str = 'pk_traditional') -> str:
    """Return a human-readable area string for the given measurement system."""
    if area_sqm is None:
        return 'N/A'
    if measurement_system == 'pk_traditional':
        marla = area_sqm * _MARLA_PER_SQM
        return f"{marla:.1f} Marla"
    if measurement_system == 'imperial':
        sqft = area_sqm * _SQFT_PER_SQM
        return f"{sqft:,.0f} sqft"
    return f"{area_sqm:,.0f} m²"


# ─── Brand colours ────────────────────────────────────────────────────────────
BRAND_BLUE   = HexColor('#1B4F72')
GREEN        = HexColor('#27AE60')
RED          = HexColor('#E74C3C')
YELLOW       = HexColor('#F39C12')
LIGHT_GREY   = HexColor('#F2F3F4')
MID_GREY     = HexColor('#BDC3C7')
DARK_GREY    = HexColor('#566573')
WHITE        = colors.white
BLACK        = colors.black

PAGE_W, PAGE_H = A4
MARGIN = 2 * cm

# ─── Style helpers ────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    normal = base['Normal']
    return {
        'brand_title': ParagraphStyle(
            'brand_title',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=18,
            textColor=BRAND_BLUE,
            spaceAfter=2,
        ),
        'brand_subtitle': ParagraphStyle(
            'brand_subtitle',
            parent=normal,
            fontName='Helvetica',
            fontSize=10,
            textColor=DARK_GREY,
            spaceAfter=4,
        ),
        'section_heading': ParagraphStyle(
            'section_heading',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=13,
            textColor=BRAND_BLUE,
            spaceBefore=14,
            spaceAfter=6,
        ),
        'sub_heading': ParagraphStyle(
            'sub_heading',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=10,
            textColor=DARK_GREY,
            spaceBefore=8,
            spaceAfter=4,
        ),
        'body': ParagraphStyle(
            'body',
            parent=normal,
            fontName='Helvetica',
            fontSize=9,
            textColor=BLACK,
            spaceAfter=3,
            leading=13,
        ),
        'body_bold': ParagraphStyle(
            'body_bold',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=9,
            textColor=BLACK,
            spaceAfter=3,
        ),
        'small': ParagraphStyle(
            'small',
            parent=normal,
            fontName='Helvetica',
            fontSize=8,
            textColor=DARK_GREY,
            spaceAfter=2,
        ),
        'verdict_buy': ParagraphStyle(
            'verdict_buy',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=22,
            textColor=GREEN,
        ),
        'verdict_consider': ParagraphStyle(
            'verdict_consider',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=22,
            textColor=BRAND_BLUE,
        ),
        'verdict_caution': ParagraphStyle(
            'verdict_caution',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=22,
            textColor=YELLOW,
        ),
        'verdict_avoid': ParagraphStyle(
            'verdict_avoid',
            parent=normal,
            fontName='Helvetica-Bold',
            fontSize=22,
            textColor=RED,
        ),
        'disclaimer': ParagraphStyle(
            'disclaimer',
            parent=normal,
            fontName='Helvetica-Oblique',
            fontSize=8,
            textColor=DARK_GREY,
            leading=11,
        ),
    }


def _header(s, brand_context=None):
    """Return page header elements, optionally branded to an org."""
    import io as _io
    import urllib.request as _req
    from reportlab.platypus import Image as RLImage

    org_name = brand_context.org_name if brand_context else 'RealTron AI'
    hr_color = HexColor(brand_context.brand_color) if (brand_context and brand_context.brand_color) else BRAND_BLUE

    elements = []
    if brand_context and brand_context.logo_url:
        try:
            raw = _req.urlopen(brand_context.logo_url, timeout=5).read()
            img = RLImage(_io.BytesIO(raw), width=3 * cm, height=2 * cm, kind='proportional')
            elements.append(img)
        except Exception:
            pass

    elements += [
        Paragraph(org_name, s['brand_title']),
        Paragraph('Property Audit Report', s['brand_subtitle']),
        HRFlowable(width='100%', thickness=1.5, color=hr_color, spaceAfter=8),
    ]
    return elements


def _pkr(n):
    try:
        return f"PKR {int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _pct(n):
    try:
        return f"{float(n):.1f}%"
    except (TypeError, ValueError):
        return str(n)


def _liquidity_bar(score: int, total: int = 10) -> str:
    filled = max(0, min(score, total))
    return '█' * filled + '░' * (total - filled) + f'  {score}/{total}'


def _verdict_color(verdict: str):
    return {
        'BUY':      GREEN,
        'CONSIDER': BRAND_BLUE,
        'CAUTION':  YELLOW,
        'AVOID':    RED,
    }.get(verdict.upper(), BLACK)


def _risk_color(risk: int):
    if risk <= 3:
        return GREEN
    if risk <= 6:
        return YELLOW
    return RED


def _grade_color(grade: str):
    return {
        'A': GREEN,
        'B': BRAND_BLUE,
        'C': YELLOW,
        'D': RED,
    }.get(grade, BLACK)


def _status_color(status: str):
    return {
        'REQUIRED': RED,
        'VERIFY':   YELLOW,
        'OK':       GREEN,
    }.get(status.upper(), BLACK)


def _table_style(header_bg=BRAND_BLUE, row_alt=LIGHT_GREY):
    return TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  header_bg),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  WHITE),
        ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, 0),  9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, row_alt]),
        ('FONTNAME',     (0, 1), (-1, -1),  'Helvetica'),
        ('FONTSIZE',     (0, 1), (-1, -1),  9),
        ('ALIGN',        (1, 0), (-1, -1),  'RIGHT'),
        ('ALIGN',        (0, 0), (0, -1),   'LEFT'),
        ('VALIGN',       (0, 0), (-1, -1),  'MIDDLE'),
        ('GRID',         (0, 0), (-1, -1),  0.5, MID_GREY),
        ('TOPPADDING',   (0, 0), (-1, -1),  4),
        ('BOTTOMPADDING',(0, 0), (-1, -1),  4),
        ('LEFTPADDING',  (0, 0), (-1, -1),  6),
        ('RIGHTPADDING', (0, 0), (-1, -1),  6),
    ])


# ─── Page builders ────────────────────────────────────────────────────────────

def _page1_executive_summary(audit: dict, s: dict, measurement_system: str = 'pk_traditional', brand_context=None) -> list:
    """Page 1 — Executive Summary."""
    ov = audit.get('overview', {})
    sc = audit.get('scores', {})
    elements = _header(s, brand_context)

    today = date.today().strftime('%B %d, %Y')
    elements.append(Paragraph(f"Generated: {today}", s['small']))
    elements.append(Spacer(1, 8))

    # Property overview table
    area = _format_area_display(ov.get('area_sqm'), measurement_system)
    currency = ov.get('value_currency', 'PKR')
    est_val = ov.get('estimated_value') or ov.get('estimated_value_pkr') or 0
    val_display = f"{currency} {int(est_val):,}" if currency != 'PKR' else _pkr(est_val)
    ppm = _pkr(ov.get('price_per_marla')) if ov.get('price_per_marla') else 'N/A'
    prop_data = [
        ['Property Details', ''],
        ['City',            ov.get('city', 'N/A')],
        ['Location',        ov.get('location', 'N/A')],
        ['Type',            ov.get('property_type', 'N/A')],
        ['Area',            area],
        ['Estimated Value', val_display],
        ['Price / Marla',   ppm],
        ['Owner Name',      ov.get('owner_name', 'N/A') or 'N/A'],
    ]
    prop_tbl = Table(prop_data, colWidths=[4 * cm, 11 * cm])
    prop_tbl.setStyle(_table_style())
    elements.append(prop_tbl)
    elements.append(Spacer(1, 10))

    # Score badges (Risk, Grade, Liquidity) side by side
    risk = sc.get('risk_score', 5)
    grade = sc.get('investment_grade', 'C')
    liq = sc.get('liquidity_score', 5)

    badge_data = [
        [
            Paragraph(f'<font color="{_risk_color(risk).hexval()}" size="28"><b>{risk}/10</b></font>', s['body']),
            Paragraph(f'<font color="{_grade_color(grade).hexval()}" size="28"><b>{grade}</b></font>', s['body']),
            Paragraph(f'<font size="14"><b>{_liquidity_bar(liq)}</b></font>', s['body']),
        ],
        [
            Paragraph(f'<b>Risk Score</b><br/>{sc.get("risk_label", "")}', s['small']),
            Paragraph(f'<b>Investment Grade</b><br/>Excellent → Poor', s['small']),
            Paragraph(f'<b>Liquidity Score</b><br/>{sc.get("liquidity_label", "")}', s['small']),
        ],
    ]
    badge_tbl = Table(badge_data, colWidths=[5 * cm, 3 * cm, 7 * cm])
    badge_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, -1), LIGHT_GREY),
        ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX',          (0, 0), (-1, -1), 1, MID_GREY),
        ('INNERGRID',    (0, 0), (-1, -1), 0.5, MID_GREY),
        ('TOPPADDING',   (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 8),
    ]))
    elements.append(badge_tbl)
    elements.append(Spacer(1, 12))

    # Verdict box
    verdict = sc.get('verdict', 'N/A')
    v_color = _verdict_color(verdict)
    verdict_data = [
        [Paragraph(f'<font color="{v_color.hexval()}" size="26"><b>{verdict}</b></font>', s['body'])],
        [Paragraph(sc.get('verdict_reason', ''), s['body'])],
    ]
    verdict_tbl = Table(verdict_data, colWidths=[15 * cm])
    verdict_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, -1), LIGHT_GREY),
        ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
        ('BOX',          (0, 0), (-1, -1), 2, v_color),
        ('TOPPADDING',   (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 10),
        ('LEFTPADDING',  (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    elements.append(verdict_tbl)
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"Investment Grade: <b>{grade}</b> — {sc.get('investment_grade_label', '')}",
        s['body'],
    ))
    elements.append(PageBreak())
    return elements


def _page2_financial(audit: dict, s: dict) -> list:
    """Page 2 — Financial Analysis."""
    fa = audit.get('financial_analysis', {})
    elements = _header(s)
    elements.append(Paragraph('Financial Analysis', s['section_heading']))

    # Table 1: True Cost for Buyer
    elements.append(Paragraph('True Cost for Buyer', s['sub_heading']))
    tcb = fa.get('true_cost_buyer', {})
    cost_data = [
        ['Item',              'Amount'],
        ['Asking Price',      _pkr(tcb.get('asking_price', 0))],
        ['Stamp Duty (3%)',   _pkr(tcb.get('stamp_duty', 0))],
        ['Registration (1%)', _pkr(tcb.get('registration_fee', 0))],
        ['Legal Fees',        _pkr(tcb.get('legal_fees', 0))],
        ['Agent Commission',  _pkr(tcb.get('agent_commission', 0))],
        ['TOTAL COST',        _pkr(tcb.get('total', 0))],
    ]
    cost_tbl = Table(cost_data, colWidths=[9 * cm, 6 * cm])
    cost_tbl.setStyle(_table_style())
    # Bold the last row
    cost_tbl.setStyle(TableStyle([
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), BRAND_BLUE),
        ('TEXTCOLOR', (0, -1), (-1, -1), WHITE),
    ]))
    elements.append(cost_tbl)
    elements.append(Spacer(1, 8))

    # Table 2: Net-in-Hand for Seller
    elements.append(Paragraph('Net-in-Hand for Seller', s['sub_heading']))
    nis = fa.get('net_in_hand_seller', {})
    seller_data = [
        ['Item',                    'Amount'],
        ['Asking Price',             _pkr(nis.get('asking_price', 0))],
        ['Less: WHT (3%)',          f"-{_pkr(nis.get('withholding_tax', 0))}"],
        ['Less: Agent Commission',  f"-{_pkr(nis.get('agent_commission', 0))}"],
        ['Less: Legal Fees',        f"-{_pkr(nis.get('legal_fees', 0))}"],
        ['NET RECEIVED',             _pkr(nis.get('net', 0))],
    ]
    seller_tbl = Table(seller_data, colWidths=[9 * cm, 6 * cm])
    seller_tbl.setStyle(_table_style())
    seller_tbl.setStyle(TableStyle([
        ('FONTNAME',   (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), GREEN),
        ('TEXTCOLOR',  (0, -1), (-1, -1), WHITE),
    ]))
    elements.append(seller_tbl)
    elements.append(Spacer(1, 8))

    # Table 3: Tax Summary
    elements.append(Paragraph('Tax Summary', s['sub_heading']))
    tt = fa.get('tax_table', {})
    tax_data = [
        ['Tax Type',              'Filer',                     'Non-Filer'],
        ['7E Annual Tax',         _pkr(tt.get('7e_annual_filer', 0)),     _pkr(tt.get('7e_annual_nonfiler', 0))],
        ['CGT (1 year hold)',     _pkr(tt.get('cgt_1yr_filer', 0)),       'Higher rate'],
        ['CGT (2 years)',         _pkr(tt.get('cgt_2yr_filer', 0)),       'Higher rate'],
        ['CGT (3 years)',         _pkr(tt.get('cgt_3yr_filer', 0)),       'Higher rate'],
        ['CGT (4+ years)',        'PKR 0 (exempt)',                        'Varies'],
        ['WHT on Sale',           _pkr(tt.get('wht_filer', 0)),           _pkr(tt.get('wht_nonfiler', 0))],
        ['Stamp Duty (est.)',     _pkr(tt.get('stamp_duty_estimate', 0)), _pkr(tt.get('stamp_duty_estimate', 0))],
    ]
    tax_tbl = Table(tax_data, colWidths=[6 * cm, 4.5 * cm, 4.5 * cm])
    tax_tbl.setStyle(_table_style())
    elements.append(tax_tbl)
    elements.append(Spacer(1, 8))

    # Table 4: Rental Analysis
    elements.append(Paragraph('Rental Analysis', s['sub_heading']))
    ra = fa.get('rental_analysis', {})
    rental_data = [
        ['Metric',                      'Value'],
        ['Estimated Monthly Rent',       _pkr(ra.get('estimated_monthly_rent', 0))],
        ['Annual Rental Income',         _pkr(ra.get('annual_rental_income', 0))],
        ['Gross Yield',                  _pct(ra.get('gross_yield_pct', 0))],
        ['Rental Tax (15%)',             _pct(ra.get('rental_tax_pct', 15))],
        ['Net Yield (after tax)',        _pct(ra.get('net_yield_pct', 0))],
    ]
    rental_tbl = Table(rental_data, colWidths=[9 * cm, 6 * cm])
    rental_tbl.setStyle(_table_style())
    elements.append(rental_tbl)
    elements.append(PageBreak())
    return elements


def _page3_market(audit: dict, s: dict, measurement_system: str = 'pk_traditional') -> list:
    """Page 3 — Market Analysis."""
    ma = audit.get('market_analysis', {})
    fa = audit.get('financial_analysis', {})
    elements = _header(s)
    elements.append(Paragraph('Market Analysis', s['section_heading']))

    # Market comparison table
    pct_diff = ma.get('price_vs_market_pct', 0)
    pct_label = f"+{pct_diff:.1f}%" if pct_diff >= 0 else f"{pct_diff:.1f}%"
    pvs = ma.get('price_vs_market', 'N/A')
    pvs_color = GREEN if pvs == 'BELOW MARKET' else RED if pvs == 'ABOVE MARKET' else BRAND_BLUE

    mkt_data = [
        ['Metric',                  'Value'],
        ['Benchmark PPM (Min)',      _pkr(ma.get('benchmark_ppm_min', 0))],
        ['Benchmark PPM (Max)',      _pkr(ma.get('benchmark_ppm_max', 0))],
        ['Market Average PPM',       _pkr(ma.get('market_avg_ppm', 0))],
        ['Your Price / Marla',       _pkr(ma.get('your_ppm')) if ma.get('your_ppm') else 'N/A (area unknown)'],
        ['Price vs Market',          f"{pvs} ({pct_label})"],
        ['Est. Fair Value (Min)',     _pkr(ma.get('estimated_fair_value_min', 0))],
        ['Est. Fair Value (Max)',     _pkr(ma.get('estimated_fair_value_max', 0))],
        ['Area Approved',            'Yes' if ma.get('area_approved') is True
                                     else 'No' if ma.get('area_approved') is False
                                     else 'Unverified'],
    ]
    mkt_tbl = Table(mkt_data, colWidths=[9 * cm, 6 * cm])
    mkt_tbl.setStyle(_table_style())
    mkt_tbl.setStyle(TableStyle([
        ('TEXTCOLOR', (1, 6), (1, 6), pvs_color),
        ('FONTNAME',  (1, 6), (1, 6), 'Helvetica-Bold'),
    ]))
    elements.append(mkt_tbl)
    elements.append(Spacer(1, 10))

    # ROI Projections
    elements.append(Paragraph('ROI Projections', s['sub_heading']))
    roi = fa.get('roi_projections', {})
    roi_data = [
        ['Horizon', 'Projected Value', 'Capital Gain', 'Gain %'],
    ]
    for label, key in [('1 Year', '1_year'), ('3 Years', '3_year'), ('5 Years', '5_year')]:
        r = roi.get(key, {})
        roi_data.append([
            label,
            _pkr(r.get('value', 0)),
            _pkr(r.get('gain', 0)),
            _pct(r.get('gain_pct', 0)),
        ])
    roi_tbl = Table(roi_data, colWidths=[4 * cm, 5 * cm, 4 * cm, 2 * cm])
    roi_tbl.setStyle(_table_style())
    elements.append(roi_tbl)
    elements.append(Spacer(1, 10))

    # Comparable listings (if any)
    comps = ma.get('comparable_listings', [])
    if comps:
        elements.append(Paragraph('Comparable Listings', s['sub_heading']))
        comp_data = [['Title', 'Price', 'Area', 'Source']]
        for c in comps[:5]:
            comp_data.append([
                c.get('title', 'N/A')[:50],
                _pkr(c.get('price', 0)),
                _format_area_display(c.get('area_sqm'), measurement_system),
                c.get('source', 'N/A'),
            ])
        comp_tbl = Table(comp_data, colWidths=[6 * cm, 4 * cm, 3 * cm, 2 * cm])
        comp_tbl.setStyle(_table_style())
        elements.append(comp_tbl)
    else:
        elements.append(Paragraph(
            'No comparable listings available. Run a property search for live market data.',
            s['small'],
        ))

    elements.append(PageBreak())
    return elements


def _page4_legal(audit: dict, s: dict) -> list:
    """Page 4 — Legal & Due Diligence."""
    lc = audit.get('legal_checklist', {})
    elements = _header(s)
    elements.append(Paragraph('Legal & Due Diligence', s['section_heading']))
    elements.append(Paragraph(
        f"Registration Authority: <b>{lc.get('registration_authority', 'N/A')}</b>",
        s['body'],
    ))
    elements.append(Spacer(1, 8))

    # Checklist table
    elements.append(Paragraph('Due Diligence Checklist', s['sub_heading']))
    chk_data = [['#', 'Item', 'Status', 'Note']]
    for i, item in enumerate(lc.get('items', []), 1):
        status = item.get('status', 'VERIFY')
        chk_data.append([
            str(i),
            item.get('label', ''),
            status,
            item.get('note', ''),
        ])

    chk_tbl = Table(chk_data, colWidths=[0.6 * cm, 5.5 * cm, 2 * cm, 6.5 * cm])
    chk_style = _table_style()
    # Colour-code the status column per row
    for row_idx, item in enumerate(lc.get('items', []), 1):
        status = item.get('status', 'VERIFY')
        bg = _status_color(status)
        chk_style.add('BACKGROUND', (2, row_idx), (2, row_idx), bg)
        chk_style.add('TEXTCOLOR',  (2, row_idx), (2, row_idx), WHITE)
        chk_style.add('FONTNAME',   (2, row_idx), (2, row_idx), 'Helvetica-Bold')
    elements.append(chk_tbl)
    elements.append(Spacer(1, 10))

    # Required Documents
    elements.append(Paragraph('Documents Required — Buyer', s['sub_heading']))
    for doc in lc.get('required_documents_buyer', []):
        elements.append(Paragraph(f"•  {doc}", s['body']))
    elements.append(Spacer(1, 6))

    elements.append(Paragraph('Documents Required — Seller', s['sub_heading']))
    for doc in lc.get('required_documents_seller', []):
        elements.append(Paragraph(f"•  {doc}", s['body']))

    elements.append(PageBreak())
    return elements


def _page5_investment(audit: dict, s: dict) -> list:
    """Page 5 — Investment Intelligence."""
    fa = audit.get('financial_analysis', {})
    ma = audit.get('market_analysis', {})
    sc = audit.get('scores', {})
    ri = audit.get('role_insights', {}).get('developer', {})
    elements = _header(s)
    elements.append(Paragraph('Investment Intelligence', s['section_heading']))

    ra = fa.get('rental_analysis', {})
    gross = ra.get('gross_yield_pct', 0)
    net_y = ra.get('net_yield_pct', 0)
    nat_avg_low, nat_avg_high = 3.0, 5.0
    vs_nat = 'ABOVE NATIONAL AVERAGE' if gross > nat_avg_high else 'BELOW NATIONAL AVERAGE' if gross < nat_avg_low else 'IN LINE WITH MARKET'

    yield_data = [
        ['Metric',                         'Value'],
        ['This Property — Gross Yield',     _pct(gross)],
        ['This Property — Net Yield',       _pct(net_y)],
        ['Pakistan National Avg Yield',     '3.0% – 5.0%'],
        ['Yield vs National Average',        vs_nat],
        ['Monthly Rent Estimate',           _pkr(ra.get('estimated_monthly_rent', 0))],
        ['Annual Rental Income',            _pkr(ra.get('annual_rental_income', 0))],
    ]
    yield_tbl = Table(yield_data, colWidths=[9 * cm, 6 * cm])
    yield_tbl.setStyle(_table_style())
    elements.append(yield_tbl)
    elements.append(Spacer(1, 10))

    # Development potential
    elements.append(Paragraph('Development Potential', s['sub_heading']))
    dev_data = [
        ['Attribute',              'Detail'],
        ['Development Potential',  ri.get('development_potential', 'N/A')],
        ['Permissible Floors',     ri.get('permissible_floors', 'N/A')],
        ['Rental Yield',           ri.get('estimated_rental_yield', 'N/A')],
        ['Investment Grade Note',  ri.get('investment_grade_note', 'N/A')],
    ]
    dev_tbl = Table(dev_data, colWidths=[5 * cm, 10 * cm])
    dev_tbl.setStyle(_table_style())
    elements.append(dev_tbl)
    elements.append(Spacer(1, 10))

    # 5-year value projection
    elements.append(Paragraph('5-Year Value Projection', s['sub_heading']))
    roi = fa.get('roi_projections', {})
    proj_data = [
        ['Year', 'Projected Value', 'Total Gain', 'Gain %'],
        ['Today (Year 0)', _pkr(fa.get('estimated_value_pkr', 0)), 'PKR 0', '0.0%'],
    ]
    for label, key in [('Year 1', '1_year'), ('Year 3', '3_year'), ('Year 5', '5_year')]:
        r = roi.get(key, {})
        proj_data.append([
            label,
            _pkr(r.get('value', 0)),
            _pkr(r.get('gain', 0)),
            _pct(r.get('gain_pct', 0)),
        ])
    proj_tbl = Table(proj_data, colWidths=[3 * cm, 5 * cm, 4 * cm, 3 * cm])
    proj_tbl.setStyle(_table_style())
    elements.append(proj_tbl)
    elements.append(Spacer(1, 10))

    # Liquidity comparison
    liq = sc.get('liquidity_score', 5)
    elements.append(Paragraph('Liquidity Summary', s['sub_heading']))
    liq_data = [
        ['Metric',                    'Value'],
        ['Liquidity Score',            f"{liq}/10"],
        ['Liquidity Category',         sc.get('liquidity_label', 'N/A')],
        ['Est. Months to Sell',        'N/A — see area benchmark'],
        ['Area Approval Status',       'Approved' if ma.get('area_approved') is True
                                       else 'Not Approved' if ma.get('area_approved') is False
                                       else 'Unverified'],
    ]
    liq_tbl = Table(liq_data, colWidths=[9 * cm, 6 * cm])
    liq_tbl.setStyle(_table_style())
    elements.append(liq_tbl)
    elements.append(PageBreak())
    return elements


def _page6_roles(audit: dict, s: dict) -> list:
    """Page 6 — Role-Specific Insights."""
    ri = audit.get('role_insights', {})
    elements = _header(s)
    elements.append(Paragraph('Role-Specific Insights', s['section_heading']))

    roles = [
        ('Buyer',     ri.get('buyer',     {}),    BRAND_BLUE),
        ('Seller',    ri.get('seller',    {}),    GREEN),
        ('Agent',     ri.get('agent',     {}),    YELLOW),
        ('Developer', ri.get('developer', {}),    RED),
    ]

    for role_name, data, color in roles:
        elements.append(Paragraph(f"{role_name} Insights", s['sub_heading']))
        box_rows = []

        if role_name == 'Buyer':
            box_rows = [
                ('Summary',           data.get('summary', '')),
                ('Negotiation Tip',   data.get('negotiation_tip', '')),
            ]
            for action in data.get('action_items', []):
                box_rows.append(('', f"•  {action}"))

        elif role_name == 'Seller':
            box_rows = [
                ('Summary',               data.get('summary', '')),
                ('Optimal Asking Price',  data.get('optimal_asking_price', '')),
                ('Best Time to Sell',     data.get('best_time_to_sell', '')),
            ]
            for action in data.get('action_items', []):
                box_rows.append(('', f"•  {action}"))

        elif role_name == 'Agent':
            box_rows = [
                ('Commission',           _pkr(data.get('commission_pkr', 0))),
                ('Pitch',                data.get('pitch', '')),
                ('Deal Readiness',       data.get('deal_readiness', '')),
                ('Buyer Income Needed',  data.get('buyer_income_needed', '')),
            ]

        elif role_name == 'Developer':
            box_rows = [
                ('Development Potential', data.get('development_potential', '')),
                ('Permissible Floors',    data.get('permissible_floors', '')),
                ('Est. Rental Yield',     data.get('estimated_rental_yield', '')),
                ('Investment Note',       data.get('investment_grade_note', '')),
            ]

        if box_rows:
            tbl_data = [[Paragraph(f"<b>{k}</b>" if k else '', s['body']),
                         Paragraph(str(v), s['body'])] for k, v in box_rows]
            role_tbl = Table(tbl_data, colWidths=[4 * cm, 11 * cm])
            role_tbl.setStyle(TableStyle([
                ('BACKGROUND',   (0, 0), (-1, -1), LIGHT_GREY),
                ('BOX',          (0, 0), (-1, -1), 1.5, color),
                ('INNERGRID',    (0, 0), (-1, -1), 0.3, MID_GREY),
                ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING',   (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
                ('LEFTPADDING',  (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ]))
            elements.append(role_tbl)
        elements.append(Spacer(1, 8))

    elements.append(PageBreak())
    return elements


def _page7_recommendations(audit: dict, s: dict) -> list:
    """Page 7 — Recommendations & Verdict."""
    rec = audit.get('recommendations', {})
    sc  = audit.get('scores', {})
    fi  = audit.get('fraud_indicators', {})
    elements = _header(s)
    elements.append(Paragraph('Recommendations', s['section_heading']))

    verdict = rec.get('verdict', sc.get('verdict', 'N/A'))
    v_color = _verdict_color(verdict)
    elements.append(Paragraph(
        f'<font color="{v_color.hexval()}" size="24"><b>VERDICT: {verdict}</b></font>',
        s['body'],
    ))
    elements.append(Spacer(1, 8))

    # Top 3 Actions
    elements.append(Paragraph('Top 3 Actions', s['sub_heading']))
    for i, action in enumerate(rec.get('top_3_actions', []), 1):
        elements.append(Paragraph(f"<b>{i}.</b>  {action}", s['body']))
    elements.append(Spacer(1, 8))

    # Green Flags
    green_flags = rec.get('green_flags', [])
    if green_flags:
        elements.append(Paragraph('Green Flags', s['sub_heading']))
        for flag in green_flags:
            elements.append(Paragraph(
                f'<font color="{GREEN.hexval()}">✓</font>  {flag}',
                s['body'],
            ))
        elements.append(Spacer(1, 6))

    # Red Flags
    red_flags = rec.get('red_flags', [])
    if red_flags:
        elements.append(Paragraph('Red Flags', s['sub_heading']))
        for flag in red_flags:
            elements.append(Paragraph(
                f'<font color="{RED.hexval()}">⚠</font>  {flag}',
                s['body'],
            ))
        elements.append(Spacer(1, 6))

    # Fraud summary
    fraud_risk = fi.get('risk', 'low')
    fraud_score = fi.get('score', 0)
    f_color = RED if fraud_risk == 'high' else YELLOW if fraud_risk == 'medium' else GREEN
    elements.append(Paragraph('Fraud Risk Assessment', s['sub_heading']))
    elements.append(Paragraph(
        f'<font color="{f_color.hexval()}"><b>Fraud Risk: {fraud_risk.upper()} (Score: {fraud_score}/100)</b></font>',
        s['body'],
    ))
    for flag in fi.get('flags', []):
        elements.append(Paragraph(f"⚠  {flag}", s['body']))

    elements.append(Spacer(1, 16))
    elements.append(HRFlowable(width='100%', thickness=0.8, color=MID_GREY))
    elements.append(Spacer(1, 6))

    # Disclaimer
    elements.append(Paragraph(
        "DISCLAIMER: This report is generated by AI using publicly available benchmarks and provided information. "
        "It does not constitute legal, financial, or investment advice. "
        "Consult a registered property lawyer and Chartered Accountant before making any real estate decisions. "
        "RealTron AI is not liable for any decisions made based on this report.",
        s['disclaimer'],
    ))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"RealTron AI  |  Property Audit Report  |  Generated {date.today().strftime('%d %B %Y')}",
        s['small'],
    ))
    return elements


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_audit_pdf(audit_data: dict, output_path: str, measurement_system: str = 'pk_traditional') -> str:
    """
    Generate a multi-page PDF audit report from a structured audit_data dict.

    Args:
        audit_data: The dict returned by AuditEngine.run()
        output_path: Absolute file path to write the PDF (must end in .pdf)
        measurement_system: 'pk_traditional' | 'imperial' | 'metric'

    Returns:
        The output_path string on success.
    """
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title='RealTron AI Property Audit Report',
        author='RealTron AI',
    )

    s = _styles()
    story = []

    story += _page1_executive_summary(audit_data, s, measurement_system)
    story += _page2_financial(audit_data, s)
    story += _page3_market(audit_data, s, measurement_system)
    story += _page4_legal(audit_data, s)
    story += _page5_investment(audit_data, s)
    story += _page6_roles(audit_data, s)
    story += _page7_recommendations(audit_data, s)

    doc.build(story)
    return output_path


def generate_audit_pdf_bytes(
    audit_data: dict,
    brand_context: 'OrgBrandContext | None' = None,
    measurement_system: str = 'pk_traditional',
) -> bytes:
    """
    Generate a multi-page PDF audit report and return raw bytes.

    Args:
        audit_data:        The dict returned by AuditEngine.run()
        brand_context:     Optional OrgBrandContext for org logo/name/color
        measurement_system: 'pk_traditional' | 'imperial' | 'metric'
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title='Property Audit Report',
        author=brand_context.org_name if brand_context else 'RealTron AI',
    )
    ms = brand_context.measurement_system if brand_context else measurement_system
    s = _styles()
    story = (
        _page1_executive_summary(audit_data, s, ms, brand_context)
        + _page2_financial(audit_data, s)
        + _page3_market(audit_data, s, ms)
        + _page4_legal(audit_data, s)
        + _page5_investment(audit_data, s)
        + _page6_roles(audit_data, s)
        + _page7_recommendations(audit_data, s)
    )
    doc.build(story)
    return buf.getvalue()
