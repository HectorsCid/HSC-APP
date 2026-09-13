import unittest

import reportes_bp as reports


class ReportDriveReferenceTests(unittest.TestCase):
    def test_drive_image_parser_accepts_direct_file_ids(self):
        file_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        self.assertEqual(reports._extract_drive_id(file_id), file_id)


if __name__ == "__main__":
    unittest.main()
