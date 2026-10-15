"""Professional, print-ready terminal report PDF rendering (reportlab).

This module turns the assembled :func:`~apps.academics.services.reports.\
terminal_report_data` payload into a genuine school report card: a centred
school crest, a subtle crest watermark behind every page, a neat student
information block, a wrapping results table that repeats its headings across
pages, an attendance/performance summary, the reconciled fee figures, comments
and signature areas, with page numbering and consistent A4 margins.

Design rules
------------
* All figures come straight from the academic services and the existing fees
  ledger; nothing here recomputes a grade or invents a financial value.
* The school crest is optional. When it is missing, unreadable or an
  unsupported format it is skipped and a drawn monogram seal is used instead,
  so PDF generation never fails because of a bad image.
* Only the *selected student's* records for the selected school/period are
  read, preserving the school-isolation guarantees of the rest of the module.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from django.contrib.staticfiles import finders
from django.utils import timezone

logger = logging.getLogger('apps.academics')

# --------------------------------------------------------------------------- #
# Palette & layout constants
# --------------------------------------------------------------------------- #
NAVY = '#1f3a5f'
NAVY_LIGHT = '#2f5688'
GOLD = '#b8912a'
GREY = '#5b6470'
LIGHT = '#eef2f7'
ROW_ALT = '#f6f8fb'
BORDER = '#c3ccd8'
INK = '#1a1a1a'

PAGE = 'A4'
MARGIN_X_MM = 14.0
CONTENT_WIDTH_MM = 182.0
TOP_MARGIN_MM = 16.0
BOTTOM_MARGIN_MM = 15.0


def _mm(value):
    from reportlab.lib.units import mm
    return value * mm


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
def _esc(value):
    """Escape text for a reportlab ``Paragraph`` (which parses light XML)."""
    if value is None:
        return ''
    text = str(value)
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _num(value):
    """Render a number without trailing zeros, or an em dash when absent."""
    if value is None:
        return '—'
    try:
        return format(Decimal(value).normalize(), 'f')
    except Exception:  # pragma: no cover - defensive
        return str(value)


def _quantize(value):
    if value is None:
        return None
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Crest / logo resolution (never raises)
# --------------------------------------------------------------------------- #
def _logo_source(school):
    """Return a filesystem path to the school's crest, or ``None``.

    Resolution order: an uploaded ``School.logo`` (or any ``logo`` attribute),
    then a conventional static asset ``schools/<code>.<ext>``. Any error simply
    yields ``None`` so the report falls back to a monogram seal.
    """
    if school is None:
        return None

    field = getattr(school, 'logo', None)
    if field:
        try:
            path = field.path
        except Exception:
            path = None
        if path and _is_readable_image(path):
            return path

    code = (getattr(school, 'code', '') or '').strip().lower()
    if code:
        for ext in ('png', 'jpg', 'jpeg', 'webp'):
            found = finders.find(f'schools/{code}.{ext}')
            if found and _is_readable_image(found):
                return found
    return None


def _is_readable_image(path):
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return False
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        logger.warning('Ignoring unreadable school logo: %s', path)
        return False


def _normalised_png(path, max_px=900):
    """Load ``path`` with Pillow, trim to a sane resolution and return PNG bytes.

    Re-encoding to PNG guarantees reportlab can decode the result regardless of
    the original format (e.g. WebP).
    """
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert('RGBA')
        width, height = img.size
        if not width or not height:
            return None
        scale = min(1.0, max_px / float(max(width, height)))
        if scale < 1.0:
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, 'PNG')
        buffer.seek(0)
        return buffer


def _faded_png(path, alpha=0.08):
    """A pale, white-composited copy of the crest used for the watermark."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert('RGBA')
        width, height = img.size
        scale = min(1.0, 800 / float(max(width, height)))
        if scale < 1.0:
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS)
        alpha_channel = img.split()[3].point(lambda p: int(p * alpha))
        img.putalpha(alpha_channel)
        canvas_img = Image.new('RGBA', img.size, (255, 255, 255, 255))
        canvas_img.alpha_composite(img)
        out = canvas_img.convert('RGB')
        buffer = BytesIO()
        out.save(buffer, 'PNG')
        buffer.seek(0)
        return buffer


