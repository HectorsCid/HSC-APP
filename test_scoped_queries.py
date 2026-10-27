import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from contextlib import contextmanager
from operaciones_store import OperationsStore


class ScopedQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=OperationsStore(local_path=Path(self.temp.name)/'scoped.sqlite3')
        self.store.import_matrix_snapshot({'clients':[{'id':'VC','name':'Visscher'},{'id':'OTHER','name':'Otra'}],
            'equipment':[{'id':'VC4','client_id':'VC','name':'Calidad'},{'id':'OTHER1','client_id':'OTHER','name':'Otro'}],
            'reports':[{'id':'R1','client_id':'VC','equipment_id':'VC4','round':'1','completed':True},
                       {'id':'R2','client_id':'OTHER','equipment_id':'OTHER1','round':'1','completed':True}],
            'faults':[]})
        with self.store.connection() as conn:
            for ident,client,origin in [('L1','VC','client'),('L2','OTHER','client'),('L3','VC','technician')]:
                conn.execute('INSERT INTO operations_worklists(id,revision,payload_json) VALUES (?,?,?)',
                    (ident,1,json.dumps(dict(id=ident,client_id=client,origin=origin))))

    def test_sql_scopes_data_before_materializing_snapshot(self):
        statements=[];original=self.store.connection
        @contextmanager
        def traced():
            with original() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        with patch.object(self.store,'connection',traced):
            payload=self.store.snapshot(client_id='VC')
        self.assertEqual([r['id'] for r in payload['clients']],['VC'])
        self.assertEqual([r['id'] for r in payload['equipment']],['VC4'])
        self.assertEqual([r['id'] for r in payload['reports']],['R1'])
        self.assertEqual([r['id'] for r in payload['worklists']],['L1'])
        self.assertEqual(payload['expenses'],[])
        for table in ('operations_clients','operations_equipment','operations_reports'):
            queries=[sql for sql in statements if 'FROM '+table in sql]
            self.assertTrue(queries)
            self.assertTrue(all('WHERE' in sql for sql in queries))
        self.assertFalse(any('FROM operations_expenses' in sql or 'FROM operations_meta' in sql for sql in statements))

    def test_empty_and_untrusted_company_never_fall_back_to_all(self):
        for client in ('',"VC' OR 1=1 --",'missing'):
            payload=self.store.snapshot(client_id=client)
            for key in ('clients','equipment','reports','worklists'):self.assertEqual(payload[key],[])

    def test_admin_snapshot_and_lightweight_lookup(self):
        self.assertEqual(len(self.store.snapshot()['equipment']),2)
        with patch.object(self.store,'snapshot',side_effect=AssertionError('No global snapshot')):
            self.assertEqual([r['id'] for r in self.store.list_clients(client_id='VC')],['VC'])
            self.assertEqual(len(self.store.list_clients()),2)


if __name__=='__main__':unittest.main()
