import tempfile
import unittest
from pathlib import Path

import app as cotizador


class BorradoresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_draft_path = cotizador._ruta_borradores
        draft_path = Path(self.tmp.name) / cotizador.BORRADORES_FILENAME
        cotizador._ruta_borradores = lambda: draft_path
        cotizador.IS_RENDER = False
        cotizador.AUTO_SYNC_FROM_DRIVE = False
        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        cotizador._reiniciar_costos_internos()
        self.client = cotizador.app.test_client()

    def tearDown(self):
        cotizador._ruta_borradores = self.original_draft_path
        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        cotizador._reiniciar_costos_internos()
        self.tmp.cleanup()

    def test_guardar_continuar_y_eliminar(self):
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

        cotizador.partidas.clear()
        cotizador.datos_cliente.clear()
        response = self.client.get("/borradores/9001/continuar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cotizador.datos_cliente["cotizacion"], "9001")
        self.assertEqual(cotizador.datos_cliente["cliente"], "Cliente de prueba")
        self.assertEqual(cotizador.datos_cliente["nombre_borrador"], "Tuberías Bticino")
        self.assertEqual(len(cotizador.partidas), 1)

        response = self.client.post("/borradores/9001/eliminar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/borradores/list").get_json(), [])

    def test_paginas_muestran_los_nuevos_controles(self):
        cotizador.datos_cliente["cotizacion"] = "9002"
        form = self.client.get("/")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"Guardar borrador", form.data)
        self.assertIn(b"Nombre del borrador", form.data)
        self.assertIn(b"Escribe un nombre para identificar este borrador", form.data)
        self.assertIn(b'href="/cotizaciones"', form.data)

        listado = self.client.get("/cotizaciones")
        self.assertEqual(listado.status_code, 200)
        self.assertIn(b"Borradores", listado.data)
        self.assertIn(b"Continuar editando", listado.data)

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
        self.assertEqual(drafts[0]["costos_internos"]["items"][0]["nombre"], "Tubería privada")

        form["ganancia_valor"] = "50"
        response = self.client.post("/costos-internos/guardar", data=form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(cotizador.partidas), 1)
        self.assertEqual(cotizador.partidas[0]["precio"], 600.0)

        page = self.client.get("/costos-internos")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Minicotizador interno", page.data)
        self.assertIn(b"Usar precio en la cotizaci", page.data)

    def test_varios_calculos_internos_crean_partidas_independientes(self):
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
