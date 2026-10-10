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
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root.parent))
from records.calculations import calculate

BASE='http://127.0.0.1:19846'
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
    exe=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'target/release/labwork-preview.exe'
    process=subprocess.Popen([str(exe),'--headless','--port','19846','--home',str(home)],stdout=log,stderr=log)
    try:
        for attempt in range(60):
            try:
                page=request('/').decode();break
            except Exception:
                if process.poll() is not None:raise RuntimeError((Path(folder)/'server.log').read_text())
                time.sleep(.1)
        token=re.search(r'name="labwork-token" content="([a-f0-9]+)"',page)[1]
        fails(403,'/api/catalog',headers={'Host':'evil.example'})
        active=request('/api/catalog')
        assert len(active['projects'])==2
        working=home/'months'/active['active_month']/'workbooks'
        linked={}
        for code in ('D006','Q'):
            month=active['active_month']
            path=Path(folder)/(code+'-理化')/month[:4]/month.replace('-','.')/(code+'-理化-'+month.replace('-','')+'.xlsx')
            path.parent.mkdir(parents=True);path.write_bytes((working/(code+'.xlsx')).read_bytes())
            request('/api/workbook/'+code+'/source',{'path':str(path)})
            linked[code]=path
        total=0
        last_payload=None
        for project in catalog:
            for method in project['methods']:
                view=request(f"/api/sheet/{project['code']}/{method['key']}")
                assert view['view']['columns'] and view['view']['styles']
                if project['code']=='D006' and method['key']=='chloride':
                    historical=next(c for r in view['view']['rows'] for c in r['cells'] if c['ref']=='A4')
                    assert historical['value']=='2026-10-08' and historical['raw']=='20261008'
                raw={f['key']:f['sample'] for f in method['fields']}
                payload={'project':project['code'],'method':method['key'],'date':'2026-10-09','batch':f'RUST-TEST-{total}','notes':'isolated smoke','measurements':[raw,dict(raw)]}
                _,expected=calculate(method['kind'],raw,method['params'])
                before=members(working/f"{project['code']}.xlsx")
                result=request('/api/preview',payload)
                assert result['outputs'][0]['display']==expected['display'],(method['key'],result['outputs'],expected)
                if 'result' in expected:assert Decimal(result['outputs'][0]['result'])==Decimal(expected['result'])
                assert len(result['rows'])==2
                pending=[r for r in result['view']['rows'] if r['number'] in result['rows']]
                date_col=method['columns']['date'];batch_col=method['columns']['batch']
                assert next(c for c in pending[1]['cells'] if c['ref'].startswith(date_col) and c['ref'][len(date_col):].isdigit())['value']==''
                assert next(c for c in pending[1]['cells'] if c['ref'].startswith(batch_col) and c['ref'][len(batch_col):].isdigit())['value']==''
                preview=home/'previews'/f"{result['id']}.xlsx"
                after=members(preview)
                changed=[name for name in before if before[name]!=after[name]]
                assert any(n.startswith('xl/worksheets/') for n in changed) and all(n.startswith('xl/worksheets/') or n=='xl/styles.xml' for n in changed),changed
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                sheet_path=next(n for n in changed if n.startswith('xl/worksheets/'))
                before_xml,after_xml=ET.fromstring(before[sheet_path]),ET.fromstring(after[sheet_path])
                assert [m.attrib for m in before_xml.findall('.//s:mergeCell',ns)]==[m.attrib for m in after_xml.findall('.//s:mergeCell',ns)]
                if 'result' in result['outputs'][0]:
                    for r,o in zip(result['rows'],result['outputs']):
                        v=after_xml.find(f".//s:c[@r='{method['columns']['result']}{r}']/s:v",ns)
                        assert v is not None and Decimal(v.text)==Decimal(o['result'])
                assert before==members(working/f"{project['code']}.xlsx"),'Preview must not write working file'
                receipt=request(f"/api/commit/{result['id']}",{})
                assert receipt['state']=='done' and receipt['sheet']==method['sheet'] and receipt['rows']==result['rows']
                assert Path(receipt['workbook_path']).resolve()==(working/f"{project['code']}.xlsx").resolve()
                assert request(receipt['download'])==(working/f"{project['code']}.xlsx").read_bytes()
                retry=request(f"/api/commit/{result['id']}",{})
                assert retry['state']=='done' and retry['workbook_path']==receipt['workbook_path']
                assert members(working/f"{project['code']}.xlsx")==after
                assert members(linked[project['code']])==after,'Confirmed result missing from associated original'
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
        assert len(list((home/'backups').glob('source-*.xlsx')))==15
        method=catalog[1]['methods'][0]
        raw={f['key']:f['sample'] for f in method['fields']}
        payload={'project':'D006','method':'water','entries':[
            {'date':'2026-10-11','batch':'MULTI-03','measurement':dict(raw,result='23')},
            {'date':'2026-10-10','batch':'MULTI-01','measurement':dict(raw,result='11')},
            {'date':'2026-10-10','batch':'MULTI-02','measurement':dict(raw,result='21')},
            {'date':'2026-10-10','batch':'MULTI-01','measurement':dict(raw,result='12')},
            {'date':'2026-10-10','batch':'MULTI-02','measurement':dict(raw,result='22')}]}
        result=request('/api/preview',payload)
        assert [e['batch'] for e in result['entries']]==['MULTI-01','MULTI-01','MULTI-02','MULTI-02','MULTI-03']
        assert [o['display'] for o in result['outputs']]==['11.00000','12.00000','21.00000','22.00000','23.00000']
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        def sheet_cells(preview):
            data=members(home/'previews'/f"{preview['id']}.xlsx")
            view=next(ET.fromstring(data[n]) for n in data if n.startswith('xl/worksheets/') and ET.fromstring(data[n]).find(f".//s:c[@r='B{preview['rows'][0]}']",ns) is not None and b'MULTI' in data[n])
            def value(ref):
                cell=view.find(f".//s:c[@r='{ref}']",ns)
                if cell is None:return ''
                return ''.join(cell.itertext()).strip()
            return view,value
        xml,value=sheet_cells(result)
        rr=result['rows']
        assert [value('A'+str(r)) for r in rr]==['46305','','','','46306']
        assert [value('B'+str(r)) for r in rr]==['MULTI-01','','MULTI-02','','MULTI-03']
        assert xml.find('s:mergeCells',ns) is None or all(not any(str(r) in m.attrib['ref'] for r in rr) for m in xml.find('s:mergeCells',ns))
        highlighted=[r for r in result['view']['rows'] if r['number'] in rr]
        assert [r['cells'][0]['value'] for r in highlighted]==['2026-10-10','','','','2026-10-11']
        request(f"/api/commit/{result['id']}",{})
        again=request('/api/preview',{'project':'D006','method':'water','entries':[{'date':'2026-10-11','batch':'MULTI-03','measurement':raw}]*2})
        _,value=sheet_cells(again)
        assert [value('A'+str(r)) for r in again['rows']]==['','']
        assert [value('B'+str(r)) for r in again['rows']]==['','']
        assert members(home/'previews'/f"{again['id']}.xlsx")['xl/styles.xml']==members(working/'D006.xlsx')['xl/styles.xml'],'Date styles must be reused'
        assert request('/api/sheet/D006/water')['next_row']==again['rows'][0]
        # A full 200-row candidate must show every pending result, not just the last 13.
        many=request('/api/preview',{'project':'D006','method':'water','entries':[{'date':'2026-10-12','batch':f'BULK-{i//2:03d}','measurement':raw} for i in range(200)]})
        assert len(many['rows'])==200 and len([r for r in many['view']['rows'] if r['number'] in many['rows']])==200
        fails(400,'/api/preview',dict(payload,entries=payload['entries']*41))
        invalid=dict(payload['entries'][0],date='2026-02-30')
        fails(400,'/api/preview',dict(payload,entries=[invalid]))
        before_invalid=members(working/'D006.xlsx')
        invalid=dict(payload['entries'][0],measurement={})
        with closing(sqlite3.connect(home/'preview.sqlite3')) as db:count_before=db.execute('SELECT count(*) FROM previews').fetchone()[0]
        fails(400,'/api/preview',dict(payload,entries=[payload['entries'][1],invalid]))
        with closing(sqlite3.connect(home/'preview.sqlite3')) as db:assert count_before==db.execute('SELECT count(*) FROM previews').fetchone()[0]
        assert before_invalid==members(working/'D006.xlsx')
        fails(400,'/api/preview',dict(payload,measurements=[raw]))
        assert len(request('/api/history'))>=18
        qbook=linked['Q']
        parts=members(qbook)
        wb=ET.fromstring(parts['xl/workbook.xml'])
        props=wb.find('s:workbookPr',ns)
        if props is None:props=ET.SubElement(wb,'{'+ns['s']+'}workbookPr')
        props.set('date1904','1');parts['xl/workbook.xml']=ET.tostring(wb,encoding='utf-8',xml_declaration=True)
        with zipfile.ZipFile(qbook,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in parts.items():z.writestr(name,data)
        q1904=request('/api/preview',{'project':'Q','method':'melting','entries':[{'date':'2026-10-20','batch':'DATE1904','measurement':{'start':'150','end':'153'}}]})
        pending=[r for r in q1904['view']['rows'] if r['number'] in q1904['rows']]
        assert pending[0]['cells'][0]['value']=='2026-10-20' and pending[0]['cells'][0]['raw']=='44853'
        assert original_hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.xlsx')}
        result={'methods':total,'associated_originals_match_confirmed_results':True,'decimal_parity':True,'unrelated_zip_members_preserved':True,'preview_no_write':True,'idempotence':True,'concurrent_conflict':True,'interrupted_finalize_recovery':True,'csrf_and_host_check':True,'sqlite_snapshots':True,'originals_unchanged':True,'multi_batch_grouping':True,'date_display_and_single_labels':True,'continued_group_blanks':True,'all_200_pending_rows_visible':True,'invalid_batch_atomic_rejection':True,'date1904_and_style_reuse':True,'historical_compact_dates':True}
        (root.parent/'runtime/rust-smoke-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))
    finally:
        process.terminate();process.wait(timeout=10);log.close()
