from operaciones_store import OperationsStore
from operaciones_sync import sync_operations_outbox


REPORT_HEADERS = [
    "ID_Reporte", "ID_Equipo", "ID_Cliente", "FechaInicio", "FechaFin",
    "PresionCto1", "PresionCto2", "TempCto1", "TempCto2", "Amperaje1",
    "Amperaje2", "ObsElectrico", "OBsElectrónico", "ObsMecanico", "Comentarios",
    "MtoCorrectivo", "PartesUtilizadas", "NombreEquipo", "Marca", "Direccion",
    "Foto1", "Foto2", "Foto3", "Foto4", "Foto5", "Foto6", "Ronda", "Realizado",
    "ClienteDebug", "ID_Visita", "Modelo", "NoSerie", "NoInventario", "Ubicacion",
    "Departamento", "Responsable", "NoContrato", "Vigencia",
]


class _Request:
    def __init__(self, result=None, failure=None):
        self.result, self.failure = result or {}, failure

    def execute(self):
        if self.failure:
            raise self.failure
        return self.result


class _Values:
    def __init__(self, fake):
        self.fake = fake

    def batchGet(self, **kwargs):
        title = kwargs["ranges"][0].split("!", 1)[0].strip("'")
        headers = self.fake.sheets[title]["headers"]
        rows = self.fake.sheets[title]["rows"]
        return _Request({"valueRanges": [{"values": [headers]}, {"values": rows}]})

    def append(self, **kwargs):
        title = kwargs["range"].split("!", 1)[0].strip("'")
        row = list(kwargs["body"]["values"][0])
        self.fake.sheets[title]["rows"].append(row)
        row_number = len(self.fake.sheets[title]["rows"]) + 1
        self.fake.writes.append(("append", title, row_number, row))
        return _Request({"updates": {"updatedRange": f"'{title}'!A{row_number}:AL{row_number}"}})

    def batchUpdate(self, **kwargs):
        self.fake.writes.append(("batchUpdate", kwargs["body"]["data"]))
        return _Request({"totalUpdatedCells": len(kwargs["body"]["data"])})

    def update(self, **kwargs):
        self.fake.writes.append(("update", kwargs["range"], kwargs["body"]["values"]))
        if self.fake.fail_completion_once:
            self.fake.fail_completion_once = False
            return _Request(failure=RuntimeError("fallo temporal"))
        return _Request({"updatedCells": 1})


class _Spreadsheets:
    def __init__(self, fake):
        self.fake = fake

    def get(self, **kwargs):
        return _Request({"sheets": [{"properties": {"title": title}} for title in self.fake.sheets]})

    def values(self):
        return _Values(self.fake)


class FakeSheets:
    def __init__(self, *, fail_completion_once=False):
        self.sheets = {
            "Clientes": {"headers": ["ID_Cliente", "NombreCliente", "Direccion", "Foto", "", "CorreoAutorizado", "RondaSeleccionadaCliente", "URL_Reportes"], "rows": []},
            "Equipos": {"headers": ["ID_Equipo", "ID_Cliente", "NombreEquipo", "Marca", "Foto", "Modelo", "NoSerie", "Estatus", "Inventario", "Ubicacion", "Departamento", "Responsable", "NoContrato", "Vigencia"], "rows": []},
            "Reportes": {"headers": REPORT_HEADERS, "rows": []},
            "ReportesFalla": {"headers": ["ID_ReporteFalla", "ID_Cliente", "ID_Equipo", "Fecha", "DescripcionFalla", "Foto", "Estado", "ID_Reporte", "TipoFalla", "MostrarCliente", "Origen"], "rows": []},
        }
        self.writes = []
        self.fail_completion_once = fail_completion_once

    def spreadsheets(self):
        return _Spreadsheets(self)


def _store_with_report(tmp_path):
    store = OperationsStore(local_path=tmp_path / "sync.sqlite3")
    store.import_matrix_snapshot({
        "clients": [{"id": "UVMQ", "name": "UVM Querétaro", "address": "Campus"}],
        "equipment": [{"id": "UVMQ1", "client_id": "UVMQ", "name": "Chiller 1", "brand": "York", "model": "Y1", "serial": "S1", "status": "Activo"}],
        "reports": [], "faults": [],
    })
    draft = store.save_report_draft({
        "client_id": "UVMQ", "equipment_id": "UVMQ1", "round": "2",
        "payload": {"inicio": "2026-09-12", "fin": "2026-09-12", "p1": "120", "t1": "4"},
    })
    store.save_report_evidence(draft["id"], 1, "drive-foto-1")
    store.save_report_evidence(draft["id"], 2, "drive-foto-2")
    store.finalize_report(draft["id"])
    return store, draft["id"]


def test_dry_run_reads_plan_without_writing_or_consuming_queue(tmp_path):
    store, report_id = _store_with_report(tmp_path)
    fake = FakeSheets()

    result = sync_operations_outbox(store, fake, "sheet-id", dry_run=True)

    assert result["failed"] == 0
    assert result["items"][0]["entity_id"] == report_id
    assert result["items"][0]["action"] == "append"
    assert "Foto1" in result["items"][0]["columns"]
    assert fake.writes == []
    assert len(store.pending_sync()) == 1


def test_report_is_appended_then_completed_in_a_separate_request(tmp_path):
    store, report_id = _store_with_report(tmp_path)
    fake = FakeSheets()

    result = sync_operations_outbox(store, fake, "sheet-id")

    assert result["synced"] == 1
    append = fake.writes[0]
    row = append[3]
    assert row[REPORT_HEADERS.index("ID_Reporte")] == report_id
    assert row[REPORT_HEADERS.index("Foto1")] == "drive-foto-1"
    assert row[REPORT_HEADERS.index("Foto2")] == "drive-foto-2"
    assert row[REPORT_HEADERS.index("Realizado")] == ""
    assert fake.writes[-1][0] == "update"
    assert fake.writes[-1][2] == [[True]]
    assert store.pending_sync() == []


def test_failed_completion_stays_queued_for_idempotent_retry(tmp_path):
    store, report_id = _store_with_report(tmp_path)
    fake = FakeSheets(fail_completion_once=True)

    first = sync_operations_outbox(store, fake, "sheet-id")
    assert first["failed"] == 1
    assert store.pending_sync()[0]["attempts"] == 1

    second = sync_operations_outbox(store, fake, "sheet-id")
    assert second["synced"] == 1
    assert store.pending_sync() == []
    assert sum(write[0] == "append" for write in fake.writes) == 1


def test_pilot_allowlist_leaves_other_clients_queued(tmp_path):
    store, report_id = _store_with_report(tmp_path)
    store.save_client({"id": "OTRO", "name": "Cliente no piloto", "address": ""})
    fake = FakeSheets()

    result = sync_operations_outbox(
        store, fake, "sheet-id", dry_run=True, allowed_client_ids={"UVMQ"},
    )

    assert result["pending"] == 1
    assert result["skipped_by_pilot"] == 1
    assert result["items"][0]["entity_id"] == report_id
    assert len(store.pending_sync()) == 2
