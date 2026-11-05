"""Verified, program-only updates from the owner's repository or a local release ZIP."""
import ast
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import urllib.request
import zipfile

APP = 'local-work-diary'
REPO = 'Dung-yi/index'
BRANCH = 'desktop'
ALLOWED = {'server.py', 'local-client.js', 'index.html', '啟動.cmd',
           'migrate_cloud.py', '搬移原網站資料.cmd', '使用說明.txt', 'launcher.py',
           'update_core.py', 'panel.exe', '建立桌面捷徑.cmd', 'diary.ico'}
LIMIT = 8_000_000

def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', value):
        raise ValueError('更新版本格式錯誤')
    return tuple(map(int, value.split('.')))

def validate_manifest(value):
    if not isinstance(value, dict) or value.get('app') != APP or value.get('format') != 1:
        raise ValueError('這不是工作日誌的更新檔')
    version_tuple(value.get('version'))
    files = value.get('files')
    if not isinstance(files, dict) or set(files) != ALLOWED:
        raise ValueError('更新檔案清單不完整或包含不允許的檔案')
    for name, digest in files.items():
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('檔案驗證資訊錯誤')
    return value

def verify_files(manifest, files):
    validate_manifest(manifest)
    if set(files) != ALLOWED or sum(map(len, files.values())) > LIMIT:
        raise ValueError('更新檔案不完整或過大')
    for name, data in files.items():
        if hashlib.sha256(data).hexdigest() != manifest['files'][name]:
            raise ValueError('更新檔案驗證失敗：' + name)
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), filename=name)
    return manifest, files

def read_release_zip(path):
    if Path(path).stat().st_size > LIMIT:
        raise ValueError('更新檔太大')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        manifests = [n for n in names if n == 'manifest.json' or n == '工作日誌本機版/manifest.json']
        if len(manifests) != 1 or len(names) != len(set(names)):
            raise ValueError('找不到唯一的更新版本資訊')
        prefix = manifests[0][:-len('manifest.json')]
        expected = {prefix + n for n in ALLOWED | {'manifest.json'}}
        if set(names) != expected or sum(x.file_size for x in archive.infolist()) > LIMIT:
            raise ValueError('更新壓縮檔包含額外檔案或大小超過限制')
        manifest = validate_manifest(json.loads(archive.read(prefix+'manifest.json')))
        files = {n:archive.read(prefix+n) for n in ALLOWED}
    return verify_files(manifest, files)

def download(url):
    request = urllib.request.Request(url, headers={'User-Agent':'LocalWorkDiary-Updater/1', 'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(LIMIT+1)
        if len(data) > LIMIT:
            raise ValueError('下載內容過大')
        return data

def online_manifest():
    commit = json.loads(download(f'https://api.github.com/repos/{REPO}/commits/{BRANCH}'))['sha']
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('更新來源版本錯誤')
    base = f'https://raw.githubusercontent.com/{REPO}/{commit}/desktop/'
    return validate_manifest(json.loads(download(base+'manifest.json'))), base

def online_files(manifest, base):
    # base is supplied only by online_manifest, never a URL from the manifest.
    from urllib.parse import quote
    return verify_files(manifest, {n:download(base+quote(n)) for n in ALLOWED})

@contextlib.contextmanager
def installation_lock(root):
    root = Path(root)
    data = root/'data'
    data.mkdir(exist_ok=True)
    stream = (data/'application.lock').open('a+b')
    if stream.tell() == 0:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('另一個啟動視窗正在啟動或更新，請稍後重試')
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == 'nt':
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()

def atomic_write(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.diary-', dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

def data_backup(root):
    root = Path(root)
    folder = root/'backups'
    folder.mkdir(exist_ok=True)
    db = root/'data/diary.sqlite3'
    if not db.exists():
        return None
    target = folder/('diary-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
    with contextlib.closing(sqlite3.connect(db)) as source, contextlib.closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
        dest.execute('DELETE FROM sessions')
        dest.commit()
    return target

def recover_update(root):
    root = Path(root)
    journal = root/'data/update-journal.json'
    if not journal.exists():
        return False
    record = json.loads(journal.read_text(encoding='utf-8'))
    backup_name = record.get('backup', '')
    if not re.fullmatch(r'program-\d{8}-\d{6}-\d{6}\.zip', backup_name):
        raise ValueError('更新復原資訊錯誤，請保留資料並聯絡協助人員')
    path = root/'backups'/backup_name
    with zipfile.ZipFile(path) as archive:
        allowed = ALLOWED | {'manifest.json'}
        if not set(archive.namelist()) <= allowed:
            raise ValueError('更新備份格式錯誤')
        for name in allowed:
            if name in archive.namelist():
                atomic_write(root/name, archive.read(name))
            else:
                (root/name).unlink(missing_ok=True)
    journal.unlink()
    return True

def apply_release(root, manifest, files):
    root = Path(root)
    verify_files(manifest, files)  # Complete validation before stopping/writing is handled by caller too.
    if (root/'data/update-journal.json').exists():
        raise RuntimeError('有尚未復原的更新，請先重新啟動啟動視窗')
    data_backup(root)
    folder = root/'backups'
    folder.mkdir(exist_ok=True)
    program = folder/('program-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.zip')
    with zipfile.ZipFile(program,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in ALLOWED | {'manifest.json'}:
            if (root/name).is_file():
                archive.write(root/name,name)
    journal = root/'data/update-journal.json'
    atomic_write(journal,json.dumps({'backup':program.name}).encode())
    try:
        for name,data in files.items():
            atomic_write(root/name,data)
        atomic_write(root/'manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'))
        journal.unlink()
    except Exception:
        recover_update(root)
        raise
    return program
