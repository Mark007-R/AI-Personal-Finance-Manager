from flask import Blueprint, current_app, render_template, request, session, redirect, url_for
import os
import shutil
import subprocess
import tempfile

from src.extraction import extract_fields
from src.insights import suggest_category, CATEGORY_LABELS

extract_bill_bp = Blueprint(
    'extract_bill_bp', __name__, template_folder='templates')


def _resolve_pdftotext():
    """Locate poppler's pdftotext without a machine-specific hardcoded path.

    Resolution order:
      1. POPPLER_PDFTOTEXT env var (explicit override),
      2. a `pdftotext` binary already on PATH (Linux/macOS/Docker, or a Windows
         install added to PATH).
    Returns the executable path, or None if poppler is unavailable.
    """
    env = os.getenv('POPPLER_PDFTOTEXT')
    if env and os.path.exists(env):
        return env
    return shutil.which('pdftotext')


def extract_text_from_pdf(pdf_path):
    """Extract text from a PDF, preferring the pure-Python pdfplumber backend
    (cross-platform, no system binary) and falling back to poppler's pdftotext.

    The previous implementation hardcoded ``C:\\poppler-24.07.0\\...`` so it only
    ran on one machine; this version is portable and Docker-friendly.
    """
    # Backend 1: pdfplumber (pure Python, no external binary).
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages = [(page.extract_text() or '') for page in pdf.pages]
        text = '\n'.join(pages).strip()
        if text:
            return text
    except ImportError:
        pass  # pdfplumber not installed -> try poppler

    # Backend 2: poppler pdftotext, located dynamically (no hardcoded path).
    pdftotext = _resolve_pdftotext()
    if not pdftotext:
        raise Exception(
            'No PDF backend available. Install pdfplumber (pip install pdfplumber) '
            'or set POPPLER_PDFTOTEXT to a pdftotext executable.')

    with tempfile.TemporaryDirectory() as tmp:
        output_path = os.path.join(tmp, 'extracted_text.txt')
        result = subprocess.run(
            [pdftotext, pdf_path, output_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception('Error extracting text from PDF: ' + result.stderr)
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8') as f:
                return f.read()
    raise Exception('Extracted text file not found.')


def find_bill_details(text):
    """Extract (signed_amount, bill_date) from receipt text.

    Day-5 change: delegates to the Day-2 champion extractor (`rules_smart`,
    amount-acc 0.58 / date-acc 0.87 vs the old regex's 0.15 / 0.49). The naive
    first-decimal regex is retained inside the extractor purely as a fallback,
    so the (amount, date) return signature is unchanged for every caller. (The
    scan route now reads all three fields via `extract_fields` for its review form.)
    """
    res = extract_fields(text)
    amount = float(res.get('amount') or 0.0)
    bill_date = res.get('date') or 'Not found'
    return -abs(amount), bill_date


@extract_bill_bp.route('/extract_bill', methods=['GET', 'POST'])
def extract_bill():
    """Scan a PDF receipt, then let the user review the fields before saving.

    Nothing is written here: the review form posts to the normal add-transaction
    route, so a misread amount or a missing date never lands in the ledger.
    """
    if 'user_id' not in session:
        return redirect(url_for('login_bp.login'))
    if request.method == 'GET':
        return render_template('extract_bill.html', active='scan')

    file = request.files.get('pdf')
    if not file or not file.filename:
        return render_template('extract_bill.html', active='scan',
                               error_message='Choose a PDF receipt to scan.')
    if not file.filename.lower().endswith('.pdf'):
        return render_template('extract_bill.html', active='scan',
                               error_message='Only PDF receipts can be scanned for now.')

    # A server-chosen temp name, so the uploaded filename never touches the filesystem.
    fd, temp_path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd)
    try:
        file.save(temp_path)
        text = extract_text_from_pdf(temp_path)
    except Exception:
        current_app.logger.exception('receipt text extraction failed')
        return render_template('extract_bill.html', active='scan',
                               error_message="We couldn't read any text in that PDF. Scanned "
                                             "photos aren't supported yet, so try a PDF bill or invoice.")
    finally:
        os.remove(temp_path)

    fields = extract_fields(text)
    merchant = (fields.get('merchant') or '').strip()
    description = merchant.title() if merchant.isupper() else merchant
    category = suggest_category(description) if description else None
    review = {
        'amount': f"{abs(float(fields.get('amount') or 0)):.2f}" if fields.get('amount') else '',
        'date': fields.get('date') or '',
        'description': description[:255] or 'Receipt',
        'category_label': CATEGORY_LABELS.get(category) if category else None,
        'found': {k: bool(fields.get(k)) for k in ('amount', 'date', 'merchant')},
    }
    return render_template('extract_bill.html', active='scan', review=review,
                           debug_text=text, filename=os.path.basename(file.filename))
