import hashlib
import io
import re
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from lxml import etree as ET
from django.conf import settings
from .calculations import formulas

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
N = {'s': NS}
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024


def xml(data):
    try:
        return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False))
    except ET.XMLSyntaxError as exc:
        raise ValueError('工作簿 XML 结构无效') from exc


def fingerprint(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def workbook_path(book):
    path = (settings.WORKBOOK_ROOT / book.relative_path).resolve()
    if not path.is_relative_to(settings.WORKBOOK_ROOT) or path.suffix.lower() != '.xlsx':
        raise ValueError('目标工作簿不在允许的目录内')
    return path


def read_archive(data):
    if isinstance(data, Path):
        if data.stat().st_size > MAX_ARCHIVE:
            raise ValueError('工作簿不得超过 20 MB')
        data = data.read_bytes()
    if len(data) > MAX_ARCHIVE:
        raise ValueError('工作簿不得超过 20 MB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 3000 or sum(x.file_size for x in infos) > MAX_EXPANDED:
                raise ValueError('工作簿解压体积或文件数量过大')
            names = [x.filename for x in infos]
            if len(set(names)) != len(names) or any('..' in PurePosixPath(x).parts or x.startswith('/') for x in names):
                raise ValueError('工作簿压缩结构不合法')
            if any('vbaProject' in x for x in names):
                raise ValueError('当前版本不接收含宏的文件')
            parts = {info.filename: archive.read(info) for info in infos}
    except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        raise ValueError('无法读取有效的 xlsx 工作簿') from exc
    if 'xl/workbook.xml' not in parts or 'xl/_rels/workbook.xml.rels' not in parts:
        raise ValueError('缺少工作簿结构')
    strings = []
    if 'xl/sharedStrings.xml' in parts:
        strings = [''.join(si.xpath('.//s:t/text()', namespaces=N)) for si in xml(parts['xl/sharedStrings.xml']).findall('s:si', N)]
    relations = {item.get('Id'): item.get('Target') for item in xml(parts['xl/_rels/workbook.xml.rels']) if item.get('TargetMode') != 'External'}
    sheets = {}
    for sheet in xml(parts['xl/workbook.xml']).findall('s:sheets/s:sheet', N):
        target = relations.get(sheet.get(f'{{{REL}}}id'), '')
        path = target.lstrip('/') if target.startswith('/') else str(PurePosixPath('xl') / target)
        if path not in parts or not path.startswith('xl/worksheets/'):
            raise ValueError('工作表链接无效')
        sheets[sheet.get('name')] = path
    return parts, infos, strings, sheets


def cell_value(cell, strings):
    if cell.get('t') == 'inlineStr':
        return ''.join(cell.find('s:is', N).itertext()) if cell.find('s:is', N) is not None else ''
    value = cell.findtext('s:v', '', N)
    if cell.get('t') == 's':
        try:
            return strings[int(value)]
        except (ValueError, IndexError):
            raise ValueError('共享字符串引用无效') from None
    return value


def inspect_workbook(path):
    _, _, _, sheets = read_archive(path)
    return list(sheets)


def browse_sheet(path, sheet_name, start=1, count=80):
    parts, _, strings, sheets = read_archive(path)
    if sheet_name not in sheets:
        raise ValueError('工作表不存在')
    root = xml(parts[sheets[sheet_name]])
    rows = []
    for row in root.findall('s:sheetData/s:row', N):
        row_no = int(row.get('r'))
        if row_no < start:
            continue
        cells = [{'ref': c.get('r'), 'value': cell_value(c, strings),
            'formula': c.find('s:f', N) is not None, 'error': c.get('t') == 'e'} for c in row.findall('s:c', N)]
        if any(c['value'] for c in cells):
            rows.append({'number': row_no, 'cells': cells})
        if len(rows) >= count:
            break
    return rows


BASE_COLUMNS = {'date': 'A', 'batch': 'B', 'mass': 'C', 'volume': 'D', 'result': 'E', 'notes': 'F'}
SHEET_NAMES = {'chloride': '氯离子', 'hydrogen': '氢离子', 'total_n': '总氮', 'ammonia_n': '氨氮',
    'ammonia': '氨水', 'effective_cl': '有效氯', 'water': '水分', 'water_diluted': '水分',
    'fluoride': '氟离子', 'loss': '干燥失重', 'melting': '熔点'}


def columns_for(key):
    if key == 'water':
        return {'date': 'A', 'batch': 'B', 'result': 'E', 'notes': 'F'}
    if key == 'water_diluted':
        return {'date': 'A', 'batch': 'B', 'raw_result': 'E', 'result': 'F', 'original_mass': 'G', 'total_mass': 'H', 'water_result': 'I'}
    if key == 'fluoride':
        return {'date': 'A', 'batch': 'B', 'mass1': 'C', 'total1': 'D', 'mass2': 'E', 'total2': 'F', 'reading': 'G', 'result': 'H', 'percent': 'I', 'notes': 'J'}
    if key == 'loss':
        return {'date': 'A', 'batch': 'B', 'mass': 'C', 'before': 'D', 'after1': 'E', 'after2': 'F', 'result': 'G', 'notes': 'H'}
    if key == 'melting':
        return {'date': 'A', 'batch': 'B', 'start': 'C', 'end': 'D'}
    return dict(BASE_COLUMNS)


def detect_mapping(project, path, method_keys):
    parts, _, strings, sheets = read_archive(path)
    if project not in {'D006', 'Q'}:
        return {}
    mapping = {}
    for key in method_keys:
        sheet = next((name for name in sheets if name.strip() == SHEET_NAMES[key]), None)
        if not sheet:
            continue
        root = xml(parts[sheets[sheet]])
        values = {cell.get('r'): cell_value(cell, strings) for cell in root.findall('s:sheetData/s:row/s:c', N)}
        cols = columns_for(key)
        if '批' not in values.get('B1', ''):
            continue
        if key in {'water', 'water_diluted'} and '水分' not in values.get('E1', ''):
            continue
        if key == 'water_diluted':
            candidate_formulas = [c.findtext('s:f', '', N) for c in root.findall('s:sheetData/s:row/s:c', N)]
            known_formula = any(re.fullmatch(r'\(E\d+/100\*H\d+-\(H\d+-G\d+\)\*I\d+/100\)/G\d+\*100', f) for f in candidate_formulas)
            if not known_formula and not all(label in values.get(f'{col}1', '') for col, label in [('G', '原样'), ('H', '总质量'), ('I', '水分')]):
                continue
        required = {'fluoride': ['一次称量', '一次稀释', '二次称量', '二次稀释'],
            'loss': ['样品质量', '恒重前', '恒重后', '恒重后'],
            'melting': ['初熔点', '终熔点']}.get(key)
        if required and not all(label in values.get(f'{chr(67+i)}1', '') for i, label in enumerate(required)):
            continue
        if key not in {'water', 'water_diluted', 'fluoride', 'loss', 'melting'}:
            if '质量' not in values.get('C1', '') or '体积' not in values.get('D1', ''):
                continue
        header = {ref: value for ref, value in values.items() if re.fullmatch(r'[A-Z]+[12]', ref)}
        mapping[key] = {'sheet': sheet, 'columns': cols, 'header': header, 'first_row': 4, 'version': 1}
    return mapping


def prepare_write(path, destination, record, mapping):
    parts, infos, strings, sheets = read_archive(path)
    if mapping['sheet'] not in sheets:
        raise ValueError('目标工作表已变化')
    sheet_path = sheets[mapping['sheet']]
    root = xml(parts[sheet_path])
    data = root.find('s:sheetData', N)
    current = {c.get('r'): cell_value(c, strings) for c in data.findall('s:row/s:c', N)}
    if any(current.get(ref, '') != value for ref, value in mapping['header'].items()):
        raise ValueError('表头已变化，请重新核定映射')
    columns = mapping['columns']
    batch_col = columns['batch']
    last = mapping['first_row'] - 1
    # ponytail: append-only revisions avoid modifying historical rows; in-place revisions require explicit reconciliation.
    for ref, value in current.items():
        row_no = int(re.search(r'\d+$', ref)[0])
        data_cols = {col for key, col in columns.items() if key not in {'date', 'notes'}}
        if row_no >= mapping['first_row'] and value and re.sub(r'\d', '', ref) in data_cols:
            last = max(last, row_no)
    rows = list(range(last + 1, last + 1 + len(record.measurements)))
    if max(rows) > 1048576:
        raise ValueError('工作表记录行已满')
    occupied = [c for c in data.findall('s:row/s:c', N) if int(re.search(r'\d+$', c.get('r'))[0]) in rows and cell_value(c, strings)]
    if occupied:
        raise ValueError('拟追加行已有其他数据，请核对记录区')
    for merged in root.findall('s:mergeCells/s:mergeCell', N):
        bounds = [int(re.search(r'\d+$', x)[0]) for x in merged.get('ref').split(':')]
        if any(min(bounds) <= row <= max(bounds) for row in rows):
            raise ValueError('拟追加行涉及合并区域，须先核定映射')
    source_row = data.find(f"s:row[@r='{last}']", N)
    if source_row is None:
        source_row = data.find("s:row[@r='3']", N)
    source_styles = {re.sub(r'\d', '', c.get('r')): c.get('s') for c in source_row} if source_row is not None else {}
    for index, row_no in enumerate(rows):
        row = data.find(f"s:row[@r='{row_no}']", N)
        if row is None:
            row = ET.SubElement(data, f'{{{NS}}}row', r=str(row_no))
        values = dict(record.measurements[index])
        if record.method_snapshot['kind'] == 'water_diluted':
            values['raw_result'] = values.pop('result')
        values.update(record.outputs[index])
        values.update({'date': str((record.detected_on - date(1899, 12, 30)).days), 'batch': record.batch,
            'notes': (f'修订 #{record.supersedes_id}；' if record.supersedes_id else '') + record.notes + (f'；试剂批次：{record.reagent_batch}' if record.reagent_batch else '')})
        expressions = formulas(record.method_snapshot['kind'], columns, row_no, record.method_snapshot['params'])
        for key, col in columns.items():
            if key not in values:
                continue
            ref = f'{col}{row_no}'
            cell = row.find(f"s:c[@r='{ref}']", N)
            if cell is None:
                cell = ET.SubElement(row, f'{{{NS}}}c', r=ref)
            for child in list(cell):
                cell.remove(child)
            cell.attrib.pop('t', None)
            if source_styles.get(col) is not None:
                cell.set('s', source_styles[col])
            if key in {'batch', 'notes'}:
                cell.set('t', 'inlineStr')
                text = ET.SubElement(ET.SubElement(cell, f'{{{NS}}}is'), f'{{{NS}}}t')
                text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                text.text = str(values[key])
            else:
                if key in expressions:
                    ET.SubElement(cell, f'{{{NS}}}f').text = expressions[key]
                ET.SubElement(cell, f'{{{NS}}}v').text = str(values[key])
        row[:] = sorted(row, key=lambda c: (len(re.sub(r'\d', '', c.get('r'))), re.sub(r'\d', '', c.get('r'))))
    data[:] = sorted(data, key=lambda row: int(row.get('r')))
    dimension = root.find('s:dimension', N)
    if dimension is not None:
        old_end = dimension.get('ref', 'A1').split(':')[-1]
        end_col = re.sub(r'\d', '', old_end)
        end_col = max([end_col, *columns.values()], key=lambda col: (len(col), col))
        dimension.set('ref', f'A1:{end_col}{max(max(rows), int(re.search(r"\d+$", old_end)[0]))}')
    parts[sheet_path] = ET.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    with zipfile.ZipFile(destination, 'w') as archive:
        for info in infos:
            archive.writestr(info, parts[info.filename])
    # Read back values and ensure every untouched zip member is byte-identical.
    check, _, new_strings, _ = read_archive(Path(destination))
    if any(check[key] != value for key, value in parts.items()):
        raise ValueError('回写文件验证失败')
    saved = xml(check[sheet_path])
    for row_no in rows:
        cell = saved.find(f"s:sheetData/s:row[@r='{row_no}']/s:c[@r='{batch_col}{row_no}']", N)
        if cell is None or cell_value(cell, new_strings) != record.batch:
            raise ValueError('记录回读验证失败')
    return rows


def create_demo(path, keys):
    sheets = []
    for key in keys:
        if SHEET_NAMES[key] not in [x[0] for x in sheets]:
            sheets.append((SHEET_NAMES[key], key))
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1)) + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        wb = ET.Element(f'{{{NS}}}workbook', nsmap={None: NS, 'r': REL})
        wb_sheets = ET.SubElement(wb, f'{{{NS}}}sheets')
        relationships = ET.Element('Relationships', nsmap={None: 'http://schemas.openxmlformats.org/package/2006/relationships'})
        for i, (name, key) in enumerate(sheets, 1):
            ET.SubElement(wb_sheets, f'{{{NS}}}sheet', name=name, sheetId=str(i), **{f'{{{REL}}}id': f'rId{i}'})
            ET.SubElement(relationships, 'Relationship', Id=f'rId{i}', Type=f'{REL}/worksheet', Target=f'worksheets/sheet{i}.xml')
            root = ET.Element(f'{{{NS}}}worksheet', nsmap={None: NS})
            cols = columns_for(key)
            if key == 'water' and 'water_diluted' in keys:
                cols = columns_for('water_diluted')
            last_col = max(cols.values(), key=lambda x: (len(x), x))
            ET.SubElement(root, f'{{{NS}}}dimension', ref=f'A1:{last_col}3')
            data = ET.SubElement(root, f'{{{NS}}}sheetData')
            headers = {'date': '日期', 'batch': '样品批号', 'mass': '样品质量', 'volume': '滴定体积', 'result': '水分结果%' if key == 'water' else '检测结果', 'notes': '备注', 'mass1': '一次称量', 'total1': '一次稀释总质量', 'mass2': '二次称量', 'total2': '二次稀释总质量', 'reading': '仪器示数', 'percent': '结果%', 'before': '恒重前样品与瓶总重', 'after1': '恒重后样品与瓶总重', 'after2': '恒重后样品与瓶总重', 'start': '初熔点', 'end': '终熔点', 'raw_result': '稀释样水分结果%', 'original_mass': '原样质量', 'total_mass': '稀释总质量', 'water_result': '所加水水分'}
            for r in (1, 2, 3):
                row = ET.SubElement(data, f'{{{NS}}}row', r=str(r))
                for field, col in sorted(cols.items(), key=lambda pair: pair[1]):
                    cell = ET.SubElement(row, f'{{{NS}}}c', r=f'{col}{r}', s='1' if field == 'date' and r == 3 else '0')
                    if r == 1 or (r == 3 and field == 'batch'):
                        cell.set('t', 'inlineStr')
                        ET.SubElement(ET.SubElement(cell, f'{{{NS}}}is'), f'{{{NS}}}t').text = headers.get(field, field) if r == 1 else '例'
            archive.writestr(f'xl/worksheets/sheet{i}.xml', ET.tostring(root, encoding='UTF-8', xml_declaration=True))
        ET.SubElement(relationships, 'Relationship', Id='styles', Type=f'{REL}/styles', Target='styles.xml')
        archive.writestr('xl/workbook.xml', ET.tostring(wb, encoding='UTF-8', xml_declaration=True))
        archive.writestr('xl/_rels/workbook.xml.rels', ET.tostring(relationships, encoding='UTF-8', xml_declaration=True))
        archive.writestr('xl/styles.xml', f'<styleSheet xmlns="{NS}"><fonts count="1"><font><sz val="11"/><name val="等线"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
