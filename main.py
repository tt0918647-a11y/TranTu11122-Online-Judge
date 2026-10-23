
import os
import json
import time
import sqlite3
import requests
import re
import tempfile
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, instance_relative_config=True)
app.secret_key = os.environ.get(
    'SECRET_KEY',
    'change-this-secret-before-deployment'
)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

os.makedirs(app.instance_path, exist_ok=True)

DB = os.path.join(app.instance_path, 'trantu.sqlite3')
DATA = os.path.join(BASE, 'data', 'problems.json')

JUDGE0_URL = os.environ.get(
    'JUDGE0_URL',
    'https://ce.judge0.com'
).rstrip('/')
JUDGE0_TOKEN = os.environ.get('JUDGE0_TOKEN', '')
CPP_LANGUAGE_ID = int(os.environ.get('CPP_LANGUAGE_ID', '54'))

with open(DATA, encoding='utf-8') as f:
    PROBLEMS = json.load(f)

PROBLEM_MAP = {p['id'].upper(): p for p in PROBLEMS}


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB, timeout=15)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    con = g.pop('db', None)
    if con is not None:
        con.close()


def init_db():
    con = sqlite3.connect(DB, timeout=15)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        problem_id TEXT NOT NULL,
        language TEXT NOT NULL,
        source_code TEXT NOT NULL,
        result TEXT NOT NULL,
        score INTEGER NOT NULL DEFAULT 0,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS solves(
        user_id INTEGER NOT NULL,
        problem_id TEXT NOT NULL,
        best_score INTEGER NOT NULL,
        PRIMARY KEY(user_id, problem_id)
    );
    """)

    cols = {
        r[1] for r in con.execute(
            'PRAGMA table_info(users)'
        ).fetchall()
    }

    if 'is_admin' not in cols:
        con.execute(
            'ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0'
        )

    admin_user = os.environ.get('ADMIN_USERNAME', '').strip()
    admin_password = os.environ.get('ADMIN_PASSWORD', '')

    if admin_user and admin_password:
        row = con.execute(
            'SELECT id FROM users WHERE username=?',
            (admin_user,)
        ).fetchone()

        if row:
            con.execute(
                'UPDATE users SET is_admin=1 WHERE id=?',
                (row[0],)
            )
        else:
            con.execute(
                'INSERT INTO users(username,password_hash,is_admin) '
                'VALUES(?,?,1)',
                (
                    admin_user,
                    generate_password_hash(admin_password)
                )
            )

    con.commit()
    con.close()


init_db()


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None

    return db().execute(
        'SELECT id,username,is_admin FROM users WHERE id=?',
        (uid,)
    ).fetchone()


def admin_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        user = current_user()

        if not user:
            return jsonify(error='Vui lòng đăng nhập.'), 401

        if not user['is_admin']:
            return jsonify(
                error='Chỉ quản trị viên mới được truy cập.'
            ), 403

        return fn(*args, **kwargs)

    return wrap


def login_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if not current_user():
            return jsonify(error='Vui lòng đăng nhập.'), 401
        return fn(*args, **kwargs)

    return wrap


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/api/problems')
def problems():
    q = request.args.get('q', '').lower()
    topic = request.args.get('topic', '')
    diff = request.args.get('difficulty', '')

    arr = [
        {
            k: v for k, v in p.items()
            if k not in ('tests', 'function')
        }
        for p in PROBLEMS
        if (
            not q
            or q in (
                p['id'] + ' ' + p['title'] + ' ' + p['topic']
            ).lower()
        )
        and (not topic or p['topic'] == topic)
        and (not diff or p['difficulty'] == diff)
    ]

    return jsonify(arr)


@app.get('/api/problems/<pid>')
def problem(pid):
    p = PROBLEM_MAP.get(pid.upper())

    if not p:
        return jsonify(error='Không tìm thấy bài.'), 404

    return jsonify({
        k: v for k, v in p.items()
        if k not in ('tests', 'function')
    })


@app.post('/api/register')
def register():
    d = request.get_json(silent=True) or {}
    username = str(d.get('username', '')).strip()
    password = str(d.get('password', ''))

    if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', username):
        return jsonify(
            error='Tên tài khoản cần 3–24 ký tự, chỉ gồm chữ, số, dấu _.'
        ), 400

    if len(password) < 8:
        return jsonify(
            error='Mật khẩu cần ít nhất 8 ký tự.'
        ), 400

    try:
        cur = db().execute(
            'INSERT INTO users(username,password_hash) VALUES(?,?)',
            (username, generate_password_hash(password))
        )
        db().commit()
        session['user_id'] = cur.lastrowid

        return jsonify(username=username)

    except sqlite3.IntegrityError:
        return jsonify(error='Tên tài khoản đã tồn tại.'), 409


@app.post('/api/login')
def login():
    d = request.get_json(silent=True) or {}
    username = str(d.get('username', '')).strip()
    password = str(d.get('password', ''))

    row = db().execute(
        'SELECT * FROM users WHERE username=?',
        (username,)
    ).fetchone()

    if not row or not check_password_hash(
        row['password_hash'], password
    ):
        return jsonify(
            error='Sai tên tài khoản hoặc mật khẩu.'
        ), 401

    session['user_id'] = row['id']
    return jsonify(username=row['username'])


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get('/api/me')
def me():
    user = current_user()

    if not user:
        return jsonify(user=None)

    rows = db().execute(
        'SELECT problem_id,best_score FROM solves WHERE user_id=?',
        (user['id'],)
    ).fetchall()

    return jsonify(
        user={
            'username': user['username'],
            'is_admin': bool(user['is_admin'])
        },
        solved=[dict(row) for row in rows]
    )


def judge_one(code, stdin, expected):
    headers = {'Content-Type': 'application/json'}

    if JUDGE0_TOKEN:
        headers['X-Auth-Token'] = JUDGE0_TOKEN

    body = {
        'source_code': code,
        'language_id': CPP_LANGUAGE_ID,
        'stdin': stdin,
        'expected_output': expected,
        'cpu_time_limit': 2,
        'wall_time_limit': 5,
        'memory_limit': 128000
    }

    try:
        response = requests.post(
            JUDGE0_URL + '/submissions',
            params={
                'base64_encoded': 'false',
                'wait': 'false'
            },
            json=body,
            headers=headers,
            timeout=20
        )

        if not response.ok:
            raise requests.RequestException(
                'Judge0 POST HTTP '
                + str(response.status_code)
                + ': ' + response.text[:1000]
            )

        token = response.json().get('token')

        if not token:
            raise requests.RequestException(
                'Judge0 không trả submission token.'
            )

        deadline = time.time() + 25

        while time.time() < deadline:
            result_response = requests.get(
                JUDGE0_URL + '/submissions/' + token,
                params={'base64_encoded': 'false'},
                headers=headers,
                timeout=10
            )

            if not result_response.ok:
                raise requests.RequestException(
                    'Judge0 GET HTTP '
                    + str(result_response.status_code)
                    + ': ' + result_response.text[:1000]
                )

            result = result_response.json()
            status = (result.get('status') or {}).get('id')

            if status not in (1, 2):
                return result

            time.sleep(0.5)

        return {
            'status': {
                'id': 2,
                'description': 'Judge Timeout'
            },
            'message': 'Hết thời gian chờ kết quả từ Judge0.'
        }

    except requests.RequestException:
        raise
    except (ValueError, TypeError) as exc:
        raise requests.RequestException(
            'Phản hồi Judge0 không hợp lệ: ' + str(exc)
        ) from exc


@app.post('/api/submit')
@login_required
def submit():
    user = current_user()
    d = request.get_json(silent=True) or {}

    pid = str(d.get('problem_id', '')).upper()
    code = str(d.get('source_code', ''))

    if pid not in PROBLEM_MAP:
        return jsonify(error='Mã bài không tồn tại.'), 404

    if not code.strip():
        return jsonify(error='Mã nguồn đang trống.'), 400

    if len(code) > 100000:
        return jsonify(error='Mã nguồn vượt quá 100 KB.'), 400

    problem_data = PROBLEM_MAP[pid]
    tests = problem_data.get('tests', [])

    if not isinstance(tests, list) or not tests:
        return jsonify(error='Bài này chưa có test hợp lệ.'), 500

    score = 0
    details = []
    final = 'Wrong Answer'

    try:
        for idx, test in enumerate(tests):
            result = judge_one(
                code,
                str(test.get('input', '')),
                str(test.get('output', ''))
            )

            status = (
                (result.get('status') or {}).get('description')
                or 'Unknown'
            )

            if status == 'Accepted':
                test_score = int(test.get('score', 0))
                score += test_score

                details.append({
                    'test': idx + 1,
                    'status': status,
                    'score': test_score,
                    'time': result.get('time')
                })

            else:
                message = (
                    result.get('compile_output')
                    or result.get('stderr')
                    or result.get('message')
                    or ''
                )

                details.append({
                    'test': idx + 1,
                    'status': status,
                    'score': 0,
                    'message': str(message)[:1200]
                })

                if status in (
                    'Compilation Error',
                    'Compilation error'
                ):
                    final = 'Compilation Error'
                elif status == 'Time Limit Exceeded':
                    final = status
                elif status == 'Memory Limit Exceeded':
                    final = status
                elif status == 'Judge Timeout':
                    final = status
                else:
                    final = 'Wrong Answer'

    except requests.RequestException as exc:
        final = 'Judge Unavailable'
        details = [{
            'test': len(details) + 1,
            'status': 'Không lấy được kết quả từ Judge0.',
            'score': 0,
            'message': str(exc)[:1200]
        }]

    if final != 'Judge Unavailable':
        if score == 100 and all(
            item.get('status') == 'Accepted' for item in details
        ):
            final = 'Accepted'
        elif score > 0:
            final = 'Partially Accepted'

    db().execute(
        'INSERT INTO submissions '
        '(user_id,problem_id,language,source_code,result,score,detail) '
        'VALUES(?,?,?,?,?,?,?)',
        (
            user['id'], pid, 'C++17', code, final,
            score, json.dumps(details, ensure_ascii=False)
        )
    )
    db().commit()

    old = db().execute(
        'SELECT best_score FROM solves '
        'WHERE user_id=? AND problem_id=?',
        (user['id'], pid)
    ).fetchone()

    if score > 0 and (not old or score > old['best_score']):
        db().execute(
            'INSERT INTO solves(user_id,problem_id,best_score) '
            'VALUES(?,?,?) '
            'ON CONFLICT(user_id,problem_id) '
            'DO UPDATE SET best_score=excluded.best_score',
            (user['id'], pid, score)
        )
        db().commit()

    return jsonify(
        result=final,
        score=score,
        details=details
    )


@app.get('/api/submissions')
@login_required
def submissions():
    user = current_user()

    rows = db().execute(
        'SELECT id,problem_id,language,result,score,created_at '
        'FROM submissions WHERE user_id=? '
        'ORDER BY id DESC LIMIT 200',
        (user['id'],)
    ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.get('/api/ranking')
def ranking():
    rows = db().execute(
        'SELECT u.username,COUNT(s.problem_id) solved,'
        'COALESCE(SUM(s.best_score),0) score '
        'FROM users u LEFT JOIN solves s ON s.user_id=u.id '
        'GROUP BY u.id '
        'ORDER BY score DESC,solved DESC,u.username ASC LIMIT 100'
    ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.get('/admin')
def admin_page():
    return render_template('admin.html')


@app.get('/api/admin/overview')
@admin_required
def admin_overview():
    con = db()

    return jsonify(
        users=con.execute(
            'SELECT COUNT(*) FROM users'
        ).fetchone()[0],
        submissions=con.execute(
            'SELECT COUNT(*) FROM submissions'
        ).fetchone()[0],
        problems=len(PROBLEMS),
        accepted=con.execute(
            "SELECT COUNT(*) FROM submissions "
            "WHERE result='Accepted'"
        ).fetchone()[0]
    )


@app.get('/api/admin/users')
@admin_required
def admin_users():
    rows = db().execute(
        'SELECT u.id,u.username,u.is_admin,u.created_at,'
        'COUNT(s.id) submission_count '
        'FROM users u LEFT JOIN submissions s ON s.user_id=u.id '
        'GROUP BY u.id ORDER BY u.id DESC LIMIT 500'
    ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.post('/api/admin/users/<int:uid>/role')
@admin_required
def admin_user_role(uid):
    d = request.get_json(silent=True) or {}
    make_admin = bool(d.get('is_admin'))
    me = current_user()

    if uid == me['id'] and not make_admin:
        return jsonify(
            error='Không thể tự hạ quyền admin đang đăng nhập.'
        ), 400

    cur = db().execute(
        'UPDATE users SET is_admin=? WHERE id=?',
        (1 if make_admin else 0, uid)
    )
    db().commit()

    if not cur.rowcount:
        return jsonify(error='Không tìm thấy tài khoản.'), 404

    return jsonify(ok=True)


@app.get('/api/admin/submissions')
@admin_required
def admin_submissions():
    rows = db().execute(
        'SELECT s.id,u.username,s.problem_id,s.language,'
        's.result,s.score,s.created_at '
        'FROM submissions s JOIN users u ON s.user_id=u.id '
        'ORDER BY s.id DESC LIMIT 500'
    ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.get('/api/admin/problems')
@admin_required
def admin_problems():
    return jsonify(PROBLEMS)


@app.put('/api/admin/problems/<pid>')
@admin_required
def admin_update_problem(pid):
    global PROBLEMS, PROBLEM_MAP

    pid = pid.upper()
    problem_data = PROBLEM_MAP.get(pid)

    if not problem_data:
        return jsonify(error='Không tìm thấy bài.'), 404

    d = request.get_json(silent=True) or {}

    fields = (
        'title', 'topic', 'difficulty', 'statement', 'input',
        'output', 'constraints', 'sampleInput', 'sampleOutput',
        'explanation'
    )

    for key in fields:
        if key in d:
            value = str(d[key]).strip()

            if not value:
                return jsonify(
                    error=f'Trường {key} không được để trống.'
                ), 400

            problem_data[key] = value

    if 'tests' in d:
        tests = d['tests']

        if not isinstance(tests, list) or not tests:
            return jsonify(
                error='Danh sách test phải là mảng không rỗng.'
            ), 400

        total = 0
        clean_tests = []

        for test in tests:
            if not isinstance(test, dict) or not all(
                key in test for key in ('input', 'output', 'score')
            ):
                return jsonify(
                    error='Mỗi test cần input, output và score.'
                ), 400

            try:
                test_score = int(test['score'])
            except (TypeError, ValueError):
                return jsonify(
                    error='Điểm test phải là số nguyên.'
                ), 400

            if test_score < 0:
                return jsonify(
                    error='Điểm test không được âm.'
                ), 400

            clean_tests.append({
                'input': str(test['input']),
                'output': str(test['output']),
                'score': test_score
            })
            total += test_score

        if total != 100:
            return jsonify(
                error='Tổng điểm các test phải bằng 100.'
            ), 400

        problem_data['tests'] = clean_tests

    temp_path = None

    try:
        fd, temp_path = tempfile.mkstemp(
            prefix='problems_',
            suffix='.json',
            dir=os.path.dirname(DATA)
        )

        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(
                PROBLEMS,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_path, DATA)
        temp_path = None

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify(
        ok=True,
        problem={
            k: v for k, v in problem_data.items()
            if k != 'function'
        }
    )


@app.get('/api/health')
def health():
    return jsonify(
        ok=True,
        problems=len(PROBLEMS),
        judge0=JUDGE0_URL
    )


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', '8080')),
        debug=False
    )
