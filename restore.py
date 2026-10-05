"""Restore a trusted SQL backup into a NEW SQLite file."""
import argparse
import os
import sqlite3
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('backup',type=Path)
p.add_argument('destination',type=Path)
a=p.parse_args()
sql=a.backup.read_text(encoding='utf-8')
a.destination.parent.mkdir(parents=True,exist_ok=True)
fd=os.open(a.destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
os.close(fd)
try:
    with sqlite3.connect(a.destination) as db:
        db.executescript(sql)
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Повреждённая база')
        for table in ('assets','flows','rates','snapshots'):
            db.execute('SELECT id,data FROM '+table+' LIMIT 1').fetchall()
except Exception:
    a.destination.unlink(missing_ok=True)
    raise
print('Создана база:',a.destination)
