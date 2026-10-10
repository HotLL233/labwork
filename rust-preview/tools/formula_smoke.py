"""Original EF/K001 formula regression on isolated workbook copies."""
import ast
from decimal import Decimal, localcontext
import hashlib
import io
import json
import re
import socket
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import month_smoke as server

server.PORT = 19849
ROOT = Path(__file__).resolve().parents[1]
NS = server.NS


def calculate(expression, lookup):
    expression = expression.lstrip('=').replace('$', '')

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(ast.get_source_segment(expression, node))
        if isinstance(node, ast.Name):
            return lookup(node.id)
        if isinstance(node, ast.UnaryOp):
            return -evaluate(node.operand) if isinstance(node.op, ast.USub) else evaluate(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            if isinstance(node.op, ast.Div): return a / b
        raise AssertionError(f'Unexpected original formula syntax: {expression}')

    with localcontext() as context:
        context.prec = 60
        return evaluate(ast.parse(expression, mode='eval'))


def expected_outputs(method, raw):
    params = method['params']
    template_row = int(params['template_row'])
    formulas = {key[2:]: value for key, value in params.items() if key.startswith('f:')}
    fixed = {key[6:].replace('$', ''): Decimal(value) for key, value in params.items() if key.startswith('fixed:')}
    result = {}
    visiting = set()

    def lookup(reference):
        if reference in fixed:
            return fixed[reference]
        column, row = re.fullmatch(r'([A-Z]+)(\d+)', reference).groups()
        assert int(row) == template_row, (reference, template_row)
        if column in result:
            return result[column]
        if column in formulas:
            assert column not in visiting, 'Formula cycle'
            visiting.add(column)
            result[column] = calculate(formulas[column], lookup)
            visiting.remove(column)
            return result[column]
        return Decimal(raw['col_' + column])

    for column in formulas:
        lookup(column + str(template_row))
    return result


def sheet_cells(data, sheet):
    members = server.parts(data)
    path = next(path for name, path in server.sheet_paths(members).items() if name.strip() == sheet.strip())
    tree = ET.fromstring(members[path])
    return members, path, {c.attrib['r']: c for c in tree.findall('.//s:c', NS)}


def main():
    inventory = json.loads(Path(r'C:\labwork\tmp\physchem-research\workbook-inventory.json').read_text(encoding='utf-8-sig'))
    sources = {code: Path(next(item['Path'] for item in inventory if item['Name'].startswith(code + '-'))) for code in ('EF', 'K001')}
    originals = {code: path.read_bytes() for code, path in sources.items()}
    hashes = {code: hashlib.sha256(data).hexdigest() for code, data in originals.items()}
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', server.PORT))
    tested = []
    with tempfile.TemporaryDirectory(prefix='labwork-formula-') as folder:
        home = Path(folder) / 'data'
        process = None
        with (Path(folder) / 'server.log').open('wb') as log:
            try:
                process = server.start(home, log)
                for code, data in originals.items():
                    server.ok('/api/workbook/' + code, data)
                catalog = server.ok('/api/catalog')
                month = catalog['active_month']
                projects = {p['code']: p for p in catalog['projects']}
                cases = [('EF', '二氟乙酸钠', {'col_C': '23.3', 'col_D': '0.15', 'col_E': '10'}), ('K001', '氢离子含量', {'col_C': '1', 'col_D': '12'}), ('K001', '硫酸根', {'col_C': '1.0537', 'col_D': '2.71', 'col_E': '0', 'col_H': '2', 'col_I': '10'})]
                for code, sheet, overrides in cases:
                    method = next(m for m in projects[code]['methods'] if m['sheet'].strip() == sheet)
                    assert method['kind'] == 'workbook_formula', method
                    raw = {f['key']: str(f.get('sample') or '1') for f in method['fields']}
                    raw.update({key: value for key, value in overrides.items() if key in raw})
                    if code == 'K001' and sheet == '氢离子含量':
                        assert '0.0993' in method['params']['f:E'], method['params']
                    payload = {'project': code, 'method': method['key'], 'entries': [{'date': month + '-10', 'batch': 'FORMULA-01', 'measurement': raw}, {'date': month + '-10', 'batch': 'FORMULA-02', 'measurement': raw}]}
                    working = home / 'months' / month / 'workbooks' / f'{code}.xlsx'
                    before = working.read_bytes()
                    result = server.ok('/api/preview', payload)
                    assert working.read_bytes() == before
                    candidate = server.ok('/api/download/' + result['id'])
                    members, path, cells = sheet_cells(candidate, sheet)
                    expected = expected_outputs(method, raw)
                    for row in result['rows']:
                        for column, value in expected.items():
                            cell = cells[f'{column}{row}']
                            actual = Decimal(cell.find('s:v', NS).text)
                            assert abs(actual - value) < Decimal('1e-12'), (code, sheet, column, actual, value)
                            formula = cell.find('s:f', NS)
                            assert formula is not None and formula.text
                    if code == 'EF':
                        assert abs(expected['F'] - Decimal('82.558533192217325')) < Decimal('1e-12')
                    old_members = server.parts(before)
                    assert all(members[name] == data for name, data in old_members.items() if name not in (path, 'xl/styles.xml'))
                    server.ok('/api/commit/' + result['id'], {})
                    assert working.read_bytes() == candidate
                    assert server.ok('/api/commit/' + result['id'], {})['state'] == 'done'
                    bad = dict(raw)
                    if 'col_D' in raw and code == 'EF':
                        bad['col_D'] = '0'
                        assert server.api('/api/preview', {**payload, 'entries': [{'date': month + '-10', 'batch': 'BAD-ZERO', 'measurement': bad}]})[0] == 400
                        assert working.read_bytes() == candidate
                    tested.append({'project': code, 'sheet': sheet, 'template_row': method['params']['template_row'], 'outputs': list(expected)})
            except BaseException:
                log.flush()
                print((Path(folder) / 'server.log').read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                server.stop(process)
    assert all(hashlib.sha256(sources[code].read_bytes()).hexdigest() == digest for code, digest in hashes.items())
    result = {'cases': tested, 'original_formula_decimal_parity': True, 'latest_calibration_selected': True, 'fixed_parameters_and_formula_dependencies': True, 'preview_no_write': True, 'commit_matches_preview': True, 'other_parts_preserved': True, 'zero_denominator_rejected': True, 'originals_unchanged': True}
    (ROOT.parent / 'runtime/formula-smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
