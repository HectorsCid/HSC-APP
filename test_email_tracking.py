import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import email_tracking


class EmailTrackingTests(unittest.TestCase):
    def test_records_delivery_and_reports_status(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "envios.json"
            with (
                patch.object(email_tracking, "DATA_FILE", target),
                patch.object(email_tracking, "load_json_file", return_value={}),
                patch.object(email_tracking, "backup_json_file") as backup,
            ):
                first = email_tracking.record_email_delivery(
                    "factura", "invoice-1", recipients=["cliente@example.com"],
                    cc=["supervisor@example.com"], client_name="Cliente", folio="1594",
                )
                second = email_tracking.record_email_delivery(
                    "factura", "invoice-1", recipients=["cliente@example.com"], folio="1594",
                )
            self.assertTrue(first["sent"])
            self.assertEqual(second["sent_count"], 2)
            self.assertEqual(second["recipients"], ["cliente@example.com"])
            self.assertEqual(backup.call_count, 2)


if __name__ == "__main__":
    unittest.main()
