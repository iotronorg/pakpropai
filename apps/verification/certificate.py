import io
import logging

logger = logging.getLogger(__name__)


def generate_trust_certificate(verification, qr_url: str = '') -> bytes:
    """
    Render a branded PDF trust certificate for a passed Verification.
    qr_url: if provided, a QR code pointing to this URL is embedded in the PDF.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, Image,
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
                            textColor=BRAND_BLUE, fontSize=20, spaceAfter=6)
    H2     = ParagraphStyle('H2', parent=styles['Heading2'],
                            textColor=BRAND_BLUE, fontSize=13, spaceAfter=4)
    BODY   = styles['BodyText']
    SMALL  = ParagraphStyle('Small', parent=BODY, fontSize=8,
                            textColor=HexColor('#666666'))
    GREEN_STYLE = ParagraphStyle('Green', parent=BODY, textColor=GREEN,
                                 fontName='Helvetica-Bold')

    prop     = verification.property
    org      = getattr(prop, 'organization', None)
    org_name = org.name if org else 'RealTron AI'

    verified_date = (
        verification.verified_at.strftime('%d %B %Y')
        if verification.verified_at else 'N/A'
    )
    reviewer_name = (
        verification.reviewer.name or verification.reviewer.phone
        if verification.reviewer else 'RealTron AI System'
    )
    score = verification.signal_score if verification.signal_score is not None else '—'
    fraud_flags = verification.fraud_flags or []

    story = [
        Paragraph('RealTron AI', ParagraphStyle('Brand', parent=H1, fontSize=10,
                                                 textColor=HexColor('#888888'))),
        Paragraph('Trust Certificate', H1),
        HRFlowable(width='100%', color=BRAND_BLUE, thickness=2),
        Spacer(1, 0.2 * inch),
        Paragraph(
            'This certificate confirms that the property listed below has passed '
            'the RealTron AI verification process.',
            BODY,
        ),
        Spacer(1, 0.2 * inch),

        # Property details
        Paragraph('Property', H2),
        _kv_table([
            ('Title',    prop.title),
            ('City',     getattr(prop, 'city', '—')),
            ('Type',     getattr(prop, 'property_type', '—')),
            ('Status',   'Verified'),
        ]),
        Spacer(1, 0.2 * inch),

        # Verification details
        Paragraph('Verification', H2),
        _kv_table([
            ('Date Verified', verified_date),
            ('Signal Score',  score),
            ('Reviewed By',   reviewer_name),
            ('Issued By',     org_name),
        ]),
        Spacer(1, 0.2 * inch),

        # Trust summary
        Paragraph('Trust Summary', H2),
    ]

    if not fraud_flags:
        story.append(Paragraph('✓ Zero fraud flags detected.', GREEN_STYLE))
    else:
        story.append(Paragraph(f'⚠ {len(fraud_flags)} flag(s) noted during review.', BODY))
        for flag in fraud_flags:
            story.append(Paragraph(f'  • {flag}', BODY))

    story.append(Spacer(1, 0.25 * inch))

    # QR code
    if qr_url:
        qr_img = _make_qr_image(qr_url)
        if qr_img:
            story += [
                Paragraph('Scan to verify', H2),
                qr_img,
                Paragraph('Scan the QR code to open this certificate.', SMALL),
                Spacer(1, 0.2 * inch),
            ]

    story += [
        HRFlowable(width='100%', color=HexColor('#BDC3C7'), thickness=0.5),
        Paragraph(
            f'Issued by {org_name} via RealTron AI. '
            'This certificate is for informational purposes only and does not '
            'constitute a legal guarantee of title or ownership.',
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
        ('FONTNAME',   (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('GRID',       (0, 0), (-1, -1), 0.5, HexColor('#BDC3C7')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [None, GREY]),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t


def _make_qr_image(url: str):
    try:
        import qrcode
        from reportlab.lib.units import inch
        from reportlab.platypus import Image

        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        pil_img = qr.make_image(fill_color='black', back_color='white')

        buf = io.BytesIO()
        pil_img.save(buf, format='PNG')
        buf.seek(0)
        return Image(buf, width=1.5 * inch, height=1.5 * inch)
    except Exception as exc:
        logger.warning(f'QR code generation failed: {exc}')
        return None
