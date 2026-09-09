"""Respaldo idempotente de facturas y complementos en Google Drive."""
import io
import json
import re
import unicodedata

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from auth_google import get_drive_service_user


FACTURAS_ROOT_FOLDER_ID = "1uIl0PsJMWXapKKwEXJyW0ZpNlwt9ZPPh"
FOLDER_MIME = "application/vnd.google-apps.folder"
PAYMENTS_INDEX_FILE = "HSC-pagos-index.json"


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
        fields="files(id,name,mimeType,webViewLink,appProperties,size)",
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


def _download_file(service, file_id):
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def save_pending_document(cliente, quote_id, filename, content, mimetype, category="otro", order_number=""):
    """Guarda un documento previo a la factura conservando su nombre original."""
    if not content:
        raise ValueError("El archivo está vacío")
    service = get_drive_service_user(timeout=35)
    client_folder_id = _get_or_create_folder(
        service, FACTURAS_ROOT_FOLDER_ID, safe_drive_name(cliente, "SIN_CLIENTE")
    )
    pending_folder_id = _get_or_create_folder(service, client_folder_id, "Pendientes")
    expected = safe_drive_name(filename, "documento")
    quote_key = str(quote_id or "").strip()
    existing = next((item for item in _list_children(service, pending_folder_id)
                     if item.get("mimeType") != FOLDER_MIME
                     and str(item.get("name") or "").strip().casefold() == expected.casefold()
                     and str((item.get("appProperties") or {}).get("hscQuoteId") or "") == quote_key), None)
    properties = {
        "hscQuoteId": quote_key[:120],
        "hscCategory": str(category or "otro")[:120],
        "hscOrderNumber": str(order_number or "")[:120],
    }
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
    if existing:
        item = service.files().update(
            fileId=existing["id"], body={"appProperties": properties}, media_body=media,
            fields="id,name,webViewLink,appProperties,size",
        ).execute()
    else:
        item = service.files().create(
            body={"name": expected, "parents": [pending_folder_id], "appProperties": properties},
            media_body=media, fields="id,name,webViewLink,appProperties,size",
        ).execute()
    return {"ok": True, **item}


def list_pending_documents(cliente, quote_id):
    service = get_drive_service_user(timeout=35)
    client_name = safe_drive_name(cliente, "SIN_CLIENTE")
    client = next((item for item in _list_children(service, FACTURAS_ROOT_FOLDER_ID, True)
                   if str(item.get("name") or "").casefold() == client_name.casefold()), None)
    if not client:
        return []
    pending = next((item for item in _list_children(service, client["id"], True)
                    if str(item.get("name") or "").casefold() == "pendientes"), None)
    if not pending:
        return []
    quote_key = str(quote_id or "").strip()
    return [item for item in _list_children(service, pending["id"])
            if str((item.get("appProperties") or {}).get("hscQuoteId") or "") == quote_key]


def delete_pending_document(cliente, quote_id, file_id):
    service = get_drive_service_user(timeout=35)
    allowed = {item["id"] for item in list_pending_documents(cliente, quote_id)}
    if str(file_id) not in allowed:
        raise FileNotFoundError("No se encontró el documento pendiente")
    service.files().update(fileId=str(file_id), body={"trashed": True}, fields="id,trashed").execute()
    return True


def move_pending_documents(cliente, quote_id, invoice_folder_id):
    """Mueve a la factura únicamente los documentos relacionados con esa cotización."""
    service = get_drive_service_user(timeout=35)
    documents = list_pending_documents(cliente, quote_id)
    moved = []
    for item in documents:
        parents = service.files().get(fileId=item["id"], fields="parents").execute().get("parents", [])
        result = service.files().update(
            fileId=item["id"], addParents=invoice_folder_id,
            removeParents=",".join(parents), fields="id,name,webViewLink,appProperties,size",
        ).execute()
        moved.append(result)
    return moved


