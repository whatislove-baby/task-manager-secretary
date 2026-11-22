"""Rebuild synthetic attachment acceptance files; never touches an account."""
from pathlib import Path
from io import BytesIO
import csv
import hashlib
import json

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from docx import Document
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'inputs'
OUT.mkdir(exist_ok=True)


def font(size):
    for candidate in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


chart = Image.new('RGB', (1500, 1000), 'white')
d = ImageDraw.Draw(chart)
d.text((90, 55), 'SYNTHETIC COURSE LAB', fill='#172434', font=font(38))
d.text((90, 112), 'Completed exercises by month', fill='#172434', font=font(54))
d.text((90, 194), 'Read the chart. Values exist only in the pixels.', fill='#536174', font=font(28))
left, top, bottom = 170, 300, 790
for value in range(0, 41, 10):
    y = bottom - value * 11
    d.line((left, y, 1370, y), fill='#dce1e7', width=2)
    d.text((90, y-20), str(value), fill='#536174', font=font(27))
for x, month, value, color in ((300, 'January', 18, '#8aabc8'), (680, 'February', 27, '#557eaa'), (1060, 'March', 39, '#234b7a')):
    y = bottom - value * 11
    d.rounded_rectangle((x, y, x+190, bottom), radius=6, fill=color)
    d.text((x+65, y-60), str(value), fill='#172434', font=font(44))
    d.text((x-5, bottom+28), month, fill='#172434', font=font(33))
d.text((90, 922), 'Fixture TM-DOC-01 | Synthetic data | No real student records', fill='#536174', font=font(24))
chart.save(OUT / 'chart-source.png')
pdf = canvas.Canvas(str(OUT / 'course-chart-scan.pdf'), pagesize=(750, 500))
pdf.setTitle('Synthetic course chart scan')
pdf.drawImage(ImageReader(chart), 0, 0, width=750, height=500)
pdf.showPage()
pdf.save()

rows = [
    ['001', 'A', '4', 'active'], ['002', 'A', '6', 'active'],
    ['002', 'A', '6', 'active'], ['003', 'B', '8', 'active'],
    ['004', 'B', '12', 'active'], ['005', 'A', '', 'active'],
    ['006', 'C', '9', 'cancelled'],
]
with (OUT / 'course-hours.csv').open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f); writer.writerow(['record_id', 'team', 'hours', 'status']); writer.writerows(rows)

doc = Document()
normal = doc.styles['Normal']; normal.font.name = 'Arial'; normal.font.size = Pt(11)
for name in ('Title', 'Heading 1'):
    doc.styles[name].font.name = 'Arial'; doc.styles[name].font.color.rgb = RGBColor.from_string('172434')
doc.sections[0].header.paragraphs[0].text = 'SYNTHETIC COURSE LAB  |  TM-DOC-03'
doc.sections[0].footer.paragraphs[0].text = 'Synthetic acceptance document - preserve this footer.'
doc.add_heading('Course project progress', 0)
doc.add_paragraph('This synthetic report checks whether a targeted edit preserves the rest of a Word file.')
doc.add_heading('Current analysis', 1)
paragraph = doc.add_paragraph()
paragraph.add_run('Analysis is ').bold = True
paragraph.add_run('pending.').italic = True
doc.add_paragraph('Keep this sentence exactly unchanged. The chart below and the review table must remain present.')
doc.add_picture(str(OUT / 'chart-source.png'), width=Inches(5.7))
table = doc.add_table(rows=1, cols=3); table.style = 'Light Shading Accent 1'
for cell, value in zip(table.rows[0].cells, ['Item', 'Owner', 'Status']): cell.text = value
for values in (['Data review', 'Team A', 'Pending'], ['Presentation', 'Team B', 'Scheduled']):
    for cell, value in zip(table.add_row().cells, values): cell.text = value
doc.add_paragraph('Reference identifier TM-DOC-03 must remain unchanged.')
doc.save(OUT / 'course-progress.docx')
(OUT / 'corrupt.pdf').write_bytes(b'%PDF-1.7\nThis synthetic file is intentionally broken.\n')

expected = {
    'synthetic': True,
    'pdf': {'file': 'course-chart-scan.pdf', 'pages': 1, 'textLayerCharacters': 0,
            'values': {'January': 18, 'February': 27, 'March': 39}, 'increaseJanuaryToMarch': 21,
            'requiresActualImageInput': True},
    'csv': {'file': 'course-hours.csv', 'sourceRows': 7, 'cleanedRows': 5,
            'remainingIds': ['001','002','003','004','005'], 'totalHours': 30,
            'teamHours': {'A':10,'B':20}, 'missingFill': {'005':'0'}},
    'docx': {'file':'course-progress.docx', 'replaceFrom':'Analysis is pending.',
             'replaceTo':'Analysis is complete. Total eligible hours are 30.',
             'table': {'tableIndex':0,'row':1,'column':2,'from':'Pending','to':'Reviewed'},
             'preserveText':['Keep this sentence exactly unchanged. The chart below and the review table must remain present.',
                             'Reference identifier TM-DOC-03 must remain unchanged.', 'Presentation','Scheduled'],
             'preserveImages':True,'preserveHeadersFooters':True},
    'failures': [{'file':'corrupt.pdf','expected':'Reject unreadable PDF without fabricated content'},
                 {'case':'conflicting-docx-expected-text','expected':'Reject stale target, original bytes unchanged'}],
    'draft': {'to':'training@example.invalid', 'send':False,
              'attachments':['course-analysis.pdf','course-hours-cleaned.csv','course-hours-summary.csv','course-progress-reviewed.docx']},
    'sha256': {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file()},
}
(ROOT / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print('Created synthetic PDF, CSV and Word inputs with deterministic expectations; no network or accounts.')
