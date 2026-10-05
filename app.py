#!/usr/bin/env python3
"""Local portfolio ledger. Python 3.10+, standard library only."""
import argparse
import csv
import io
import json
import os
import re
import secrets
import sqlite3
import webbrowser
from datetime import date
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
KINDS = {'broker', 'property', 'crypto'}


def number(value, positive=False):
    try:
        n = Decimal(str(value).replace(',', '.'))
        if not n.is_finite() or n < 0 or n > Decimal('1e18') or (positive and n == 0):
            raise ValueError()
        return str(n)
    except (InvalidOperation, ValueError):
        raise ValueError('Введите конечное неотрицательное число до 10¹⁸; курс должен быть больше нуля.')


def text(value, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Заполните текстовое поле (не более %s символов).' % limit)
    return value.strip()


def day(value):
    date.fromisoformat(value)
    if value > date.today().isoformat():
        raise ValueError('Дата не может быть в будущем.')
    return value


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flows(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rates(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO rates VALUES (?,?)', ('RUB', json.dumps({'value':'1','date':date.today().isoformat()})))
        os.chmod(self.path, 0o600)

    def db(self):
        return sqlite3.connect(self.path)

    def state(self):
        with self.db() as db:
            state = {t: [dict(json.loads(raw), id=key) for key, raw in db.execute('SELECT id,data FROM '+t)] for t in ['assets','flows','snapshots','rates']}
        rates = {r['id']: Decimal(r['value']) for r in state['rates']}
        total = cost = debt = income = expense = Decimal(0)
        groups = {k: Decimal(0) for k in KINDS}
        for a in state['assets']:
            rate = rates[a['currency']]
            v, c, d = (Decimal(a[k]) * rate for k in ['value','cost','debt'])
            total += v
            cost += c
            debt += d
            groups[a['kind']] += v
        for f in state['flows']:
            # Historical cash flows retain their entered conversion rate.
            v = Decimal(f['amount']) * Decimal(f['rate'])
            if f['kind'] == 'income': income += v
            else: expense += v
        state['summary'] = {k:str(v) for k,v in dict(total=total,cost=cost,debt=debt,net=total-debt,change=total-cost,income=income,expense=expense).items()}
        state['groups'] = {k:str(v) for k,v in groups.items()}
        return state

    def save(self, table, data):
        today = date.today().isoformat()
        key = data.get('id') or secrets.token_hex(12)
        if table == 'rates':
            key = text(data['id']).upper()
            if not re.fullmatch('[A-Z]{3,8}', key): raise ValueError('Код валюты: 3–8 латинских букв.')
            value = number(data['value'], True)
            if key == 'RUB' and Decimal(value) != 1: raise ValueError('Курс RUB всегда равен 1.')
            item = {'value':value, 'date':day(data.get('date',today))}
        elif table == 'assets':
            if data['kind'] not in KINDS: raise ValueError('Неизвестный тип актива.')
            currency = text(data['currency']).upper()
            if currency not in {r['id'] for r in self.state()['rates']}: raise ValueError('Сначала добавьте курс валюты.')
            item = {k:text(data[k]) for k in ['name','account']}
            item.update(kind=data['kind'], currency=currency, date=day(data['date']), notes=str(data.get('notes',''))[:2000])
            item.update({k:number(data[k]) for k in ['cost','value','debt']})
        elif table == 'flows':
            if data['kind'] not in {'income','expense'}: raise ValueError('Неизвестная операция.')
            currency = text(data['currency']).upper()
            if not re.fullmatch('[A-Z]{3,8}',currency): raise ValueError('Некорректная валюта.')
            item = {k:text(data[k]) for k in ['name','account']}
            item.update(kind=data['kind'],currency=currency,amount=number(data['amount'],True),rate=number(data['rate'],True),date=day(data['date']))
            if currency == 'RUB' and Decimal(item['rate']) != 1: raise ValueError('Курс RUB всегда равен 1.')
        elif table == 'snapshots':
            key = today
            item = {'date':today, **self.state()['summary']}
        else: raise ValueError('Неизвестная таблица.')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO '+table+' VALUES (?,?)',(key,json.dumps(item,ensure_ascii=False)))
        return key

    def delete(self, table, key):
        if table not in {'assets','flows','snapshots'}: raise ValueError('Удаление недоступно.')
        with self.db() as db: db.execute('DELETE FROM '+table+' WHERE id=?',(key,))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def reply(self, status, body, mime='application/json; charset=utf-8', attachment=None):
        if isinstance(body,(dict,list)): body=json.dumps(body,ensure_ascii=False).encode()
        elif isinstance(body,str): body=body.encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if attachment: self.send_header('Content-Disposition','attachment; filename="'+attachment+'"')
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host(): return self.reply(403,{'error':'Недопустимый адрес.'})
        path = self.path.split('?')[0]
        if path == '/api/state': return self.reply(200,dict(self.server.store.state(),token=TOKEN))
        if path == '/api/export': return self.reply(200,self.server.store.state(),attachment='portfolio.json')
        if path == '/api/backup':
            with self.server.store.db() as source, sqlite3.connect(':memory:') as dest:
                source.backup(dest)
                raw = ('\n'.join(dest.iterdump())+'\n').encode()
            return self.reply(200,raw,'application/sql', 'capital-backup.sql')
        if path == '/api/csv':
            out=io.StringIO(); writer=csv.writer(out)
            fields=['name','kind','account','currency','cost','value','debt','date','notes']
            writer.writerow(fields)
            for a in self.server.store.state()['assets']:
                writer.writerow([("'"+str(a[k]) if str(a[k]).startswith(('=','+','-','@','\t','\r','\n')) else a[k]) for k in fields])
            return self.reply(200,('\ufeff'+out.getvalue()).encode(),'text/csv; charset=utf-8','assets.csv')
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
        if path in files:
            name,mime=files[path]; return self.reply(200,(ROOT/'static'/name).read_bytes(),mime)
        self.reply(404,{'error':'Не найдено.'})

    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-CSRF-Token') != TOKEN: return self.reply(403,{'error':'Обновите страницу и повторите.'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768: raise ValueError('Некорректный размер запроса.')
            data=json.loads(self.rfile.read(size))
            if self.path == '/api/save': self.server.store.save(data['table'],data['item'])
            elif self.path == '/api/delete': self.server.store.delete(data['table'],data['id'])
            else: return self.reply(404,{'error':'Не найдено.'})
            self.reply(200,{'ok':True})
        except (ValueError,KeyError,TypeError,AttributeError) as e:
            self.reply(400,{'error':str(e) or 'Проверьте введённые данные.'})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--db',default=str(ROOT/'data'/'capital.sqlite3'))
    p.add_argument('--no-browser',action='store_true')
    args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.store=Store(args.db)
    url=f'http://127.0.0.1:{server.server_port}'
    print(f'Капитал · {url}\nБаза: {args.db}\nДля остановки нажмите Ctrl+C.',flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
