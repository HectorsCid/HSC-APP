import unittest
from unittest.mock import patch

import reportes_bp as reports


class ReportDriveReferenceTests(unittest.TestCase):
    def test_drive_image_parser_accepts_direct_file_ids(self):
        file_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        self.assertEqual(reports._extract_drive_id(file_id), file_id)

    def test_six_evidence_routes_keep_appsheet_photo_positions(self):
        with patch.object(reports, "REPORTES_ROOT_ID", "root"), \
                patch.object(reports, "REPORTES_APPSHEET_PATH_PREFIX", "/HSC/04. Reportes"), \
                patch.object(reports, "_optimize_photo_bytes", return_value=(b"photo", "image/jpeg")), \
                patch.object(reports, "_ensure_folder", side_effect=["client-folder", "report-folder"] * 6), \
                patch.object(reports, "_upsert_bytes", side_effect=[f"drive-{i}" for i in range(1, 7)]):
            stored = [reports.store_operations_evidence("UVM Queretaro", "UVMQ6_R 4", i, b"raw")
                      for i in range(1, 7)]
        self.assertEqual([item["drive_ref"] for item in stored], [f"drive-{i}" for i in range(1, 7)])
        for position, item in enumerate(stored, start=1):
            self.assertIn(f"UVMQ6_R 4.Foto {position}.", item["storage_ref"])
            self.assertTrue(item["storage_ref"].endswith(".jpg"))


if __name__ == "__main__":
    unittest.main()
