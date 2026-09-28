import copy
from html.parser import HTMLParser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as cotizador


class FormControls(HTMLParser):
    """Read rendered form attributes without depending on icon markup or spacing."""
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def find(self, tag, **attributes):
        return [attrs for actual_tag, attrs in self.tags
                if actual_tag == tag and all(attrs.get(key) == value for key, value in attributes.items())]


class BorradoresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_draft_path = cotizador._ruta_borradores
        self.original_quote_path = cotizador._ruta_cotizaciones
        draft_path = Path(self.tmp.name) / cotizador.BORRADORES_FILENAME
        quote_path = Path(self.tmp.name) / cotizador.COTIZACIONES_FILENAME
        cotizador._ruta_borradores = lambda: draft_path
        cotizador._ruta_cotizaciones = lambda: quote_path
        self.enterContext(patch.object(cotizador, 'IS_RENDER', False))
        self.enterContext(patch.object(cotizador, 'AUTO_SYNC_FROM_DRIVE', False))
        # These routes validate catalog membership; never replace that validator.
        # A fresh catalog also prevents one test from supplying another's client.
        self.catalog = {}
        self.enterContext(patch.object(cotizador, 'clientes_predefinidos', self.catalog))
        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        cotizador._reiniciar_costos_internos()
        self.client = cotizador.app.test_client()

    def registrar_cliente(self, nombre):
        self.catalog[nombre] = {
            'atencion': ['Compras'], 'direccion': 'Dirección de prueba',
            'tiempo': '3 días', 'anticipo': '0%', 'vigencia': '30 días',
            'sucursales': [],
        }

    def tearDown(self):
        cotizador._ruta_borradores = self.original_draft_path
        cotizador._ruta_cotizaciones = self.original_quote_path
        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        cotizador._reiniciar_costos_internos()
        self.tmp.cleanup()

    def test_guardar_continuar_y_eliminar(self):
        self.registrar_cliente('Cliente de prueba')
        cotizador.partidas.append({
            "descripcion": "Material pendiente",
            "cantidad": 2,
            "precio": 100.0,
            "total": 200.0,
        })
        response = self.client.post("/borradores/guardar", data={
            "cliente": "Cliente de prueba",
            "nombre_borrador": "Tuberías Bticino",
            "atencion": "Compras",
            "direccion": "Dirección de prueba",
            "fecha": "2026-08-31",
            "cotizacion": "9001",
            "comentarios": "Falta confirmar precio",
        })
        self.assertEqual(response.status_code, 302)

        response = self.client.get("/api/borradores/list")
        self.assertEqual(response.status_code, 200)
        drafts = response.get_json()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["folio"], "9001")
        self.assertEqual(drafts[0]["nombre_borrador"], "Tuberías Bticino")
        self.assertEqual(drafts[0]["partidas"][0]["descripcion"], "Material pendiente")
        self.assertEqual(drafts[0]['datos']['comentarios'], 'Falta confirmar precio')

        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        response = self.client.get("/borradores/9001/continuar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cotizador.datos_cliente["cotizacion"], "9001")
        self.assertEqual(cotizador.datos_cliente["cliente"], "Cliente de prueba")
        self.assertEqual(cotizador.datos_cliente["nombre_borrador"], "Tuberías Bticino")
        self.assertEqual(cotizador.datos_cliente['comentarios'], 'Falta confirmar precio')
        self.assertEqual(len(cotizador.partidas), 1)
        pagina_guardada = self.client.get("/")
        self.assertIn(b"let cotizacionConCambios = false", pagina_guardada.data)

        response = self.client.post("/borradores/9001/eliminar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/borradores/list").get_json(), [])

    def test_paginas_muestran_los_nuevos_controles(self):
        cotizador.datos_cliente["cotizacion"] = "9002"
        form = self.client.get("/")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"Guardar borrador", form.data)
        controls = FormControls(form.get_data(as_text=True))
        # The name is now collected by a prompt and stored in a hidden field.
        self.assertEqual(len(controls.find('input', id='nombre-borrador', name='nombre_borrador', type='hidden')), 1)
        save_buttons = controls.find('button', id='btn-guardar-borrador')
        self.assertEqual(len(save_buttons), 1)
        self.assertEqual(save_buttons[0]['form'], 'form-datos')
        self.assertEqual(save_buttons[0]['formaction'], '/borradores/guardar')
        self.assertEqual(save_buttons[0]['formmethod'], 'post')
        self.assertIn('formnovalidate', save_buttons[0])
        self.assertIn(b"Escribe un nombre para identificar este borrador", form.data)
        self.assertIn(b"Salir de la cotizaci", form.data)
        self.assertIn(b'href="/cotizaciones"', form.data)

        listado = self.client.get("/cotizaciones")
        self.assertEqual(listado.status_code, 200)
        self.assertIn(b"Borradores", listado.data)
        self.assertIn(b"Continuar editando", listado.data)
        self.assertNotIn(b"Si el formulario actual no se guard", listado.data)
        self.assertNotIn(b"Mandar a facturar", listado.data)
        acciones = listado.data.decode("utf-8")
        # These rows are rendered by JS. Scope expectations to each branch so
        # the draft's Delete button cannot satisfy a quotation action assertion.
        draft_actions, quote_actions = acciones.split('${it.borrador ? `', 1)[1].split('` : `', 1)
        quote_actions = quote_actions.split('`}\n', 1)[0]
        primary, menu = quote_actions.split('<div class="quote-menu"', 1)
        self.assertIn('title="Continuar editando"', draft_actions)
        self.assertIn('/borradores/${encodeURIComponent(it.id)}/continuar', draft_actions)
        self.assertLess(draft_actions.index('Continuar</a>'), draft_actions.index('Eliminar</button>'))
        self.assertIn('method="post" action="/borradores/${encodeURIComponent(it.id)}/eliminar"', draft_actions)
        self.assertIn('href="${it.view_url}"', primary)
        self.assertIn('href="${it.facturar_url}"', primary)
        order = ['PDF</a>', 'Facturar</a>', 'data-quote-share=', 'data-quote-more=']
        self.assertEqual(sorted(primary.index(item) for item in order), [primary.index(item) for item in order])
        self.assertIn('aria-label="Más acciones" aria-expanded="false"', primary)
        order = ['Corregir</a>', 'Duplicar</a>', 'Archivos</button>', 'Eliminar</button>']
        self.assertEqual(sorted(menu.index(item) for item in order), [menu.index(item) for item in order])
        self.assertIn('/cotizaciones/${encodeURIComponent(it.id)}/editar', menu)
        self.assertIn('/cotizaciones/${encodeURIComponent(it.id)}/duplicar', menu)
        self.assertIn('method="post" action="/cotizaciones/${encodeURIComponent(it.id)}/eliminar"', menu)
        self.assertIn('El PDF permanecerá en Google Drive.', menu)
        self.assertIn("onsubmit=\"return confirm(", menu)

        factura = self.client.get("/facturas/nueva")
        self.assertEqual(factura.status_code, 200)
        self.assertIn(b"/api/catalogos/fiscales", factura.data)
        self.assertIn(b'value="S01"', factura.data)
        self.assertNotIn(b'value="P01"', factura.data)

    def test_folio_se_asigna_hasta_guardar_borrador(self):
        self.registrar_cliente('Cliente sin reservar')
        original = cotizador.obtener_siguiente_folio
        llamadas = []
        cotizador.obtener_siguiente_folio = lambda: llamadas.append(9100) or 9100
        try:
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(llamadas, [])
            self.assertFalse(cotizador.datos_cliente.get("cotizacion"))

            response = self.client.post("/guardar_datos", data={"cliente": "Cliente sin reservar"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(cotizador.datos_cliente['cliente'], 'Cliente sin reservar')
            self.assertEqual(llamadas, [])

            self.client.get("/limpiar", follow_redirects=True)
            self.client.get("/nueva-cotizacion", follow_redirects=True)
            self.assertEqual(llamadas, [])

            response = self.client.post("/borradores/guardar", data={
                "cliente": "Cliente sin reservar", "nombre_borrador": "Prueba tardía",
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(llamadas, [9100])
            self.assertEqual(cotizador.datos_cliente["cotizacion"], "9100")
            saved = self.client.get('/api/borradores/list').get_json()
            self.assertEqual([item['folio'] for item in saved], ['9100'])

            self.client.get("/")
            self.assertEqual(llamadas, [9100])
        finally:
            cotizador.obtener_siguiente_folio = original

    def test_cliente_inexistente_no_guarda_borrador_ni_reserva_folio(self):
        with patch.object(cotizador, 'obtener_siguiente_folio') as next_folio:
            response = self.client.post('/borradores/guardar', data={
                'cliente': 'NO-REGISTRADO-ZZ99', 'nombre_borrador': 'Debe rechazarse',
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Selecciona un cliente válido', response.get_data(as_text=True))
            next_folio.assert_not_called()
        self.assertEqual(self.client.get('/api/borradores/list').get_json(), [])
        self.assertFalse(cotizador.datos_cliente.get('cliente'))
        self.assertFalse(cotizador.datos_cliente.get('cotizacion'))

    def test_acciones_conservan_cliente_y_condiciones_sin_guardar_datos(self):
        self.registrar_cliente('Cliente directo')
        response = self.client.post("/agregar", data={
            "preservar_datos_cotizacion": "1",
            "cliente": "Cliente directo",
            "atencion": "Compras",
            "direccion": "Dirección especial",
            "fecha": "2026-09-03",
            "tiempo": "Entrega inmediata",
            "anticipo": "30%",
            "vigencia": "15 días",
            "comentarios": "Condiciones particulares",
            "descripcion": "Partida directa",
            "cantidad": "1",
            "precio": "100",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cotizador.datos_cliente["cliente"], "Cliente directo")
        self.assertEqual(cotizador.datos_cliente["tiempo"], "Entrega inmediata")
        self.assertEqual(cotizador.datos_cliente["anticipo"], "30%")
        self.assertEqual(cotizador.datos_cliente["vigencia"], "15 días")
        self.assertEqual(cotizador.datos_cliente['atencion'], ['Compras'])
        self.assertEqual(cotizador.datos_cliente['direccion'], 'Dirección especial')
        self.assertEqual(cotizador.datos_cliente['comentarios'], 'Condiciones particulares')
        self.assertEqual(len(cotizador.partidas), 1)
        self.assertEqual(cotizador.partidas[0]['descripcion'], 'Partida directa')

        preview = self.client.post("/vista_previa", data={
            "cliente": "Cliente directo",
            "atencion": "Compras",
            "direccion": "Dirección especial",
            "fecha": "2026-09-03",
            "tiempo": "48 horas",
            "anticipo": "50%",
            "vigencia": "7 días",
            "comentarios": "Cambio antes de vista previa",
        })
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(cotizador.datos_cliente["tiempo"], "48 horas")
        self.assertEqual(cotizador.datos_cliente["anticipo"], "50%")
        self.assertEqual(cotizador.datos_cliente["vigencia"], "7 días")
        self.assertEqual(cotizador.datos_cliente['cliente'], 'Cliente directo')
        self.assertEqual(cotizador.datos_cliente['comentarios'], 'Cambio antes de vista previa')
        self.assertIn("48 horas".encode(), preview.data)

    def test_corregir_conserva_folio_y_duplicar_solicita_uno_nuevo(self):
        cotizador.registrar_cotizacion({
            "id": "9200",
            "folio": "9200",
            "cliente": "Cliente histórico",
            "conceptos": [{
                "descripcion": "Texto por corregir",
                "cantidad": 2,
                "precio_unitario": 150,
            }],
            "datos": {
                "cliente": "Cliente histórico",
                "tiempo": "3 días",
                "anticipo": "40%",
                "vigencia": "10 días",
            },
        })

        response = self.client.get("/cotizaciones/9200/editar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cotizador.datos_cliente["cotizacion"], "9200")
        self.assertEqual(cotizador.datos_cliente["anticipo"], "40%")
        self.assertEqual(cotizador.partidas[0]["descripcion"], "Texto por corregir")

        response = self.client.get("/cotizaciones/9200/duplicar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cotizador.datos_cliente["cotizacion"], "")
        self.assertEqual(cotizador.datos_cliente["vigencia"], "10 días")

    def test_eliminar_cotizacion_solo_la_quita_del_registro(self):
        cotizador.registrar_cotizacion({"id": "9300", "folio": "9300", "cliente": "Uno"})
        cotizador.registrar_cotizacion({"id": "9301", "folio": "9301", "cliente": "Dos"})

        response = self.client.post("/cotizaciones/9300/eliminar")
        self.assertEqual(response.status_code, 302)
        items = self.client.get("/api/cotizaciones/list").get_json()
        self.assertEqual([item["folio"] for item in items], ["9301"])

    def test_precio_pendiente_se_guarda_pero_no_genera_pdf(self):
        response = self.client.post("/agregar", data={
            "descripcion": "Refacción por cotizar",
            "cantidad": "1",
            "precio": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(cotizador.partidas), 1)
        self.assertTrue(cotizador.partidas[0]["precio_pendiente"])

        cotizador.datos_cliente["cotizacion"] = "9003"
        response = self.client.get("/generar_pdf")
        self.assertEqual(response.status_code, 302)

        response = self.client.post("/editar/0", data={
            "descripcion": "Refacción confirmada",
            "cantidad": "1",
            "precio": "250.50",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(cotizador.partidas[0]["precio_pendiente"])
        self.assertEqual(cotizador.partidas[0]["total"], 250.50)

    def test_costos_internos_se_guardan_y_transfieren_solo_el_precio_final(self):
        self.registrar_cliente('Cliente interno')
        cotizador.datos_cliente.update({
            "cotizacion": "9004",
            "cliente": "Cliente interno",
            "fecha": "2026-08-31",
        })
        form = {
            "categoria": ["material", "mano_obra"],
            "nombre": ["Tubería privada", "Horas privadas"],
            "cantidad": ["2", "3"],
            "unidad": ["Metro", "Hora"],
            "costo_unitario": ["100", "50"],
            "merma": ["10", "0"],
            "nota": ["Proveedor privado", ""],
            "gastos_extra": "30",
            "ganancia_modo": "porcentaje",
            "ganancia_valor": "25",
            "redondeo": "50",
            "descripcion_publica": "Suministro e instalación",
            "accion": "transferir",
        }
        response = self.client.post("/costos-internos/guardar", data=form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(cotizador.costos_internos["items"]), 2)
        self.assertEqual(len(cotizador.partidas), 1)
        self.assertEqual(cotizador.partidas[0]["descripcion"], "Suministro e instalación")
        self.assertEqual(cotizador.partidas[0]["precio"], 500.0)
        self.assertNotIn("Tubería privada", cotizador.partidas[0]["descripcion"])

        drafts = self.client.get("/api/borradores/list").get_json()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["costos_internos"]["items"][0]["nombre"], "Tubería privada")
        self.assertEqual(drafts[0]['costos_internos']['items'][0]['nota'], 'Proveedor privado')
        self.assertEqual(drafts[0]['costos_internos']['items'][0]['costo_unitario'], 100.0)
        self.assertEqual(drafts[0]['costos_internos']['items'][1]['nombre'], 'Horas privadas')

        form["ganancia_valor"] = "50"
        response = self.client.post("/costos-internos/guardar", data=form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(cotizador.partidas), 1)
        self.assertEqual(cotizador.partidas[0]["precio"], 600.0)
        self.assertEqual(cotizador.partidas[0]['cantidad'], 1)
        self.assertEqual(cotizador.partidas[0]['total'], 600.0)
        self.assertTrue({'items', 'nota', 'costo_unitario', 'merma', 'ganancia_valor'}.isdisjoint(cotizador.partidas[0]))

        saved = self.client.get('/api/borradores/list').get_json()[0]
        internal = copy.deepcopy(saved['costos_internos'])
        self.assertEqual(internal['ganancia_valor'], 50.0)
        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        cotizador._reiniciar_costos_internos()
        self.assertEqual(self.client.get('/borradores/9004/continuar').status_code, 302)
        self.assertEqual(cotizador.costos_internos['items'], internal['items'])
        self.assertEqual(cotizador.costos_internos['desgloses'], internal['desgloses'])
        self.assertEqual(cotizador.partidas[0]['precio'], 600.0)

        # Preview renders the same public template as the PDF, without generating
        # or uploading a document. Private names, notes and costs must not leak.
        preview = self.client.get('/vista_previa')
        self.assertEqual(preview.status_code, 200)
        public_html = preview.get_data(as_text=True)
        self.assertIn('Suministro e instalación', public_html)
        self.assertIn('$600.00', public_html)
        for private in ('Tubería privada', 'Horas privadas', 'Proveedor privado',
                        'costo_unitario', 'ganancia_valor', '$100.00', '$50.00'):
            self.assertNotIn(private, public_html)

        page = self.client.get("/costos-internos")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('<h1>Costos internos</h1>', html)
        self.assertIn('Este desglose permanece interno.', html)
        controls = FormControls(html)
        self.assertEqual(len(controls.find('form', id='cost-form', method='post', action='/costos-internos/guardar')), 1)
        for value, label in [('guardar', 'Guardar costos'), ('transferir', 'Usar precio en la cotización'),
                             ('transferir_todos', 'Usar todas las partidas')]:
            self.assertEqual(len(controls.find('button', type='submit', name='accion', value=value)), 1)
            self.assertIn(label, html)
        for tag, control_id in [('button', 'toggle-entry'), ('input', 'cost-search'),
                                ('select', 'type-filter'), ('select', 'desglose-selector')]:
            self.assertEqual(len(controls.find(tag, id=control_id)), 1)

    def test_varios_calculos_internos_crean_partidas_independientes(self):
        self.registrar_cliente('Cliente interno')
        cotizador.datos_cliente.update({"cotizacion": "9005", "cliente": "Cliente interno"})
        form = {
            "categoria": ["material"], "nombre": ["Primer costo"],
            "cantidad": ["1"], "unidad": ["Pieza"], "costo_unitario": ["100"],
            "merma": ["0"], "nota": [""], "gastos_extra": "0",
            "ganancia_modo": "porcentaje", "ganancia_valor": "0", "redondeo": "1",
            "descripcion_publica": "Primera partida", "accion": "transferir",
        }
        self.client.post("/costos-internos/guardar", data=form)
        primer_id = cotizador.costos_internos["id"]

        self.client.get("/costos-internos?nuevo=1")
        form.update({"nombre": ["Segundo costo"], "costo_unitario": ["250"],
                     "descripcion_publica": "Segunda partida",
                     "desglose_id": cotizador.costos_internos["id"]})
        self.client.post("/costos-internos/guardar", data=form)

        self.assertEqual(len(cotizador.partidas), 2)
        self.assertEqual(cotizador.partidas[0]["descripcion"], "Primera partida")
        self.assertEqual(cotizador.partidas[1]["descripcion"], "Segunda partida")
        self.assertNotEqual(cotizador.partidas[0]["costos_internos_id"], cotizador.partidas[1]["costos_internos_id"])

        self.client.get(f"/costos-internos?desglose={primer_id}")
        form.update({"nombre": ["Primer costo actualizado"], "costo_unitario": ["175"],
                     "descripcion_publica": "Primera partida actualizada", "desglose_id": primer_id})
        self.client.post("/costos-internos/guardar", data=form)
        self.assertEqual(len(cotizador.partidas), 2)
        self.assertEqual(cotizador.partidas[0]["precio"], 175.0)
        self.assertEqual(cotizador.partidas[1]["precio"], 250.0)

        cotizador.partidas.clear()
        cotizador.partidas.append({
            "descripcion": "Partida normal", "cantidad": 1,
            "precio": 999.0, "total": 999.0,
        })
        form["accion"] = "transferir_todos"
        self.client.post("/costos-internos/guardar", data=form)
        self.assertEqual(len(cotizador.partidas), 3)
        self.assertEqual([p["precio"] for p in cotizador.partidas], [999.0, 175.0, 250.0])
        self.client.post("/costos-internos/guardar", data=form)
        self.assertEqual(len(cotizador.partidas), 3)

        self.client.post("/borradores/guardar", data={
            "cliente": "Cliente interno", "cotizacion": "9005",
        })
        cotizador.partidas.clear()
        cotizador._reiniciar_costos_internos()
        self.client.get("/borradores/9005/continuar")
        self.assertEqual(len(cotizador.partidas), 3)
        self.assertEqual(len(cotizador.costos_internos["desgloses"]), 2)


if __name__ == "__main__":
    unittest.main()
