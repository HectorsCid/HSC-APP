"""Respaldo idempotente de facturas y complementos en Google Drive."""
import io
import re
import unicodedata

from googleapiclient.http import MediaIoBaseUpload

from auth_google import get_drive_service_user


FACTURAS_ROOT_FOLDER_ID = "1uIl0PsJMWXapKKwEXJyW0ZpNlwt9ZPPh"
FOLDER_MIME = "application/vnd.google-apps.folder"


def safe_drive_name(value, fallback):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return (text or fallback)[:120]


def _list_children(service, parent_id, folders_only=False):
    query = f"'{parent_id}' in parents and trashed=false"
    if folders_only:
        query += f" and mimeType='{FOLDER_MIME}'"
    return service.files().list(
        q=query,
        spaces="drive",
        fields="files(id,name,mimeType,webViewLink)",
        pageSize=1000,
    ).execute().get("files", [])


def _get_or_create_folder(service, parent_id, name):
    expected = safe_drive_name(name, "SIN_NOMBRE")
    existing = next((item for item in _list_children(service, parent_id, True)
                     if str(item.get("name") or "").strip().casefold() == expected.casefold()), None)
    if existing:
        return existing["id"]
    return service.files().create(
        body={"name": expected, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        fields="id",
    ).execute()["id"]


def _upsert_file(service, parent_id, filename, content, mimetype):
    expected = safe_drive_name(filename, "archivo")
    existing = next((item for item in _list_children(service, parent_id)
                     if item.get("mimeType") != FOLDER_MIME
                     and str(item.get("name") or "").strip().casefold() == expected.casefold()), None)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
    if existing:
        return service.files().update(
            fileId=existing["id"], media_body=media, fields="id,name,webViewLink"
        ).execute()
    return service.files().create(
        body={"name": expected, "parents": [parent_id]},
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()


def backup_cfdi(cliente, folio_interno, uuid, pdf_bytes, xml_bytes, document_type="Factura"):
    """Guarda ambos archivos en 05.Facturas/Cliente/Folio interno."""
    if not pdf_bytes or not xml_bytes:
        raise ValueError("Facturama no entregó ambos archivos del CFDI")
    service = get_drive_service_user(timeout=35)
    client_name = safe_drive_name(cliente, "SIN_CLIENTE")
    folio_name = safe_drive_name(folio_interno, f"SIN_FOLIO-{uuid}")
    client_folder_id = _get_or_create_folder(service, FACTURAS_ROOT_FOLDER_ID, client_name)
    folio_folder_id = _get_or_create_folder(service, client_folder_id, folio_name)

    prefix = safe_drive_name(document_type, "CFDI")
    file_base = f"{prefix}-{folio_name}" if prefix == "Factura" else f"{prefix}-{uuid}"
    pdf = _upsert_file(
        service, folio_folder_id, f"{file_base}.pdf", pdf_bytes, "application/pdf"
    )
    xml = _upsert_file(
        service, folio_folder_id, f"{file_base}.xml", xml_bytes, "application/xml"
    )
    return {
        "ok": True,
        "folder_id": folio_folder_id,
        "folder_url": f"https://drive.google.com/drive/folders/{folio_folder_id}",
        "pdf_id": pdf.get("id"),
        "pdf_url": pdf.get("webViewLink"),
        "xml_id": xml.get("id"),
        "xml_url": xml.get("webViewLink"),
    }
