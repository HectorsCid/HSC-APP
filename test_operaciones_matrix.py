from operaciones_matrix import MATRIX_RANGES, build_operaciones_bootstrap, read_operaciones_matrix


def test_build_operaciones_bootstrap_uses_ids_and_omits_sensitive_columns():
    payload = [
        {"range": "Clientes!A1:H9", "values": [
            ["ID_Cliente", "NombreCliente", "Direccion", "Foto", "", "CorreoAutorizado", "RondaSeleccionadaCliente", "URL_Reportes"],
            ["DEMO", "Cliente prueba", "Querétaro", "Clientes_Images/demo.jpg", "", "privado@example.com", "R 2", "url"],
        ]},
        {"range": "Equipos!A1:N9", "values": [
            ["ID_Equipo", "ID_Cliente", "NombreEquipo", "Marca", "Foto", "Modelo", "NoSerie", "Estatus", "Inventario", "Ubicacion", "Departamento", "Responsable", "NoContrato", "Vigencia"],
            ["DEMO1", "DEMO", "Cámara", "Marca", "Equipos_Images/demo.jpg", "M1", "S1", "Activo", "", "Cocina", "Alimentos"],
        ]},
        {"range": "Reportes!A1:AL9", "values": [
            ["ID_Reporte", "ID_Equipo", "ID_Cliente", "FechaInicio", "FechaFin", "PresionCto1", "PresionCto2", "TempCto1", "TempCto2", "Amperaje1", "Amperaje2", "ObsElectrico", "OBsElectrónico", "ObsMecanico", "Comentarios", "MtoCorrectivo", "PartesUtilizadas", "NombreEquipo", "Marca", "Direccion", "Foto1", "Foto2", "Foto3", "Foto4", "Foto5", "Foto6", "Ronda", "Realizado"],
            ["DEMO1_R 2", "DEMO1", "DEMO", "2026-09-11", "2026-09-11", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "a.jpg", "b.jpg", "", "", "", "", "R 2", "TRUE"],
        ]},
    ]
    result = build_operaciones_bootstrap(payload)
    assert result["clients"][0] == {
        "id": "DEMO", "name": "Cliente prueba", "address": "Querétaro",
        "selected_round": "2", "has_photo": True,
    }
    assert "CorreoAutorizado" not in str(result)
    assert result["equipment"][0]["client_id"] == "DEMO"
    assert result["reports"][0]["equipment_id"] == "DEMO1"
    assert result["reports"][0]["completed"] is True
    assert result["reports"][0]["photo_count"] == 2
    assert result["stats"]["evidence_photos"] == 2


def test_read_operaciones_matrix_is_batch_get_only():
    calls = {}

    class Request:
        def execute(self):
            return {"valueRanges": []}

    class Values:
        def batchGet(self, **kwargs):
            calls.update(kwargs)
            return Request()

    class Spreadsheets:
        def values(self):
            return Values()

    class Service:
        def spreadsheets(self):
            return Spreadsheets()

    result = read_operaciones_matrix(Service(), "sheet-id")
    assert result["clients"] == []
    assert calls == {
        "spreadsheetId": "sheet-id",
        "ranges": MATRIX_RANGES,
        "majorDimension": "ROWS",
    }
