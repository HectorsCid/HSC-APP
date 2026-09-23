"""Customer branch catalog and quotation regression tests (no PDF generation)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as cotizador
from quotation_branches import catalog_branches, select_branch, validate_branches


class BranchNamesTest(unittest.TestCase):
    def test_normalizes_spaces_and_ignores_blank_rows(self):
        self.assertEqual(validate_branches([' Lote  86 ', '', 'Lote 83']), ['Lote 86', 'Lote 83'])

    def test_rejects_duplicates_paths_and_excess(self):
        for values in (['Lote 86', 'lote   86'], ['../Otro'], ['..'], ['Lote:86'],
                       ['x' * 101], [str(i) for i in range(101)]):
            with self.subTest(values=values[:2]), self.assertRaises(ValueError):
                validate_branches(values)

    def test_only_legacy_clients_inherit_historic_names(self):
        self.assertEqual(catalog_branches({}, ['Lote 86', 'lote 86']), ['Lote 86'])
        self.assertEqual(catalog_branches({'sucursales': []}, ['Lote 86']), [])

    def test_selection_is_canonical_but_not_arbitrary(self):
        self.assertEqual(select_branch('lote  86', ['Lote 86']), 'Lote 86')
        self.assertEqual(select_branch('', []), '')
        with self.assertRaises(ValueError):
            select_branch('Lote 87', ['Lote 86'])

    def test_removed_branch_only_survives_for_its_original_customer(self):
        self.assertEqual(select_branch('Lote viejo', [], previous_branch='Lote viejo',
                                      previous_client='A', current_client='A'), 'Lote viejo')
        with self.assertRaises(ValueError):
            select_branch('Lote viejo', [], previous_branch='Lote viejo',
                          previous_client='A', current_client='B')


class BranchRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hsc-branches-')
        self.addCleanup(self.tmp.cleanup)
        self.quotes = Path(self.tmp.name) / 'cotizaciones.json'
        self.drafts = Path(self.tmp.name) / 'borradores.json'
        self.quotes.write_text('[]', encoding='utf-8')
        self.drafts.write_text('[]', encoding='utf-8')
        self.catalog = {
            'Visscher Caravelle': {'rfc': 'VCA010101XXX', 'sucursales': ['Lote 86', 'Lote 83'],
                                  'contactos': [], 'atencion': [], 'direccion': 'Dirección fiscal'},
            'Otro cliente': {'rfc': 'OTR010101XXX', 'sucursales': ['Norte']},
        }
        self.snapshots = []
        for target, value in (
            ('clientes_predefinidos', self.catalog), ('_ruta_cotizaciones', lambda: self.quotes),
            ('_ruta_borradores', lambda: self.drafts), ('AUTO_SYNC_FROM_DRIVE', False),
            ('guardar_clientes', lambda data: self.snapshots.append(copy.deepcopy(data))),
        ):
            self.enterContext(patch.object(cotizador, target, value))
        self.enterContext(patch.dict(cotizador.app.config, TESTING=True))
        cotizador.datos_cliente.clear()
        cotizador.partidas.clear()
        cotizador._reiniciar_costos_internos()
        self.addCleanup(cotizador.datos_cliente.clear)
        self.addCleanup(cotizador.partidas.clear)
        self.client = cotizador.app.test_client()

    def edit(self, names, **extra):
        return self.client.post('/editar_cliente', data={
            'cliente_original': 'Visscher Caravelle', 'nombre': 'Visscher Caravelle',
            'rfc': 'VCA010101XXX', 'sucursales_present': '1', 'sucursal_nombre': names, **extra,
        })

    def test_create_customer_stores_branches_without_duplicating_rfc(self):
        response = self.client.post('/nuevo_cliente', data={
            'nombre': 'Cliente nuevo', 'rfc': 'VCA010101XXX',
            'sucursal_nombre': ['Lote 81', 'Lote 82'],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshots[-1]['Cliente nuevo']['sucursales'], ['Lote 81', 'Lote 82'])
        self.assertEqual(self.snapshots[-1]['Cliente nuevo']['rfc'], 'VCA010101XXX')
        self.assertEqual(self.catalog['Visscher Caravelle']['sucursales'], ['Lote 86', 'Lote 83'])

    def test_create_cannot_overwrite_existing_customer(self):
        before = copy.deepcopy(self.catalog)
        self.client.post('/nuevo_cliente', data={'nombre': 'Visscher Caravelle', 'sucursal_nombre': ['X']})
        self.assertEqual(self.catalog, before)
        self.assertEqual(self.snapshots, [])

    def test_edit_updates_catalog_and_retains_fiscal_identity(self):
        self.assertEqual(self.edit(['Lote 86', 'Lote 81']).status_code, 302)
        self.assertEqual(self.snapshots[-1]['Visscher Caravelle']['sucursales'], ['Lote 86', 'Lote 81'])
        self.assertEqual(self.snapshots[-1]['Visscher Caravelle']['rfc'], 'VCA010101XXX')

    def test_duplicate_rejected_without_partial_save(self):
        before = copy.deepcopy(self.catalog)
        response = self.edit(['Lote 86', 'lote 86'])
        self.assertEqual(self.catalog, before)
        self.assertEqual(self.snapshots, [])
        self.assertIn('repetida', self.client.get(response.location).get_data(as_text=True))

    def test_remove_all_does_not_modify_history_or_revive_old_branches(self):
        self.quotes.write_text(json.dumps([{'cliente': 'Visscher Caravelle', 'sucursal': 'Lote 86'}]), encoding='utf-8')
        before = self.quotes.read_bytes()
        self.edit([])
        self.assertEqual(cotizador._catalogo_sucursales_cotizacion()['Visscher Caravelle'], [])
        self.assertEqual(self.quotes.read_bytes(), before)

    def test_old_form_preserves_catalog(self):
        self.client.post('/editar_cliente', data={
            'cliente_original': 'Visscher Caravelle', 'nombre': 'Visscher Caravelle', 'rfc': 'VCA010101XXX',
        })
        self.assertEqual(self.catalog['Visscher Caravelle']['sucursales'], ['Lote 86', 'Lote 83'])

    def test_fiscal_update_does_not_erase_branches(self):
        response = self.client.post('/api/clientes/fiscales', json={
            'cliente': 'Visscher Caravelle', 'rfc': 'VCA010101XXX',
            'razon_social': 'Visscher Caravelle', 'cp': '76120',
            'regimen_fiscal': '601', 'uso_cfdi': 'G03',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshots[-1]['Visscher Caravelle']['sucursales'], ['Lote 86', 'Lote 83'])

    def test_draft_roundtrip_retains_branch_after_catalog_removal(self):
        # Existing draft persistence is exercised locally; Drive is not contacted.
        with patch.object(cotizador, 'subir_borradores_a_drive', return_value=True):
            response = self.client.post('/borradores/guardar', data={
                'cliente': 'Visscher Caravelle', 'sucursal': 'Lote 83', 'cotizacion': '9100',
            })
        self.assertEqual(response.status_code, 302)
        saved = json.loads(self.drafts.read_text(encoding='utf-8'))[0]
        self.assertEqual(saved['sucursal'], 'Lote 83')
        self.assertEqual(saved['datos']['sucursal'], 'Lote 83')
        self.edit(['Lote 86'])
        cotizador.datos_cliente.clear()
        self.client.get('/borradores/9100/continuar')
        self.assertEqual(cotizador.datos_cliente['sucursal'], 'Lote 83')
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('Lote 83 · guardada en esta cotización', page)

    def test_history_populates_legacy_customer_only(self):
        del self.catalog['Visscher Caravelle']['sucursales']
        self.quotes.write_text(json.dumps([{'cliente': 'Visscher Caravelle', 'sucursal': 'Lote 86'}]), encoding='utf-8')
        self.drafts.write_text(json.dumps([{'datos': {'cliente': 'Visscher Caravelle', 'sucursal': 'Lote 81'}}]), encoding='utf-8')
        self.assertEqual(cotizador._catalogo_sucursales_cotizacion()['Visscher Caravelle'], ['Lote 81', 'Lote 86'])

    def test_saved_catalog_does_not_need_to_read_history(self):
        with patch.object(cotizador, '_ruta_cotizaciones', side_effect=AssertionError('No history read')):
            self.assertEqual(cotizador._catalogo_sucursales_cotizacion()['Visscher Caravelle'], ['Lote 86', 'Lote 83'])

    def test_broken_quote_history_does_not_hide_branches_in_drafts(self):
        del self.catalog['Visscher Caravelle']['sucursales']
        self.quotes.write_text('{broken', encoding='utf-8')
        self.drafts.write_text(json.dumps([{'datos': {'cliente': 'Visscher Caravelle', 'sucursal': 'Lote 81'}}]), encoding='utf-8')
        self.assertEqual(cotizador._catalogo_sucursales_cotizacion()['Visscher Caravelle'], ['Lote 81'])

    def test_manage_saves_quote_capture_before_leaving(self):
        cotizador.partidas.append({'descripcion': 'Trabajo', 'cantidad': 1, 'precio': 20, 'total': 20})
        response = self.client.post('/cotizacion/sucursales', data={
            'cliente': 'Visscher Caravelle', 'sucursal': 'Lote 86', 'comentarios': 'No perder',
        })
        self.assertTrue(response.location.endswith('#sucursales'))
        self.assertEqual(cotizador.datos_cliente['sucursal'], 'Lote 86')
        self.assertEqual(cotizador.datos_cliente['comentarios'], 'No perder')
        self.assertEqual(len(cotizador.partidas), 1)

    def test_visiting_another_customer_does_not_change_open_quote(self):
        cotizador.datos_cliente.update(cliente='Visscher Caravelle', sucursal='Lote 86')
        response = self.client.get('/editar_cliente?cliente=Otro+cliente')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cotizador.datos_cliente['cliente'], 'Visscher Caravelle')
        self.assertEqual(cotizador.datos_cliente['sucursal'], 'Lote 86')

    def test_tampered_branch_rejected_at_all_capture_routes(self):
        cotizador.datos_cliente.update(cliente='Visscher Caravelle', sucursal='Lote 86', comentarios='Original')
        before = dict(cotizador.datos_cliente)
        for url in ('/guardar_datos', '/borradores/guardar', '/costos-internos/abrir',
                    '/generar_pdf', '/vista_previa', '/cotizacion/sucursales', '/agregar'):
            with self.subTest(url=url):
                response = self.client.post(url, data={
                    'cliente': 'Otro cliente', 'sucursal': 'Lote 86', 'comentarios': 'Cambio',
                    'preservar_datos_cotizacion': '1', 'descripcion': 'No agregar', 'cantidad': '1', 'precio': '2',
                })
                self.assertEqual(response.status_code, 302)
                self.assertEqual(dict(cotizador.datos_cliente), before)
                self.assertEqual(list(cotizador.partidas), [])

    def test_quote_uses_selector_and_retains_removed_historic_value(self):
        cotizador.datos_cliente.update(cliente='Visscher Caravelle', sucursal='Lote viejo')
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('<select name="sucursal"', page)
        self.assertIn('Lote viejo · guardada en esta cotización', page)
        self.assertNotIn('list="sucursales-list"', page)
        self.client.post('/guardar_datos', data={'cliente': 'Visscher Caravelle', 'sucursal': 'Lote viejo'})
        self.assertEqual(cotizador.datos_cliente['sucursal'], 'Lote viejo')

    def test_reopen_legacy_quote_retains_top_level_branch(self):
        self.quotes.write_text(json.dumps([{
            'id': '100', 'folio': '100', 'cliente': 'Visscher Caravelle', 'sucursal': 'Lote retirado',
            'datos': {'cliente': 'Visscher Caravelle'}, 'partidas': [],
        }]), encoding='utf-8')
        response = self.client.get('/cotizaciones/100/editar')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cotizador.datos_cliente['sucursal'], 'Lote retirado')

    def test_both_customer_forms_render_editor(self):
        for url in ('/nuevo_cliente', '/editar_cliente?cliente=Visscher+Caravelle'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'data-branch-editor', response.data)
                self.assertIn(b'quotation_branches.js', response.data)


if __name__ == '__main__':
    unittest.main()
