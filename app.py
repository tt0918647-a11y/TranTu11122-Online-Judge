
import os
import json
import time
import base64
import sqlite3
import requests
import re

from functools import wraps
from flask import Flask, render_template, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash


BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCE = os.path.join(BASE, 'instance')
os.makedirs(INSTANCE, exist_ok=True)

app = Flask(__name__, instance_relative_config=True)
app.secret_key = os.environ.get(
    'SECRET_KEY', 'change-this-secret-before-deployment'
)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

DB = os.path.join(INSTANCE, 'trantu.sqlite3')
DATA = os.path.join(BASE, 'data', 'problems.json')

JUDGE0_URL = os.environ.get(
    'JUDGE0_URL', 'https://ce.judge0.com'
).rstrip('/')
JUDGE0_TOKEN = os.environ.get('JUDGE0_TOKEN', '')
CPP_LANGUAGE_ID = int(os.environ.get('CPP_LANGUAGE_ID', '54'))

with open(DATA, encoding='utf-8') as f:
    PROBLEMS = json.load(f)

PROBLEM_MAP = {p['id'].upper(): p for p in PROBLEMS}


# ==================== DATABASE ====================

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
    con.execute('PRAGMA foreign_keys = ON')

    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS submissions (
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

    CREATE TABLE IF NOT EXISTS solves (
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
        con.execute("""
            ALTER TABLE users
            ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0
        """)

    admin_user = os.environ.get('ADMIN_USERNAME', '').strip()
    admin_password = os.environ.get('ADMIN_PASSWORD', '')

    if admin_user and admin_password:
        row = con.execute(
            'SELECT id FROM users WHERE username = ?',
            (admin_user,)
        ).fetchone()

        if row:
            con.execute(
                'UPDATE users SET is_admin = 1 WHERE id = ?',
                (row[0],)
            )
        else:
            con.execute(
                """
                INSERT INTO users(username, password_hash, is_admin)
                VALUES (?, ?, 1)
                """,
                (
                    admin_user,
                    generate_password_hash(admin_password)
                )
            )

    con.commit()
    con.close()


init_db()


# ==================== AUTHENTICATION ====================

def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None

    return db().execute(
        'SELECT id, username, is_admin FROM users WHERE id = ?',
        (user_id,)
    ).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if not current_user():
            return jsonify(error='Vui lòng đăng nhập.'), 401
        return fn(*args, **kwargs)
    return wrap


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


# ==================== PAGES AND PROBLEMS ====================

@app.get('/')
def index():
    return render_template('index.html')


@app.get('/api/problems')
def problems():
    q = request.args.get('q', '').lower()
    topic = request.args.get('topic', '')
    difficulty = request.args.get('difficulty', '')

    arr = []

    for p in PROBLEMS:
        if q and q not in (
            p['id'] + ' ' + p['title'] + ' ' + p['topic']
        ).lower():
            continue

        if topic and p['topic'] != topic:
            continue

        if difficulty and p['difficulty'] != difficulty:
            continue

        arr.append({
            k: v for k, v in p.items()
            if k not in ('tests', 'function')
        })

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


# ==================== ACCOUNT ROUTES ====================

@app.post('/api/register')
def register():
    d = request.get_json(silent=True) or {}

    username = str(d.get('username', '')).strip()
    password = str(d.get('password', ''))

    if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', username):
        return jsonify(
            error='Tên tài khoản phải dài 3–24 ký tự, '
                  'chỉ gồm chữ, số và dấu gạch dưới.'
        ), 400

    if len(password) < 8:
        return jsonify(
            error='Mật khẩu cần ít nhất 8 ký tự.'
        ), 400

    try:
        cur = db().execute(
            """
            INSERT INTO users(username, password_hash)
            VALUES (?, ?)
            """,
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
        'SELECT * FROM users WHERE username = ?',
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
        """
        SELECT problem_id, best_score
        FROM solves
        WHERE user_id = ?
        """,
        (user['id'],)
    ).fetchall()

    return jsonify(
        user={
            'username': user['username'],
            'is_admin': bool(user['is_admin'])
        },
        solved=[dict(r) for r in rows]
    )


# ==================== JUDGE0 ====================

class JudgeUnavailable(Exception):
    pass


class JudgeTimeout(Exception):
    pass


def judge_one(code, stdin, expected):
    headers = {'Content-Type': 'application/json'}

    if JUDGE0_TOKEN:
        headers['X-Auth-Token'] = JUDGE0_TOKEN

    def encode64(value):
        return base64.b64encode(
            str(value).encode('utf-8')
        ).decode('ascii')

    def decode64(value):
        if not value:
            return value

        try:
            return base64.b64decode(
                value, validate=True
            ).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return value

    body = {
        'source_code': encode64(code),
        'language_id': CPP_LANGUAGE_ID,
        'stdin': encode64(stdin),
        'expected_output': encode64(expected),
        'cpu_time_limit': 2,
        'wall_time_limit': 5,
        'memory_limit': 128000
    }

    try:
        response = requests.post(
            JUDGE0_URL + '/submissions/',
            params={
                'base64_encoded': 'true',
                'wait': 'false'
            },
            json=body,
            headers=headers,
            timeout=20
        )
    except requests.RequestException as e:
        raise JudgeUnavailable(
            'Không gửi được bài tới Judge0: ' + str(e)
        ) from e

    print(
        'JUDGE0 POST HTTP:', response.status_code,
        flush=True
    )

    if not response.ok:
        raise JudgeUnavailable(
            'Judge0 POST HTTP '
            + str(response.status_code)
            + ': ' + response.text[:800]
        )

    try:
        token = response.json().get('token')
    except ValueError as e:
        raise JudgeUnavailable(
            'Judge0 trả về JSON không hợp lệ khi gửi bài.'
        ) from e

    if not token:
        raise JudgeUnavailable(
            'Judge0 không trả submission token.'
        )

    print('JUDGE0 TOKEN RECEIVED: YES', flush=True)

    deadline = time.monotonic() + 60
    last_status = None

    while time.monotonic() < deadline:
        try:
            rr = requests.get(
                JUDGE0_URL + '/submissions/' + token,
                params={
                    'base64_encoded': 'true',
                    'fields': (
                        'token,status,stdout,stderr,'
                        'compile_output,message,time,memory'
                    )
                },
                headers=headers,
                timeout=15
            )
        except requests.RequestException as e:
            raise JudgeUnavailable(
                'Lỗi kết nối khi lấy kết quả Judge0: ' + str(e)
            ) from e

        print('JUDGE0 GET HTTP:', rr.status_code, flush=True)

        if not rr.ok:
            raise JudgeUnavailable(
                'Judge0 GET HTTP '
                + str(rr.status_code)
                + ': ' + rr.text[:800]
            )

        try:
            result = rr.json()
        except ValueError as e:
            raise JudgeUnavailable(
                'Judge0 trả kết quả JSON không hợp lệ.'
            ) from e

        status = result.get('status') or {}
        status_id = status.get('id')
        description = status.get('description', 'Unknown')

        if status_id is None:
            raise JudgeUnavailable(
                'Phản hồi Judge0 không có status: '
                + str(result)[:500]
            )

        if status_id != last_status:
            print(
                'JUDGE0 STATUS:',
                status_id,
                description,
                flush=True
            )
            last_status = status_id

        if status_id not in (1, 2):
            for key in (
                'stdout', 'stderr', 'compile_output', 'message'
            ):
                result[key] = decode64(result.get(key))

            return result

        time.sleep(1)

    print(
        'JUDGE0 POLLING TIMEOUT; LAST STATUS:',
        last_status,
        flush=True
    )

    raise JudgeTimeout(
        'Quá 60 giây chưa nhận được kết quả kết thúc '
        'từ Judge0. Trạng thái cuối: '
        + str(last_status)
    )


# ==================== SUBMISSION ROUTE ====================

@app.post('/api/submit')
@login_required
def submit():
    user = current_user()
    d = request.get_json(silent=True) or {}

    pid = str(
        d.get('problem_id', d.get('pid', ''))
    ).upper()

    code = d.get('code', d.get('source_code', ''))

    if not isinstance(code, str) or not code.strip():
        return jsonify(error='Mã nguồn không được để trống.'), 400

    if len(code) > 200000:
        return jsonify(error='Mã nguồn quá dài.'), 400

    p = PROBLEM_MAP.get(pid)

    if not p:
        return jsonify(error='Không tìm thấy bài.'), 404

    tests = p.get('tests', [])

    if not isinstance(tests, list) or not tests:
        return jsonify(
            error='Bài tập chưa có bộ test hợp lệ.'
        ), 500

    details = []
    score = 0
    total_score = sum(
        max(0, int(t.get('score', 0)))
        for t in tests
    )

    if total_score <= 0:
        return jsonify(
            error='Tổng điểm test phải lớn hơn 0.'
        ), 500

    final = 'Wrong Answer'

    try:
        for idx, test in enumerate(tests):
            result = judge_one(
                code,
                test.get('input', ''),
                test.get('output', '')
            )

            status_obj = result.get('status') or {}
            status = status_obj.get('description', 'Unknown')

            if status == 'Accepted':
                points = max(0, int(test.get('score', 0)))
                score += points

                details.append({
                    'test': idx + 1,
                    'status': 'Accepted',
                    'score': points,
                    'time': result.get('time'),
                    'memory': result.get('memory')
                })
            else:
                details.append({
                    'test': idx + 1,
                    'status': status,
                    'score': 0,
                    'message': (
                        result.get('compile_output')
                        or result.get('stderr')
                        or result.get('message')
                        or ''
                    )[:1200],
                    'time': result.get('time'),
                    'memory': result.get('memory')
                })

        if score == total_score:
            final = 'Accepted'
        elif score > 0:
            final = 'Partially Accepted'
        else:
            statuses = [x['status'] for x in details]

            if any('Compilation Error' in s for s in statuses):
                final = 'Compilation Error'
            elif any('Time Limit Exceeded' in s for s in statuses):
                final = 'Time Limit Exceeded'
            elif any('Memory Limit Exceeded' in s for s in statuses):
                final = 'Memory Limit Exceeded'
            elif any('Runtime Error' in s for s in statuses):
                final = 'Runtime Error'
            else:
                final = 'Wrong Answer'

    except JudgeTimeout as e:
        final = 'Judge Timeout'
        details.append({
            'status': 'Judge Timeout',
            'score': 0,
            'message': str(e)
        })

    except JudgeUnavailable as e:
        final = 'Judge Unavailable'
        details.append({
            'status': 'Judge Unavailable',
            'score': 0,
            'message': str(e)
        })

    except Exception as e:
        app.logger.exception('Unexpected error in /api/submit')
        final = 'Judge Unavailable'
        details.append({
            'status': 'Lỗi hệ thống khi chấm bài',
            'score': 0,
            'message': str(e)[:800]
        })

    # Lưu submission kể cả khi Judge0 gặp sự cố.
    try:
        con = db()

        cur = con.execute(
            """
            INSERT INTO submissions(
                user_id, problem_id, language,
                source_code, result, score, detail
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user['id'],
                pid,
                'cpp',
                code,
                final,
                score,
                json.dumps(details, ensure_ascii=False)
            )
        )

        submission_id = cur.lastrowid

        # Chỉ cập nhật thành tích khi kết quả chấm không lỗi dịch vụ.
        if final not in ('Judge Timeout', 'Judge Unavailable'):
            con.execute(
                """
                INSERT INTO solves(user_id, problem_id, best_score)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, problem_id)
                DO UPDATE SET best_score =
                    MAX(best_score, excluded.best_score)
                """,
                (user['id'], pid, score)
            )

        con.commit()

    except sqlite3.Error:
        app.logger.exception('Could not save submission')
        return jsonify(
            result=final,
            score=score,
            total_score=total_score,
            details=details,
            error='Không lưu được kết quả vào cơ sở dữ liệu.'
        ), 500

    payload = {
        'submission_id': submission_id,
        'result': final,
        'score': score,
        'total_score': total_score,
        'details': details
    }

    if final == 'Judge Unavailable':
        return jsonify(payload), 503

    if final == 'Judge Timeout':
        return jsonify(payload), 504

    return jsonify(payload)


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', '5000')),
        debug=False
    )
