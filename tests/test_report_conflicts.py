"""Concurrent/offline report regressions. Temporary databases; no network."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from operaciones_store import OperationsStore, ReportConflictError


class ReportConflictTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = OperationsStore(local_path=Path(temp.name) / "reports.sqlite")
        self.store.import_matrix_snapshot({"clients": [{"id": "C", "name": "Client"}],
            "equipment": [{"id": "E", "client_id": "C", "name": "Equipment"}], "reports": [], "faults": []})
        self.base = {"inicio": "2026-09-28", "fin": "2026-09-28", "notas": "original", "p1": ""}

    def save(self, draft=None, changes=None, **extra):
        base = draft["payload"] if draft else self.base
        return self.store.save_report_draft(dict(id=draft["id"] if draft else "", client_id="C", equipment_id="E", round="1",
            payload={**base, **(changes or {})}, changed_fields=changes or {}, base_values=base,
            base_revision=draft["revision"] if draft else -1, **extra))

    def submission(self, draft, ident="submission-1", photos=None):
        return dict(id=draft["id"], client_id="C", equipment_id="E", round="1", payload=draft["payload"],
                    changed_fields={}, submission_id=ident, photo_mutation_ids=photos or [])

    def test_disjoint_stale_changes_merge_but_same_field_conflicts(self):
        initial = self.save()
        self.save(initial, {"notas": "technician A"})
        merged = self.save(initial, {"p1": "72"})
        self.assertEqual(merged["payload"]["notas"], "technician A")
        with self.assertRaises(ReportConflictError) as caught:
            self.save(initial, {"notas": "stale technician B"})
        self.assertEqual(caught.exception.conflicts[0]["remote"], "technician A")
        self.assertEqual(self.store.get_report_draft("E", "1")["payload"]["notas"], "technician A")

    def test_unversioned_existing_form_cannot_replace_shared_fields(self):
        draft = self.save()
        with self.assertRaises(ReportConflictError):
            self.store.save_report_draft(dict(id=draft["id"], client_id="C", equipment_id="E", round="1", payload={"notas": "stale"}))

    def test_duplicate_draft_rejected_before_any_photo_reservation(self):
        draft = self.save()
        self.store.finalize_report(draft["id"])
        with self.assertRaises(ReportConflictError) as caught:
            self.save(changes={"p1": "70"})
        self.assertEqual(caught.exception.code, "report_completed")
        self.assertEqual(len(self.store.snapshot()["reports"]), 1)

    def test_active_photo_blocks_both_normal_and_edit_finalization(self):
        draft = self.save()
        self.store.reserve_report_evidence(draft["id"], 1, "upload")
        with self.assertRaises(ReportConflictError):
            self.store.finalize_report(draft["id"])
        self.assertEqual(self.store.pending_sync(), [])
        self.store.complete_report_evidence(draft["id"], "upload", "drive")
        original = self.store.finalize_report(draft["id"])
        edit = self.save(edit_report_id=original["id"])
        self.store.reserve_report_evidence(edit["id"], 2, "upload-edit")
        with self.assertRaises(ReportConflictError):
            self.store.finalize_report(edit["id"])
        self.assertIsNotNone(self.store.get_report_detail(edit["id"]))

    def test_receipt_is_per_submission_and_survives_later_edit(self):
        draft = self.save()
        body = self.submission(draft)
        original = self.store.finalize_report(draft["id"], submission=body)
        self.assertEqual(self.store.get_report_receipt(body), original)
        other = self.submission(draft, "technician-b")
        self.assertIsNone(self.store.get_report_receipt(other))
        with self.assertRaises(ReportConflictError):
            self.store.finalize_report(draft["id"], submission=other)
        edit = self.save(changes={"notas": "new edition"}, edit_report_id=original["id"])
        self.store.finalize_report(edit["id"])
        self.assertEqual(self.store.get_report_receipt(body)["payload"]["notas"], "original")

    def test_receipt_and_report_rollback_together(self):
        draft = self.save()
        body = self.submission(draft)
        with patch.object(self.store, "_queue_sync_in_transaction", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):self.store.finalize_report(draft["id"], submission=body)
        self.assertIsNone(self.store.get_report_receipt(body))
        self.assertEqual(self.store.get_report_detail(draft["id"])["state"], "draft")

    def test_finalize_requires_every_requested_photo_and_unchanged_fields(self):
        draft = self.save()
        body = self.submission(draft, photos=["missing"])
        with self.assertRaises(ReportConflictError):self.store.finalize_report(draft["id"], submission=body)
        body["photo_mutation_ids"] = []
        body["changed_fields"] = {"notas": "not saved"}
        with self.assertRaises(ReportConflictError):self.store.finalize_report(draft["id"], submission=body)

    def test_late_photo_cannot_mutate_a_finished_report(self):
        draft = self.save()
        self.store.finalize_report(draft["id"])
        with self.assertRaises(ReportConflictError):self.store.complete_report_evidence(draft["id"], "late", "drive")

    def test_six_simultaneous_photos_keep_six_distinct_slots(self):
        draft = self.save()
        def upload(index):
            ident = f'photo-{index}'
            self.store.reserve_report_evidence(draft['id'], 1, ident, actor_id=f'T{index}')
            return self.store.complete_report_evidence(draft['id'], ident, f'drive-{index}')
        with ThreadPoolExecutor(max_workers=6) as pool:
            evidence = list(pool.map(upload, range(6)))
        self.assertEqual({item['position'] for item in evidence}, set(range(1, 7)))
        with self.assertRaises(ValueError):
            self.store.reserve_report_evidence(draft['id'], 1, 'seventh', actor_id='T7')
        body = self.submission(draft, photos=[f'photo-{i}' for i in range(6)])
        confirmed = self.store.finalize_report(draft['id'], submission=body)
        self.assertEqual(len(confirmed['evidence']), 6)
        self.assertEqual(len(self.store.pending_sync()[0]['payload']['evidence']), 6)

    def test_new_edit_preserves_remote_fields_not_changed_offline(self):
        initial = self.save()
        current = self.save(initial, {"notas": "remote note"})
        original = self.store.finalize_report(current["id"])
        edit = self.save(changes={"p1": "72"}, edit_report_id=original["id"])
        self.assertEqual(edit["payload"]["notas"], "remote note")
        self.assertEqual(edit["payload"]["p1"], "72")

    def test_old_receipt_cannot_overwrite_a_newer_fault_description(self):
        draft = self.save()
        first = self.store.finalize_report(draft['id'], submission=self.submission(draft))
        edit = self.save(changes={'notas': 'later edition'}, edit_report_id=first['id'])
        later = self.store.finalize_report(edit['id'])
        self.store.ensure_report_fault(later['id'], client_id='C', equipment_id='E',
            description='new finding', expected_revision=later['revision'])
        result = self.store.ensure_report_fault(first['id'], client_id='C', equipment_id='E',
            description='outdated finding', expected_revision=first['revision'])
        self.assertIsNone(result)
        self.assertEqual(self.store.snapshot()['faults'][0]['description'], 'new finding')


if __name__ == "__main__":
    unittest.main()
