"""Builds the two Google Sheet import templates (run: python build_templates.py).
Columns must stay compatible with n8n/tools/website_src/sheet_normalize.js."""
import re, os
import openpyxl
from openpyxl.utils import get_column_letter as L
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
SQL = os.path.join(HERE, '..', 'migrations', '019_eval_instruments.sql')

SECTIONS = {'A': 'A. Management of Teaching and Learning',
            'B': 'B. Content Knowledge, Pedagogy and Technology',
            'C': 'C. Commitment and Transparency'}
SET_TEXT = [
    "Comes to class on time.",
    "Explains learning outcomes, expectations, grading system, and various requirements of the subject/course.",
    "Maximizes the allocated time/learning hours effectively.",
    "Facilitates students to think critically and creatively by providing appropriate learning activities.",
    "Guides students to learn on their own, reflect on new ideas and experiences, and make decisions in accomplishing given tasks.",
    "Communicates constructive feedback to students for their academic growth.",
    "Demonstrates extensive and broad knowledge of the subject/course.",
    "Simplifies complex ideas in the lesson for ease of understanding.",
    "Relates the subject matter to contemporary issues and developments in the discipline and/or daily life activities.",
    "Promotes active learning and student engagement by using appropriate teaching and learning resources including ICT tools and platforms.",
    "Uses appropriate assessments (projects, exams, quizzes, assignments, etc.) aligned with the learning outcomes.",
    "Recognizes and values the unique diversity and individual differences among students.",
    "Assists students with their learning challenges during consultation hours.",
    "Provides immediate feedback on student outputs and performance.",
    "Provides transparent and clear criteria in rating student's performance."]
SUP_TEXT = list(SET_TEXT)
SUP_TEXT[1] = "Submits updated syllabus, grade sheets, and other required reports on time."
SUP_TEXT[3] = "Provide appropriate learning activities that facilitate critical thinking and creativity of students."
SUP_TEXT[8] = "Integrates contemporary issues and developments in the discipline and/or daily life activities in the syllabus."
SEC_OF = ['A'] * 6 + ['B'] * 5 + ['C'] * 4
SCALE = [(5, 'Always manifested'), (4, 'Often manifested'), (3, 'Sometimes manifested'),
         (2, 'Seldom manifested'), (1, 'Never/Rarely manifested')]


def hints():
    """Means of verification, read from migration 019 (last quoted string of each item row)."""
    sql = open(SQL, encoding='utf-8').read()
    block = sql[sql.index('-- SET-15 and SUP-15: same items'):]
    block = block[:block.index('as t(item_no')]
    out, cur = {}, None
    for line in block.splitlines():
        m = re.match(r"\s*\((\d+)::smallint", line)
        if m:
            cur = int(m.group(1))
        m2 = re.match(r"\s*'((?:[^']|'')*)'\)\s*,?\s*$", line)
        if m2 and cur:
            out[cur] = m2.group(1).replace("''", "'")
    assert len(out) == 15, out
    return out


def build(path, title, texts, sup):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SAST Survey Data'   # the import reads this tab name
    heads = ((['Student ID'] if not sup else []) + ['Instructor/Professor', 'Subject Taught']
             + (['Supervisor Email', 'Academic Rank', 'Designation'] if sup else [])
             + ['Section', 'Program Level', 'Program Year', 'Date']
             + [f'Q{i}' for i in range(1, 16)] + ['Comments', 'Stamp No'])
    ws.append(heads)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill('solid', fgColor='DDEBF7')
        c.alignment = Alignment(wrap_text=True, vertical='top')
    q1, q15 = heads.index('Q1') + 1, heads.index('Q15') + 1
    dv = DataValidation(type='list', formula1='"5,4,3,2,1"', allow_blank=True, showErrorMessage=True,
                        errorTitle='Rating', error='Use 5, 4, 3, 2 or 1 (leave empty if not answered).')
    dv.add(f'{L(q1)}2:{L(q15)}1000')
    ws.add_data_validation(dv)
    ws.freeze_panes = 'A2'
    for i, h in enumerate(heads, 1):
        ws.column_dimensions[L(i)].width = 26 if h in ('Instructor/Professor', 'Subject Taught', 'Supervisor Email', 'Comments', 'Academic Rank', 'Designation') else (13 if h in ('Student ID', 'Section', 'Program Level', 'Program Year', 'Date') else 9)

    it = wb.create_sheet('Item texts')
    h = hints() if sup else {}
    it.append(['Item', 'Q column', 'Section', 'Item text'] + (['Suggested Means of Verification'] if sup else []))
    for i in range(15):
        it.append([i + 1, f'Q{i+1}', SECTIONS[SEC_OF[i]], texts[i]] + ([h[i + 1]] if sup else []))
    for c in it[1]:
        c.font = Font(bold=True)
    for col, w in zip('ABCDE', (6, 9, 44, 90, 90)):
        it.column_dimensions[col].width = w
    for r in it.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical='top')

    hw = wb.create_sheet('How to use')
    rows = [title, '',
            'Keep the first tab named exactly: SAST Survey Data (the import reads that tab). One row per filled paper form.',
            'Instructor/Professor and Subject Taught: the same names or subject codes as in the SAST sheet.']
    if sup:
        rows.append('Supervisor Email: the email (or the exact full name) of the department head who filled the form. '
                    'A row whose supervisor is not found is not saved.')
        rows.append('Academic Rank and Designation: as written on the form (kept as a snapshot).')
    else:
        rows.append('Student ID: the student ID number on the form. It is only visible to the SAO office (never to '
                    'instructors or department heads). It is used to stop the same student evaluating the same '
                    'instructor for the same subject twice in a term: the second form is not saved. Leave it empty if unknown.')
    rows.append('Section, Program Level, Program Year and Date (the date on the form) are optional.')
    rows += ['', 'Rating scale for Q1 to Q15 (pick from the dropdown; leave a cell empty if the item was not answered):']
    rows += [f'   {n} = {t}' for n, t in SCALE]
    rows += ['', 'Rating = (Total Score / 75) x 100',
             'Blank items are skipped: Rating = answered total / (5 x number answered) x 100.',
             'At least 5 of the 15 items must be answered, otherwise the form is kept but left out of the scores.', '',
             'Comments and Stamp No are optional. Stamp No: 3 to 12 digits, kept as text.']
    rows.append('Paper-form fields with no column (not imported): College/Department, Course Title (use the subject code '
                'or title in Subject Taught), Semester/AY (the term is chosen in the import).')
    rows += ['Do not add student names: they are never stored.',
             'Sections and item texts are on the Item texts tab.']
    for r in rows:
        hw.append([r])
    hw['A1'].font = Font(bold=True, size=20)
    hw.column_dimensions['A'].width = 130
    for r in hw.iter_rows():
        r[0].alignment = Alignment(wrap_text=True, vertical='top')
    wb.save(path)


build(os.path.join(HERE, 'Student Evaluation of Teachers (SET) - import template.xlsx'),
      'STUDENT EVALUATION OF TEACHERS (SET)', SET_TEXT, False)
build(os.path.join(HERE, 'Supervisors Evaluation of Faculty - import template.xlsx'),
      "SUPERVISOR'S EVALUATION OF FACULTY", SUP_TEXT, True)
