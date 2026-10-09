"""HTTP, SQLite and XLSX regression on isolated copies; never touches the production service."""
import concurrent.futures
from contextlib import closing
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from decimal import Decimal
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root.parent))
from records.calculations import calculate

BASE='http://127.0.0.1:18802'
catalog=json.loads((root/'catalog.json').read_text(encoding='utf-8'))
source=root.parent/'runtime/rust-preview/workbooks'
original_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.xlsx')}
token=''
def request(path,body=None,headers=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers=dict({'Content-Type':'application/json','X-Labwork-Token':token} if body is not None else {},**(headers or {})))
    with urllib.request.urlopen(req,timeout=10) as r:
        data=r.read()
        return json.loads(data) if r.headers.get_content_type()=='application/json' else data
def fails(code,path,body=None,headers=None):
    try:request(path,body,headers)
    except urllib.error.HTTPError as exc:assert exc.code==code,(exc.code,exc.read())
    else:raise AssertionError('Expected rejection')
def members(path):
    with zipfile.ZipFile(path) as z:return {n:z.read(n) for n in z.namelist()}

with tempfile.TemporaryDirectory(prefix='labwork-rust-test-') as folder:
    home=Path(folder)/'isolated'
    home.mkdir();(home/'.labwork-rust-preview').write_text('test')
    shutil.copytree(source,home/'workbooks')
    log=open(Path(folder)/'server.log','w')
    process=subprocess.Popen([str(root/'target/release/labwork-preview.exe'),'--home',str(home)],stdout=log,stderr=log)
    try:
        for attempt in range(60):
            try:
                page=request('/').decode();break
            except Exception:
                if process.poll() is not None:raise RuntimeError((Path(folder)/'server.log').read_text())
                time.sleep(.1)
        token=re.search(r'name="labwork-token" content="([a-f0-9]+)"',page)[1]
        fails(403,'/api/catalog',headers={'Host':'evil.example'})
        assert len(request('/api/catalog')['projects'])==12
        total=0
        last_payload=None
        for project in catalog:
            for method in project['methods']:
                view=request(f"/api/sheet/{project['code']}/{method['key']}")
                assert view['view']['columns'] and view['view']['styles']
                raw={f['key']:f['sample'] for f in method['fields']}
                payload={'project':project['code'],'method':method['key'],'date':'2026-10-09','batch':f'RUST-TEST-{total}','notes':'isolated smoke','measurements':[raw,dict(raw)]}
                _,expected=calculate(method['kind'],raw,method['params'])
                before=members(home/'workbooks'/f"{project['code']}.xlsx")
                result=request('/api/preview',payload)
                assert result['outputs'][0]['display']==expected['display'],(method['key'],result['outputs'],expected)
                if 'result' in expected:assert Decimal(result['outputs'][0]['result'])==Decimal(expected['result'])
                assert len(result['rows'])==2
                preview=home/'previews'/f"{result['id']}.xlsx"
                after=members(preview)
                changed=[name for name in before if before[name]!=after[name]]
                assert len(changed)==1 and changed[0].startswith('xl/worksheets/'),changed
                assert before==members(home/'workbooks'/f"{project['code']}.xlsx"),'Preview must not write working file'
                assert request(f"/api/commit/{result['id']}",{})['state']=='done'
                assert request(f"/api/commit/{result['id']}",{})['state']=='done'
                assert members(home/'workbooks'/f"{project['code']}.xlsx")==after
                total+=1;last_payload=payload
        assert total==14
        fails(403,'/api/preview',last_payload,{'X-Labwork-Token':'wrong'})
        fails(403,'/api/preview',last_payload,{'Origin':'https://evil.example'})
        bad=dict(last_payload,measurements=[{'start':'153','end':'150'}]);fails(400,'/api/preview',bad)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a,b=list(pool.map(lambda _:request('/api/preview',last_payload),range(2)))
        request(f"/api/commit/{a['id']}",{})
        fails(409,f"/api/commit/{b['id']}",{})
        # Simulate interrupted database finalization after a successful file replacement.
        with closing(sqlite3.connect(home/'preview.sqlite3')) as db, db:
            db.execute("UPDATE previews SET state='writing' WHERE id=?",[a['id']])
        assert request(f"/api/commit/{a['id']}",{})['state']=='done'
        with closing(sqlite3.connect(home/'preview.sqlite3')) as db:
            assert db.execute("SELECT count(*) FROM previews WHERE state='done'").fetchone()[0]==15
            assert json.loads(db.execute('SELECT method_snapshot FROM previews LIMIT 1').fetchone()[0])['kind']
        assert len(list((home/'backups').glob('*.xlsx')))==15
        assert original_hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.xlsx')}
        result={'methods':total,'decimal_parity':True,'unrelated_zip_members_preserved':True,'preview_no_write':True,'idempotence':True,'concurrent_conflict':True,'interrupted_finalize_recovery':True,'csrf_and_host_check':True,'sqlite_snapshots':True,'originals_unchanged':True}
        (root.parent/'runtime/rust-smoke-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))
    finally:
        process.terminate();process.wait(timeout=10);log.close()
