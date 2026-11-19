from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from operaciones_store import OperationsStore


class DatabaseLimitsTests(unittest.TestCase):
    def test_postgres_connect_and_query_limits(self):
        connection=MagicMock()
        connect=MagicMock(return_value=connection)
        with patch.dict('sys.modules',{'psycopg':SimpleNamespace(connect=connect)}):
            with OperationsStore(database_url='postgresql://example/db').connection():
                pass
        self.assertEqual(connect.call_args.kwargs['connect_timeout'],10)
        self.assertIn('statement_timeout=30000',connect.call_args.kwargs['options'])
        self.assertIn('lock_timeout=5000',connect.call_args.kwargs['options'])
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_photo_lookup_does_not_load_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'db.sqlite')
            store.import_matrix_snapshot({'clients':[{'id':'C','name':'Client'}],'equipment':[{'id':'E','name':'Equipment','client_id':'C'}],'reports':[],'faults':[]})
            with patch.object(store,'snapshot',side_effect=AssertionError('No full snapshot')):
                self.assertEqual(store.photo_entity('equipment','E'),{'id':'E','client_id':'C'})
                self.assertEqual(store.photo_entity('client','C'),{'id':'C','client_id':'C'})
                self.assertIsNone(store.photo_entity('equipment','missing'))
