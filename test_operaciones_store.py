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
                     "completed": True, "photo_count": 3,
                     "_raw": {"PresionCto1": "120", "DatoNuevo": "conservado"},
                     "_evidence_refs": ["foto-1.jpg", "", "foto-3.jpg", "foto-4.jpg", "", ""]}],
        "faults": [{"id": "F-UVM-1", "client_id": "UVMQ", "equipment_id": "UVMQ1",
                    "report_id": "UVMQ1_R 2", "description": "Temperatura alta",
                    "priority": "Alta", "status": "Reportada", "reported_at": "2026-09-12"}],
    }


def test_matrix_snapshot_round_trip_uses_ids_and_evidence_rows(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    counts = store.import_matrix_snapshot(_payload())
    result = store.snapshot()

    assert counts == {"clients": 1, "equipment": 1, "reports": 1, "faults": 1}
    assert result["clients"][0]["id"] == "UVMQ"
    assert result["clients"][0]["_photo_ref"] == "Clientes_Images/uvm.jpg"
    assert result["equipment"][0]["client_id"] == "UVMQ"
    assert result["reports"][0]["equipment_id"] == "UVMQ1"
    assert result["reports"][0]["photo_count"] == 3
    assert result["faults"][0]["equipment_id"] == "UVMQ1"
    assert result["stats"]["evidence_photos"] == 3
    assert result["stats"]["pending_sync"] == 0

    with store.connection() as conn:
        evidence = conn.execute(
            "SELECT position,storage_ref FROM operations_evidence ORDER BY position"
        ).fetchall()
        report_raw = conn.execute(
            "SELECT payload_json FROM operations_reports WHERE id=?", ("UVMQ1_R 2",)
        ).fetchone()[0]
    assert evidence == [(1, "foto-1.jpg"), (3, "foto-3.jpg"), (4, "foto-4.jpg")]
    assert '"DatoNuevo": "conservado"' in report_raw


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


def test_import_keeps_legacy_placeholder_out_of_active_policies(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    payload = _payload()
    payload["clients"].append({
        "id": "IHPJ", "name": "Cliente pendiente de vincular (IHPJ)",
        "policy_active": False, "has_photo": False,
    })
    store.import_matrix_snapshot(payload)

    placeholder = next(item for item in store.snapshot()["clients"] if item["id"] == "IHPJ")
    assert placeholder["policy_active"] is False


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


def test_thumbnail_cache_is_persistent_and_invalidates_when_photo_changes(tmp_path):
    database = tmp_path / "operations.sqlite3"
    store = OperationsStore(local_path=database)
    store.import_matrix_snapshot(_payload())
    store.save_cached_thumbnail(
        "client", "UVMQ", "Clientes_Images/uvm.jpg", b"miniatura-v1", "image/webp", 240, 180
    )

    reopened = OperationsStore(local_path=database)
    cached = reopened.get_cached_thumbnail("client", "UVMQ", "Clientes_Images/uvm.jpg")
    assert cached["content"] == b"miniatura-v1"
    assert (cached["width"], cached["height"]) == (240, 180)
    assert reopened.get_cached_thumbnail("client", "UVMQ", "Clientes_Images/nueva.jpg") is None
    assert reopened.get_media_ref("client", "UVMQ") == "Clientes_Images/uvm.jpg"
    assert reopened.snapshot()["stats"]["cached_thumbnails"] == 1


def test_client_order_is_explicit_and_survives_matrix_refresh(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    payload = _payload()
    payload["clients"].append({"id": "ZZZ", "name": "Cliente Z", "policy_active": True})
    store.import_matrix_snapshot(payload)
    store.reorder_clients(["ZZZ", "UVMQ"])
    store.import_matrix_snapshot(payload)

    clients = store.snapshot()["clients"]
    assert [client["id"] for client in clients[:2]] == ["ZZZ", "UVMQ"]
    assert [client["sort_order"] for client in clients[:2]] == [1, 2]


def test_report_detail_is_loaded_on_demand(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.import_matrix_snapshot(_payload())

    report = store.get_report_detail("UVMQ1_R 2")
    assert report["equipment_id"] == "UVMQ1"
    assert report["client_id"] == "UVMQ"
    assert report["payload"]["PresionCto1"] == "120"
    assert report["photo_count"] == 3
    assert [item["position"] for item in report["evidence"]] == [1, 3, 4]
    assert store.get_report_evidence_ref("UVMQ1_R 2", 3)["photo_ref"] == "foto-3.jpg"


def test_report_draft_can_be_finalized(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.import_matrix_snapshot(_payload())
    draft = store.save_report_draft({
        "client_id": "UVMQ", "equipment_id": "UVMQ1", "round": "3",
        "payload": {"inicio": "2026-09-12", "fin": "2026-09-12", "p1": "120"},
    })

    report = store.finalize_report(draft["id"])

    assert report["completed"] is True
    assert report["state"] == "completed"
    assert report["sync_status"] == "pending"
    assert report["payload"]["p1"] == "120"
    assert any(item["entity_type"] == "report" for item in store.pending_sync())


def test_partner_fault_is_linked_to_existing_equipment(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.import_matrix_snapshot(_payload())
    fault = store.save_fault({"client_id": "UVMQ", "equipment_id": "UVMQ1",
                              "description": "No enfría", "priority": "Alta"})

    assert fault["client_id"] == "UVMQ"
    assert fault["equipment_id"] == "UVMQ1"
    assert fault["status"] == "Reportada"
    assert any(item["entity_type"] == "fault" for item in store.pending_sync())


def test_partner_fault_can_be_marked_as_attended(tmp_path):
    store = OperationsStore(local_path=tmp_path / "operations.sqlite3")
    store.import_matrix_snapshot(_payload())

    fault = store.resolve_fault(
        "F-UVM-1", resolved_by="Cliente", resolution_notes="Se ajustó el control.",
    )

    assert fault["status"] == "Atendida"
    assert fault["resolved_at"]
    assert fault["resolved_by"] == "Cliente"
    assert fault["resolution_notes"] == "Se ajustó el control."
    queued = [item for item in store.pending_sync() if item["entity_type"] == "fault"]
    assert queued[-1]["payload"]["status"] == "Atendida"
    store.import_matrix_snapshot(_payload())
    assert store.snapshot()["faults"][0]["status"] == "Atendida"
