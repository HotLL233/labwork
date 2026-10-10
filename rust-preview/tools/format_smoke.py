"""Exercise XLSX formatting through an isolated HTTP server, never user files."""
import argparse
import copy
import hashlib
import io
import json
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def fixture():
    files={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="格式测试" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml':'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>',
        'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:D3"/><cols><col min="1" max="4" width="10" customWidth="1"/></cols><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>日期</t></is></c><c r="B1" t="inlineStr"><is><t>批号</t></is></c><c r="C1" t="inlineStr"><is><t>测量值</t></is></c><c r="D1" t="inlineStr"><is><t>计算结果</t></is></c></row><row r="2"><c r="C2" t="inlineStr"><is><t>g</t></is></c></row><row r="3"><c r="A3"><v>46000</v></c><c r="B3" t="inlineStr"><is><t>QA-001</t></is></c><c r="C3"><v>1.2345</v></c><c r="D3"><f>C3*2</f><v>2.469</v></c></row></sheetData></worksheet>',
    }
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,text in files.items():archive.writestr(name,text)
    return output.getvalue()


def parts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:return {name:archive.read(name) for name in archive.namelist()}


def contents(data):
    sheet=ET.fromstring(parts(data)['xl/worksheets/sheet1.xml'])
    def semantic(node):
        return (node.tag,sorted(node.attrib.items()),node.text if node.text and node.text.strip() else None,
                tuple(semantic(child) for child in node))
    result={}
    for node in sheet.findall('.//s:c',NS):
        cell=copy.deepcopy(node);cell.attrib.pop('s',None)
        result[cell.attrib['r']]=semantic(cell)
    return result


def run(exe):
    with tempfile.TemporaryDirectory(prefix='labwork-format-') as folder:
        folder=Path(folder);home=folder/'data';process=None
        with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        base=f'http://127.0.0.1:{port}';token=''
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def api(path,body=None,month=None):
            headers={'Origin':base,'X-Labwork-Token':token}
            if month:headers['X-Labwork-Month']=month
            if body is not None and not isinstance(body,bytes):headers['Content-Type']='application/json';body=json.dumps(body).encode()
            try:response=opener.open(urllib.request.Request(base+path,data=body,headers=headers),timeout=15)
            except urllib.error.HTTPError as error:response=error
            with response:
                data=response.read()
                return response.status,json.loads(data) if response.headers.get_content_type()=='application/json' else data
        def ok(path,body=None,month=None):
            status,result=api(path,body,month);assert status==200,(path,status,result);return result
        source=fixture()
        try:
            with (folder/'server.log').open('wb') as log:
                process=subprocess.Popen([str(exe),'--headless','--home',str(home),'--port',str(port)],stdout=log,stderr=log)
            for _ in range(100):
                assert process.poll() is None,'isolated server exited'
                try:page=ok('/').decode();break
                except OSError:time.sleep(.1)
            else:raise AssertionError('server readiness timeout')
            token=re.search(r'name="labwork-token" content="([^"]+)"',page)[1]
            ok('/api/workbook/FORMATQA',source)
            month=ok('/api/catalog')['active_month']
            target=home/'months'/month/'workbooks/FORMATQA.xlsx'
            assert target.read_bytes()==source
            view=ok('/api/book-sheet/FORMATQA/s0',month=month)
            original_hash=view['fingerprint'];assert original_hash==hashlib.sha256(source).hexdigest()
            payload={'sheet':'格式测试','range':'C3:D3','number_format':'0.00','font_name':'Arial','font_size':14,'bold':True,'font_color':'#112233','fill_color':'#FFFF00','alignment':'center','column_width':18,'row_height':28,'fingerprint':original_hash}
            saved=ok('/api/workbook/FORMATQA/format',payload,month)
            changed=target.read_bytes()
            assert saved['fingerprint']==hashlib.sha256(changed).hexdigest()!=original_hash
            assert Path(saved['workbook_path'])==target and saved['view']
            assert contents(changed)==contents(source),'formatting changed cell values or formulas'
            members=parts(changed);old=parts(source)
            assert all(members[name]==data for name,data in old.items() if name not in ('xl/styles.xml','xl/worksheets/sheet1.xml'))
            tree=ET.fromstring(members['xl/worksheets/sheet1.xml']);styles=ET.fromstring(members['xl/styles.xml'])
            cell=tree.find('.//s:c[@r="D3"]',NS);xf=styles.find('s:cellXfs',NS)[int(cell.attrib['s'])]
            font=styles.find('s:fonts',NS)[int(xf.attrib['fontId'])]
            assert font.find('s:name',NS).attrib['val']=='Arial' and font.find('s:sz',NS).attrib['val']=='14'
            assert font.find('s:b',NS) is not None and font.find('s:color',NS).attrib['rgb'].endswith('112233')
            fill=styles.find('s:fills',NS)[int(xf.attrib['fillId'])]
            assert fill.find('s:patternFill/s:fgColor',NS).attrib['rgb'].endswith('FFFF00')
            assert xf.find('s:alignment',NS).attrib['horizontal']=='center'
            assert int(xf.attrib['numFmtId'])==2 or any(node.attrib.get('numFmtId')==xf.attrib['numFmtId'] and node.attrib.get('formatCode')=='0.00' for node in styles.findall('s:numFmts/s:numFmt',NS))
            assert any(int(col.attrib['min'])<=3<=int(col.attrib['max']) and float(col.attrib['width'])==18 for col in tree.findall('s:cols/s:col',NS))
            assert float(tree.find('.//s:row[@r="3"]',NS).attrib['ht'])==28
            assert ok('/api/book-sheet/FORMATQA/s0',month=month)['fingerprint']==saved['fingerprint']
            assert ok('/api/workbook/FORMATQA/download?month='+month)==changed
            backups=list((home/'backups').glob('FORMATQA-format-*.xlsx'))
            assert len(backups)==1 and backups[0].read_bytes()==source
            assert api('/api/workbook/FORMATQA/format',payload,month)[0]==409
            for invalid in ({'range':'A0:B3'},{'range':'A1:XFD1048576'},{'font_size':0},{'column_width':-1},{'row_height':1},{'font_color':'not-a-color'},{'alignment':'sideways'},{'number_format':'INVALID_FORMAT'}):
                assert api('/api/workbook/FORMATQA/format',{**payload,'fingerprint':saved['fingerprint'],**invalid},month)[0]==400,invalid
                assert target.read_bytes()==changed
            previous=(date.fromisoformat(month+'-01')-timedelta(days=1)).strftime('%Y-%m')
            history=home/'months'/previous/'workbooks';history.mkdir(parents=True);(history/'FORMATQA.xlsx').write_bytes(source)
            status,_=api('/api/workbook/FORMATQA/format',payload,previous)
            assert status==409,status
            assert (history/'FORMATQA.xlsx').read_bytes()==source
        finally:
            if process is not None:
                if process.poll() is None:process.terminate()
                process.wait(timeout=10)
        result=dict.fromkeys(['format_persisted_in_xlsx','style_properties_readback','formula_and_values_unchanged','unrelated_parts_unchanged','actual_target_reported','download_matches_saved','backup_matches_original','stale_fingerprint_rejected','invalid_format_and_range_rejected','history_month_read_only'],True)
        (ROOT.parent/'runtime/format-smoke-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',type=Path,default=ROOT/'target/release/labwork-preview.exe')
    run(parser.parse_args().exe.resolve())