# --------------------------------------------------------------------------- #
# Data shaping
# --------------------------------------------------------------------------- #
def _class_teacher_name(student, academic_year, term):
    """The single teacher assigned to the class, or ``None`` when ambiguous."""
    from ..models import TeacherAssignment
    from django.db.models import Q

    teacher_ids = list(
        TeacherAssignment.objects.filter(
            school_class=student.school_class,
            academic_year=academic_year,
            is_active=True,
        ).filter(Q(term=term) | Q(term__isnull=True))
        .values_list('teacher_id', flat=True)
        .distinct())
    if len(teacher_ids) != 1:
        return None
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=teacher_ids[0]).first()
    if user is None:
        return None
    return user.get_full_name() or user.username


def _overall_position(student, academic_year, term, settings):
    """The student's class position when the school enables positions."""
    if settings is None:
        return None
    if not (getattr(settings, 'show_position', False)
            and getattr(settings, 'show_position_on_report', False)):
        return None
    try:
        from . import results as results_service

        rows, _subjects = results_service.class_results(
            student.school_class, academic_year, term, students=None)
    except Exception:  # pragma: no cover - defensive
        return None
    for row in rows:
        if row['student'].pk == student.pk:
            return row.get('position')
    return None


def _report_reference(student, academic_year, term):
    """The official (or latest non-superseded) report for date/reference."""
    from ..models import TerminalReport

    base = TerminalReport.objects.filter(
        student=student, academic_year=academic_year, term=term)
    official = base.filter(
        status=TerminalReport.Status.PUBLISHED,
        superseded_by__isnull=True).order_by('-version', '-created_at').first()
    return official or base.exclude(
        status=TerminalReport.Status.SUPERSEDED).order_by(
        '-version', '-created_at').first()


def _type_percentage(result, exam):
    """Percentage for the continuous-assessment or examination components."""
    components = [
        c for c in result.get('components', [])
        if (getattr(c['assessment'], 'assessment_type', None) == 'EXAM') == exam
    ]
    if not components:
        return None
    countable = [c for c in components if c['countable']]
    total_weight = sum((c['weight'] for c in countable), Decimal('0'))
    if total_weight > 0:
        awarded = sum((c['weighted_award'] for c in countable), Decimal('0'))
        return (awarded / total_weight * 100).quantize(
            Decimal('0.1'), rounding=ROUND_HALF_UP)
    ratios = [c['ratio'] for c in countable if c['ratio'] is not None]
    if not ratios:
        return None
    return (sum(ratios) / len(ratios) * 100).quantize(
        Decimal('0.1'), rounding=ROUND_HALF_UP)


def _padding(size=6):
    return [
        ('LEFTPADDING', (0, 0), (-1, -1), size),
        ('RIGHTPADDING', (0, 0), (-1, -1), size),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]


# --------------------------------------------------------------------------- #
# Page furniture (watermark, border, footer)
# --------------------------------------------------------------------------- #
def _draw_monogram(c, cx, cy, radius, initials, *, font_size, fill, stroke):
    """Draw a simple circular seal with the school's initials."""
    c.saveState()
    try:
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(1.2)
        c.circle(cx, cy, radius, stroke=1, fill=1)
        c.setFillColor(stroke)
        c.setFont('Helvetica-Bold', font_size)
        c.drawCentredString(cx, cy - font_size * 0.35, initials)
    finally:
        c.restoreState()


