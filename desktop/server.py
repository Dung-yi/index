"""Single-owner diary; loopback only. Publish privately via approved Tailscale Serve."""
import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import threading
import contextlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB = DATA / 'diary.sqlite3'
APP_ID = 'local-work-diary'

def app_version():
    try:
        return json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        return '1.0.0'

def installation_id():
    return hashlib.sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()
STEPS = ['parking_checked_online', 'parking_written_details', 'parking_submitted_original',
         'fine_scanned', 'fine_emailed_admin', 'fine_uploaded_eip', 'fine_written_details', 'fine_submitted_original',
         'insurance_scanned', 'insurance_saved_folder', 'insurance_uploaded_eip',
         'insurance_original_delivered', 'insurance_receipt_delivered']
FIELDS = {'work_date', 'title', 'category', 'content', 'status', *STEPS}
FAILURES = []

@contextlib.contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=20)
    try:
        c.execute('PRAGMA journal_mode=WAL')
        with c:
            yield c
    finally:
        c.close()

def initialize():
    DATA.mkdir(exist_ok=True)
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS owner(email TEXT, salt TEXT, hash TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires REAL);
        CREATE TABLE IF NOT EXISTS works(id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports(source_id TEXT PRIMARY KEY);''')

def setup():
    with connect() as c:
        if c.execute('SELECT 1 FROM owner').fetchone():
            return
        print('首次使用：請建立本機版登入帳號（不會變更原網站帳號）。')
        email = input('Email: ').strip().lower()
        if '@' not in email:
            raise ValueError('請輸入有效的 Email')
        password = getpass.getpass('本機密碼（至少 12 字元）: ')
        if len(password) < 12 or password != getpass.getpass('再輸入一次密碼: '):
            raise ValueError('密碼至少 12 字元，兩次必須一致')
        salt = secrets.token_hex(32)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
        c.execute('INSERT INTO owner VALUES(?,?,?)', (email, salt, digest))

def backup():
    folder = ROOT / 'backups'
    folder.mkdir(exist_ok=True)
    dest = folder / ('diary-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
    with connect() as src, contextlib.closing(sqlite3.connect(dest)) as target:
        src.backup(target)
        target.execute('DELETE FROM sessions')
        target.commit()
    return dest

def clean(value):
    if not isinstance(value, dict) or set(value) - FIELDS - {'user_id', 'id', 'created_at'}:
        raise ValueError('工作紀錄格式錯誤')
    result = {k: v for k, v in value.items() if k in FIELDS}
    for k, v in result.items():
        if k in STEPS:
            if not isinstance(v, bool):
                raise ValueError('流程勾選格式錯誤')
        elif not isinstance(v, str) or len(v) > 200000:
            raise ValueError('欄位格式錯誤或內容太長')
    if 'work_date' in result:
        dt.date.fromisoformat(result['work_date'])
    if 'status' in result and result['status'] not in ['未處理', '處理中', '已完成']:
        raise ValueError('工作進度格式錯誤')
    return result

def insert(c, value):
    row = dict(work_date=dt.date.today().isoformat(), title='', content='', category='其他', status='未處理')
    row.update({k: False for k in STEPS})
    row.update(clean(value))
    if not (row['title'].strip() or row['content'].strip()):
        raise ValueError('請填寫標題或工作內容')
    row['created_at'] = value.get('created_at') or dt.datetime.now(dt.timezone.utc).isoformat()
    row['user_id'] = 'local-owner'
    cursor = c.execute('INSERT INTO works(body) VALUES(?)', (json.dumps(row, ensure_ascii=False),))
    row['id'] = cursor.lastrowid
    c.execute('UPDATE works SET body=? WHERE id=?', (json.dumps(row, ensure_ascii=False), row['id']))
    return row

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Do not log passwords, session cookies or diary content.

    def respond(self, code, value, cookie=None, mime='application/json; charset=utf-8', download=None):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; connect-src 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
        self.end_headers()
        self.wfile.write(payload)

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['diary_session'].value if 'diary_session' in cookie else ''
        except Exception:
            return ''

    def user(self, c):
        token = hashlib.sha256(self.token().encode()).hexdigest()
        if c.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?', (token, time.time())).fetchone():
            return dict(id='local-owner', email=c.execute('SELECT email FROM owner').fetchone()[0])
        return None

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            self.respond(200, dict(app=APP_ID, version=app_version(), installation=installation_id()))
            return
        if path in ['/', '/index.html', '/local-client.js']:
            name = 'index.html' if path != '/local-client.js' else 'local-client.js'
            mime = 'text/html; charset=utf-8' if name.endswith('html') else 'text/javascript; charset=utf-8'
            self.respond(200, (ROOT / name).read_bytes(), mime=mime)
            return
        with connect() as c:
            user = self.user(c)
            if path == '/api/session':
                self.respond(200, dict(data=dict(session=dict(user=user) if user else None), error=None))
            elif not user:
                self.respond(401, dict(error=dict(message='請先登入本機版')))
            elif path == '/api/export':
                rows = [json.loads(x[0]) for x in c.execute('SELECT body FROM works ORDER BY id')]
                self.respond(200, dict(version=1, rows=rows), download='diary-backup.json')
            else:
                self.respond(404, dict(error=dict(message='找不到頁面')))

    def do_POST(self):
        try:
            # Browser requests must be same-origin and use a non-simple content type.
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                self.respond(403, dict(error=dict(message='拒絕跨網站要求')))
                return
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('需要 JSON 格式')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4_000_000:
                raise ValueError('資料大小超過限制')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('格式錯誤')
            if self.path == '/api/control/stop':
                expected = getattr(self.server, 'control_token', '')
                supplied = body.get('token')
                if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
                    self.respond(403, dict(error=dict(message='無權停止主機')))
                    return
                self.respond(200, dict(ok=True))
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            with connect() as c:
                if self.path == '/api/login':
                    now = time.time()
                    FAILURES[:] = [x for x in FAILURES if x > now - 300]
                    if len(FAILURES) >= 10:
                        self.respond(429, dict(error=dict(message='嘗試次數過多，請五分鐘後再試')))
                        return
                    owner = c.execute('SELECT email,salt,hash FROM owner').fetchone()
                    password = str(body.get('password', ''))
                    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(owner[1]), 600000).hex()
                    if str(body.get('email', '')).strip().lower() != owner[0] or not hmac.compare_digest(actual, owner[2]):
                        FAILURES.append(now)
                        self.respond(401, dict(error=dict(message='Email 或本機密碼錯誤')))
                        return
                    raw = secrets.token_urlsafe(32)
                    c.execute('DELETE FROM sessions WHERE expires<?', (now,))
                    c.execute('INSERT INTO sessions VALUES(?,?)', (hashlib.sha256(raw.encode()).hexdigest(), now+43200))
                    user = dict(id='local-owner', email=owner[0])
                    secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
                    self.respond(200, dict(data=dict(user=user, session=dict(user=user)), error=None),
                                 'diary_session='+raw+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'+secure)
                    return
                user = self.user(c)
                if not user:
                    self.respond(401, dict(error=dict(message='請先登入本機版')))
                    return
                if self.path == '/api/logout':
                    c.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(self.token().encode()).hexdigest(),))
                    self.respond(200, dict(error=None), 'diary_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                    return
                if self.path != '/api/query' or body.get('table') != 'work_diary':
                    raise ValueError('不支援的操作')
                op = body.get('op', 'select')
                filters = body.get('filters', {})
                if set(filters) - {'id', 'user_id'}:
                    raise ValueError('篩選格式錯誤')
                if filters.get('user_id', 'local-owner') != 'local-owner':
                    raise ValueError('無權存取此帳號')
                if op == 'select':
                    rows = [json.loads(x[0]) for x in c.execute('SELECT body FROM works')]
                    rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in filters.items())]
                    rows.sort(key=lambda r: (r.get('work_date', ''), r.get('created_at', '')), reverse=True)
                elif op == 'insert':
                    values = body.get('values')
                    values = values if isinstance(values, list) else [values]
                    rows = [insert(c, v) for v in values]
                elif op in ['update', 'delete']:
                    if 'id' not in filters:
                        raise ValueError('必須指定工作紀錄')
                    found = c.execute('SELECT body FROM works WHERE id=?', (filters['id'],)).fetchone()
                    if not found:
                        raise ValueError('工作紀錄已刪除，請重新整理')
                    row = json.loads(found[0])
                    if op == 'delete':
                        c.execute('DELETE FROM works WHERE id=?', (row['id'],))
                    else:
                        row.update(clean(body['values']))
                        if not (row['title'].strip() or row['content'].strip()):
                            raise ValueError('請填寫標題或工作內容')
                        c.execute('UPDATE works SET body=? WHERE id=?', (json.dumps(row, ensure_ascii=False), row['id']))
                    rows = [row]
                else:
                    raise ValueError('不支援的操作')
                c.commit()
                self.respond(200, dict(data=rows[0] if body.get('single') and rows else rows, error=None))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self.respond(400, dict(error=dict(message='資料格式錯誤，請檢查日期及內容')))
        except Exception:
            self.respond(500, dict(error=dict(message='儲存失敗，請檢查主機磁碟空間後重試')))

def import_file(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows = raw.get('rows') if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError('需要工作紀錄陣列或備份 JSON')
    backup()
    count = 0
    with connect() as c:
        for value in rows:
            source = str(value.get('user_id', 'backup')) + ':' + str(value.get('id', ''))
            if value.get('id') is None:
                source = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            if c.execute('SELECT 1 FROM imports WHERE source_id=?', (source,)).fetchone():
                continue
            insert(c, value)
            c.execute('INSERT INTO imports VALUES(?)', (source,))
            count += 1
    print('已匯入', count, '筆；原資料不會被刪除。')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--import-file')
    parser.add_argument('--backup', action='store_true')
    parser.add_argument('--setup-only', action='store_true')
    args = parser.parse_args()
    initialize()
    setup()
    if args.setup_only:
        return
    if args.import_file:
        import_file(args.import_file)
        return
    if args.backup:
        print(backup())
        return
    backup()
    print(f'工作日誌已啟動：http://127.0.0.1:{args.port} （關閉此視窗即停止）')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.control_token = secrets.token_urlsafe(32)
    control = DATA / 'control.json'
    control.write_text(json.dumps(dict(token=server.control_token, pid=os.getpid(), port=args.port,
                                      installation=installation_id())), encoding='utf-8')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        control.unlink(missing_ok=True)

if __name__ == '__main__':
    main()