def list_invoice_support_documents(cliente, folio_interno):
    service = get_drive_service_user(timeout=35)
    client_name = safe_drive_name(cliente, "SIN_CLIENTE")
    folio_name = safe_drive_name(folio_interno, "SIN_FOLIO")
    client = next((item for item in _list_children(service, FACTURAS_ROOT_FOLDER_ID, True)
                   if str(item.get("name") or "").casefold() == client_name.casefold()), None)
    if not client:
        return []
    folder = next((item for item in _list_children(service, client["id"], True)
                   if str(item.get("name") or "").casefold() == folio_name.casefold()), None)
    if not folder:
        return []
    return [item for item in _list_children(service, folder["id"])
            if item.get("mimeType") != FOLDER_MIME and (item.get("appProperties") or {}).get("hscQuoteId")]


def download_invoice_support_documents(cliente, folio_interno, selected_ids):
    wanted = {str(item) for item in (selected_ids or [])}
    service = get_drive_service_user(timeout=35)
    available = {item["id"]: item for item in list_invoice_support_documents(cliente, folio_interno)}
    result = []
    for file_id in wanted:
        item = available.get(file_id)
        if not item:
            continue
        result.append({
            "data": _download_file(service, file_id),
            "filename": item.get("name") or "documento",
            "content_type": item.get("mimeType") or "application/octet-stream",
        })
    return result


def load_json_file(filename, parent_id=FACTURAS_ROOT_FOLDER_ID, default=None):
    """Lee un archivo JSON pequeño respaldado en Drive."""
    service = get_drive_service_user(timeout=35)
    item = next((row for row in _list_children(service, parent_id)
                 if row.get("name") == filename), None)
    if not item:
        return default
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=item["id"]))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return json.loads(buffer.getvalue().decode("utf-8"))


def backup_json_file(filename, value, parent_id=FACTURAS_ROOT_FOLDER_ID):
    """Crea o actualiza un archivo JSON pequeño en Drive."""
    service = get_drive_service_user(timeout=35)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    result = _upsert_file(service, parent_id, filename, payload, "application/json")
    return {"ok": True, "file_id": result.get("id"), "file_url": result.get("webViewLink")}


def backup_cfdi(cliente, folio_interno, uuid, pdf_bytes, xml_bytes, document_type="Factura", folder_alias=""):
    """Guarda ambos archivos en 05.Facturas/Cliente/Alias - Folio."""
    if not pdf_bytes or not xml_bytes:
        raise ValueError("Facturama no entregó ambos archivos del CFDI")
    service = get_drive_service_user(timeout=35)
    client_name = safe_drive_name(cliente, "SIN_CLIENTE")
    folio_name = safe_drive_name(folio_interno, f"SIN_FOLIO-{uuid}")
    alias_name = safe_drive_name(folder_alias, "") if str(folder_alias or "").strip() else ""
    folder_name = safe_drive_name(f"{alias_name} - {folio_name}", folio_name) if alias_name else folio_name
    client_folder_id = _get_or_create_folder(service, FACTURAS_ROOT_FOLDER_ID, client_name)
    folio_folder_id = _get_or_create_folder(service, client_folder_id, folder_name)

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
        "folder_name": folder_name,
        "folder_url": f"https://drive.google.com/drive/folders/{folio_folder_id}",
        "pdf_id": pdf.get("id"),
        "pdf_url": pdf.get("webViewLink"),
        "xml_id": xml.get("id"),
        "xml_url": xml.get("webViewLink"),
    }


def load_payments_index():
    """Recupera el control de parcialidades desde la misma carpeta de Facturas."""
    value = load_json_file(PAYMENTS_INDEX_FILE, default={})
    return value if isinstance(value, dict) else {}


def backup_payments_index(index):
    """Guarda el control de saldos fuera de Render para sobrevivir despliegues."""
    return backup_json_file(PAYMENTS_INDEX_FILE, index if isinstance(index, dict) else {})
