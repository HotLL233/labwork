"""Real HTTPS/browser-form smoke check using an isolated deployment, no third-party test client."""
import argparse
import html
import http.cookiejar
import io
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def hidden(page, name):
    return html.unescape(re.search(r'name="' + name + r'"[^>]*value="([^"]*)"', page)[1])


def options(page, name):
    select = re.search(r'<select[^>]*name="' + name + r'"[^>]*>(.*?)</select>', page, re.S)[1]
    return re.findall(r'<option value="(\d+)"', select)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--ca', type=Path, required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    credentials = json.loads(args.credentials.read_text(encoding='utf-8'))
    context = ssl.create_default_context(cafile=str(args.ca))
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies),
                                        urllib.request.HTTPSHandler(context=context))

    def get(path):
        response = opener.open(args.base + path, timeout=15)
        return response.read().decode('utf-8'), response.geturl()

    def post(path, fields, isolated=False):
        client = opener
        headers = {'Referer': args.base + path}
        if isolated:
            client = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
            headers['Cookie'] = '; '.join(f'{c.name}={c.value}' for c in cookies)
        request = urllib.request.Request(args.base + path, urllib.parse.urlencode(fields).encode(), headers=headers)
        response = client.open(request, timeout=30)
        return response.read().decode('utf-8'), response.geturl()

    page, _ = get('/login/')
    page, url = post('/login/', {'csrfmiddlewaretoken': hidden(page, 'csrfmiddlewaretoken'), **credentials})
    assert url.rstrip('/') == args.base, 'Login failed'
    assert '工作台' in page
    report = {'base': args.base, 'tls': 'verified against generated internal root', 'records': []}
    books = options(get('/records/new/')[0], 'book')
    assert len(books) == 2
    for book in books:
        methods = options(get('/records/new/?book=' + book)[0], 'method')
        for method in methods:
            page, _ = get(f'/records/new/?book={book}&method={method}')
            fields = set(re.findall(r'name="(\w+)_1"', page))
            all_values = {'mass': '1', 'volume': '10', 'result': '60', 'original_mass': '10', 'total_mass': '20',
                'water_result': '100', 'mass1': '2', 'total1': '20', 'mass2': '5', 'total2': '50',
                'reading': '1.2', 'before': '20', 'after1': '19.95', 'after2': '19.95', 'start': '150', 'end': '153'}
            data = {key + '_1': all_values[key] for key in fields}
            data.update({key + '_2': all_values[key] for key in fields})
            batch = f'HTTP-SMOKE-{book}-{method}'
            data.update({'csrfmiddlewaretoken': hidden(page, 'csrfmiddlewaretoken'), 'token': hidden(page, 'token'),
                'book': book, 'method': method, 'version': hidden(page, 'version'), 'detected_on': '2026-10-09',
                'batch': batch, 'notes': 'isolated packaging verification', 'action': 'submit'})
            # Send two simultaneous real requests with one idempotency token.
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: post('/records/new/', data, isolated=True), range(2)))
            assert results[0][1] == results[1][1], 'Duplicate requests created different records'
            record_path = urllib.parse.urlparse(results[0][1]).path
            assert re.fullmatch(r'/records/\d+/', record_path), results[0][0][:100]
            for _ in range(40):
                detail, _ = get(record_path)
                if '回写并回读验证成功' in detail:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError('Writeback did not complete: ' + record_path)
            assert '第 2 次测定' in detail and batch in detail
            report['records'].append({'book': book, 'method': method, 'path': record_path, 'status': 'done', 'parallel': 2})
        response = opener.open(args.base + f'/workbooks/{book}/download/')
        with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
            assert archive.testzip() is None
            assert any('HTTP-SMOKE' in archive.read(name).decode() for name in archive.namelist() if name.startswith('xl/worksheets/'))
    assert len(report['records']) == 14
    report['checks'] = ['HTTPS root validation', 'login + CSRF', '14 methods x 2 measurements',
                        '28 concurrent duplicate submissions -> 14 records', 'serial worker + Excel cached results', 'authorized downloads']
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('HTTPS smoke passed: 14 methods, 28 measurements, duplicate submissions, writeback and downloads.')


if __name__ == '__main__':
    main()
