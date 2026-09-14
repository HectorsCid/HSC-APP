import tempfile
import unittest
from pathlib import Path
from operaciones_store import OperationsStore
from test_operaciones_store import _payload


class ClientCompanyChangeTests(unittest.TestCase):
    def test_changes_only_account_and_rejects_stale_or_invalid_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'accounts.db')
            payload=_payload();payload['clients'].append({'id':'NEW','name':'Otra empresa'})
            store.import_matrix_snapshot(payload)
            store.create_invite({'token_hash':'client','name':'Prueba','email':'client@example.com','role':'client','client_id':'UVMQ','expires_at':'2099-01-01T00:00:00+00:00'})
            user=store.accept_invite('client','hash')
            before=store.snapshot()
            changed=store.change_client_user_company(user['id'],'NEW','UVMQ')
            self.assertEqual(changed['client_id'],'NEW')
            self.assertEqual(changed['email'],user['email'])
            after=store.snapshot()
            for kind in ['reports','equipment','clients','faults']:
                self.assertEqual(before[kind],after[kind])
            with self.assertRaises(ValueError):store.change_client_user_company(user['id'],'UVMQ','UVMQ')
            with self.assertRaises(ValueError):store.change_client_user_company(user['id'],'missing','NEW')
            with self.assertRaises(ValueError):store.change_client_user_company('owner','NEW','')


if __name__=='__main__':unittest.main()
