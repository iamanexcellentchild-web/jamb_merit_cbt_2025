from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, make_response
)
from markupsafe import Markup
import os
import json
import logging
import secrets
import string
from datetime import datetime
import random

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.logger.setLevel(logging.DEBUG)

EXAM_DURATION_SECONDS = 7200  # 2 hours

# Admin password — set ADMIN_PASSWORD in your environment to override the default.
# Change this default before deploying!
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'MeritAdmin2025')

# ---------------------------------------------------------------------------
# Subject configuration
# ---------------------------------------------------------------------------
# English always gets 60 questions; every other subject gets 40.
# Add or remove subjects here — the rest of the code adapts automatically.
import json as _json
import os

SUBJECT_CONFIG = {
    "English":     60,
    "Mathematics": 40,
    "Physics":     40,
    "Chemistry":   40,
    "Biology":     40,
    "Economics":   40,
    "Literature":  40,
    "Government":  40,
}

def _load(filename):
    # Support both bare filename and data/filename paths
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        # Try inside data/ subfolder automatically
        path = os.path.join(os.path.dirname(__file__), 'data', os.path.basename(filename))
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return _json.load(f)
    return []

def _load_physics(filename):
    """
    Flatten the nested Physics JSON (years > questions) into a plain list.
    Each question gets normalised keys:
      'question'   : question text
      'options'    : dict {A: ..., B: ..., C: ..., D: ...}
      'answer'     : correct letter, e.g. "A"
      'has_diagram': bool
      'image_url'  : Flask-ready path prefixed with /static/, or None
    """
    data = _load(filename)
    if not data or not isinstance(data, dict):
        return []
    questions = []
    for year_obj in data.get('years', []):
        for q in year_obj.get('questions', []):
            raw_img = q.get('image_url')
            if raw_img:
                # Strip any /static/ prefix — url_for('static', filename=...) adds it
                raw_img = raw_img.lstrip('/')
                if raw_img.startswith('static/'):
                    raw_img = raw_img[len('static/'):]
                # If bare filename or missing subject subfolder, put in jamb_diagrams/physics/
                if '/' not in raw_img:
                    raw_img = 'jamb_diagrams/physics/' + raw_img
                elif raw_img.startswith('jamb_diagrams/') and raw_img.count('/') == 1:
                    # e.g. jamb_diagrams/2010_Q2.jpeg — missing the physics/ subfolder
                    filename = raw_img.split('/')[-1]
                    raw_img = 'jamb_diagrams/physics/' + filename
            questions.append({
                'question':    q.get('question', ''),
                'options':     q.get('options', {}),
                'answer':      q.get('answer', ''),
                'has_diagram': q.get('has_diagram', False),
                'image_url':   raw_img,
            })
    return questions


def _normalise_options(raw_options):
    """
    Convert options to a consistent dict {A: text, B: text, C: text, D: text}.
    Handles:
      - Already a dict  →  returned as-is
      - List like ["A. text", "B. text", ...]  →  split on first ". "
      - List like ["text1", "text2", ...]  →  assigned keys A/B/C/D
    """
    if isinstance(raw_options, dict):
        return raw_options
    if not isinstance(raw_options, list):
        return {}
    letters = ['A', 'B', 'C', 'D']
    result  = {}
    for i, item in enumerate(raw_options[:4]):
        text = str(item)
        # Strip leading "A. " / "B. " etc. if present
        if len(text) >= 3 and text[1] == '.' and text[2] == ' ':
            text = text[3:]
        elif len(text) >= 2 and text[1] == '.':
            text = text[2:]
        result[letters[i]] = text.strip()
    return result


