import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from operaciones_store import OperationsStore, _now


class ReportCollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = OperationsStore(local_path=Path(self.tempdir.name) / "operations.sqlite3")
        self.store.initialize()
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO operations_clients(id,name,updated_at) VALUES (?,?,?)",
                ("C1", "Cliente", _now()),
            )
            conn.execute(
                "INSERT INTO operations_equipment(id,client_id,name,updated_at) VALUES (?,?,?,?)",
                ("EQ1", "C1", "Equipo", _now()),
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_shared_draft_merges_only_changed_fields(self):
        first = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "1",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25", "notas": "Primero",
                        "_draft_user_id": "tech-a"},
        })
        discovered = self.store.get_report_draft("EQ1", "1", user_id="tech-b")
        self.assertEqual(first["id"], discovered["id"])

        second = self.store.save_report_draft({
            "id": first["id"], "client_id": "C1", "equipment_id": "EQ1", "round": "1",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25", "notas": "Copia vieja",
                        "p1": "65", "_draft_user_id": "tech-b"},
            "changed_fields": {"p1": "65"}, "base_values": {"p1": ""},
        })
        self.assertEqual("Primero", second["payload"]["notas"])
        self.assertEqual("65", second["payload"]["p1"])
        self.assertGreater(second["revision"], first["revision"])

    def test_offline_retry_discovers_shared_draft_without_overwriting_it(self):
        shared = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "1",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25",
                        "notas": "Cambio reciente del técnico conectado"},
        })
        retried = self.store.save_report_draft({
            "id": "", "client_id": "C1", "equipment_id": "EQ1", "round": "1",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25",
                        "notas": "Copia offline antigua", "p1": "72"},
            "changed_fields": {"p1": "72"}, "base_values": {"p1": ""},
        })
        self.assertEqual(shared["id"], retried["id"])
        self.assertEqual("Cambio reciente del técnico conectado", retried["payload"]["notas"])
        self.assertEqual("72", retried["payload"]["p1"])

    def test_evidence_reservations_are_distinct_and_idempotent(self):
        draft = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "2",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25",
                        "_draft_user_id": "tech-a"},
        })
        first = self.store.reserve_report_evidence(
            draft["id"], 1, "photo-a", actor_id="tech-a", actor_name="Ana"
        )
        second = self.store.reserve_report_evidence(
            draft["id"], 1, "photo-b", actor_id="tech-b", actor_name="Beto"
        )
        retry = self.store.reserve_report_evidence(
            draft["id"], 1, "photo-a", actor_id="tech-a", actor_name="Ana"
        )
        self.assertEqual(1, first["position"])
        self.assertEqual(2, second["position"])
        self.assertEqual(first["position"], retry["position"])

        self.store.complete_report_evidence(
            draft["id"], "photo-a", "drive-a", storage_ref="storage-a"
        )
        state = self.store.report_live_state(
            draft["id"], user_id="tech-b", user_name="Beto"
        )
        self.assertEqual([1], [item["position"] for item in state["evidence"]])
        self.assertEqual("Ana", state["evidence"][0]["actor_name"])
        self.assertEqual("photo-a", state["evidence"][0]["mutation_id"])
        self.assertEqual("Beto", state["participants"][0]["name"])

    def test_confirmed_evidence_can_be_removed_from_a_draft_and_its_slot_reused(self):
        draft = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "2",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25", "_draft_user_id": "tech-a"},
        })
        self.store.reserve_report_evidence(draft["id"], 1, "wrong-photo", actor_id="tech-a")
        self.store.complete_report_evidence(draft["id"], "wrong-photo", "drive-wrong")

        removed = self.store.delete_report_evidence(
            draft["id"], "wrong-photo", actor_id="tech-a"
        )
        self.assertEqual(1, removed["position"])
        self.assertEqual([], self.store.report_live_state(draft["id"])["evidence"])
        replacement = self.store.reserve_report_evidence(
            draft["id"], 1, "right-photo", actor_id="tech-a"
        )
        self.assertEqual(1, replacement["position"])

    def test_technician_cannot_remove_another_technicians_photo_but_admin_can(self):
        draft = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "2",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25"},
        })
        self.store.reserve_report_evidence(draft["id"], 1, "shared-photo", actor_id="tech-a")
        self.store.complete_report_evidence(draft["id"], "shared-photo", "drive-shared")
        with self.assertRaisesRegex(ValueError, "otro técnico"):
            self.store.delete_report_evidence(draft["id"], "shared-photo", actor_id="tech-b")
        self.assertTrue(self.store.delete_report_evidence(
            draft["id"], "shared-photo", actor_id="owner", allow_any=True
        ))

    def test_two_simultaneous_uploads_never_take_the_same_slot(self):
        draft = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "3",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25"},
        })

        def reserve(mutation):
            return self.store.reserve_report_evidence(
                draft["id"], 1, mutation, actor_id=mutation, actor_name=mutation
            )["position"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            positions = list(executor.map(reserve, ("tech-a-photo", "tech-b-photo")))
        self.assertEqual([1, 2], sorted(positions))

    def test_interrupted_empty_reservation_does_not_block_a_photo_slot_forever(self):
        draft = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "2",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25"},
        })
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="microseconds")
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO operations_evidence"
                "(id,report_id,position,mutation_id,sync_status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("stale", draft["id"], 1, "stale-upload", "uploading", stale, stale),
            )
        reserved = self.store.reserve_report_evidence(draft["id"], 1, "fresh-upload")
        self.assertEqual(1, reserved["position"])
        report = next(item for item in self.store.snapshot()["reports"] if item["id"] == draft["id"])
        self.assertEqual(0, report["photo_count"])

    def test_editing_report_preserves_original_evidence_and_adds_new_photo(self):
        original_draft = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "4",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-25"},
        })
        self.store.reserve_report_evidence(
            original_draft["id"], 1, "original-photo", actor_id="tech-a", actor_name="Ana"
        )
        self.store.complete_report_evidence(
            original_draft["id"], "original-photo", "drive-original", storage_ref="storage-original"
        )
        original = self.store.finalize_report(original_draft["id"])

        edit = self.store.save_report_draft({
            "edit_report_id": original["id"], "base_values": original["payload"], "client_id": "C1",
            "equipment_id": "EQ1", "round": "4",
            "payload": {"inicio": "2026-09-25", "fin": "2026-09-26"},
        })
        inherited = self.store.report_live_state(edit["id"])["evidence"]
        self.assertEqual([1], [item["position"] for item in inherited])
        self.assertEqual("original-photo", inherited[0]["mutation_id"])

        added = self.store.reserve_report_evidence(
            edit["id"], 1, "new-photo", actor_id="tech-b", actor_name="Beto"
        )
        self.assertEqual(2, added["position"])
        self.store.complete_report_evidence(
            edit["id"], "new-photo", "drive-new", storage_ref="storage-new"
        )
        edited = self.store.finalize_report(edit["id"])
        confirmed_from_shared_draft = self.store.get_report_submission(edit["id"])
        self.assertEqual(edited["id"], confirmed_from_shared_draft["id"])
        self.assertEqual([1, 2], [item["position"] for item in edited["evidence"]])
        self.assertEqual(
            ["original-photo", "new-photo"],
            [item["mutation_id"] for item in edited["evidence"]],
        )
        self.assertEqual(["Ana", "Beto"], [item["actor_name"] for item in edited["evidence"]])

    def test_finalize_rejects_invalid_or_reversed_dates(self):
        invalid = self.store.save_report_draft({
            "client_id": "C1", "equipment_id": "EQ1", "round": "1",
            "payload": {"inicio": "27/09/2026", "fin": "2026-09-27"},
        })
        with self.assertRaisesRegex(ValueError, "formato válido"):
            self.store.finalize_report(invalid["id"])

        reversed_dates = self.store.save_report_draft({
            "id": invalid["id"], "base_values": invalid["payload"], "client_id": "C1", "equipment_id": "EQ1", "round": "1",
            "payload": {"inicio": "2026-09-28", "fin": "2026-09-27"},
        })
        with self.assertRaisesRegex(ValueError, "anterior"):
            self.store.finalize_report(reversed_dates["id"])


if __name__ == "__main__":
    unittest.main()
