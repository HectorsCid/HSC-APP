from operaciones_store import OperationsStore


def _payload():
    return {
        "clients": [{"id": "UVMQ", "name": "UVM Querétaro", "address": "Querétaro",
                     "selected_round": "2", "has_photo": True,
                     "_photo_ref": "Clientes_Images/uvm.jpg"}],
        "equipment": [{"id": "UVMQ1", "client_id": "UVMQ", "name": "Equipo 1",
                       "brand": "Marca", "model": "M1", "serial": "S1", "status": "Activo",
                       "location": "Azotea", "department": "Mantenimiento", "has_photo": True,
                       "_photo_ref": "Equipos_Images/uvmq1.jpg"}],
        "reports": [{"id": "UVMQ1_R 2", "equipment_id": "UVMQ1", "client_id": "UVMQ",
                     "round": "2", "start": "2026-09-12", "end": "2026-09-12",
                     "completed": True, "photo_count": 3}],
    }


def test_matrix_snapshot_round_trip_uses_ids_and_evidence_rows(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    counts = store.import_matrix_snapshot(_payload())
    result = store.snapshot()

    assert counts == {"clients": 1, "equipment": 1, "reports": 1}
    assert result["clients"][0]["id"] == "UVMQ"
    assert result["clients"][0]["_photo_ref"] == "Clientes_Images/uvm.jpg"
    assert result["equipment"][0]["client_id"] == "UVMQ"
    assert result["reports"][0]["equipment_id"] == "UVMQ1"
    assert result["reports"][0]["photo_count"] == 3
    assert result["stats"]["evidence_photos"] == 3
    assert result["stats"]["pending_sync"] == 0


def test_reimport_updates_without_duplicating(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    payload = _payload()
    store.import_matrix_snapshot(payload)
    payload["clients"][0]["name"] = "UVM Campus Querétaro"
    store.import_matrix_snapshot(payload)
    result = store.snapshot()

    assert len(result["clients"]) == 1
    assert len(result["equipment"]) == 1
    assert len(result["reports"]) == 1
    assert result["clients"][0]["name"] == "UVM Campus Querétaro"


def test_sync_queue_is_idempotent(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.queue_sync("report", "UVMQ1_R 2", "drive", "upload_pdf", {"version": 1})
    store.queue_sync("report", "UVMQ1_R 2", "drive", "upload_pdf", {"version": 2})

    pending = store.pending_sync()
    assert len(pending) == 1
    assert pending[0]["entity_id"] == "UVMQ1_R 2"
    assert pending[0]["payload"] == {"version": 2}


def test_client_and_equipment_writes_are_kept_across_matrix_refresh(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.import_matrix_snapshot(_payload())
    store.save_client({"id": "UVMQ", "name": "UVM Piloto", "matrix_id": "UVMQ",
                       "address": "Campus prueba", "policy_active": True})
    created = store.save_equipment([{"id": "UVMQ2", "client_id": "UVMQ",
                                     "name": "Equipo nuevo", "equipment_type": "Chiller"}])
    store.import_matrix_snapshot(_payload())
    result = store.snapshot()

    assert next(item for item in result["clients"] if item["id"] == "UVMQ")["name"] == "UVM Piloto"
    assert created[0]["equipment_type"] == "Chiller"
    assert any(item["id"] == "UVMQ2" for item in result["equipment"])
    assert len(store.pending_sync()) == 2


def test_report_draft_is_durable_and_does_not_queue_google(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.import_matrix_snapshot(_payload())
    first = store.save_report_draft({"client_id": "UVMQ", "equipment_id": "UVMQ1",
                                     "round": "3", "report_type": "refrigeration",
                                     "payload": {"inicio": "2026-09-12", "p1": "120"}})
    second = store.save_report_draft({"id": first["id"], "client_id": "UVMQ",
                                      "equipment_id": "UVMQ1", "round": "3",
                                      "payload": {"inicio": "2026-09-12", "p1": "125"}})

    assert first["id"] == second["id"]
    assert store.get_report_draft("UVMQ1", "3")["payload"]["p1"] == "125"
    assert store.get_report_draft("UVMQ1", "2") is None
    assert store.pending_sync() == []
