import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from flask import session
from werkzeug.exceptions import NotFound
import app as hsc
from operaciones_store import OperationsStore


class PartnerPhotoScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationsStore(local_path=Path(self.temp.name) / 'photos.sqlite3')
        self.store.import_matrix_snapshot({
            'clients': [{'id': 'VC', 'name': 'Visscher', '_photo_ref': 'vc-logo.jpg'},
                        {'id': 'OTHER', 'name': 'Otra empresa', '_photo_ref': 'other-logo.jpg'}],
            'equipment': [{'id': 'VC4', 'client_id': 'VC', 'name': 'Gerencia Calidad', '_photo_ref': 'vc4.jpg'},
                          {'id': 'OTHER1', 'client_id': 'OTHER', 'name': 'Otro equipo', '_photo_ref': 'other.jpg'},
                          {'id': 'VC5', 'client_id': 'VC', 'name': 'Sin foto'}],
            'reports': [], 'faults': [],
        })

    def request_photo(self, kind, record_id, *, role='client', company='VC', cache=None):
        with hsc.app.test_request_context('/api/operaciones/photo/' + kind + '/' + record_id):
            session.update(hsc_role=role, hsc_client_id=company)
            with patch.object(hsc, 'OPERACIONES_STORE', self.store), \
                 patch.dict(hsc._OPERACIONES_MATRIX_CACHE, payload=cache), \
                 patch.object(hsc, '_serve_operations_thumbnail', return_value='PHOTO') as serve:
                result = hsc.api_operaciones_photo(kind, record_id)
                return result, serve.call_args.args

    def test_own_photos_work_without_memory_cache(self):
        self.assertEqual(self.request_photo('equipment', 'VC4'), ('PHOTO', ('equipment', 'VC4', 'vc4.jpg')))
        self.assertEqual(self.request_photo('client', 'VC')[0], 'PHOTO')

    def test_other_company_denied_even_if_stale_cache_claims_ownership(self):
        stale = {'equipment': [{'id': 'OTHER1', 'client_id': 'VC'}]}
        for kind, record_id in [('equipment', 'OTHER1'), ('client', 'OTHER')]:
            with self.subTest(kind=kind), self.assertRaises(NotFound):
                self.request_photo(kind, record_id, cache=stale)

    def test_missing_company_record_or_photo_denied(self):
        for record_id, company in [('VC4', ''), ('missing', 'VC'), ('VC5', 'VC')]:
            with self.subTest(record_id=record_id, company=company), self.assertRaises(NotFound):
                self.request_photo('equipment', record_id, company=company)

    def test_admin_and_technician_access_unchanged(self):
        for role in ('admin', 'technician'):
            self.assertEqual(self.request_photo('equipment', 'OTHER1', role=role)[0], 'PHOTO')

    def test_scoped_store_never_returns_other_company_or_user_photo(self):
        self.assertEqual(self.store.get_media_ref('equipment', 'VC4', client_id='VC'), 'vc4.jpg')
        self.assertEqual(self.store.get_media_ref('equipment', 'VC4', client_id='OTHER'), '')
        self.assertEqual(self.store.get_media_ref('client', 'VC', client_id='OTHER'), '')
        self.assertEqual(self.store.get_media_ref('user', 'owner', client_id='VC'), '')


if __name__ == '__main__':
    unittest.main()
