import unittest
from unittest.mock import patch

import cfdi_drive


class _Result:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value() if callable(self.value) else self.value


class _Files:
    def __init__(self):
        self.rows = []
        self.created = 0
        self.updated = 0

    def list(self, q, **_):
        parent = q.split("'")[1]
        folders_only = cfdi_drive.FOLDER_MIME in q
        rows = [row for row in self.rows if row["parent"] == parent]
        if folders_only:
            rows = [row for row in rows if row["mimeType"] == cfdi_drive.FOLDER_MIME]
        return _Result({"files": [dict(row) for row in rows]})

    def create(self, body, media_body=None, **_):
        def perform():
            self.created += 1
            row = {
                "id": f"id-{self.created}", "name": body["name"],
                "mimeType": body.get("mimeType") or media_body._mimetype,
                "parent": body["parents"][0], "webViewLink": f"https://drive/{self.created}",
            }
            self.rows.append(row)
            return dict(row)
        return _Result(perform)

    def update(self, fileId, **_):
        def perform():
            self.updated += 1
            return next(dict(row) for row in self.rows if row["id"] == fileId)
        return _Result(perform)


class _Drive:
    def __init__(self):
        self.api = _Files()

    def files(self):
        return self.api


class CfdiDriveTests(unittest.TestCase):
    def test_uses_requested_facturas_root(self):
        self.assertEqual(
            cfdi_drive.FACTURAS_ROOT_FOLDER_ID,
            "1uIl0PsJMWXapKKwEXJyW0ZpNlwt9ZPPh",
        )

    def test_folder_names_are_safe_without_losing_accents(self):
        self.assertEqual(cfdi_drive.safe_drive_name('Cliente: División / Norte', 'x'), 'Cliente- División - Norte')
        self.assertEqual(cfdi_drive.safe_drive_name('', 'SIN_FOLIO'), 'SIN_FOLIO')

    def test_creates_client_and_folio_structure_then_updates_same_files(self):
        drive = _Drive()
        with patch.object(cfdi_drive, "get_drive_service_user", return_value=drive):
            first = cfdi_drive.backup_cfdi(
                "Bticino", "1587", "uuid-1", b"%PDF", b"<xml/>", "Factura"
            )
            second = cfdi_drive.backup_cfdi(
                "Bticino", "1587", "uuid-1", b"%PDF2", b"<xml2/>", "Factura"
            )
        client = next(row for row in drive.api.rows if row["name"] == "Bticino")
        folio = next(row for row in drive.api.rows if row["name"] == "1587")
        self.assertEqual(client["parent"], cfdi_drive.FACTURAS_ROOT_FOLDER_ID)
        self.assertEqual(folio["parent"], client["id"])
        self.assertEqual(first["folder_id"], folio["id"])
        self.assertEqual(second["folder_id"], folio["id"])
        self.assertEqual(drive.api.created, 4)  # dos carpetas + PDF + XML
        self.assertEqual(drive.api.updated, 2)


if __name__ == "__main__":
    unittest.main()
