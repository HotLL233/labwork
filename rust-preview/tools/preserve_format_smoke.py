"""End-to-end source writeback preserves original XLSX layout on disposable files."""
import argparse
import io
import json
import socket
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import format_smoke
import month_smoke as server
import startup_smoke

ROOT = Path(__file__).resolve().parents[1]
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
AC = 'http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac'
NS = {'s': S}


def fixture():
    parts = server.parts(format_smoke.fixture())
    parts['xl/styles.xml'] = f'''<styleSheet xmlns="{S}" xmlns:mc="{MC}" xmlns:x14ac="{AC}" mc:Ignorable="x14ac">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy/mm/dd"/></numFmts>
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/></cellXfs>
</styleSheet>'''.encode()
    parts['xl/worksheets/sheet1.xml'] = f'''<worksheet xmlns="{S}" xmlns:r="{R}" xmlns:mc="{MC}" xmlns:x14ac="{AC}" mc:Ignorable="x14ac">
<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:H40"/>
<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane xSplit="2" ySplit="2" topLeftCell="C3" activePane="bottomRight" state="frozen"/><selection pane="bottomRight" activeCell="C4" sqref="C4"/></sheetView></sheetViews>
<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.25"/>
<cols><col min="1" max="1" width="16.5" style="2" customWidth="1"/><col min="2" max="8" width="21.25" style="1" bestFit="1" customWidth="1" hidden="0"/></cols>
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>日期</t></is></c><c r="B1" t="inlineStr"><is><t>批号</t></is></c><c r="C1" t="inlineStr"><is><t>测量值</t></is></c><c r="D1" t="inlineStr"><is><t>计算结果</t></is></c></row>
<row r="2"><c r="C2" t="inlineStr"><is><t>g</t></is></c></row>
<row r="3" ht="25" customHeight="1"><c r="A3" s="2"><v>46000</v></c><c r="B3" s="1" t="inlineStr"><is><t>HISTORY-KEEP</t></is></c><c r="C3" s="1"><v>1.25</v></c><c r="D3" s="1"><f>C3*2</f><v>2.5</v></c></row>
<row r="4" spans="1:8" ht="29" customHeight="1" customFormat="1" s="1" x14ac:dyDescent="0.4"><c r="A4" s="2"/><c r="B4" s="1" ph="1"/><c r="C4" s="1" cm="1" vm="2" ph="1"/><c r="D4" s="1"><f>C4*2</f></c><c r="E4" s="1" ph="1"/></row>
</sheetData><mergeCells count="1"><mergeCell ref="F1:H1"/></mergeCells>
<hyperlinks><hyperlink ref="B3" r:id="rId1"/></hyperlinks>
<printOptions horizontalCentered="1"/><pageMargins left="0.2" right="0.2" top="0.4" bottom="0.4" header="0.1" footer="0.1"/>
<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0" r:id="rId2"/>
</worksheet>'''.encode()
    parts['xl/worksheets/_rels/sheet1.xml.rels'] = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/keep" TargetMode="External"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/printerSettings" Target="../printerSettings/printerSettings1.bin"/></Relationships>'
    parts['xl/printerSettings/printerSettings1.bin'] = b'isolated printer settings sentinel'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items(): archive.writestr(name, data)
    return output.getvalue()


def semantic(node):
    return (node.tag, tuple(sorted(node.attrib.items())), node.text.strip() if node.text else '', tuple(semantic(child) for child in node))


