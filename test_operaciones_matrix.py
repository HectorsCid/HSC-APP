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
    assert MATRIX_RANGES == ["Clientes!A1:ZZ", "Equipos!A1:ZZ", "Reportes!A1:ZZ"]


def test_media_references_are_opt_in_and_remain_private():
    rows = [
        {"range": "Clientes!A1:H3", "values": [
            ["ID_Cliente", "NombreCliente", "Foto"],
            ["UVMQ", "UVM Queretaro", "Clientes_Images/uvm.jpg"],
        ]},
        {"range": "Equipos!A1:N3", "values": [
            ["ID_Equipo", "ID_Cliente", "NombreEquipo", "Foto"],
            ["UVMQ1", "UVMQ", "Equipo UVM", "Equipos_Images/uvm1.jpg"],
        ]},
    ]
    public_payload = build_operaciones_bootstrap(rows)
    private_payload = build_operaciones_bootstrap(rows, include_media_refs=True)

    assert "_photo_ref" not in public_payload["clients"][0]
    assert "_photo_ref" not in public_payload["equipment"][0]
    assert private_payload["clients"][0]["_photo_ref"] == "Clientes_Images/uvm.jpg"
    assert private_payload["equipment"][0]["_photo_ref"] == "Equipos_Images/uvm1.jpg"


def test_full_import_keeps_every_column_and_real_evidence_positions():
    rows = [
        {"range": "Clientes!A1:ZZ", "values": [
            ["ID_Cliente", "NombreCliente", "CorreoAutorizado", "DatoNuevo"],
            ["UVMQ", "UVM Queretaro", "privado@example.com", "se conserva"],
        ]},
        {"range": "Equipos!A1:ZZ", "values": [
            ["ID_Equipo", "ID_Cliente", "NombreEquipo", "NoContrato"],
            ["UVMQ1", "UVMQ", "Equipo UVM", "POL-2026"],
        ]},
        {"range": "Reportes!A1:ZZ", "values": [
            ["ID_Reporte", "ID_Equipo", "ID_Cliente", "Foto1", "Foto2", "Foto3", "LecturaExtra"],
            ["UVMQ1_R 2", "UVMQ1", "UVMQ", "uno.jpg", "", "tres.jpg", "42"],
        ]},
    ]
    result = build_operaciones_bootstrap(
        rows, include_media_refs=True, include_raw=True
    )

    assert result["clients"][0]["_raw"]["CorreoAutorizado"] == "privado@example.com"
    assert result["clients"][0]["_raw"]["DatoNuevo"] == "se conserva"
    assert result["equipment"][0]["_raw"]["NoContrato"] == "POL-2026"
    assert result["reports"][0]["_raw"]["LecturaExtra"] == "42"
    assert result["reports"][0]["_evidence_refs"][:3] == ["uno.jpg", "", "tres.jpg"]


def test_appsheet_legacy_report_headers_are_understood():
    rows = [{"range": "Reportes!A1:ZZ", "values": [
        ["ID_Reporte", "ID_Equipo", "ID_Cliente", "Periodo", "FECHA DE INICIO",
         "FECHA DE TERMINACIÓN", "Foto 1", "Realizado"],
        ["UVMQ1_R 3", "UVMQ1", "UVMQ", "R 3", "2026-09-10", "2026-09-11",
         "evidencia.jpg", "TRUE"],
    ]}]

    report = build_operaciones_bootstrap(rows, include_media_refs=True)["reports"][0]
    assert report["round"] == "3"
    assert report["start"] == "2026-09-10"
    assert report["end"] == "2026-09-11"
    assert report["completed"] is True
    assert report["_evidence_refs"][0] == "evidencia.jpg"


def test_fault_sheet_is_imported_and_related_by_ids():
    rows = [
        {"range": "Equipos!A1:ZZ", "values": [
            ["ID_Equipo", "ID_Cliente", "NombreEquipo"],
            ["UVMQ1", "UVMQ", "Equipo UVM"],
        ]},
        {"range": "'Fallas reportadas'!A1:ZZ", "values": [
            ["ID_Falla", "ID_Equipo", "Descripción de la falla", "Prioridad", "Estado", "Fecha"],
            ["F-1", "UVMQ1", "Temperatura alta", "Alta", "En revisión", "2026-09-12"],
        ]},
    ]

    fault = build_operaciones_bootstrap(rows)["faults"][0]
    assert fault["id"] == "F-1"
    assert fault["client_id"] == "UVMQ"
    assert fault["equipment_id"] == "UVMQ1"
    assert fault["description"] == "Temperatura alta"


def test_exact_reportes_falla_headers_keep_original_id_and_type():
    rows = [{"range": "ReportesFalla!A1:K", "values": [
        ["ID_ReporteFalla", "ID_Cliente", "ID_Equipo", "Fecha", "DescripcionFalla",
         "Foto", "Estado", "ID_Reporte", "TipoFalla", "MostrarCliente", "Origen"],
        ["RF-100", "UVMQ", "UVMQ1", "2026-09-12", "No enfría", "", "Reportada",
         "UVMQ1_R 2", "Grave", True, "Cliente"],
    ]}]

    fault = build_operaciones_bootstrap(rows)["faults"][0]

    assert fault["id"] == "RF-100"
    assert fault["priority"] == "Grave"
