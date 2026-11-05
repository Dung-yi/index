"""Desktop control panel. Never stops an unidentified service on the configured port."""
import hashlib
import contextlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import update_core as updates

ROOT = Path(__file__).resolve().parent
PORT = 8765
URL = f'http://127.0.0.1:{PORT}'
INSTALL = hashlib.sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()
PYTHON = str(Path(sys.executable).with_name('python.exe')) if os.name == 'nt' else sys.executable
WINDOWLESS = str(Path(PYTHON).with_name('pythonw.exe')) if os.name == 'nt' else PYTHON

def request(path, body=None):
    req = urllib.request.Request(URL+path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=2) as response:
        return json.load(response)

def running():
    try:
        value = request('/api/health')
        return value.get('app') == updates.APP and value.get('installation') == INSTALL
    except (OSError,ValueError):
        return False

def occupied():
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1',PORT)) == 0

def stop_server():
    if not running():
        if occupied():
            raise RuntimeError('8765 連接埠正由舊版或其他程式使用。請先手動關閉舊版工作日誌的啟動視窗。')
        return
    control = json.loads((ROOT/'data/control.json').read_text(encoding='utf-8'))
    if control.get('installation') != INSTALL:
        raise RuntimeError('主機識別不符，未停止任何程式')
    request('/api/control/stop', {'token':control['token']})
    for _ in range(60):
        if not occupied():
            return
        time.sleep(.1)
    raise RuntimeError('主機尚未停止，請稍後重試')

def has_owner():
    db = ROOT/'data/diary.sqlite3'
    if not db.exists():
        return False
    try:
        with contextlib.closing(sqlite3.connect(db)) as connection:
            return bool(connection.execute('SELECT 1 FROM owner').fetchone())
    except sqlite3.Error:
        raise RuntimeError('無法讀取本機帳號，請保留 data 資料夾並聯絡協助人員')

def start_server():
    if running():
        return
    if occupied():
        raise RuntimeError('請先關閉舊版工作日誌的啟動視窗，再按「開啟工作日誌」。未停止其他程式。')
    if not has_owner():
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        process = subprocess.Popen([PYTHON,str(ROOT/'server.py'),'--setup-only'],cwd=ROOT,creationflags=flags)
        process.wait()
        if process.returncode or not has_owner():
            raise RuntimeError('本機帳號尚未建立，請重新開啟並完成帳號設定')
    logs = ROOT/'data/host.log'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with logs.open('ab') as log:
        process = subprocess.Popen([PYTHON,'-u',str(ROOT/'server.py'),'--port',str(PORT)],cwd=ROOT,stdout=log,stderr=log,
                                   stdin=subprocess.DEVNULL,creationflags=flags)
    for _ in range(120):
        if running():
            return
        if process.poll() is not None:
            break
        time.sleep(.1)
    raise RuntimeError('主機啟動失敗，請檢查 data/host.log，或確認連接埠沒有被使用')

def install_release(release):
    manifest, files = release
    current = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))['version']
    if updates.version_tuple(manifest['version']) <= updates.version_tuple(current):
        raise ValueError('更新檔與目前版本相同或較舊，未安裝')
    updates.verify_files(manifest,files)
    was_running = running()
    stop_server()
    try:
        updates.apply_release(ROOT,manifest,files)
    except Exception:
        if was_running:
            start_server()
        raise
    start_server()
    webbrowser.open(URL)
    return dict(message='更新已完成，工作資料已保留。',version=manifest['version'])

def command(action,package=None):
    with updates.installation_lock(ROOT):
        if (ROOT/'data/update-journal.json').exists():
            stop_server()
            updates.recover_update(ROOT)
            raise RuntimeError('已復原上次中斷的更新，請關閉啟動中心，再重新開啟。')
        if action == 'open':
            start_server()
            webbrowser.open(URL)
            return dict(message='主機運行中，可在瀏覽器使用工作日誌。')
        if action == 'stop':
            stop_server()
            return dict(message='主機已停止。按「開啟工作日誌」即可重新啟動。')
        if action == 'backup':
            path = updates.data_backup(ROOT)
            return dict(message='備份完成：'+str(path) if path else '尚未建立本機資料。')
        if action in ['check','online-install']:
            manifest,base = updates.online_manifest()
            current = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))['version']
            newer = updates.version_tuple(manifest['version']) > updates.version_tuple(current)
            if action == 'check':
                return dict(message='有新版可安裝。' if newer else '目前已是最新版本。',newer=newer,
                            version=manifest['version'],notes=manifest.get('notes',''))
            if not newer:
                return dict(message='目前已是最新版本。')
            return install_release(updates.online_files(manifest,base))
        if action in ['inspect-file','file-install']:
            release = updates.read_release_zip(package)
            current = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))['version']
            newer = updates.version_tuple(release[0]['version']) > updates.version_tuple(current)
            if action == 'inspect-file':
                return dict(message='更新檔已通過驗證。' if newer else '更新檔與目前版本相同或較舊。',
                            newer=newer,version=release[0]['version'],notes=release[0].get('notes',''))
            return install_release(release)
        raise ValueError('不支援的操作')

def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--action',choices=['open','stop','backup','check','online-install','inspect-file','file-install'])
    parser.add_argument('--package')
    parser.add_argument('--result')
    parser.add_argument('--shortcut',action='store_true')
    parser.add_argument('--quiet',action='store_true')
    args=parser.parse_args()
    if args.action:
        try:
            result=dict(ok=True,**command(args.action,args.package))
        except Exception as error:
            import traceback
            try:
                with (ROOT/'data/launcher.log').open('a',encoding='utf-8') as log:
                    traceback.print_exc(file=log)
            except OSError:
                pass
            text = str(error) if isinstance(error,(RuntimeError,ValueError)) else '無法完成操作，請檢查網路或資料夾權限。更新未完成，請重試或改用更新檔。'
            result=dict(ok=False,message=text)
        if args.result:
            updates.atomic_write(Path(args.result),json.dumps(result,ensure_ascii=False).encode('utf-8'))
        else:
            print(json.dumps(result,ensure_ascii=False))
        return
    (ROOT/'data').mkdir(exist_ok=True)
    # Run a copy, allowing updates to replace panel.exe while this window is open.
    binary=(ROOT/'panel.exe').read_bytes()
    runtime=ROOT/'data/runtime'
    runtime.mkdir(exist_ok=True)
    executable=runtime/('panel-'+hashlib.sha256(binary).hexdigest()[:20]+'.exe')
    if not executable.exists():
        updates.atomic_write(executable,binary)
    panel_command=[str(executable),'--root',str(ROOT),'--python',WINDOWLESS]
    if args.shortcut:
        panel_command.append('--shortcut')
    if args.quiet:
        panel_command.append('--quiet')
    process=subprocess.Popen(panel_command,cwd=ROOT)
    process.wait()
    if process.returncode:
        if args.quiet:
            raise SystemExit(process.returncode)
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,'啟動中心未能開啟，請確認公司允許執行此程式，並保留 data 資料夾。','每日工作日誌',16)

if __name__ == '__main__':
    main()