def _initials(school):
    if school is None:
        return 'Z'
    name = (getattr(school, 'short_name', '') or getattr(school, 'name', '') or '')
    parts = [p for p in name.replace('–', ' ').replace('-', ' ').split() if p]
    if not parts:
        return 'Z'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _make_page_furniture(school, logo_path, qr=None):
    """Return an ``onPage`` callback that paints the watermark/border/footer."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader

    page_w, page_h = A4
    watermark_reader = None
    if logo_path:
        try:
            faded = _faded_png(logo_path)
            if faded is not None:
                watermark_reader = ImageReader(faded)
        except Exception:
            logger.warning('Could not build logo watermark', exc_info=True)

    school_name = (getattr(school, 'name', '') if school else '') or ''
    footer_left = school_name[:70]

    def draw(c, doc):
        c.saveState()
        # Watermark first, so all text sits on top of it.
        if watermark_reader is not None:
            try:
                iw, ih = watermark_reader.getSize()
                target_w = _mm(120)
                target_h = target_w * ih / iw
                if target_h > _mm(120):
                    target_h = _mm(120)
                    target_w = target_h * iw / ih
                c.drawImage(
                    watermark_reader,
                    (page_w - target_w) / 2.0,
                    (page_h - target_h) / 2.0,
                    width=target_w, height=target_h, mask='auto')
            except Exception:  # pragma: no cover - defensive
                logger.warning('Watermark draw failed', exc_info=True)
        else:
            _draw_monogram(
                c, page_w / 2.0, page_h / 2.0, _mm(38),
                _initials(school), font_size=90,
                fill=colors.HexColor('#f2f5f9'),
                stroke=colors.HexColor('#e6ebf2'))

        # Restrained double border inside the printable area.
        c.setStrokeColor(colors.HexColor(BORDER))
        c.setLineWidth(0.6)
        c.rect(_mm(8), _mm(8), page_w - _mm(16), page_h - _mm(16))
        c.setStrokeColor(colors.HexColor(GOLD))
        c.setLineWidth(0.4)
        c.rect(_mm(9), _mm(9), page_w - _mm(18), page_h - _mm(18))

        # Footer line + text (page number is stamped by the canvas).
        c.setStrokeColor(colors.HexColor(BORDER))
        c.setLineWidth(0.5)
        c.line(_mm(14), _mm(13), page_w - _mm(14), _mm(13))
        c.setFont('Helvetica', 7)
        c.setFillColor(colors.HexColor(GREY))
        c.drawString(_mm(14), _mm(9.5), footer_left)
        c.drawRightString(page_w - _mm(14), _mm(9.5), 'Official school document')
        c.restoreState()

    return draw


def _make_canvas(school):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    school_name = (getattr(school, 'name', '') if school else '') or ''

    class _NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):  # noqa: N802 - reportlab API
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._stamp_page_number(total)
                super().showPage()
            super().save()

        def _stamp_page_number(self, total):
            page_w = A4[0]
            self.saveState()
            self.setFont('Helvetica', 7)
            self.setFillColor(colors.HexColor(GREY))
            self.drawCentredString(
                page_w / 2.0, _mm(9.5), f'Page {self._pageNumber} of {total}')
            self.restoreState()

    return _NumberedCanvas


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _section_title(title, styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(
        [[_paragraph(_esc(title.upper()), styles['section'])]],
        colWidths=[_mm(CONTENT_WIDTH_MM)])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(NAVY)),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
    ]))
    return table


def _paragraph(text, style):
    from reportlab.platypus import Paragraph
    return Paragraph(text, style)


def _student_info_table(data, styles, *, class_teacher, report_date):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    student = data['student']
    year = data['academic_year']
    term = data['term']

    def label(text):
        return _paragraph(_esc(text), styles['label'])

    def value(text):
        return _paragraph(_esc(text), styles['value'])

    rows = [
        [label('Student Name'), value(student.get_full_name()),
         label('Admission No.'), value(student.student_id)],
        [label('Class'), value(student.school_class.name if student.school_class_id else '—'),
         label('Academic Year'), value(year.name)],
        [label('Term'), value(term.name),
         label('Report Date'), value(report_date)],
        [label('Class Teacher'), value(class_teacher or '—'),
         label('Status'), value('Official' if data.get('official') else 'For review')],
    ]
    table = Table(rows, colWidths=[_mm(26), _mm(65), _mm(26), _mm(65)])
    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor(LIGHT)),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor(LIGHT)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]
    table.setStyle(TableStyle(style))
    return table


def _results_table(data, styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    results = data['results']['subjects']
    header = [
        _paragraph('#', styles['head']),
        _paragraph('SUBJECT', styles['head']),
        _paragraph('C.A. %', styles['head']),
        _paragraph('EXAM %', styles['head']),
        _paragraph('TOTAL', styles['head']),
        _paragraph('GRADE', styles['head']),
        _paragraph('REMARK', styles['head']),
    ]
    rows = [header]
    for index, result in enumerate(results, start=1):
        remark = result.get('remark') or result.get('grade_description') or ''
        if result.get('incomplete'):
            remark = (remark + ' (incomplete)').strip()
        elif result.get('has_absent'):
            remark = (remark + ' (absent)').strip()
        rows.append([
            _paragraph(str(index), styles['cell_c']),
            _paragraph(_esc(result['subject'].name), styles['cell']),
            _paragraph(_num(_type_percentage(result, exam=False)), styles['cell_c']),
            _paragraph(_num(_type_percentage(result, exam=True)), styles['cell_c']),
            _paragraph(_num(result.get('percentage')), styles['cell_c']),
            _paragraph(_esc(result.get('grade') or '—'), styles['cell_c']),
            _paragraph(_esc(remark or '—'), styles['cell']),
        ])

    if len(rows) == 1:
        rows.append([
            _paragraph('', styles['cell']), _paragraph('No subjects configured.', styles['cell']),
            _paragraph('', styles['cell']), _paragraph('', styles['cell']),
            _paragraph('', styles['cell']), _paragraph('', styles['cell']),
            _paragraph('', styles['cell']),
        ])

    widths = [_mm(8), _mm(48), _mm(20), _mm(20), _mm(20), _mm(16), _mm(50)]
    table = Table(rows, colWidths=widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(NAVY)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor(ROW_ALT)))
    table.setStyle(TableStyle(style))
    return table


def _kv_summary(rows, col_widths):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=col_widths)
    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]
    for col in range(0, len(col_widths), 2):
        style.append(('BACKGROUND', (col, 0), (col, -1), colors.HexColor(LIGHT)))
    table.setStyle(TableStyle(style))
    return table


def _attendance_summary(data, styles):
    attendance = data['attendance'] or {}
    if not attendance.get('total'):
        return None
    pairs = [
        ('Days Present', _num(attendance.get('present'))),
        ('Days Absent', _num(attendance.get('absent'))),
        ('Late', _num(attendance.get('late'))),
        ('Excused', _num(attendance.get('excused'))),
        ('Attendance', f"{_num(attendance.get('percentage'))}%"
         if attendance.get('percentage') is not None else '—'),
    ]
    rows = [[]]
    for index, (label, value) in enumerate(pairs):
        rows[0].extend([_paragraph(_esc(label), styles['label']),
                        _paragraph(_esc(value), styles['value'])])
    widths = [_mm(22), _mm(15)] * len(pairs)
    return _kv_summary(rows, widths)


def _comment_box(title, text, styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    inner = _paragraph(
        f'<b>{_esc(title)}</b><br/>{_esc(text)}', styles['comment'])
    table = Table([[inner]], colWidths=[_mm(CONTENT_WIDTH_MM)])
    table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor(BORDER)),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafbfc')),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _fees_table(data, styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    rows = [[_paragraph('ITEM', styles['head']),
             _paragraph('AMOUNT', styles['head'])]]
    for field in data['report_fields']:
        if field.get('value') is not None:
            shown = field.get('formatted', '—')
        elif field.get('flag'):
            shown = field['flag']
        else:
            shown = '—'
        rows.append([
            _paragraph(_esc(field.get('label', '')), styles['cell']),
            _paragraph(_esc(shown), styles['cell_r']),
        ])
    table = Table(rows, colWidths=[_mm(120), _mm(62)], repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(NAVY_LIGHT)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor(ROW_ALT)))
    table.setStyle(TableStyle(style))
    return table


def _signature_block(date_text, styles):
    from reportlab.lib import colors
    from reportlab.platypus import KeepTogether, Spacer, Table, TableStyle

    signature = Table(
        [['', ''],
         [_paragraph("Class Teacher's Signature", styles['small_c']),
          _paragraph("Head Teacher's Signature", styles['small_c'])]],
        colWidths=[_mm(88), _mm(94)])
    signature.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (0, 0), 0.8, colors.HexColor(INK)),
        ('LINEBELOW', (1, 0), (1, 0), 0.8, colors.HexColor(INK)),
        ('TOPPADDING', (0, 0), (-1, 0), 0),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 16),
        ('TOPPADDING', (0, 1), (-1, 1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))

    stamp = Table(
        [[_paragraph(f'Report Date: <b>{_esc(date_text)}</b>', styles['small']),
          _paragraph('School Stamp', styles['small_c'])]],
        colWidths=[_mm(120), _mm(62)])
    stamp.setStyle(TableStyle([
        ('BOX', (1, 0), (1, 0), 0.6, colors.HexColor(BORDER)),
        ('TOPPADDING', (1, 0), (1, 0), 10),
        ('BOTTOMPADDING', (1, 0), (1, 0), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    return KeepTogether([signature, Spacer(1, 6), stamp])


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
def _build_styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    def style(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        'school': style('school', fontName='Helvetica-Bold', fontSize=15,
                        leading=17, alignment=TA_CENTER, textColor=colors.HexColor(NAVY)),
        'school_meta': style('school_meta', fontName='Helvetica', fontSize=8.5,
                             leading=11, alignment=TA_CENTER,
                             textColor=colors.HexColor(GREY)),
        'title': style('title', fontName='Helvetica-Bold', fontSize=12.5,
                       leading=15, alignment=TA_CENTER, textColor=colors.HexColor(NAVY)),
        'period': style('period', fontName='Helvetica-Bold', fontSize=9.5,
                        leading=12, alignment=TA_CENTER, textColor=colors.HexColor(GOLD)),
        'section': style('section', fontName='Helvetica-Bold', fontSize=9.5,
                         leading=11.5, textColor=colors.white, spaceBefore=0, spaceAfter=0),
        'label': style('label', fontName='Helvetica-Bold', fontSize=8,
                       leading=10, textColor=colors.HexColor(GREY)),
        'value': style('value', fontName='Helvetica', fontSize=9.5, leading=12),
        'head': style('head', fontName='Helvetica-Bold', fontSize=8,
                      leading=10, alignment=TA_CENTER, textColor=colors.white),
        'cell': style('cell', fontName='Helvetica', fontSize=8.5, leading=10.5,
                      alignment=TA_LEFT),
        'cell_c': style('cell_c', fontName='Helvetica', fontSize=8.5,
                        leading=10.5, alignment=TA_CENTER),
        'cell_r': style('cell_r', fontName='Helvetica', fontSize=8.5,
                        leading=10.5, alignment=TA_RIGHT),
        'comment': style('comment', fontName='Helvetica', fontSize=9.5, leading=12.5,
                         alignment=TA_LEFT),
        'small': style('small', fontName='Helvetica', fontSize=7.5, leading=9.5,
                       textColor=colors.HexColor(GREY)),
        'small_c': style('small_c', fontName='Helvetica', fontSize=7.5, leading=9.5,
                         alignment=TA_CENTER, textColor=colors.HexColor(GREY)),
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def render_terminal_report_pdf(data):
    """Render ``terminal_report_data`` to PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Image, Spacer
    from reportlab.platypus import SimpleDocTemplate

    styles = _build_styles()
    school = data.get('school')
    student = data['student']
    settings = data.get('settings')
    content_width = _mm(CONTENT_WIDTH_MM)

    report = _report_reference(student, data['academic_year'], data['term'])
    if report is not None and (report.published_at or report.generated_at):
        stamp = report.published_at or report.generated_at
        report_date = timezone.localtime(stamp).strftime('%d %b %Y')
    else:
        report_date = timezone.localdate().strftime('%d %b %Y')
    class_teacher = _class_teacher_name(
        student, data['academic_year'], data['term'])

    logo_path = _logo_source(school)

    story = []

    # ----- Header -------------------------------------------------------- #
    if logo_path:
        try:
            png = _normalised_png(logo_path)
            if png is not None:
                from PIL import Image as PILImage
                with PILImage.open(png) as im:
                    px_w, px_h = im.size
                max_w, max_h = _mm(24), _mm(24)
                ratio = min(max_w / px_w, max_h / px_h) if px_w and px_h else 1
                png.seek(0)
                story.append(Image(png, width=px_w * ratio, height=px_h * ratio,
                                   hAlign='CENTER'))
                story.append(Spacer(1, 2.5 * mm))
        except Exception:  # pragma: no cover - logo is best effort
            logger.warning('Could not place school logo', exc_info=True)

    school_name = (school.name if school else '') or 'Terminal Report'
    story.append(_paragraph(_esc(school_name), styles['school']))

    meta_bits = []
    if school is not None:
        for attr in ('location', 'address', 'phone', 'email'):
            value = getattr(school, attr, '')
            if value:
                meta_bits.append(str(value).replace('\n', ', '))
    if meta_bits:
        story.append(_paragraph(_esc('  •  '.join(meta_bits)), styles['school_meta']))
    if settings is not None and getattr(settings, 'report_header', ''):
        story.append(_paragraph(_esc(settings.report_header), styles['school_meta']))

    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph('STUDENT TERMINAL REPORT', styles['title']))
    story.append(_paragraph(
        f"{_esc(data['academic_year'].name)}  •  {_esc(data['term'].name)}",
        styles['period']))
    story.append(Spacer(1, 2 * mm))
    story.append(HRFlowable(width='100%', thickness=1.1,
                            color=colors.HexColor(GOLD), spaceAfter=6))
    story.append(Spacer(1, 2 * mm))

    # ----- Student information ------------------------------------------ #
    story.append(_section_title('Student Information', styles))
    story.append(Spacer(1, 2.5 * mm))
    story.append(_student_info_table(
        data, styles, class_teacher=class_teacher, report_date=report_date))
    story.append(Spacer(1, 5 * mm))

    # ----- Results ------------------------------------------------------- #
    story.append(_section_title('Academic Performance', styles))
    story.append(Spacer(1, 2.5 * mm))
    story.append(_results_table(data, styles))
    story.append(Spacer(1, 3 * mm))

    # ----- Performance summary ------------------------------------------ #
    results = data['results']
    position = _overall_position(
        student, data['academic_year'], data['term'], settings)
    summary_pairs = [
        ('Subjects Graded', _num(results.get('graded_subject_count'))),
        ('Total Score', _num(results.get('total'))),
        ('Average', _num(results.get('average'))),
    ]
    if position is not None:
        summary_pairs.append(('Class Position', _num(position)))
    summary_rows = [[]]
    for label, value in summary_pairs:
        summary_rows[0].extend([_paragraph(_esc(label), styles['label']),
                                _paragraph(_esc(value), styles['value'])])
    story.append(_kv_summary(summary_rows, [_mm(24), _mm(16)] * len(summary_pairs)))
    story.append(Spacer(1, 4 * mm))

    attendance_table = None
    if settings is None or getattr(settings, 'show_attendance_on_report', True):
        attendance_table = _attendance_summary(data, styles)
    if attendance_table is not None:
        story.append(_section_title('Attendance Summary', styles))
        story.append(Spacer(1, 2.5 * mm))
        story.append(attendance_table)
        story.append(Spacer(1, 4 * mm))

    # ----- Comments ------------------------------------------------------ #
    comment = data.get('comment')
    comment_parts = []
    if comment is not None:
        if comment.class_teacher_comment:
            comment_parts.append(_comment_box(
                "Class Teacher's Comment", comment.class_teacher_comment, styles))
        if comment.head_teacher_remark:
            comment_parts.append(_comment_box(
                "Head Teacher's Remarks", comment.head_teacher_remark, styles))
        if comment.promotion_status and comment.get_promotion_status_display():
            comment_parts.append(_paragraph(
                f'<b>Promotion Status:</b> '
                f'{_esc(comment.get_promotion_status_display())}',
                styles['value']))
    if comment_parts:
        story.append(_section_title('Comments', styles))
        story.append(Spacer(1, 2.5 * mm))
        for index, part in enumerate(comment_parts):
            if index:
                story.append(Spacer(1, 2.5 * mm))
            story.append(part)
        story.append(Spacer(1, 4 * mm))

    # ----- Fees ---------------------------------------------------------- #
    if (data.get('report_fields')
            and (settings is None or getattr(settings, 'show_financials_on_report', True))):
        story.append(_section_title('School Fees', styles))
        story.append(Spacer(1, 2.5 * mm))
        story.append(_fees_table(data, styles))
        story.append(Spacer(1, 1.5 * mm))
        story.append(_paragraph(
            'Financial figures are taken directly from the school fees ledger '
            'and reconcile with the fee dashboard.', styles['small']))
        story.append(Spacer(1, 4 * mm))

    # ----- Signatures ---------------------------------------------------- #
    story.append(Spacer(1, 2 * mm))
    story.append(_signature_block(report_date, styles))

    # ----- Build --------------------------------------------------------- #
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=_mm(MARGIN_X_MM), rightMargin=_mm(MARGIN_X_MM),
        topMargin=_mm(TOP_MARGIN_MM), bottomMargin=_mm(BOTTOM_MARGIN_MM),
        title=f'Terminal Report - {student.get_full_name()}',
        author=school_name, subject='Student Terminal Report')
    doc.content_width = content_width
    on_page = _make_page_furniture(school, logo_path)
    canvasmaker = _make_canvas(school)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page,
              canvasmaker=canvasmaker)
    return buffer.getvalue()
