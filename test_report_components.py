import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import app as hsc
import reportes_bp as reports
from operaciones_store import OperationsStore
from report_measurements import COMPONENT_OPTIONS


class ReportComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'components.sqlite3'
        self.store=OperationsStore(local_path=self.path)
        self.matrix={'clients':[{'id':'VC','name':'Visscher'}],
                     'equipment':[{'id':'VC4','client_id':'VC','name':'Calidad'}], 'reports':[], 'faults':[]}
        self.store.import_matrix_snapshot(self.matrix)

    def create_report(self, **extra):
        draft=self.store.save_report_draft({'client_id':'VC','equipment_id':'VC4','round':'1',
            'payload':{'inicio':'2026-10-27','fin':'2026-10-27','a1':'3.8','a2':'1.2',**extra}})
        return self.store.finalize_report(draft['id'])

    def test_components_survive_restart_and_matrix_import(self):
        report=self.create_report(a1_component='Compresor',a2_component='Ventilador')
        reopened=OperationsStore(local_path=self.path)
        self.assertEqual(reopened.get_report_components(report['matrix_id'])['a2_component'],'Ventilador')
        with reopened.connection() as conn:
            conn.execute("UPDATE operations_sync_outbox SET status='synced'")
        self.matrix['reports']=[{'id':report['matrix_id'],'client_id':'VC','equipment_id':'VC4','round':'1','completed':True,'_raw':{'Amperaje1':'3.8'}}]
        reopened.import_matrix_snapshot(self.matrix)
        self.assertEqual(reopened.get_report_detail(report['id'])['payload']['a1_component'],'Compresor')

    def test_legacy_has_no_invented_component(self):
        report=self.create_report()
        self.assertNotIn('a1_component',report['payload'])

    def test_invalid_component_rejected(self):
        with self.assertRaises(ValueError):self.create_report(a1_component='unknown')

    def test_edit_can_change_and_clear_previous_component(self):
        report=self.create_report(a1_component='Compresor',a2_component='Ventilador')
        payload={**report['payload'],'a1_component':'Resistencia','a2_component':''}
        draft=self.store.save_report_draft({'client_id':'VC','equipment_id':'VC4','round':'1',
            'edit_report_id':report['id'],'base_values':report['payload'],'payload':payload})
        changed=self.store.finalize_report(draft['id'])
        self.assertEqual(changed['payload']['a1_component'],'Resistencia')
        self.assertEqual(changed['payload']['a2_component'],'')
        self.assertEqual(self.store.get_report_components(changed['matrix_id'])['a2_component'],'')

    def test_pdf_and_manual_data_keep_component(self):
        payload={'datos':{'amperaje1':'3.8','amperaje2':'1.2','a1_component':'Resistencia','a2_component':'Motor'}}
        data=reports._diag_refrigeration_data(payload)
        self.assertEqual(data['a1_component'],'Resistencia')
        with hsc.app.test_request_context('/'):
            html=hsc.render_template('reporte_formato.html',datos=data,fotos=[],embed_for_pdf=True,show_toolbar=False)
        self.assertIn('<strong>Resistencia</strong>',html)
        self.assertIn('<strong>Motor</strong>',html)
        self.assertIn('3.8 A',html)

    def test_pdf_uses_durable_labels_without_changing_sheet(self):
        report=self.create_report(a1_component='Motor',a2_component='Resistencia')
        with hsc.app.test_request_context('/'), patch.dict(hsc.app.extensions,operations_store=self.store), \
             patch.object(reports,'get_reporte_by_id',return_value={'Amperaje1':'3.8'}), \
             patch.object(reports,'_apply_cliente_y_overrides',side_effect=lambda data,ident:data):
            self.assertEqual(reports.get_reporte_con_overrides(report['matrix_id'])['a2_component'],'Resistencia')

    def test_ui_and_python_choices_match(self):
        source=Path('templates/app_operativa_demo.html').read_text(encoding='utf-8')
        for label in COMPONENT_OPTIONS:self.assertIn("'"+label+"'",source)

    def test_busy_thumbnail_does_not_download_original(self):
        with hsc.app.test_request_context('/'),patch.object(hsc,'OPERACIONES_STORE',self.store), \
             patch.object(hsc,'pdf_render_slot',side_effect=hsc.PdfRendererBusy('busy')), \
             patch.object(hsc,'serve_drive_image_ref_fast') as download:
            response=hsc._serve_operations_thumbnail('equipment','VC4','photo.jpg')
        self.assertEqual(response.status_code,503)
        self.assertIn('no-store',response.headers['Cache-Control'])
        download.assert_not_called()


if __name__=='__main__':unittest.main()
