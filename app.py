import os, json, time, sqlite3, requests, re, tempfile
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash

BASE=os.path.dirname(os.path.abspath(__file__))
app=Flask(__name__, instance_relative_config=True)
app.secret_key=os.environ.get('SECRET_KEY','change-this-secret-before-deployment')
app.config['MAX_CONTENT_LENGTH']=1024*1024
DB=os.path.join(app.instance_path,'trantu.sqlite3')
DATA=os.path.join(BASE,'data','problems.json')
JUDGE0_URL=os.environ.get('JUDGE0_URL','https://ce.judge0.com').rstrip('/')
JUDGE0_TOKEN=os.environ.get('JUDGE0_TOKEN','')
CPP_LANGUAGE_ID=int(os.environ.get('CPP_LANGUAGE_ID','54'))
with open(DATA,encoding='utf-8') as f: PROBLEMS=json.load(f)
PROBLEM_MAP={p['id']:p for p in PROBLEMS}
os.makedirs(app.instance_path,exist_ok=True)

def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB); g.db.row_factory=sqlite3.Row
    return g.db
@app.teardown_appcontext
def close_db(exc):
    c=g.pop('db',None)
    if c is not None:c.close()
def init_db():
    con=sqlite3.connect(DB)
    con.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP); CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,problem_id TEXT NOT NULL,language TEXT NOT NULL,source_code TEXT NOT NULL,result TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(user_id) REFERENCES users(id)); CREATE TABLE IF NOT EXISTS solves(user_id INTEGER NOT NULL,problem_id TEXT NOT NULL,best_score INTEGER NOT NULL,PRIMARY KEY(user_id,problem_id));""")
    cols={r[1] for r in con.execute('PRAGMA table_info(users)').fetchall()}
    if 'is_admin' not in cols: con.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
    admin_user=os.environ.get('ADMIN_USERNAME','').strip()
    admin_password=os.environ.get('ADMIN_PASSWORD','')
    if admin_user and admin_password:
        row=con.execute('SELECT id FROM users WHERE username=?',(admin_user,)).fetchone()
        if row:
            con.execute('UPDATE users SET is_admin=1 WHERE id=?',(row[0],))
        else:
            con.execute('INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,1)',(admin_user,generate_password_hash(admin_password)))
    con.commit();con.close()
init_db()
def current_user():
    if not session.get('user_id'): return None
    return db().execute('SELECT id,username,is_admin FROM users WHERE id=?',(session['user_id'],)).fetchone()
def admin_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        u=current_user()
        if not u: return jsonify(error='Vui lòng đăng nhập.'),401
        if not u['is_admin']: return jsonify(error='Chỉ quản trị viên mới được truy cập.'),403
        return fn(*a,**kw)
    return wrap

def login_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not current_user():return jsonify(error='Vui lòng đăng nhập.'),401
        return fn(*a,**kw)
    return wrap
@app.get('/')
def index():return render_template('index.html')
@app.get('/api/problems')
def problems():
    q=request.args.get('q','').lower(); topic=request.args.get('topic',''); diff=request.args.get('difficulty','')
    arr=[{k:v for k,v in p.items() if k not in ('tests','function')} for p in PROBLEMS if (not q or q in (p['id']+' '+p['title']+' '+p['topic']).lower()) and (not topic or p['topic']==topic) and (not diff or p['difficulty']==diff)]
    return jsonify(arr)
@app.get('/api/problems/<pid>')
def problem(pid):
    p=PROBLEM_MAP.get(pid.upper())
    if not p:return jsonify(error='Không tìm thấy bài.'),404
    return jsonify({k:v for k,v in p.items() if k not in ('tests','function')})
@app.post('/api/register')
def register():
    d=request.get_json(force=True); u=str(d.get('username','')).strip(); pw=str(d.get('password',''))
    if not re.fullmatch(r'[A-Za-z0-9_]{3,24}',u):return jsonify(error='Tên tài khoản dài 3–24 ký tự, chỉ gồm chữ, số, dấu gạch dưới.'),400
    if len(pw)<8:return jsonify(error='Mật khẩu cần ít nhất 8 ký tự.'),400
    try:
        cur=db().execute('INSERT INTO users(username,password_hash) VALUES(?,?)',(u,generate_password_hash(pw)));db().commit();session['user_id']=cur.lastrowid
        return jsonify(username=u)
    except sqlite3.IntegrityError:return jsonify(error='Tên tài khoản đã tồn tại.'),409
@app.post('/api/login')
def login():
    d=request.get_json(force=True);u=str(d.get('username','')).strip();pw=str(d.get('password',''))
    row=db().execute('SELECT * FROM users WHERE username=?',(u,)).fetchone()
    if not row or not check_password_hash(row['password_hash'],pw):return jsonify(error='Sai tên tài khoản hoặc mật khẩu.'),401
    session['user_id']=row['id'];return jsonify(username=row['username'])
@app.post('/api/logout')
def logout():session.clear();return jsonify(ok=True)
@app.get('/api/me')
def me():
    u=current_user()
    if not u:return jsonify(user=None)
    rows=db().execute('SELECT problem_id,best_score FROM solves WHERE user_id=?',(u['id'],)).fetchall()
    return jsonify(user={'username':u['username'],'is_admin':bool(u['is_admin'])},solved=[dict(x) for x in rows])


def judge_one(code, stdin, expected):
    import base64

    headers = {'Content-Type': 'application/json'}
    if JUDGE0_TOKEN:
        headers['X-Auth-Token'] = JUDGE0_TOKEN

    def encode64(s):
        return base64.b64encode(s.encode('utf-8')).decode('ascii')

    body = {
        'source_code': encode64(code),
        'language_id': CPP_LANGUAGE_ID,
        'stdin': encode64(stdin),
        'expected_output': encode64(expected),
        'cpu_time_limit': 2,
        'wall_time_limit': 5,
        'memory_limit': 128000
    }

    r = requests.post(
        JUDGE0_URL.rstrip('/') + '/submissions/',
        params={'base64_encoded': 'true', 'wait': 'false'},
        json=body,
        headers=headers,
        timeout=20
    )

    if not r.ok:
        raise requests.RequestException(
            'Judge0 POST HTTP ' + str(r.status_code)
            + ': ' + r.text[:1000]
        )

    token = r.json().get('token')
    if not token:
        raise requests.RequestException(
            'Judge0 không trả submission token.'
        )

    deadline = time.time() + 60

    while time.time() < deadline:
        rr = requests.get(
            JUDGE0_URL.rstrip('/') + '/submissions/' + token,
            params={
                'base64_encoded': 'true',
                'fields': 'token,status,stdout,stderr,compile_output,message,time,memory'
            },
            headers=headers,
            timeout=15
        )

        if not rr.ok:
            raise requests.RequestException(
                'Judge0 GET HTTP ' + str(rr.status_code)
                + ': ' + rr.text[:1000]
            )

        result = rr.json()
        status = (result.get('status') or {}).get('id')

        if status not in (1, 2):
            for key in ('stdout', 'stderr', 'compile_output', 'message'):
                value = result.get(key)
                if value:
                    try:
                        result[key] = base64.b64decode(
                            value, validate=True
                        ).decode('utf-8')
                    except (ValueError, UnicodeDecodeError):
                        pass

            return result

        time.sleep(1)

    return {
        'status': {'id': 2, 'description': 'Judge Timeout'},
        'message': 'Hết thời gian chờ kết quả từ Judge0.'
    }

