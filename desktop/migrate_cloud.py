"""Run interactively on the owner PC; reads only the signed-in cloud user's records."""
import getpass
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import server

URL = 'https://yftqcaupzjrwtntqxxfw.supabase.co'
KEY = 'sb_publishable_o6G3PznegosN1WWYswC9mA_PPsFiMaP'

def call(path, token=None, payload=None):
    headers = {'apikey': KEY, 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(URL + path, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

def main():
    server.initialize()
    server.setup()
    print('搬移只會讀取原網站資料；不刪除雲端紀錄，也不會自動同步。')
    email = input('原網站 Email: ').strip()
    password = getpass.getpass('原網站密碼（不會顯示或儲存）: ')
    auth = call('/auth/v1/token?grant_type=password', payload={'email':email, 'password':password})
    del password
    token = auth['access_token']
    uid = auth['user']['id']
    rows = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({'select':'*', 'user_id':'eq.'+uid, 'order':'id.asc', 'limit':500, 'offset':offset})
        page = call('/rest/v1/work_diary?' + query, token=token)
        rows.extend(page)
        if not page:
            break
        offset += len(page)
    folder = server.ROOT / 'backups'
    folder.mkdir(exist_ok=True)
    path = folder / ('cloud-export-' + server.dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    path.write_text(json.dumps({'version':1, 'rows':rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    server.import_file(path)
    print('雲端匯出備份已存至', path)

if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        print('搬移未完成：雲端拒絕要求（HTTP', error.code, '）。請確認登入資料及專案狀態。')
        raise SystemExit(1)
    except (urllib.error.URLError, TimeoutError):
        print('搬移未完成：無法連到雲端，請確認公司網路允許 Supabase 連線。')
        raise SystemExit(1)