def _load_math(filename):
    """
    Load Mathematics JSON.  Handles three shapes:

    Shape 1 — flat list of question dicts
    Shape 2 — {years: [{questions: [...]}]}   (old Physics-style)
    Shape 3 — {years: {"2010": [...], "2011": [...], ...}}  (new per-year dict)
                Each question uses keys:  q, options (list), answer, diagram (optional)
    """
    data = _load(filename)
    if not data:
        return []

    def _build(q):
        """Turn any raw question dict into a normalised app question dict."""
        raw_img = q.get('image_url') or q.get('diagram')
        if raw_img:
            # Strip any /static/ prefix — url_for('static', filename=...) adds it
            raw_img = raw_img.lstrip('/')
            if raw_img.startswith('static/'):
                raw_img = raw_img[len('static/'):]
            # If it's just a bare filename with no folder, put it in jamb_diagrams/maths/
            if '/' not in raw_img:
                raw_img = 'jamb_diagrams/maths/' + raw_img
            # Fix old wrong path jamb_diagrams/mathematics/ → jamb_diagrams/maths/
            elif raw_img.startswith('jamb_diagrams/mathematics/'):
                raw_img = raw_img.replace('jamb_diagrams/mathematics/', 'jamb_diagrams/maths/')
        return {
            'question':    q.get('question', q.get('q', '')),
            'options':     _normalise_options(q.get('options', {})),
            'answer':      q.get('answer', ''),
            'explanation': q.get('explanation', q.get('solution', '')),
            'image_url':   raw_img,
            'has_diagram': bool(raw_img),
        }

    # --- Shape 1: flat list ---
    if isinstance(data, list):
        return [_build(q) for q in data]

    if isinstance(data, dict):
        years_val = data.get('years', [])

        # --- Shape 3: years is a dict {"2010": [...], ...} ---
        if isinstance(years_val, dict):
            normalised = []
            for year_questions in years_val.values():
                for q in year_questions:
                    normalised.append(_build(q))
            return normalised

        # --- Shape 2: years is a list [{questions: [...]}, ...] ---
        if isinstance(years_val, list):
            normalised = []
            for year_obj in years_val:
                for q in year_obj.get('questions', []):
                    normalised.append(_build(q))
            return normalised

    return []

def _load_economics(filename):
    """
    Load Economics JSON — flat list format.
    Image key is 'diagram_image', stored at static/jamb_diagrams/economics/
    """
    data = _load(filename)
    if not data or not isinstance(data, list):
        return []

    questions = []
    for q in data:
        raw_img = q.get('diagram_image') or q.get('image_url')
        if raw_img:
            raw_img = raw_img.lstrip('/')
            if raw_img.startswith('static/'):
                raw_img = raw_img[len('static/'):]
            if '/' not in raw_img:
                raw_img = 'jamb_diagrams/economics/' + raw_img
        questions.append({
            'question':    q.get('question', ''),
            'options':     _normalise_options(q.get('options', {})),
            'answer':      q.get('answer', ''),
            'explanation': q.get('explanation', ''),
            'image_url':   raw_img,
            'has_diagram': q.get('has_diagram', bool(raw_img)),
        })
    return questions


def _load_biology(filename):
    """
    Load Biology JSON — nested years > questions structure.
    Uses 'diagram_files' key (a list). We use the first file as the image.
    Images expected at static/jamb_diagrams/biology/
    """
    data = _load(filename)
    if not data or not isinstance(data, dict):
        return []

    questions = []
    for year_obj in data.get('years', []):
        for q in year_obj.get('questions', []):
            diagram_files = q.get('diagram_files', [])
            raw_img = diagram_files[0] if diagram_files else q.get('image_url')
            if raw_img:
                raw_img = raw_img.lstrip('/')
                if raw_img.startswith('static/'):
                    raw_img = raw_img[len('static/'):]
                if '/' not in raw_img:
                    raw_img = 'jamb_diagrams/biology/' + raw_img
            questions.append({
                'question':    q.get('question', ''),
                'options':     _normalise_options(q.get('options', {})),
                'answer':      q.get('answer', ''),
                'explanation': q.get('explanation', ''),
                'image_url':   raw_img,
                'has_diagram': q.get('has_diagram', bool(raw_img)),
            })
    return questions


def _load_government(filename):
    """
    Load Government JSON — dict with flat 'questions' list.
    Uses 'q' for question text, list options, no diagrams.
    """
    data = _load(filename)
    if not data or not isinstance(data, dict):
        return []
    return [
        {
            'question':    q.get('question', q.get('q', '')),
            'options':     _normalise_options(q.get('options', [])),
            'answer':      q.get('answer', ''),
            'explanation': q.get('explanation', ''),
            'image_url':   None,
            'has_diagram': False,
        }
        for q in data.get('questions', [])
    ]


