import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from app import Store, Handler, ThreadingHTTPServer, TOKEN


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'db.sqlite3')
    def tearDown(self): self.temp.cleanup()
    def asset(self, **kw):
        return dict(name='Акция',account='Брокер',kind='broker',currency='RUB',cost='0.1',value='0.3',debt='0.1',date='2026-01-01',**kw)
    def test_decimal_and_persistence(self):
        key=self.store.save('assets',self.asset())
        s=Store(self.store.path).state()['summary']
        self.assertEqual(s['net'],'0.2'); self.assertEqual(s['change'],'0.2')
        a=self.asset();a.update(id=key,value='0.5');self.store.save('assets',a)
        self.assertEqual(len(self.store.state()['assets']),1)
        self.store.delete('assets',key)
        self.assertEqual(self.store.state()['summary']['total'],'0')
    def test_currency_and_frozen_history(self):
        self.store.save('rates',dict(id='USD',value='90'))
        a=self.asset();a.update(currency='USD',cost='100',value='150',debt='20');self.store.save('assets',a)
        self.store.save('flows',dict(name='Купон',account='Брокер',kind='income',currency='USD',amount='10',rate='80',date='2026-01-01'))
        self.store.save('snapshots',{})
        self.store.save('rates',dict(id='USD',value='100'))
        s=self.store.state()
        self.assertEqual(s['summary']['net'],'13000')
        self.assertEqual(s['summary']['income'],'800')
        self.assertEqual(s['snapshots'][0]['net'],'11700')
    def test_invalid_input(self):
        for value in ['NaN','Infinity','-1','1e30']:
            a=self.asset();a['value']=value
            with self.assertRaises(ValueError): self.store.save('assets',a)
        with self.assertRaises(ValueError): self.store.save('rates',dict(id='USD',value='0'))
        a=self.asset();a['currency']='EUR'
        with self.assertRaises(ValueError): self.store.save('assets',a)
    def test_http_and_csrf(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(base+'/api/state') as r: self.assertIn('token',json.load(r))
            data=json.dumps(dict(table='assets',item=self.asset())).encode()
            req=urllib.request.Request(base+'/api/save',data=data,headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(req)
            self.assertEqual(err.exception.code,403)
            req.add_header('X-CSRF-Token',TOKEN)
            with urllib.request.urlopen(req) as r: self.assertEqual(r.status,200)
            with urllib.request.urlopen(base+'/api/backup') as r:
                sql=r.read().decode()
                import sqlite3
                with sqlite3.connect(':memory:') as db:
                    db.executescript(sql)
                    self.assertEqual(db.execute('SELECT count(*) FROM assets').fetchone()[0],1)
            req=urllib.request.Request(base+'/',headers={'Host':'evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(req)
            self.assertEqual(err.exception.code,403)
        finally: server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()