def run(exe):
    startup_smoke.EXE = exe
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); server.PORT = probe.getsockname()[1]
    original = fixture(); before = server.parts(original)
    old = ET.fromstring(before['xl/worksheets/sheet1.xml'])
    with tempfile.TemporaryDirectory(prefix='labwork-preserve-format-') as directory:
        folder = Path(directory); home = folder / 'data'; source = folder / '关联原表.xlsx'
        source.write_bytes(original); process = None
        with (folder / 'server.log').open('wb') as log:
            try:
                process = server.start(home, log)
                server.ok('/api/workbook/LAYOUT/source-import', {'path': str(source), 'name': '格式保留隔离测试'})
                catalog = server.ok('/api/catalog')
                month = catalog['active_month']; version = catalog['version']
                project = next(project for project in catalog['projects'] if project['code'] == 'LAYOUT')
                method = next(method for method in project['methods'] if method['kind'] == 'workbook_formula')
                assert source.read_bytes() == original, 'association modified original'
                for expected_row in (4, 5):
                    payload = {'project': 'LAYOUT', 'method': method['key'], 'entries': [{'date': month + '-13', 'batch': 'LAYOUT-' + str(expected_row), 'measurement': {'col_C': '3'}}]}
                    preview = server.ok('/api/preview', payload, month)
                    assert preview['rows'] == [expected_row], preview['rows']
                    saved_before = source.read_bytes()
                    candidate = server.ok(preview['download'], month=month)
                    assert source.read_bytes() == saved_before, 'preview modified original'
                    receipt = server.ok('/api/commit/' + preview['id'], {}, month)
                    assert receipt['source_sync']['state'] == 'synced'
                    assert source.read_bytes() == candidate
                    assert (home / 'months' / month / 'workbooks/LAYOUT.xlsx').read_bytes() == candidate
                    parts = server.parts(candidate); tree = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
                    assert tree.attrib == old.attrib, (old.attrib, tree.attrib)
                    for name in ('sheetPr', 'dimension', 'sheetViews', 'sheetFormatPr', 'cols', 'mergeCells', 'hyperlinks', 'printOptions', 'pageMargins', 'pageSetup'):
                        assert semantic(tree.find('s:' + name, NS)) == semantic(old.find('s:' + name, NS)), name
                    for row in (1, 2, 3):
                        assert semantic(tree.find(f's:sheetData/s:row[@r="{row}"]', NS)) == semantic(old.find(f's:sheetData/s:row[@r="{row}"]', NS)), row
                    row = tree.find(f's:sheetData/s:row[@r="{expected_row}"]', NS)
                    template = old.find('s:sheetData/s:row[@r="4"]', NS)
                    excluded = {'r'} if expected_row == 4 else {'r', 'spans'}
                    assert {k: v for k, v in row.attrib.items() if k not in excluded} == {k: v for k, v in template.attrib.items() if k not in excluded}, row.attrib
                    cell = row.find(f's:c[@r="C{expected_row}"]', NS)
                    attrs = {'r': f'C{expected_row}', 's': '1'}
                    if expected_row == 4: attrs.update({'cm': '1', 'vm': '2', 'ph': '1'})
                    assert cell.attrib == attrs, cell.attrib
                    assert cell.find('s:v', NS).text == '3'
                    result = row.find(f's:c[@r="D{expected_row}"]/s:v', NS)
                    assert result is not None and float(result.text) == 6
                    for name, data in before.items():
                        if name != 'xl/worksheets/sheet1.xml': assert parts[name] == data, name
                    assert tree.find('s:hyperlinks/s:hyperlink', NS).attrib['{' + R + '}id'] == 'rId1'
                    assert tree.find('s:pageSetup', NS).attrib['{' + R + '}id'] == 'rId2'
                view = server.ok('/api/book-sheet/LAYOUT/s0', month=month)
                server.ok('/api/workbook/LAYOUT/format', {'sheet': method['sheet'], 'range': 'D5', 'number_format': '0.0000', 'fingerprint': view['fingerprint']}, month)
                formatted = server.parts(source.read_bytes())
                assert ET.fromstring(formatted['xl/styles.xml']).attrib == ET.fromstring(before['xl/styles.xml']).attrib
                formatted_sheet = ET.fromstring(formatted['xl/worksheets/sheet1.xml'])
                assert formatted_sheet.attrib == old.attrib
                for name in ('sheetViews', 'cols', 'dimension', 'hyperlinks', 'pageSetup'):
                    assert semantic(formatted_sheet.find('s:' + name, NS)) == semantic(old.find('s:' + name, NS)), name
                assert float(formatted_sheet.find('s:sheetData/s:row[@r="5"]/s:c[@r="D5"]/s:v', NS).text) == 6
                view = server.ok('/api/book-sheet/LAYOUT/s0', month=month)
                server.ok('/api/workbook/LAYOUT/format', {'sheet': method['sheet'], 'range': 'F4:F6', 'font_color': '#123456', 'fingerprint': view['fingerprint']}, month)
                inherited = server.parts(source.read_bytes()); inherited_sheet = ET.fromstring(inherited['xl/worksheets/sheet1.xml'])
                styles = ET.fromstring(inherited['xl/styles.xml']).find('s:cellXfs', NS)
                for ref in ('F4', 'F6'):
                    cell = inherited_sheet.find('.//s:c[@r="' + ref + '"]', NS); xf = styles[int(cell.attrib['s'])]
                    assert xf.attrib['numFmtId'] == '2' and xf.attrib['borderId'] == '1', (ref, xf.attrib)
                    assert xf.find('s:alignment', NS).attrib['horizontal'] == 'center'
            finally:
                server.stop(process)
    result = {'ok': True, 'version': version, 'associated_source_and_mirror_match': True, 'qualified_attributes_preserved': True, 'explicit_format_preserves_qualified_attributes_and_layout': True, 'format_inherits_row_and_column_styles': True, 'frozen_pane_and_selection_preserved': True, 'fixed_column_widths_preserved': True, 'dimension_not_shrunk': True, 'historical_cells_and_styles_unchanged': True, 'empty_cell_attributes_preserved': True, 'new_row_inherits_style_and_height': True, 'merges_print_settings_links_preserved': True, 'unrelated_zip_members_unchanged': True, 'preview_no_write': True, 'isolated_fixture_only': True}
    (ROOT.parent / 'runtime/preserve-format-smoke-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'target/release/labwork-preview.exe')
    run(parser.parse_args().exe.resolve())