# Each question is a dict with keys:
#   'question'    : question text  (str)
#   'options'     : dict {A: ..., B: ..., C: ..., D: ...}  OR list of strings
#   'answer'      : correct option letter, e.g. "A"
#   'explanation' (optional): solution/explanation text
#   'image_url'   (optional): path relative to static/ folder
# ---------------------------------------------------------------------------

QUESTIONS = {
    "English":     _load('data/english_questions.json'),
    "Mathematics": _load_math('data/jamb_mathematics_2010_2018.json'),
    "Physics":     _load_physics('data/jamb_physics_2010_2018.json'),
    "Chemistry":   _load_physics('data/jamb_chemistry_2010_2018.json'),
    "Biology":     _load_biology('data/jamb_biology_2010_2018.json'),
    "Economics":   _load_economics('data/economics_jamb.json'),
    "Literature":  _load('data/literature_questions.json'),
    "Government":  _load_government('data/government_questions.json'),
}

# ---------------------------------------------------------------------------
# Jinja helpers
# ---------------------------------------------------------------------------

def datetimeformat(ts):
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(sep=' ')
    except Exception:
        return ts

app.jinja_env.filters['datetimeformat'] = datetimeformat

# Subjects whose question text may contain LaTeX
LATEX_SUBJECTS = {'Mathematics', 'Physics', 'Chemistry'}

def latex_safe(text):
    """
    Mark LaTeX-containing text as HTML-safe so Jinja doesn't escape
    backslashes and special characters inside math expressions.
    Use in templates as:  {{ q.question | latex_safe }}
    """
    if not text:
        return ''
    return Markup(text)

app.jinja_env.filters['latex_safe'] = latex_safe

# ---------------------------------------------------------------------------
# Access-code system
# ---------------------------------------------------------------------------

def _codes_path():
    return os.path.join(os.path.dirname(__file__), 'access_codes.json')


def _read_codes():
    path = _codes_path()
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save_codes(codes):
    with open(_codes_path(), 'w', encoding='utf-8') as f:
        json.dump(codes, f, indent=2)


