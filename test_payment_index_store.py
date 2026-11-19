from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from flask import Flask
from operaciones_store import OperationsStore
import payment_index_store as index


class PaymentIndexStoreTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app=Flask(__name__)
        self.app.extensions['operations_store']=OperationsStore(local_path=Path(tmp.name)/'db.sqlite')

    def test_durable_migration_and_concurrent_updates(self):
        with self.app.app_context():
            self.assertEqual(index.read(lambda:{'old':{'payments':[]}}),{'old':{'payments':[]}})
        def update(ident):
            with self.app.app_context(),index.mutation():
                data=index.read(lambda: self.fail('No debe volver a importar el respaldo'))
                data[ident]={'payments':[ident]}
                index.write(data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(update,['A','B']))
        with self.app.app_context():
            self.assertEqual(set(index.read(lambda:{})),{'old','A','B'})
            with self.assertRaises(RuntimeError):
                index.write({})

    def test_failed_migration_not_saved_as_empty(self):
        def offline():
            raise RuntimeError('Drive unavailable')
        with self.app.app_context():
            with self.assertRaises(RuntimeError):
                index.read(offline)
            self.assertEqual(index.read(lambda:{'recovered':{}}),{'recovered':{}})

    def test_busy_endpoint_does_not_start_mutation(self):
        @contextmanager
        def busy(*args,**kwargs):
            raise TimeoutError('busy')
            yield
        @index.endpoint
        def operation():
            self.fail('Must not start while another payment owns the lock')
        with self.app.app_context(),patch.object(self.app.extensions['operations_store'],'resource_lock',busy):
            response,status=operation()
            self.assertEqual(status,409)
            self.assertEqual(response.get_json()['stage'],'busy')

    def test_operation_timeout_is_not_reported_as_unstarted(self):
        @index.endpoint
        def operation():
            raise TimeoutError('Remote operation outcome unknown')
        with self.app.app_context(),self.assertRaises(TimeoutError):
            operation()
