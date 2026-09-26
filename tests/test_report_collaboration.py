import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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
            "changed_fields": {"p1": "65"},
        })
        self.assertEqual("Primero", second["payload"]["notas"])
        self.assertEqual("65", second["payload"]["p1"])
        self.assertGreater(second["revision"], first["revision"])

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
        self.assertEqual("Beto", state["participants"][0]["name"])

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


if __name__ == "__main__":
    unittest.main()