def _generate_code():
    """Return a new random 8-character alphanumeric access code (uppercase)."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))


def _is_valid_code(code):
    return code.strip().upper() in [c.upper() for c in _read_codes()]


# Routes that are always reachable — no access-code check performed.
_OPEN_ENDPOINTS = {'access', 'admin_login', 'admin_panel',
                   'admin_generate', 'admin_delete', 'static'}


@app.before_request
def require_access_code():
    """Block every route until the user has entered a valid access code."""
    if request.endpoint in _OPEN_ENDPOINTS:
        return  # these pages are always reachable
    if not session.get('access_granted'):
        return redirect(url_for('access'))

def results_file_path():
    return os.path.join(os.path.dirname(__file__), 'results.json')


def read_results():
    path = results_file_path()
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_result(entry):
    path = results_file_path()
    data = read_results()
    data.append(entry)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def is_exam_active():
    """Return True if the user has a live, unexpired exam in session."""
    return (
        'username' in session
        and 'selected_subjects' in session
        and 'exam_expires' in session
        and datetime.now().timestamp() < float(session['exam_expires'])
    )


def build_tracker():
    """
    Return a dict  {subject: {'total': int, 'answered': int}}
    built from the current session.
    """
    tracker = {}
    subjects   = session.get('selected_subjects', [])
    indices    = session.get('exam_indices', {})
    answers    = session.get('exam_answers', {})
    for s in subjects:
        total    = len(indices.get(s, []))
        answered = sum(1 for v in answers.get(s, {}).values() if v)
        tracker[s] = {'total': total, 'answered': answered}
    return tracker


# ---------------------------------------------------------------------------
# Routes — access code gate
# ---------------------------------------------------------------------------

@app.route('/access', methods=['GET', 'POST'])
def access():
    """First page every visitor sees.  Requires a valid access code."""
    if session.get('access_granted'):
        return redirect(url_for('login'))

    # Auto-grant if they have a saved valid access code cookie
    saved_code = request.cookies.get('merit_access_code', '').strip().upper()
    if saved_code and _is_valid_code(saved_code):
        session['access_granted'] = True
        return redirect(url_for('login'))

    if request.method == 'POST':
        entered = request.form.get('code', '').strip().upper()
        if _is_valid_code(entered):
            session['access_granted'] = True
            resp = make_response(redirect(url_for('login')))
            # Save access code in cookie for 30 days so they never enter it again
            resp.set_cookie('merit_access_code', entered, max_age=30*24*3600, httponly=True)
            return resp
        flash('Invalid access code.  Please try again or contact your administrator.')

    return render_template('access.html')


# ---------------------------------------------------------------------------
# Routes — admin panel (code management)
# ---------------------------------------------------------------------------

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    """Admin login page."""
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_panel'))
        flash('Wrong admin password.')

    return render_template('admin_login.html')


@app.route('/admin/panel')
def admin_panel():
    """Show existing codes and let the admin generate / delete them."""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    codes = _read_codes()
    return render_template('admin_panel.html', codes=codes)


@app.route('/admin/generate', methods=['POST'])
def admin_generate():
    """Generate a new access code and add it to the list."""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    codes = _read_codes()
    new_code = _generate_code()
    codes.append(new_code)
    _save_codes(codes)
    flash(f'New access code generated: {new_code}')
    return redirect(url_for('admin_panel'))


@app.route('/admin/delete', methods=['POST'])
def admin_delete():
    """Remove a specific access code."""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    code_to_del = request.form.get('code', '').strip().upper()
    codes = [c for c in _read_codes() if c.upper() != code_to_del]
    _save_codes(codes)
    flash(f'Code {code_to_del} has been removed.')
    return redirect(url_for('admin_panel'))


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


# ---------------------------------------------------------------------------
# Routes — authentication
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    if is_exam_active():
        subj = session['selected_subjects'][0]
        return redirect(url_for('exam', subject=subj))
    return redirect(url_for('select_subjects'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        # Auto-login if they have a saved name cookie
        saved_name = request.cookies.get('merit_username', '').strip()
        saved_reg  = request.cookies.get('merit_reg', '').strip()
        if saved_name and session.get('access_granted'):
            session['username']   = saved_name
            session['reg_number'] = saved_reg
            return redirect(url_for('select_subjects'))

        # Keep access_granted when clearing so the user doesn't get
        # sent back to the /access page after they log out and return.
        access_ok = session.get('access_granted', False)
        session.clear()
        session['access_granted'] = access_ok
        resp = make_response(render_template('login.html'))
        resp.set_cookie('session', '', expires=0)
        return resp

    name       = request.form.get('name', '').strip()
    reg_number = request.form.get('reg_number', '').strip()

    if not name:
        flash('Please enter your name.')
        return redirect(url_for('login'))

    access_ok = session.get('access_granted', False)
    session.clear()
    session['access_granted'] = access_ok
    session['username']   = name
    session['reg_number'] = reg_number
    resp = make_response(redirect(url_for('select_subjects')))
    # Save name and reg in cookies for 30 days for auto-login next time
    resp.set_cookie('merit_username', name,       max_age=30*24*3600, httponly=True)
    resp.set_cookie('merit_reg',      reg_number, max_age=30*24*3600, httponly=True)
    return resp


@app.route('/logout')
def logout():
    access_ok = session.get('access_granted', False)
    session.clear()
    session['access_granted'] = access_ok
    flash('You have been logged out.')
    resp = make_response(redirect(url_for('login')))
    # Clear saved cookies on explicit logout
    resp.set_cookie('merit_username', '', expires=0)
    resp.set_cookie('merit_reg',      '', expires=0)
    resp.set_cookie('merit_access_code', '', expires=0)
    return resp


# ---------------------------------------------------------------------------
# Routes — subject selection
# ---------------------------------------------------------------------------

@app.route('/select', methods=['GET', 'POST'])
def select_subjects():
    if 'username' not in session:
        return redirect(url_for('login'))

    # If an active exam already exists, go straight to it
    if is_exam_active():
        return redirect(url_for('exam', subject=session['selected_subjects'][0]))

    if request.method == 'POST':
        selected = request.form.getlist('subjects')

        # --- Validation ---
        if len(selected) != 4:
            flash('Please select exactly 4 subjects.')
            return redirect(url_for('select_subjects'))

        invalid = [s for s in selected if s not in QUESTIONS]
        if invalid:
            flash(f'Invalid subject(s): {", ".join(invalid)}')
            return redirect(url_for('select_subjects'))

        # --- Sample question indices for each chosen subject ---
        exam_indices = {}
        for subj in selected:
            pool  = QUESTIONS[subj]
            count = SUBJECT_CONFIG[subj]
            if len(pool) >= count:
                idxs = random.sample(range(len(pool)), count)
            else:
                idxs = list(range(len(pool)))   # use all available
            exam_indices[subj] = idxs

        # --- Store in session (only indices, not full question objects) ---
        now_ts = datetime.now().timestamp()
        session['selected_subjects'] = selected
        session['exam_indices']      = exam_indices
        session['exam_answers']      = {s: {} for s in selected}
        session['exam_start']        = now_ts
        session['exam_expires']      = now_ts + EXAM_DURATION_SECONDS
        session.modified             = True

        app.logger.debug('Exam started for %s, subjects: %s', session['username'], selected)
        return redirect(url_for('exam', subject=selected[0]))

    return render_template(
        'select.html',
        subjects=list(QUESTIONS.keys()),
        subject_config=SUBJECT_CONFIG,
        username=session.get('username'),
    )


# ---------------------------------------------------------------------------
# Routes — exam
# ---------------------------------------------------------------------------

@app.route('/exam/<subject>', methods=['GET', 'POST'])
def exam(subject):
    if 'username' not in session:
        return redirect(url_for('login'))

    selected = session.get('selected_subjects', [])
    if not selected:
        flash('No active exam. Please select your subjects.')
        return redirect(url_for('select_subjects'))

    if subject not in selected:
        # Redirect to first subject if the requested one isn't in the exam
        return redirect(url_for('exam', subject=selected[0]))

    # --- Expiry check ---
    expires = session.get('exam_expires')
    if expires and datetime.now().timestamp() > float(expires):
        return redirect(url_for('submit_exam', reason='timeout'))

    if request.method == 'POST':
        # Save answers for THIS subject that were submitted in the form
        current_answers = session.get('exam_answers', {})
        indices         = session.get('exam_indices', {}).get(subject, [])

        for i in range(len(indices)):
            val = request.form.get(f'q{i}')
            if val is not None:          # None means the question was skipped
                current_answers.setdefault(subject, {})[str(i)] = val

        session['exam_answers'] = current_answers
        session.modified        = True

        action = request.form.get('action', '')

        if action == 'submit':
            return redirect(url_for('submit_exam'))

        if action.startswith('goto:'):
            next_subj = action.split(':', 1)[1]
            if next_subj in selected:
                return redirect(url_for('exam', subject=next_subj))

        # Default: stay on current subject
        return redirect(url_for('exam', subject=subject))

    # --- Build question list for this subject ---
    pool    = QUESTIONS[subject]
    indices = session.get('exam_indices', {}).get(subject, [])
    questions = [pool[i] for i in indices] if pool else []

    answers = session.get('exam_answers', {}).get(subject, {})
    tracker = build_tracker()

    return render_template(
        'exam.html',
        subject         = subject,
        subjects        = selected,
        questions       = questions,
        answers         = answers,       # {str(q_index): 'A'/'B'/...}
        tracker         = tracker,
        expires_at      = session.get('exam_expires'),
        subject_config  = SUBJECT_CONFIG,
        username        = session.get('username'),
        needs_mathjax   = subject in LATEX_SUBJECTS,   # tells template to load MathJax
    )


@app.route('/save_answers', methods=['POST'])
def save_answers():
    """
    AJAX endpoint called by the JS on the exam page to persist answers
    whenever the student clicks an option — no page reload required.
    Expected JSON body: { "subject": "...", "answers": {"0": "A", "3": "C"} }
    """
    if 'username' not in session:
        return jsonify({'error': 'not logged in'}), 401

    data          = request.get_json(force=True, silent=True) or {}
    subject       = data.get('subject')
    incoming      = data.get('answers', {})
    selected      = session.get('selected_subjects', [])

    if not subject or subject not in selected:
        return jsonify({'error': 'invalid subject'}), 400

    current = session.get('exam_answers', {})
    current.setdefault(subject, {}).update(incoming)
    session['exam_answers'] = current
    session.modified        = True

    tracker = build_tracker()
    return jsonify({'ok': True, 'tracker': tracker})


# ---------------------------------------------------------------------------
# Routes — submission & results
# ---------------------------------------------------------------------------

@app.route('/submit')
def submit_exam():
    if 'username' not in session:
        return redirect(url_for('login'))

    reason   = request.args.get('reason', '')
    selected = session.get('selected_subjects', [])

    if not selected:
        flash('No exam to submit.')
        return redirect(url_for('select_subjects'))

    pool_map    = QUESTIONS
    indices_map = session.get('exam_indices', {})
    answers_map = session.get('exam_answers', {})

    results     = {}
    total_score = 0.0

    for subj in selected:
        pool       = pool_map.get(subj, [])
        indices    = indices_map.get(subj, [])
        questions  = [pool[i] for i in indices] if pool else []
        subj_ans   = answers_map.get(subj, {})

        correct = 0
        details = []

        for i, q in enumerate(questions):
            chosen     = subj_ans.get(str(i))
            is_correct = chosen == q.get('answer')
            if is_correct:
                correct += 1
            details.append({
                'question':    q.get('question', q.get('q', '')),
                'options':     q.get('options', []),
                'selected':    chosen,
                'answer':      q.get('answer'),
                'correct':     is_correct,
                'explanation': q.get('explanation', q.get('solution', '')),
                'image_url':   q.get('image_url'),   # FIX: carry image through to result page
            })

        total_q   = len(questions)
        # Each subject is worth 100 marks → total exam is 400
        subj_score = round((correct / total_q) * 100, 2) if total_q else 0.0
        total_score += subj_score

        results[subj] = {
            'correct':  correct,
            'total':    total_q,
            'score':    subj_score,
            'details':  details,
        }

    total_score = round(total_score, 2)

    # Persist result (without per-question details to keep file lean)
    entry = {
        'name':        session.get('username'),
        'reg_number':  session.get('reg_number', ''),
        'subjects':    selected,
        'results':     {
            s: {'correct': r['correct'], 'total': r['total'], 'score': r['score']}
            for s, r in results.items()
        },
        'total_score': total_score,
        'timed_out':   reason == 'timeout',
        'timestamp':   datetime.now().isoformat(),
    }
    save_result(entry)

    # Clear exam state (keep username for history page)
    for key in ('selected_subjects', 'exam_indices', 'exam_answers',
                'exam_start', 'exam_expires'):
        session.pop(key, None)
    session.modified = True

    return render_template(
        'result.html',
        username      = session.get('username'),
        reg_number    = session.get('reg_number', ''),
        selected      = selected,
        results       = results,
        total_score   = total_score,
        timed_out     = (reason == 'timeout'),
        needs_mathjax = any(s in LATEX_SUBJECTS for s in selected),  # load MathJax if any subject needs it
    )


# ---------------------------------------------------------------------------
# Routes — admin / history
# ---------------------------------------------------------------------------

@app.route('/grades')
def grades():
    results = read_results()
    return render_template('grades.html', results=results)


@app.route('/history')
def history():
    if 'username' not in session:
        flash('Please log in to view your history.')
        return redirect(url_for('login'))
    username     = session.get('username')
    all_results  = read_results()
    user_results = [r for r in all_results if r.get('name') == username]
    return render_template('history.html', results=user_results, username=username)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True)
