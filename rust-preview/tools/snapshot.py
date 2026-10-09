"""Read-only snapshot of a local preview home into a new, isolated home."""
import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--destination',type=Path,required=True)
args=parser.parse_args()
source=args.source.resolve();destination=args.destination.resolve()
assert (source/'.labwork-rust-preview').exists(),'Source is not a preview home'
assert not destination.exists(),'Destination must be new; never overwrite a data directory'
assert source not in destination.parents,'Destination cannot be inside source'
def hashes():
    return {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['workbooks','previews','backups'] for p in (source/folder).glob('*.xlsx')}
before=hashes()
destination.mkdir(parents=True)
(destination/'.snapshot-incomplete').write_text('Do not start until snapshot validation succeeds')
for folder in ['workbooks','previews','backups']:
    if (source/folder).exists():shutil.copytree(source/folder,destination/folder)
with closing(sqlite3.connect((source/'preview.sqlite3').as_uri()+'?mode=ro',uri=True)) as origin, closing(sqlite3.connect(destination/'preview.sqlite3')) as target:
    origin.backup(target)
    assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    records=target.execute('SELECT count(*) FROM previews').fetchone()[0]
assert before==hashes(),'Source files changed while snapshotting; use a fresh destination and retry'
assert all(hashlib.sha256((destination/p).read_bytes()).hexdigest()==h for p,h in before.items()),'Copied file hash mismatch'
(destination/'.labwork-rust-preview').write_text('isolated grid preview snapshot')
(destination/'snapshot.json').write_text(json.dumps({'source':str(source),'previews':records,'files':before},ensure_ascii=False,indent=2),encoding='utf-8')
(destination/'.snapshot-incomplete').unlink()
print(json.dumps({'snapshot':str(destination),'previews':records,'workbook_files':len(before),'originals_unchanged':True},ensure_ascii=False))
