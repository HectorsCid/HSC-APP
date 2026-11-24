from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
from flask import Flask
from requests import Timeout
from operaciones_store import OperationsStore
from payment_stamp_guard import stamp, StampReview


class StampInterruptionTests(TestCase):
    def test_restart_blocks_intersecting_batch_not_unrelated_invoice(self):
        with TemporaryDirectory() as path:
            def context():
                app=Flask(__name__)
                app.extensions['operations_store']=OperationsStore(local_path=Path(path)/'db')
                return app.app_context()
            with context(),self.assertRaises(StampReview):
                stamp(['b','a'],Mock(side_effect=Timeout()))
            callback=Mock()
            with context():
                with self.assertRaises(StampReview):stamp(['0','B'],callback)
                callback.assert_not_called()
                _,done=stamp(['0'],callback);done()
                _,done=stamp(['0'],callback);done()

    def test_confirmed_provider_id_requires_all_invoice_balances(self):
        with TemporaryDirectory() as path:
            app=Flask(__name__)
            app.extensions['operations_store']=OperationsStore(local_path=Path(path)/'db')
            with app.app_context():
                stamp(['A','B'],lambda:{'Id':'rep-1'})  # process stopped before recording the balances
                with self.assertRaises(StampReview):
                    stamp(['A'],Mock(),confirmed_reps=lambda key:{'rep-1'} if key=='a' else set())
                _,done=stamp(['A'],lambda:{'Id':'rep-2'},confirmed_reps=lambda key:{'rep-1'})
                done()
