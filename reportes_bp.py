from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort, jsonify, current_app
import os, io, re, time, json, random, threading, base64, socket, ssl
import requests
import httplib2
from datetime import date, datetime
from pathlib import Path
import uuid

# Credenciales OAuth de usuario (NO service account)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError
from google.auth.exceptions import TransportError
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename
from auth_google import (
    get_drive_service_user,
    get_sheets_service,
    get_drive_service,
    get_sheets_authorized_session,
    reset_thread_google_services,
)
from pdf_runtime import (
    PdfRendererBusy,
    pdf_render_slot,
    render_pdf_bytes,
    render_pdf_file,
    release_pdf_memory,
    rss_megabytes,
)
from cfdi_drive import backup_json_file, load_json_file

# ----------------------------------------------------------------------
# Blueprint
# ----------------------------------------------------------------------
# Mantén este nombre: las rutas se llaman con url_for('reportes.algo')
reportes_bp = Blueprint("reportes", __name__)

# ----------------------------------------------------------------------
# Config / entorno
# ----------------------------------------------------------------------
# Google Sheets (con defaults y posibilidad de override por entorno)
SHEET_ID = os.environ.get("REPORTES_SHEET_ID", "15xLRRfR_Leidnd34Cpr3ERbpJ7AaMelMxMa-9B0d6kQ")
SHEET_TAB = os.environ.get("REPORTES_TAB", "Reportes")
SHEET_ID_REPORTE_RANGE = os.environ.get("REPORTES_IDRANGE", f"{SHEET_TAB}!A2:A")

# Catálogo de clientes (opcional)
CLIENTES_TAB_ENV = os.environ.get("REPORTES_CLIENTES_TAB", "")
CLIENTES_GID_ENV = os.environ.get("REPORTES_CLIENTES_GID", "")
CLIENTES_DEFAULT_TAB = "Clientes"

# Cache de “Últimos 10” (TTL configurable)
LAST10_TTL = int(os.environ.get("REPORTES_LAST10_TTL", "120"))  # segundos
_LAST10_CACHE = {"ts": 0, "items": []}

# Carpeta raíz de Drive para guardar PDFs (04. Reportes)
REPORTES_ROOT_ID = os.environ.get("REPORTES_ROOT_ID", "13x9OPrPJNcT3E17lcyISbpL5uE6az5ty")
REPORTES_APPSHEET_PATH_PREFIX = os.environ.get(
    "REPORTES_APPSHEET_PATH_PREFIX",
    "/HSC/1. Refrigeración y Manto. industrial/01. Clientes/04. Reportes",
).rstrip("/")
DIAG_RECORDS_FILENAME = os.environ.get("REPORTES_MANUALES_FILENAME", "reportes_manuales.json")
_DIAG_RECORDS_LOCK = threading.RLock()

# Generación automática. El monitor consulta los IDs recientes y usa
# HistorialPDF como registro durable para no volver a procesarlos.
AUTO_PDF_ENABLED = os.environ.get("REPORTES_AUTO_PDF", "1").strip().lower() not in ("0", "false", "no", "off")
AUTO_PDF_INTERVAL = max(60, int(os.environ.get("REPORTES_AUTO_PDF_INTERVAL", "28800")))
AUTO_PDF_LOOKBACK = max(1, int(os.environ.get("REPORTES_AUTO_PDF_LOOKBACK", "25")))
AUTO_PDF_STABILITY_SECONDS = max(0, int(os.environ.get("REPORTES_AUTO_PDF_STABILITY_SECONDS", "15")))
AUTO_PDF_QUEUE_DELAY = max(30, int(os.environ.get("REPORTES_AUTO_PDF_QUEUE_DELAY", "30")))
PHOTO_TARGET_SIZE = (1400, 1400)
PHOTO_MAX_SOURCE_PIXELS = 80_000_000
PHOTO_MAX_DECODE_PIXELS = 25_000_000
_AUTO_PDF_LOCK = threading.Lock()
_AUTO_PDF_WAKE_EVENT = threading.Event()
_AUTO_PDF_CANCEL_EVENT = threading.Event()
_AUTO_PDF_CYCLE_LOCK = threading.Lock()
_AUTO_PDF_STATE_LOCK = threading.Lock()
_AUTO_PDF_STARTED = False
_AUTO_PDF_FIRST_SEEN = {}
_AUTO_PDF_STATUS = {
    "running": False,
    "phase": "idle",
    "attempt": 0,
    "max_attempts": 3,
    "operation_started_at": None,
    "finished_at": None,
    "reviewed": 0,
    "drafts": 0,
    "realized": 0,
    "already_generated": 0,
    "detected": 0,
    "queued": 0,
    "completed": 0,
    "errors": 0,
    "current_report": None,
    "cancel_requested": False,
    "last_check": None,
    "last_generated": None,
    "last_error": None,
    "waiting_for_stability": 0,
    "pending_ready": 0,
    "queue_active": False,
}

# ----------------------------------------------------------------------
# Construcción de clientes Google con token.json (como antes)
# ----------------------------------------------------------------------
_reportes_services = threading.local()

def _sheets_service():
    service = getattr(_reportes_services, "sheets", None)
    if service is None:
        is_auto_monitor = threading.current_thread().name == "reportes-auto-pdf"
        service = get_sheets_service(timeout=15) if is_auto_monitor else get_sheets_service()
        # El monitor trabaja en su propio hilo. Limitamos únicamente sus
        # conexiones para que una petición de Google no lo bloquee indefinidamente.
        _reportes_services.sheets = service
    return service

def _reset_auto_sheets_service():
    """Descarta solo la conexión Sheets del hilo automático después de un fallo."""
    if threading.current_thread().name == "reportes-auto-pdf":
        _reportes_services.sheets = None

def _drive_service():
    return get_drive_service()


def _discard_reportes_google_connections():
    """Descarta la conexión dañada del hilo antes de la siguiente operación."""
    reset_thread_google_services()
    _reportes_services.sheets = None
    image_services = globals().get("_drive_img_services")
    if image_services is not None:
        image_services.drive = None

# ----------------------------------------------------------------------
# Helpers de reintento (429/500/503)
# ----------------------------------------------------------------------
def _retry(callable_fn, *, retries=4, base_delay=0.5):
    is_auto_monitor = threading.current_thread().name == "reportes-auto-pdf"
    if is_auto_monitor:
        retries = min(retries, 2)
    last = None
    for attempt in range(retries + 1):
        if is_auto_monitor and _AUTO_PDF_CANCEL_EVENT.is_set():
            raise _AutoProcessingCancelled()
        if is_auto_monitor:
            _AUTO_PDF_STATUS["attempt"] = attempt + 1
            _AUTO_PDF_STATUS["max_attempts"] = retries + 1
        if attempt:
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            if is_auto_monitor:
                if _AUTO_PDF_CANCEL_EVENT.wait(delay):
                    raise _AutoProcessingCancelled()
            else:
                time.sleep(delay)
        try:
            return callable_fn()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            content = getattr(e, "content", b"") or b""
            retryable_403 = status == 403 and any(
                marker in content
                for marker in (b"rateLimitExceeded", b"userRateLimitExceeded", b"backendError")
            )
            if status in (429, 500, 502, 503, 504) or retryable_403:
                last = e
                _discard_reportes_google_connections()
                continue
            raise
        except (
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            ConnectionError,
            OSError,
            httplib2.HttpLib2Error,
            TransportError,
        ) as e:
            last = e
            _discard_reportes_google_connections()
            continue
    if last:
        raise last


def _sheets_call(operation, **retry_kwargs):
    return _retry(lambda: operation(_sheets_service()), **retry_kwargs)


def _drive_img_call(operation, **retry_kwargs):
    if getattr(_drive_img_services, "fast_mode", False):
        retry_kwargs.setdefault("retries", 0)
    return _retry(lambda: operation(_drive_service_for_imgs()), **retry_kwargs)


def _drive_files_call(operation, **retry_kwargs):
    return _retry(lambda: operation(_drive_service_for_files()), **retry_kwargs)


def _drive_sa_call(operation, **retry_kwargs):
    return _retry(lambda: operation(_drive_service()), **retry_kwargs)

class _AutoProcessingCancelled(Exception):
    """Detiene de forma segura únicamente la revisión automática actual."""

# ----------------------------------------------------------------------
# Sheets utils
# ----------------------------------------------------------------------
def _values_get(rng):
    return _sheets_call(lambda svc: svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=rng
    ).execute())

def _values_batch_get(ranges):
    return _sheets_call(lambda svc: svc.spreadsheets().values().batchGet(
        spreadsheetId=SHEET_ID, ranges=ranges
    ).execute())

def _values_append(rng, rows):
    body = {"values": rows}
    return _sheets_call(lambda svc: svc.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=rng,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute())

def _spreadsheet_meta(fields=None):
    def operation(svc):
        kwargs = {"spreadsheetId": SHEET_ID}
        if fields:
            kwargs["fields"] = fields
        return svc.spreadsheets().get(**kwargs).execute()
    return _sheets_call(operation)

# Encabezados y columnas (memo con TTL)
_HEADERS_CACHE = {"ts": 0, "ttl": 300, "val": []}

def _get_headers():
    now = time.time()
    if _HEADERS_CACHE["val"] and (now - _HEADERS_CACHE["ts"] < _HEADERS_CACHE["ttl"]):
        return _HEADERS_CACHE["val"]
    res = _values_get(f"{SHEET_TAB}!A1:ZZ1")
    hdr = res.get("values", [[]])[0]
    _HEADERS_CACHE["val"] = hdr
    _HEADERS_CACHE["ts"] = now
    return hdr

def _col_idx_to_letter(idx_zero_based: int) -> str:
    n = idx_zero_based
    res = ""
    while True:
        n, rem = divmod(n, 26)
        res = chr(65 + rem) + res
        if n == 0:
            break
        n -= 1
    return res

def _col_letter_to_idx(letter: str) -> int:
    s = letter.strip().upper()
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n - 1

def _resolve_id_col():
    """
    Detecta en qué columna está ID_Reporte a partir del encabezado real.
    Devuelve (idx_zero_based, letra, rango 'Reportes!<col>2:<col>').
    """
    headers = _get_headers()
    try:
        idx = headers.index("ID_Reporte")
        letter = _col_idx_to_letter(idx)
        rng = f"{SHEET_TAB}!{letter}2:{letter}"
        return idx, letter, rng
    except ValueError:
        m = re.search(r"!([A-Z]+)2:([A-Z]+)?", SHEET_ID_REPORTE_RANGE or "")
        if m:
            letter = m.group(1)
            idx = _col_letter_to_idx(letter)
            rng = f"{SHEET_TAB}!{letter}2:{letter}"
            return idx, letter, rng
        return 0, "A", f"{SHEET_TAB}!A2:A"

# ----------------------------------------------------------------------
# Lectura de IDs y registros
# ----------------------------------------------------------------------
def get_ultimos_10_items():
    """
    Devuelve [{id_reporte, nombre_equipo, cliente}] para los 10 IDs recientes **únicos**,
    resolviendo cada uno con get_reporte_con_overrides para consistencia 1:1.
    """
    # 1) Leer columna real de ID_Reporte
    _, _, id_range = _resolve_id_col()
    col_vals = _values_get(id_range).get("values", [])
    ids = [r[0].strip() for r in col_vals if r and str(r[0]).strip()]
    if not ids:
        return []

    # 2) Tomar recientes únicos (desde abajo hacia arriba)
    seen = set()
    unique_recent = []
    for val in reversed(ids):
        if val not in seen:
            seen.add(val)
            unique_recent.append(val)
        if len(unique_recent) >= 10:
            break

    # 3) Resolver con la misma lógica
    items = []
    for idv in unique_recent:
        data = get_reporte_con_overrides(idv) or {}

        cliente = (data.get("Cliente") or "").strip()
        nombre = (data.get("NombreEquipo") or "").strip()
        if not nombre:
            marca = (data.get("Marca") or "").strip()
            modelo = (data.get("Modelo") or "").strip()
            nombre = (" ".join(x for x in [marca, modelo] if x) or "")

        items.append({
            "id_reporte": idv,
            "nombre_equipo": nombre,
            "cliente": cliente,
        })
    return items

def get_reporte_by_id(id_reporte: str):
    """Devuelve el dict {columna: valor} de la ÚLTIMA fila con match EXACTO."""
    _, _, id_range = _resolve_id_col()
    col_vals = _values_get(id_range).get("values", [])
    values = [(r[0].strip() if r and str(r[0]).strip() else "") for r in col_vals]
    last_row_idx = None
    for i, val in enumerate(values, start=2):
        if val == id_reporte:
            last_row_idx = i
    if not last_row_idx:
        return None

    row = _values_get(f"{SHEET_TAB}!A{last_row_idx}:ZZ{last_row_idx}").get("values", [[]])[0]
    headers = _get_headers()
    data = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
    data.setdefault("ID_Reporte", id_reporte)
    return data

# ----------------------------------------------------------------------
# Overrides locales (por ID_Reporte)
# ----------------------------------------------------------------------
OVERRIDES_PATH = os.environ.get("REPORTES_OVERRIDES_PATH", "/tmp/reportes_overrides.json")
_overrides = {}

def _load_overrides():
    global _overrides
    try:
        if os.path.exists(OVERRIDES_PATH):
            with open(OVERRIDES_PATH, "r", encoding="utf-8") as f:
                _overrides = json.load(f)
    except Exception:
        _overrides = {}
_load_overrides()

def _save_overrides():
    try:
        with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(_overrides, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ----------------------------------------------------------------------
# Catálogo de clientes (lookup por ID_Cliente)
# ----------------------------------------------------------------------
_clientes_cache = {"ts": 0, "ttl": 300, "by_id": {}, "headers": [], "tab": ""}

def _resolve_clientes_tab_title():
    if CLIENTES_TAB_ENV.strip():
        return CLIENTES_TAB_ENV.strip()
    if CLIENTES_GID_ENV.strip():
        try:
            gid_int = int(CLIENTES_GID_ENV.strip())
            meta = _spreadsheet_meta(fields="sheets(properties(sheetId,title))")
            for sh in meta.get("sheets", []):
                props = sh.get("properties", {})
                if props.get("sheetId") == gid_int:
                    return props.get("title")
        except Exception:
            pass
    return CLIENTES_DEFAULT_TAB

def _load_clientes_cache(force=False):
    now = time.time()
    if (not force) and _clientes_cache["by_id"] and (now - _clientes_cache["ts"] < _clientes_cache["ttl"]):
        return
    tab = _resolve_clientes_tab_title()
    try:
        hdr = _values_get(f"{tab}!A1:ZZ1").get("values", [[]])[0]
        rows = _values_get(f"{tab}!A2:ZZ").get("values", [])
        by_id = {}
        try:
            idx_id = hdr.index("ID_Cliente")
        except ValueError:
            idx_id = None
        for r in rows:
            if not r:
                continue
            id_cliente = (r[idx_id].strip() if (idx_id is not None and idx_id < len(r) and r[idx_id]) else "")
            if not id_cliente:
                continue
            rec = {hdr[i]: (r[i] if i < len(hdr) and i < len(r) else "") for i in range(len(hdr))}
            by_id[id_cliente] = rec
        _clientes_cache.update({"ts": now, "by_id": by_id, "headers": hdr, "tab": tab})
    except Exception:
        _clientes_cache.update({"ts": now, "by_id": {}, "headers": [], "tab": tab})

def get_cliente_by_id(id_cliente: str):
    if not id_cliente:
        return None
    _load_clientes_cache()
    return _clientes_cache["by_id"].get(id_cliente)

def _apply_cliente_y_overrides(base, id_reporte: str):
    # Enriquecer desde catálogo de clientes según ID_Cliente (solo si faltan campos)
    id_cli = (base.get("ID_Cliente") or "").strip()
    if id_cli:
        c = get_cliente_by_id(id_cli)
        if c:
            if not (base.get("Cliente") or "").strip():
                base["Cliente"] = c.get("NombreCliente", "")
            if not (base.get("Direccion") or "").strip():
                base["Direccion"] = c.get("Direccion", "")
            if c.get("CorreoAutorizado") and not base.get("CorreoAutorizado"):
                base["CorreoAutorizado"] = c.get("CorreoAutorizado")
            if c.get("RondaSeleccionadaCliente") and not base.get("Ronda"):
                base["Ronda"] = c.get("RondaSeleccionadaCliente")
            if c.get("URL_Reportes") and not base.get("URL_Reportes"):
                base["URL_Reportes"] = c.get("URL_Reportes")
    # Overrides locales
    ov = _overrides.get(id_reporte, {}) or {}
    if "OBsElectronico" in ov and "OBsElectrónico" not in ov:
        ov["OBsElectrónico"] = ov.pop("OBsElectronico")
    base.update(ov)
    return base

def get_reporte_con_overrides(id_reporte: str):
    base = get_reporte_by_id(id_reporte)
    if not base:
        return None
    return _apply_cliente_y_overrides(base, id_reporte)

# ----------------------------------------------------------------------
# Autocomplete (datalist)
# ----------------------------------------------------------------------
_cache_ids = []
_cache_ids_ts = 0

def _get_all_ids_cached(ttl_sec: int = 180):
    global _cache_ids, _cache_ids_ts
    now = time.time()
    if not _cache_ids or (now - _cache_ids_ts) > ttl_sec:
        _, _, id_range = _resolve_id_col()
        col_vals = _values_get(id_range).get("values", [])
        _cache_ids = [r[0].strip() for r in col_vals if r and str(r[0]).strip()]
        _cache_ids_ts = now
    return _cache_ids

@reportes_bp.route("/reportes/suggest")
def reportes_suggest():
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])
    ids = _get_all_ids_cached()
    ids_rev = list(reversed(ids))
    starts = [i for i in ids_rev if i.lower().startswith(q)]
    contains = [i for i in ids_rev if q in i.lower() and i not in starts]
    return jsonify((starts + contains)[:20])

# ----------------------------------------------------------------------
# Drive helpers (IDs por ruta / shortcuts) para imágenes
# ----------------------------------------------------------------------

# Reuso de cliente y cachés en memoria para estabilidad
_drive_img_services = threading.local()
_IMG_PATH_ID_CACHE = {"ttl": 1800, "data": {}}  # path_str -> (file_id, ts)
_IMG_BYTES_CACHE = {"ttl": 600, "data": {}}     # file_id -> (bytes, mime, name, ts)
_IMG_CACHE_LOCK = threading.RLock()
_IMG_PATH_ID_CACHE_MAX = 1000

def _drive_service_for_imgs():
    fast_mode = bool(getattr(_drive_img_services, "fast_mode", False))
    attribute = "drive_fast" if fast_mode else "drive"
    service = getattr(_drive_img_services, attribute, None)
    if service is None:
        service = get_drive_service_user(timeout=5 if fast_mode else None)
        setattr(_drive_img_services, attribute, service)
    return service


def _download_drive_image(file_id: str, *, max_chunks=None) -> bytes:
    """Reinicia cliente, request y buffer completos después de un fallo de red."""
    def operation(drive):
        request_obj = drive.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request_obj)
        done = False
        chunks = 0
        while not done:
            if (
                threading.current_thread().name == "reportes-auto-pdf"
                and _AUTO_PDF_CANCEL_EVENT.is_set()
            ):
                raise _AutoProcessingCancelled()
            _, done = downloader.next_chunk()
            chunks += 1
            if max_chunks is not None and chunks >= max_chunks and not done:
                raise TimeoutError(
                    f"Drive no terminó la descarga después de {max_chunks} bloques"
                )
        payload = buffer.getvalue()
        if not payload:
            raise RuntimeError("Drive devolvió una imagen vacía")
        return payload

    return _drive_img_call(operation)

def _cache_get_path_id(path_str: str):
    with _IMG_CACHE_LOCK:
        ent = _IMG_PATH_ID_CACHE["data"].get(path_str)
        if not ent:
            return None
        file_id, ts = ent
        if time.time() - ts > _IMG_PATH_ID_CACHE["ttl"]:
            _IMG_PATH_ID_CACHE["data"].pop(path_str, None)
            return None
        return file_id

def _cache_set_path_id(path_str: str, file_id: str):
    with _IMG_CACHE_LOCK:
        now = time.time()
        entries = _IMG_PATH_ID_CACHE["data"]
        expired = [key for key, (_, ts) in entries.items() if now - ts > _IMG_PATH_ID_CACHE["ttl"]]
        for key in expired:
            entries.pop(key, None)
        while len(entries) >= _IMG_PATH_ID_CACHE_MAX:
            oldest_key = min(entries, key=lambda key: entries[key][1])
            entries.pop(oldest_key, None)
        entries[path_str] = (file_id, now)

def _cache_get_bytes(file_id: str):
    with _IMG_CACHE_LOCK:
        ent = _IMG_BYTES_CACHE["data"].get(file_id)
        if not ent:
            return None
        bts, mime, name, ts = ent
        if time.time() - ts > _IMG_BYTES_CACHE["ttl"]:
            _IMG_BYTES_CACHE["data"].pop(file_id, None)
            return None
        return bts, mime, name

def _cache_set_bytes(file_id: str, bts: bytes, mime: str, name: str):
    # Limit simple: no más de 40MB por entrada
    max_entry = 6 * 1024 * 1024
    max_total = 24 * 1024 * 1024
    if len(bts) > max_entry:
        return
    with _IMG_CACHE_LOCK:
        entries = _IMG_BYTES_CACHE["data"]
        now = time.time()
        expired = [key for key, item in entries.items() if now - item[3] > _IMG_BYTES_CACHE["ttl"]]
        for key in expired:
            entries.pop(key, None)
        current = sum(len(item[0]) for item in entries.values())
        while entries and current + len(bts) > max_total:
            oldest_key = min(entries, key=lambda key: entries[key][3])
            old = entries.pop(oldest_key)
            current -= len(old[0])
        entries[file_id] = (bts, mime, name, now)


def _clear_pdf_photo_cache():
    """Las colas usan fotos distintas; conservarlas solo eleva la memoria."""
    with _IMG_CACHE_LOCK:
        _IMG_BYTES_CACHE["data"].clear()

def _optimize_photo_bytes(bts: bytes) -> tuple[bytes, str]:
    """Reduce una foto para el PDF sin modificar el archivo original."""
    if not bts:
        raise ValueError("la imagen está vacía")

    with Image.open(io.BytesIO(bts)) as source:
        width, height = source.size
        if width <= 0 or height <= 0:
            raise ValueError("dimensiones de imagen inválidas")
        if width * height > PHOTO_MAX_SOURCE_PIXELS:
            raise ValueError(
                f"la imagen supera el límite de {PHOTO_MAX_SOURCE_PIXELS // 1_000_000} megapíxeles"
            )

        # JPEG permite reducir desde el decodificador antes de cargar todos los
        # píxeles. Debe ocurrir antes de corregir la orientación EXIF.
        if (source.format or "").upper() in ("JPEG", "JPG", "MPO"):
            source.draft("RGB", PHOTO_TARGET_SIZE)
        if source.width * source.height > PHOTO_MAX_DECODE_PIXELS:
            raise ValueError("la imagen requiere demasiada memoria para procesarse")

        image = ImageOps.exif_transpose(source)
        image.thumbnail(PHOTO_TARGET_SIZE, Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            if "A" in image.getbands():
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue(), "image/jpeg"

_DRIVE_PATTERNS = [
    r'drive\.google\.com\/file\/d\/([a-zA-Z0-9_-]+)',
    r'drive\.google\.com\/open\?id=([a-zA-Z0-9_-]+)',
    r'drive\.google\.com\/uc\?id=([a-zA-Z0-9_-]+)',
    r'[?&]id=([a-zA-Z0-9_-]+)',
]

def _extract_drive_id(url: str):
    # Las evidencias nuevas conservan además del camino de AppSheet el ID
    # directo que devuelve Drive. Aceptarlo evita intentar resolverlo como si
    # fuera una ruta y terminar mostrando el PNG transparente de respaldo.
    direct = (url or "").strip()
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", direct):
        return direct
    for pat in _DRIVE_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def _resolve_shortcut(file_id: str):
    meta = _drive_img_call(lambda drive: drive.files().get(
        fileId=file_id, fields="mimeType,shortcutDetails/targetId"
    ).execute())
    if meta.get("mimeType") == "application/vnd.google-apps.shortcut":
        return meta.get("shortcutDetails", {}).get("targetId") or file_id
    return file_id

def _normalize_relpath(p: str) -> str:
    p = (p or "").replace("\\", "/").strip().strip("/")
    key = "/04. Reportes/".lower()
    idx = p.lower().find(key)
    if idx != -1:
        p = p[idx + len(key):].strip("/")
    return p

def _resolve_path_to_id(path_str: str):
    cached = _cache_get_path_id(path_str)
    if cached:
        return cached
    parts = [s for s in _normalize_relpath(path_str).split("/") if s]
    if not parts:
        return None

    def walk(parent, remaining):
        current = parent
        for index, part in enumerate(remaining):
            is_last = index == len(remaining) - 1
            mime_filter = "" if is_last else " and mimeType='application/vnd.google-apps.folder'"
            safe = part.replace("'", "\\'")
            q = "name='{}' and '{}' in parents and trashed=false{}".format(
                safe, current, mime_filter
            )
            result = _drive_img_call(lambda drive, q=q: drive.files().list(
                q=q, spaces="drive", fields="files(id,name,mimeType)", pageSize=10
            ).execute())
            files = result.get("files", [])
            if not files:
                return None
            current = files[0]["id"]
        return current

    def folders_named(name):
        safe_folder = name.replace("'", "\\'")
        q = (
            "name='{}' and mimeType='application/vnd.google-apps.folder' "
            "and trashed=false"
        ).format(safe_folder)
        result = _drive_img_call(lambda drive: drive.files().list(
            q=q, spaces="drive", fields="files(id,name)", pageSize=10
        ).execute())
        return result.get("files", [])

    # AppSheet guarda rutas relativas como Clientes_Images/archivo.jpg. Ir
    # directo a esa carpeta evita recorrer raíces ajenas para cada miniatura.
    if len(parts) > 1 and parts[0].casefold().endswith("_images"):
        for folder in folders_named(parts[0]):
            resolved = walk(folder["id"], parts[1:])
            if resolved:
                _cache_set_path_id(path_str, resolved)
                return resolved
        return None

    # Los reportes viven bajo 04. Reportes; las fotos de AppSheet suelen vivir
    # junto a la Hoja Matriz en carpetas como Clientes_Images/ y Equipos_Images/.
    roots = []
    if REPORTES_ROOT_ID:
        roots.append(REPORTES_ROOT_ID)
    try:
        sheet_meta = _drive_img_call(lambda drive: drive.files().get(
            fileId=SHEET_ID, fields="parents"
        ).execute())
        roots.extend(sheet_meta.get("parents") or [])
    except Exception:
        pass

    for root in dict.fromkeys(filter(None, roots)):
        resolved = walk(root, parts)
        if resolved:
            _cache_set_path_id(path_str, resolved)
            return resolved

    # Respaldo para apps antiguas cuya carpeta de imágenes no comparte padre
    # directo con la hoja. Se limita a carpetas y después se recorre por ID.
    if len(parts) > 1:
        for folder in folders_named(parts[0]):
            resolved = walk(folder["id"], parts[1:])
            if resolved:
                _cache_set_path_id(path_str, resolved)
                return resolved
    return None

def _photo_data_uri(photo_ref: str) -> str | None:
    """Descarga una foto de Drive y la devuelve incrustada para WeasyPrint."""
    ref = (photo_ref or "").strip()
    if not ref:
        return None
    file_id = _extract_drive_id(ref) or _resolve_path_to_id(ref)
    if not file_id:
        return None
    file_id = _resolve_shortcut(file_id)
    cached = _cache_get_bytes(file_id)
    if cached:
        bts, mime, _ = cached
        return f"data:{mime};base64,{base64.b64encode(bts).decode('ascii')}"
    else:
        meta = _drive_img_call(lambda drive: drive.files().get(
            fileId=file_id, fields="mimeType,name"
        ).execute())
        mime = meta.get("mimeType") or "image/jpeg"
        name = meta.get("name") or "foto"
        bts = _download_drive_image(file_id)
    bts, mime = _optimize_photo_bytes(bts)
    _cache_set_bytes(file_id, bts, mime, "foto.jpg")
    return f"data:{mime};base64,{base64.b64encode(bts).decode('ascii')}"

def _pdf_photos(data: dict) -> list[str]:
    """Prepara fotos; una referencia aún inaccesible aplaza el PDF automático."""
    refs = [str(data.get(f"Foto{i}", "")).strip() for i in range(1, 7)]
    refs = [ref for ref in refs if ref]
    photos = []
    for ref in refs:
        embedded = _photo_data_uri(ref)
        if not embedded:
            raise RuntimeError(f"Foto todavía no disponible en Drive: {ref}")
        photos.append(embedded)
    return photos

_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01"
    b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


def serve_drive_image_ref(photo_ref: str):
    """Sirve una referencia de imagen de Drive usando la caché compartida."""
    url = (photo_ref or "").strip()
    if not url:
        return send_file(io.BytesIO(_TRANSPARENT_PNG), mimetype="image/png")
    try:
        # 1) ¿Es un ID de archivo de Drive en la URL?
        file_id = _extract_drive_id(url)

        # 2) ¿Es una ruta tipo ".../04. Reportes/<Cliente>/<ID>/archivo.jpg"?
        if not file_id:
            maybe = _resolve_path_to_id(url)
            if maybe:
                file_id = maybe

        if not file_id:
            # No pudimos resolver nada -> PNG transparente
            return send_file(io.BytesIO(_TRANSPARENT_PNG), mimetype="image/png")

        # Atajo (shortcuts) -> resolver target real
        file_id = _resolve_shortcut(file_id)

        # ¿Tenemos bytes en caché?
        cached = _cache_get_bytes(file_id)
        if cached:
            bts, mime, name = cached
            resp = send_file(io.BytesIO(bts), mimetype=mime, as_attachment=False, download_name=name or "img")
            resp.headers["Cache-Control"] = "public, max-age=3600"
            return resp

        # Metadatos (para saber mimetype y nombre)
        meta = _drive_img_call(lambda drive: drive.files().get(
            fileId=file_id, fields="mimeType,name"
        ).execute())
        name = meta.get("name", "img")
        mime = meta.get("mimeType", "image/jpeg") or "image/jpeg"

        bts = _download_drive_image(file_id, max_chunks=20)
        _cache_set_bytes(file_id, bts, mime, name)

        # Cabeceras cacheables para que WeasyPrint no golpee varias veces
        resp = send_file(io.BytesIO(bts), mimetype=mime, as_attachment=False, download_name=name)
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    except Exception:
        # Falla silenciosa -> imagen transparente (evitar redirects/timeouts)
        return send_file(io.BytesIO(_TRANSPARENT_PNG), mimetype="image/png")


def serve_drive_image_ref_fast(photo_ref: str):
    """Variante para miniaturas web: cinco segundos y sin reintentos largos."""
    previous_fast_mode = bool(getattr(_drive_img_services, "fast_mode", False))
    _drive_img_services.fast_mode = True
    try:
        return serve_drive_image_ref(photo_ref)
    finally:
        _drive_img_services.fast_mode = previous_fast_mode


@reportes_bp.route("/reportes/imgproxy", endpoint="reportes_imgproxy")
def reportes_imgproxy():
    """
    Devuelve bytes de imagen desde Drive.
    - Si falla cualquier cosa, devuelve un PNG transparente 1x1 (no 302).
    - Soporta IDs directos y ruta relativa bajo REPORTES_ROOT_ID.
    - Usa caché en memoria para reducir llamadas y estabilizar en Windows.
    """
    return serve_drive_image_ref(request.args.get("url") or "")

# ----------------------------------------------------------------------
# Drive helpers (guardar PDF)
# ----------------------------------------------------------------------
def _drive_service_for_files():
    return get_drive_service_user()

def _sanitize_name(name: str) -> str:
    # Evitar caracteres problemáticos
    return re.sub(r'[\\/:*?"<>|]+', '-', (name or "")).strip() or "Sin nombre"

def _ensure_folder(parent_id: str, name: str) -> str:
    safe = _sanitize_name(name)
    safe_q = safe.replace("'", "\\'")
    q = (
        "name='{}' and '{}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    ).format(safe_q, parent_id)

    def operation(drive):
        found = drive.files().list(
            q=q, spaces='drive', fields='files(id,name)', pageSize=1
        ).execute().get('files', [])
        if found:
            return found[0]['id']
        meta = {
            "name": safe,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id]
        }
        return drive.files().create(body=meta, fields="id").execute()["id"]

    return _drive_files_call(operation)


def _upsert_pdf(parent_id: str, filename: str, pdf_bytes: bytes) -> str:
    safe_name = _sanitize_name(filename)
    safe_q = safe_name.replace("'", "\\'")
    q = "name='{}' and '{}' in parents and trashed=false".format(safe_q, parent_id)

    def operation(drive):
        existing = drive.files().list(
            q=q, spaces='drive', fields='files(id,name)', pageSize=1
        ).execute().get('files', [])
        media = MediaIoBaseUpload(
            io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False
        )
        if existing:
            file_id = existing[0]['id']
            drive.files().update(fileId=file_id, media_body=media).execute()
            return file_id
        meta = {"name": safe_name, "parents": [parent_id], "mimeType": "application/pdf"}
        return drive.files().create(
            body=meta, media_body=media, fields="id"
        ).execute()["id"]

    return _drive_files_call(operation)


def _upsert_bytes(parent_id: str, filename: str, content: bytes, mimetype: str) -> str:
    """Crea o reemplaza un archivo auxiliar dentro de una carpeta de reporte."""
    safe_name = _sanitize_name(filename)
    safe_q = safe_name.replace("'", "\\'")
    q = "name='{}' and '{}' in parents and trashed=false".format(safe_q, parent_id)

    def operation(drive):
        existing = drive.files().list(
            q=q, spaces="drive", fields="files(id,name)", pageSize=1
        ).execute().get("files", [])
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
        if existing:
            file_id = existing[0]["id"]
            drive.files().update(fileId=file_id, media_body=media).execute()
            return file_id
        metadata = {"name": safe_name, "parents": [parent_id], "mimeType": mimetype}
        return drive.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()["id"]

    return _drive_files_call(operation)


def store_operations_evidence(client_name: str, report_id: str, position: int, content: bytes) -> dict:
    """Guarda evidencia y devuelve referencias para la app y para AppSheet."""
    if not REPORTES_ROOT_ID:
        raise RuntimeError("REPORTES_ROOT_ID no configurado")
    optimized, mimetype = _optimize_photo_bytes(content)
    safe_client = _sanitize_name(client_name or "Sin Cliente")
    safe_report = _sanitize_name(report_id)
    client_folder = _ensure_folder(REPORTES_ROOT_ID, safe_client)
    report_folder = _ensure_folder(client_folder, safe_report)
    extension = ".png" if mimetype == "image/png" else ".jpg"
    timestamp = datetime.utcnow().strftime("%H%M%S")
    filename = f"{safe_report}.Foto {int(position)}.{timestamp}{extension}"
    drive_id = _upsert_bytes(report_folder, filename, optimized, mimetype)
    storage_ref = f"{REPORTES_APPSHEET_PATH_PREFIX}/{safe_client}/{safe_report}/{filename}"
    return {"drive_ref": drive_id, "storage_ref": storage_ref}


def store_operations_expense_receipt(technician_name: str, expense_id: str, content: bytes) -> dict:
    """Guarda un comprobante de gasto fuera de las carpetas de reportes de clientes."""
    if not REPORTES_ROOT_ID:
        raise RuntimeError("REPORTES_ROOT_ID no configurado")
    optimized, mimetype = _optimize_photo_bytes(content)
    safe_technician = _sanitize_name(technician_name or "Tecnico")
    safe_expense = _sanitize_name(expense_id)
    expenses_folder = _ensure_folder(REPORTES_ROOT_ID, "_Comprobantes de gastos")
    technician_folder = _ensure_folder(expenses_folder, safe_technician)
    extension = ".png" if mimetype == "image/png" else ".jpg"
    filename = f"{safe_expense}.Ticket{extension}"
    drive_id = _upsert_bytes(technician_folder, filename, optimized, mimetype)
    return {"drive_ref": drive_id, "filename": filename}

def _normalize_ronda(val: str) -> str | None:
    v = (val or "").strip()
    if not v:
        return None
    m = re.search(r"(\d+)", v)
    if not m:
        return None
    n = m.group(1)
    return f"Ronda {n}"

# ----------------------------------------------------------------------
# LOGO (manual y vistas) - busca logo2 primero
# ----------------------------------------------------------------------
def _logo_paths():
    """
    Busca primero static/img/logo2.(png|jpg|svg); si no, cae a LOGO.(png|jpg|svg).
    Retorna (logo_web, logo_fs_uri) o (None, None) si no existe.
    """
    from flask import url_for  # aseguramos import para uso local
    static_dir = Path(current_app.root_path) / "static" / "img"
    candidates = [
        "logo2.png", "logo2.jpg", "logo2.svg",
        "LOGO.png", "LOGO.jpg", "LOGO.svg",
    ]
    logo_fs_path = None
    logo_web = None
    for name in candidates:
        p = static_dir / name
        if p.exists():
            logo_fs_path = p.resolve()
            logo_web = url_for('static', filename=f'img/{name}')
            break
    if not logo_fs_path:
        return None, None
    return logo_web, logo_fs_path.as_uri()


def _render_report_pdf_bytes(data: dict, *, wait_timeout: float) -> bytes:
    """Único motor para el botón manual y la generación automática."""
    with pdf_render_slot(wait_timeout=wait_timeout):
        fotos = None
        html = None
        try:
            fotos = _pdf_photos(data)
            logo_web, logo_fs = _logo_paths()
            html = render_template(
                "reporte_formato.html",
                datos=data,
                fotos=fotos,
                embed_for_pdf=True,
                logo_web=logo_web,
                logo_fs=logo_fs,
            )
            return render_pdf_bytes(html, base_url=current_app.root_path, wait_timeout=0)
        finally:
            html = None
            fotos = None
            _clear_pdf_photo_cache()
            release_pdf_memory()

# ----------------------------------------------------------------------
# Rutas (vista principal y clásicos)
# ----------------------------------------------------------------------
def _get_ultimos_10_items_cached(force_refresh: bool = False):
    """Cache simple en memoria para /reportes (TTL configurable)."""
    now = time.time()
    if (not force_refresh) and _LAST10_CACHE["items"] and (now - _LAST10_CACHE["ts"] < LAST10_TTL):
        return _LAST10_CACHE["items"]
    items = get_ultimos_10_items()
    _LAST10_CACHE["items"] = items
    _LAST10_CACHE["ts"] = now
    return items

@reportes_bp.route("/reportes", methods=["GET", "POST"])
def reportes_inicio():
    if request.method == "POST":
        id_reporte = (request.form.get("id_reporte") or "").strip()
        if not id_reporte:
            flash("Ingresa un ID_Reporte.")
            return redirect(url_for("reportes.reportes_inicio"))
        return redirect(url_for("reportes.reportes_prev", id_reporte=id_reporte))

    ultimos_items = []
    recent_loaded = (request.args.get("recent") == "1")
    force = (request.args.get("refresh") == "1")
    if recent_loaded:
        try:
            if SHEET_ID and SHEET_TAB:
                ultimos_items = _get_ultimos_10_items_cached(force_refresh=force)
            else:
                flash("Configura REPORTES_SHEET_ID / REPORTES_TAB para listar folios.")
        except Exception as e:
            if _LAST10_CACHE["items"]:
                ultimos_items = _LAST10_CACHE["items"]
                flash("Mostrando lista en caché por un problema temporal al leer Sheets.")
            else:
                flash(f"No se pudo leer Google Sheets: {e}")

    manual_records = sorted(
        _diag_read_records(refresh=request.args.get("refresh_manual") == "1"),
        key=lambda row: row.get("updated_at", ""), reverse=True,
    )[:30]
    return render_template(
        "reportes_inicio.html",
        ultimos_items=ultimos_items,
        recent_loaded=recent_loaded,
        manual_records=manual_records,
    )

@reportes_bp.route("/reportes/prev/<id_reporte>")
def reportes_prev(id_reporte):
    data = get_reporte_con_overrides(id_reporte)
    if not data:
        flash("ID_Reporte no encontrado en la hoja.")
        return redirect(url_for("reportes.reportes_inicio"))

    # VISTA PREVIA SIN IMÁGENES (para estabilidad)
    fotos = []  # <— importante: vaciamos para no golpear Drive en preview

    logo_web, logo_fs = _logo_paths()
    return render_template(
        "reporte_formato.html",
        datos=data,
        fotos=fotos,
        embed_for_pdf=False,
        logo_web=logo_web,
        logo_fs=logo_fs,
    )

@reportes_bp.route("/reportes/pdf/<id_reporte>")
def reportes_pdf(id_reporte):
    """
    Genera PDF y:
      - guarda en Drive en dos rutas (Carpeta del ID y en /Reportes[/Ronda N]),
      - si todo OK → flash + redirect a vista previa (SIN diálogo de descarga),
      - si falla el guardado → flash de error + entrega el PDF para descargar.
    Si añades ?dl=1 forzará descarga local.
    """
    # Modo forzar descarga (por si algún día lo necesitas)
    force_download = (request.args.get("dl") == "1")

    # 1) Datos y render
    data = get_reporte_con_overrides(id_reporte)
    if not data:
        flash("ID_Reporte no encontrado en la hoja.")
        return redirect(url_for("reportes.reportes_inicio"))

    try:
        pdf_bytes = _render_report_pdf_bytes(data, wait_timeout=5)
    except PdfRendererBusy:
        flash("Hay otro PDF procesándose. Inténtalo nuevamente en unos segundos.", "warning")
        return redirect(url_for("reportes.reportes_prev", id_reporte=id_reporte))

    # 2) Si se pidió forzar descarga, la damos y salimos (opcional)
    if force_download:
        nombre_equipo = (data.get("NombreEquipo") or "Reporte").strip().replace("/", "-")
        filename = f"{nombre_equipo} - {id_reporte}.pdf"
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    # 3) Guardar en Drive (dos rutas)
    cliente = _sanitize_name(data.get("Cliente") or "Sin Cliente")
    ronda_norm = _normalize_ronda(data.get("Ronda") or "")
    nombre_equipo = (data.get("NombreEquipo") or "Reporte").strip().replace("/", "-")
    filename = f"{nombre_equipo} - {id_reporte}.pdf"

    try:
        if not REPORTES_ROOT_ID:
            raise RuntimeError("No está configurado REPORTES_ROOT_ID")

        # Cliente
        client_id = _ensure_folder(REPORTES_ROOT_ID, cliente)

        # Ruta A: /04. Reportes/<Cliente>/<ID_Reporte>/
        id_folder = _ensure_folder(client_id, id_reporte)
        _upsert_pdf(id_folder, filename, pdf_bytes)

        # Ruta B: /04. Reportes/<Cliente>/Reportes[/Ronda N]/
        reportes_folder = _ensure_folder(client_id, "Reportes")
        target_parent = reportes_folder
        if ronda_norm:
            target_parent = _ensure_folder(reportes_folder, ronda_norm)
        file_id_B = _upsert_pdf(target_parent, filename, pdf_bytes)

        # 4) Respaldo local
        base_static = Path(current_app.root_path) / "static" / "reportes_pdfs" / cliente
        base_static.mkdir(parents=True, exist_ok=True)
        (base_static / filename).write_bytes(pdf_bytes)

        # 5) Éxito → mensaje y regreso a la vista previa
        carpeta_b = f"Reportes/{ronda_norm}" if ronda_norm else "Reportes"
        msg = f"✅ PDF guardado en Drive:\n• {cliente}/{id_reporte}/{filename}\n• {cliente}/{carpeta_b}/{filename}"
        flash(msg)

        # >>> REGISTRO CORRECTO EN HistorialPDF!A:F (orden real)
        try:
            archivo_url = _file_web_link(file_id_B)
            carpeta_url = _folder_web_link(target_parent)
            _log_pdf_historial(cliente, id_reporte, archivo_url, carpeta_url, tipo="reporte")
        except Exception:
            pass

        return redirect(url_for("reportes.reportes_prev", id_reporte=id_reporte))

    except Exception as e:
        # 6) Fallo al guardar → avisamos y devolvemos el archivo para no perder el trabajo
        flash(f"❌ No se pudo guardar en Drive: {type(e).__name__}: {e}")
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{nombre_equipo} - {id_reporte}.pdf"
        )

# ====================== NUEVO: snapshot/aux para la UI ======================

# >>> append a HistorialPDF (A:F)  ← incluye columna Tipo en la 6ª POSICIÓN
def _hist_append(rows, *, strict=False):
    try:
        return _values_append("HistorialPDF!A:F", rows)
    except Exception:
        current_app.logger.exception("No se pudo registrar el PDF en HistorialPDF")
        if strict:
            raise
        return None

# >>> log a HistorialPDF (en el orden correcto de encabezados reales)
def _log_pdf_historial(cliente, folio_o_id, archivo_url, carpeta_url, tipo="reporte", *, strict=False):
    try:
        # Orden correcto: [timestamp, cliente, folio, archivo_url, carpeta_url, tipo]
        ts = datetime.now().isoformat(timespec="seconds")
        result = _hist_append(
            [[ts, cliente or "", str(folio_o_id or ""), archivo_url or "", carpeta_url or "", tipo or ""]],
            strict=strict,
        )
        if strict and not result:
            raise RuntimeError("Google Sheets no confirmó el registro en HistorialPDF")
        return result
    except Exception:
        if strict:
            raise
        return None

# >>> helpers para obtener links web
def _file_web_link(file_id: str) -> str | None:
    try:
        meta = _drive_files_call(lambda drive: drive.files().get(
            fileId=file_id, fields="webViewLink"
        ).execute())
        return meta.get("webViewLink")
    except Exception:
        return None

def _folder_web_link(folder_id: str) -> str | None:
    try:
        return f"https://drive.google.com/drive/folders/{folder_id}"
    except Exception:
        return None


def find_client_reports_folder_url(client_name: str) -> str | None:
    """Localiza sin crear la carpeta de PDF de un cliente en 04. Reportes."""
    safe_client = _sanitize_name(client_name)

    def find_folder(parent_id: str, name: str):
        safe_name = _sanitize_name(name).replace("'", "\\'")
        query = (
            "name='{}' and '{}' in parents and "
            "mimeType='application/vnd.google-apps.folder' and trashed=false"
        ).format(safe_name, parent_id)
        return _drive_files_call(lambda drive: drive.files().list(
            q=query, spaces="drive", fields="files(id,name)", pageSize=1,
        ).execute()).get("files", [])

    if not REPORTES_ROOT_ID:
        return None
    clients = find_folder(REPORTES_ROOT_ID, safe_client)
    if not clients:
        return None
    client_id = clients[0]["id"]
    reports = find_folder(client_id, "Reportes")
    return _folder_web_link(reports[0]["id"] if reports else client_id)

def _generate_and_store_report_pdf(id_reporte: str):
    """Invocación interna del generador; no simula un navegador sin sesión.

    La ruta pública sigue protegida por before_request. Sólo el coordinador
    interno llama esta función, nunca se acepta una cabecera como autenticación.
    """
    endpoint = url_for("reportes.reportes_pdf_json", id_reporte=id_reporte)
    with current_app.test_request_context(endpoint, method="POST", headers={"X-HSC-Auto-PDF": "1"}):
        response = current_app.make_response(reportes_pdf_json(id_reporte))

    payload = response.get_json(silent=True) or {}
    if response.status_code != 200 or not payload.get("ok"):
        detail = payload.get("error") or f"HTTP {response.status_code}"
        raise RuntimeError(f"Generación manual interna falló: {detail}")
    return payload

def _auto_sheets_read(ranges):
    """Lee rangos por REST con un límite real y una conexión nueva por intento."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchGet"
    last_error = None
    for attempt in range(1, 4):
        if _AUTO_PDF_CANCEL_EVENT.is_set():
            raise _AutoProcessingCancelled()
        _AUTO_PDF_STATUS.update(attempt=attempt, max_attempts=3)
        session = None
        try:
            session = get_sheets_authorized_session()
            response = session.get(
                url,
                params=[("ranges", value) for value in ranges],
                timeout=(5, 15),
            )
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"Google respondió HTTP {response.status_code}", response=response)
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_error = exc
            _AUTO_PDF_STATUS["phase"] = "retrying"
            if attempt < 3 and _AUTO_PDF_CANCEL_EVENT.wait(attempt * 1.5):
                raise _AutoProcessingCancelled()
        finally:
            if session is not None:
                session.close()
    raise RuntimeError(f"Google Sheets no respondió después de 3 intentos: {last_error}")

def _generated_report_ids():
    """IDs que ya tienen un PDF de tipo reporte registrado en HistorialPDF."""
    if threading.current_thread().name == "reportes-auto-pdf":
        response = _auto_sheets_read(["HistorialPDF!C2:F"])
        value_ranges = response.get("valueRanges", [])
        rows = value_ranges[0].get("values", []) if value_ranges else []
    else:
        rows = _values_get("HistorialPDF!C2:F").get("values", [])
    return {
        str(row[0]).strip()
        for row in rows
        if row and str(row[0]).strip() and len(row) >= 4 and str(row[3]).strip().lower() == "reporte"
    }

def _recent_report_ids(limit: int):
    is_auto = threading.current_thread().name == "reportes-auto-pdf"
    if is_auto:
        header_response = _auto_sheets_read([f"{SHEET_TAB}!A1:ZZ1"])
        header_ranges = header_response.get("valueRanges", [])
        headers = header_ranges[0].get("values", [[]])[0] if header_ranges else []
    else:
        headers = _get_headers()
    realizado_idx = next(
        (i for i, header in enumerate(headers) if str(header).strip().casefold() == "realizado"),
        None,
    )
    if realizado_idx is None:
        raise RuntimeError("No se encontró la columna Realizado en la hoja Reportes")

    id_idx = next(
        (i for i, header in enumerate(headers) if str(header).strip() == "ID_Reporte"),
        0,
    )
    id_letter = _col_idx_to_letter(id_idx)
    id_range = f"{SHEET_TAB}!{id_letter}2:{id_letter}"
    realizado_letter = _col_idx_to_letter(realizado_idx)
    realizado_range = f"{SHEET_TAB}!{realizado_letter}2:{realizado_letter}"
    response = _auto_sheets_read([id_range, realizado_range]) if is_auto else _values_batch_get([id_range, realizado_range])
    value_ranges = response.get("valueRanges", [])
    id_rows = value_ranges[0].get("values", []) if len(value_ranges) > 0 else []
    realizado_rows = value_ranges[1].get("values", []) if len(value_ranges) > 1 else []

    seen = set()
    result = []
    considered = 0
    drafts = 0
    for index in range(len(id_rows) - 1, -1, -1):
        row = id_rows[index]
        report_id = str(row[0]).strip() if row else ""
        if not report_id or report_id in seen:
            continue

        seen.add(report_id)
        considered += 1
        realizado_row = realizado_rows[index] if index < len(realizado_rows) else []
        realizado = str(realizado_row[0]).strip().casefold() if realizado_row else ""
        if realizado in ("true", "verdadero", "sí", "si", "1"):
            result.append(report_id)
        else:
            drafts += 1
        # Los borradores no deben desplazar a los últimos reportes terminados.
        if len(result) >= limit:
            break
    if is_auto:
        _AUTO_PDF_STATUS.update(
            reviewed=considered,
            drafts=drafts,
            realized=len(result),
        )
    return result

def process_new_reports():
    """Procesa una vez los reportes recientes que todavía no tienen PDF registrado."""
    if not _AUTO_PDF_LOCK.acquire(blocking=False):
        return []
    try:
        _AUTO_PDF_STATUS.update(
            running=True,
            phase="reading_history",
            attempt=0,
            last_check=datetime.now().isoformat(timespec="seconds"),
            last_error=None,
        )
        generated = _generated_report_ids()
        _AUTO_PDF_STATUS["phase"] = "reading_reports"
        recent_realized = _recent_report_ids(AUTO_PDF_LOOKBACK)
        pending = [rid for rid in reversed(recent_realized) if rid not in generated]
        completed_so_far = int(_AUTO_PDF_STATUS.get("completed") or 0)
        detected = max(int(_AUTO_PDF_STATUS.get("detected") or 0), completed_so_far + len(pending))
        _AUTO_PDF_STATUS.update(
            already_generated=max(0, len(recent_realized) - len(pending)),
            detected=detected,
            queued=len(pending),
        )
        now = time.monotonic()

        pending_set = set(pending)
        for report_id in list(_AUTO_PDF_FIRST_SEEN):
            if report_id not in pending_set:
                _AUTO_PDF_FIRST_SEEN.pop(report_id, None)

        ready = []
        for report_id in pending:
            first_seen = _AUTO_PDF_FIRST_SEEN.setdefault(report_id, now)
            if now - first_seen >= AUTO_PDF_STABILITY_SECONDS:
                ready.append(report_id)
        _AUTO_PDF_STATUS["waiting_for_stability"] = len(pending) - len(ready)
        _AUTO_PDF_STATUS["pending_ready"] = len(ready)
        _AUTO_PDF_STATUS["phase"] = "waiting_stability" if pending and not ready else "generating" if ready else "finishing"

        completed = []
        # La cola se conserva en memoria y se procesa de uno en uno. Así no se
        # releen HistorialPDF y columnas completas después de cada documento.
        for position, report_id in enumerate(ready):
            if _AUTO_PDF_CANCEL_EVENT.is_set():
                raise _AutoProcessingCancelled()
            try:
                _AUTO_PDF_STATUS["phase"] = "generating"
                _AUTO_PDF_STATUS["current_report"] = report_id
                release_pdf_memory()
                _generate_and_store_report_pdf(report_id)
                completed.append(report_id)
                _AUTO_PDF_STATUS["completed"] = int(_AUTO_PDF_STATUS.get("completed") or 0) + 1
                _AUTO_PDF_FIRST_SEEN.pop(report_id, None)
                _AUTO_PDF_STATUS["last_generated"] = report_id
            except _AutoProcessingCancelled:
                raise
            except Exception as exc:
                # Un reporte incompleto no impide que los siguientes se procesen.
                _AUTO_PDF_STATUS["last_error"] = f"{report_id}: {type(exc).__name__}: {exc}"
                _AUTO_PDF_STATUS["phase"] = "error"
                _AUTO_PDF_STATUS["errors"] = int(_AUTO_PDF_STATUS.get("errors") or 0) + 1
                current_app.logger.exception("No se pudo generar automáticamente el reporte %s", report_id)
                from notification_center import publish
                publish("Reporte automático pendiente", f"No se generó el PDF de {report_id}. Revisa el estado de la cola en Reportes.",
                        category="reportes", key=f"report-error-{report_id}", url="/reportes", level="warning")
            finally:
                _AUTO_PDF_STATUS["queued"] = max(0, int(_AUTO_PDF_STATUS.get("queued") or 0) - 1)
                _clear_pdf_photo_cache()
                release_pdf_memory()
                current_app.logger.info(
                    "Reporte automático %s terminado; memoria=%s MB",
                    report_id,
                    rss_megabytes(),
                )
            if position < len(ready) - 1:
                _AUTO_PDF_STATUS["phase"] = "queue_pause"
                if _AUTO_PDF_CANCEL_EVENT.wait(AUTO_PDF_QUEUE_DELAY):
                    raise _AutoProcessingCancelled()
        return completed
    finally:
        _AUTO_PDF_STATUS["current_report"] = None
        _AUTO_PDF_STATUS["running"] = False
        _AUTO_PDF_LOCK.release()

def _run_auto_report_cycle(app):
    """Ejecuta una revisión completa; sirve al botón y al horario automático."""
    if not _AUTO_PDF_CYCLE_LOCK.acquire(blocking=False):
        with _AUTO_PDF_STATE_LOCK:
            _AUTO_PDF_STATUS.update(
                queue_active=False,
                phase="error",
                last_error="No se pudo adquirir el coordinador de la cola",
            )
        return
    try:
        while True:
            with app.app_context():
                with app.test_request_context("/"):
                    completed = process_new_reports()
            if _AUTO_PDF_CANCEL_EVENT.is_set():
                raise _AutoProcessingCancelled()
            if completed:
                if int(_AUTO_PDF_STATUS.get("queued") or 0) == 0:
                    _AUTO_PDF_STATUS["phase"] = "error" if _AUTO_PDF_STATUS.get("errors") else "complete"
                    break
                _AUTO_PDF_STATUS["phase"] = "queue_pause"
                if _AUTO_PDF_CANCEL_EVENT.wait(AUTO_PDF_QUEUE_DELAY):
                    raise _AutoProcessingCancelled()
                continue
            if _AUTO_PDF_STATUS.get("waiting_for_stability", 0) > 0:
                if _AUTO_PDF_CANCEL_EVENT.wait(max(1, AUTO_PDF_STABILITY_SECONDS)):
                    raise _AutoProcessingCancelled()
                continue
            if not _AUTO_PDF_STATUS.get("last_error"):
                _AUTO_PDF_STATUS["phase"] = "complete"
            break
    except _AutoProcessingCancelled:
        _AUTO_PDF_STATUS.update(phase="cancelled", last_error=None)
    except Exception as exc:
        _AUTO_PDF_STATUS.update(
            last_error=f"{type(exc).__name__}: {exc}", running=False, phase="error"
        )
        _reset_auto_sheets_service()
        app.logger.exception("Falló el monitor automático de reportes")
    finally:
        _discard_reportes_google_connections()
        with _AUTO_PDF_STATE_LOCK:
            _AUTO_PDF_STATUS.update(
                queue_active=False, running=False, current_report=None,
                finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        from notification_center import record_job
        record_job("reportes_automaticos", _AUTO_PDF_STATUS.get("phase"),
                   completed=_AUTO_PDF_STATUS.get("completed", 0),
                   errors=_AUTO_PDF_STATUS.get("errors", 0),
                   last_generated=_AUTO_PDF_STATUS.get("last_generated"),
                   last_error=_AUTO_PDF_STATUS.get("last_error"))
        _AUTO_PDF_CYCLE_LOCK.release()

def _launch_auto_report_cycle(app):
    """Inicia el ciclo directamente y devuelve False si ya existe uno."""
    with _AUTO_PDF_STATE_LOCK:
        if _AUTO_PDF_STATUS.get("queue_active") or _AUTO_PDF_CYCLE_LOCK.locked():
            return False
        _AUTO_PDF_CANCEL_EVENT.clear()
        _AUTO_PDF_STATUS.update(
            queue_active=True, phase="starting", attempt=0,
            operation_started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            finished_at=None, reviewed=0, drafts=0, realized=0,
            already_generated=0, detected=0, queued=0, completed=0, errors=0,
            current_report=None, cancel_requested=False, last_error=None,
            waiting_for_stability=0, pending_ready=0,
        )
        try:
            threading.Thread(
                target=_run_auto_report_cycle,
                args=(app,),
                name="reportes-auto-pdf",
                daemon=True,
            ).start()
        except Exception:
            _AUTO_PDF_STATUS.update(queue_active=False, phase="error")
            raise
        return True

def start_auto_report_monitor(app):
    """Programa una revisión cada 8 horas sin ejecutar una al desplegar."""
    global _AUTO_PDF_STARTED
    if not AUTO_PDF_ENABLED or _AUTO_PDF_STARTED:
        return
    _AUTO_PDF_STARTED = True

    def scheduler():
        while True:
            time.sleep(AUTO_PDF_INTERVAL)
            _launch_auto_report_cycle(app)

    threading.Thread(target=scheduler, name="reportes-auto-scheduler", daemon=True).start()

@reportes_bp.get("/reportes/auto/status")
def reportes_auto_status():
    return jsonify({
        "enabled": AUTO_PDF_ENABLED,
        "interval_seconds": AUTO_PDF_INTERVAL,
        "lookback": AUTO_PDF_LOOKBACK,
        "stability_seconds": AUTO_PDF_STABILITY_SECONDS,
        "batch_size": 1,
        "processing_mode": "sequential",
        "concurrency": 1,
        "queue_delay_seconds": AUTO_PDF_QUEUE_DELAY,
        "memory_mb": rss_megabytes(),
        **_AUTO_PDF_STATUS,
    })

@reportes_bp.post("/reportes/auto/run")
def reportes_auto_run():
    if not AUTO_PDF_ENABLED:
        return jsonify({"ok": False, "error": "Generación automática desactivada"}), 409
    app = current_app._get_current_object()
    if not _launch_auto_report_cycle(app):
        return jsonify({"ok": True, "already_running": True})
    return jsonify({"ok": True, "started": True})

@reportes_bp.post("/reportes/auto/cancel")
def reportes_auto_cancel():
    with _AUTO_PDF_STATE_LOCK:
        if not _AUTO_PDF_STATUS.get("queue_active"):
            return jsonify({"ok": True, "already_stopped": True})
        _AUTO_PDF_STATUS["cancel_requested"] = True
        _AUTO_PDF_STATUS["phase"] = "cancelling"
        _AUTO_PDF_CANCEL_EVENT.set()
    return jsonify({"ok": True, "cancelling": True})

# >>> meta para auxiliar (cliente/nombre_equipo)
@reportes_bp.get("/reportes/meta/<id_reporte>")
def reportes_meta(id_reporte):
    data = get_reporte_con_overrides(id_reporte)
    if not data:
        return jsonify({"ok": False, "error": "not_found"}), 404
    cliente = (data.get("Cliente") or "").strip()
    nombre = (data.get("NombreEquipo") or "").strip()
    if not nombre:
        marca = (data.get("Marca") or "").strip()
        modelo = (data.get("Modelo") or "").strip()
        nombre = (" ".join(x for x in [marca, modelo] if x) or "")
    return jsonify({"ok": True, "cliente": cliente, "nombre_equipo": nombre})

# >>> generar PDF sin redirigir (para fetch() en la UI)
@reportes_bp.post("/reportes/pdf_json/<id_reporte>")
def reportes_pdf_json(id_reporte):
    """
    Genera el PDF del reporte y devuelve JSON.
    No redirige. Pensado para usar via fetch() desde la UI.
    Respuesta: {ok, cliente, folio, archivo_url, carpeta_url, timestamp}
    """
    # 1) Datos y HTML (igual que reportes_pdf)
    data = get_reporte_con_overrides(id_reporte)
    if not data:
        return jsonify({"ok": False, "error": "not_found"}), 404

    is_auto = request.headers.get("X-HSC-Auto-PDF") == "1"
    try:
        pdf_bytes = _render_report_pdf_bytes(data, wait_timeout=45 if is_auto else 5)
    except PdfRendererBusy as exc:
        return jsonify({"ok": False, "error": "pdf_busy", "detail": str(exc)}), 409

    # 2) Guardar en Drive (dos rutas, como en reportes_pdf)
    cliente = _sanitize_name(data.get("Cliente") or "Sin Cliente")
    ronda_norm = _normalize_ronda(data.get("Ronda") or "")
    nombre_equipo = (data.get("NombreEquipo") or "Reporte").strip().replace("/", "-")
    filename = f"{nombre_equipo} - {id_reporte}.pdf"

    try:
        if not REPORTES_ROOT_ID:
            raise RuntimeError("REPORTES_ROOT_ID no configurado")

        client_id = _ensure_folder(REPORTES_ROOT_ID, cliente)

        # A) /<Cliente>/<ID_Reporte>/
        id_folder = _ensure_folder(client_id, id_reporte)
        file_id_A = _upsert_pdf(id_folder, filename, pdf_bytes)

        # B) /<Cliente>/Reportes[/Ronda N]/
        reportes_folder = _ensure_folder(client_id, "Reportes")
        target_parent = reportes_folder
        if ronda_norm:
            target_parent = _ensure_folder(reportes_folder, ronda_norm)
        file_id_B = _upsert_pdf(target_parent, filename, pdf_bytes)

        # Links web (tomamos el de B como “archivo_url” por ser la vista agregada)
        archivo_url = _file_web_link(file_id_B) or _file_web_link(file_id_A)
        carpeta_url = _folder_web_link(target_parent)

        # Respaldo local
        base_static = Path(current_app.root_path) / "static" / "reportes_pdfs" / cliente
        base_static.mkdir(parents=True, exist_ok=True)
        (base_static / filename).write_bytes(pdf_bytes)

        # Historial con orden correcto (col A..F)
        _log_pdf_historial(
            cliente,
            id_reporte,
            archivo_url,
            carpeta_url,
            tipo="reporte",
            strict=is_auto,
        )

        return jsonify({
            "ok": True,
            "cliente": cliente,
            "folio": id_reporte,
            "archivo_url": archivo_url,
            "carpeta_url": carpeta_url,
            "timestamp": datetime.now().isoformat(timespec="seconds")
        })

    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

# ----------------------------------------------------------------------
# Debug (incluye whoami de Drive)
# ----------------------------------------------------------------------
@reportes_bp.route("/reportes/debug")
def reportes_debug():
    try:
        cfg = f"SHEET_ID={SHEET_ID!r}, SHEET_TAB={SHEET_TAB!r}, RANGE={SHEET_ID_REPORTE_RANGE!r}"
        meta = _spreadsheet_meta()
        title = meta.get("properties", {}).get("title")
        hdr = _values_get(f"{SHEET_TAB}!A1:ZZ1").get("values", [])

        idx, letter, id_range = _resolve_id_col()
        sample_vals = _values_get(id_range).get("values", [])
        sample_vals = [r[0] for r in sample_vals if r and r[0]]

        head5 = sample_vals[:5]
        tail5 = sample_vals[-5:]

        # whoami Drive
        try:
            who = _drive_sa_call(
                lambda drive: drive.about().get(fields="user(displayName,emailAddress)").execute()
            )
            who_s = f"{who.get('user',{}).get('displayName','')} <{who.get('user',{}).get('emailAddress','')}>"
        except Exception as ee:
            who_s = f"(no disponible: {type(ee).__name__})"

        return f"""✅ Sheets OK<br>
        Credenciales Drive (token.json): {who_s}<br>
        REPORTES_ROOT_ID: {REPORTES_ROOT_ID}<br>
        Config: {cfg}<br>
        Documento: {title}<br>
        Encabezados A1:ZZ1 (Reportes) → {hdr}<br>
        Columna detectada para ID_Reporte: {letter} (idx {idx}) · Rango: {id_range}<br>
        Muestra (5 primeras): {head5}<br>
        Muestra (5 últimas): {tail5}
        """
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        cfg = f"SHEET_ID={SHEET_ID!r}, SHEET_TAB={SHEET_TAB!r}, RANGE={SHEET_ID_REPORTE_RANGE!r}"
        return (
            f"❌ Error Sheets:<br><pre>{type(e).__name__}: {e}</pre>"
            f"<br><br>Config usada: {cfg}"
            f"<br><br><details><summary>Traceback</summary><pre>{tb}</pre></details>",
            500,
        )

# ======================================================================
# ====== BLOQUE: Formato manual Servicio/Diagnóstico (diag) ============
# ======================================================================

_DIAG_DATA_FIELDS = (
    "cliente", "departamento", "atencion", "fecha", "fecha_fin", "direccion",
    "trabajo_solicitado", "tipo_mantenimiento", "descripcion_falla", "trabajo",
    "observaciones", "notas", "equipo", "ubicacion", "marca", "modelo",
    "no_serie", "no_inventario", "no_contrato", "responsable", "vigencia",
    "tecnico_responsable", "presion_cto1", "presion_cto2", "temperatura_cto1",
    "temperatura_cto2", "amperaje1", "amperaje2", "obs_amperaje",
    "tol_presion", "tol_temperatura", "tol_amperaje", "obs_electrico",
    "obs_electronico", "obs_mecanico",
)


def _diag_type(value):
    return "refrigeracion" if str(value or "").strip().lower() == "refrigeracion" else "trabajo"


def _diag_records_path():
    path = Path(current_app.root_path) / "data" / DIAG_RECORDS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _diag_read_records(*, refresh=False):
    """Lee el índice durable de reportes manuales; Drive repone Render al reiniciar."""
    path = _diag_records_path()
    local = []
    try:
        local = json.loads(path.read_text("utf-8")) if path.exists() else []
        if not isinstance(local, list):
            local = []
    except Exception:
        local = []
    if (refresh or not path.exists()) and REPORTES_ROOT_ID:
        try:
            remote = load_json_file(DIAG_RECORDS_FILENAME, parent_id=REPORTES_ROOT_ID, default=[])
            if isinstance(remote, list):
                local = remote
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(path)
        except Exception as exc:
            current_app.logger.warning("No se pudo leer el índice de reportes manuales: %s", exc)
    return local


def _diag_write_records(records, *, backup=True):
    path = _diag_records_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    if backup and REPORTES_ROOT_ID:
        try:
            backup_json_file(DIAG_RECORDS_FILENAME, records, parent_id=REPORTES_ROOT_ID)
            return True
        except Exception as exc:
            current_app.logger.warning("Borrador local guardado, pero Drive no respondió: %s", exc)
            return False
    return True


def _diag_next_folio(records, report_type, year=None):
    year = int(year or date.today().year)
    prefix = "RF" if _diag_type(report_type) == "refrigeracion" else "RT"
    pattern = re.compile(rf"^{prefix}-{year}-(\d+)$")
    numbers = []
    for row in records:
        match = pattern.fullmatch(str(row.get("folio") or "").strip())
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}-{year}-{(max(numbers, default=0) + 1):04d}"


def _diag_clean_data(source):
    get = source.get if hasattr(source, "get") else lambda key, default="": default
    data = {key: str(get(key, "") or "").strip() for key in _DIAG_DATA_FIELDS}
    data["fecha"] = data["fecha"] or date.today().isoformat()
    return data


def _diag_clean_parts(raw_parts):
    parts = []
    for item in raw_parts or []:
        if not isinstance(item, dict):
            continue
        description = str(item.get("descripcion") or item.get("description") or "").strip()
        quantity = str(item.get("cantidad") or item.get("quantity") or "").strip()
        if description:
            parts.append({"descripcion": description, "cantidad": quantity})
        if len(parts) >= 10:
            break
    return parts


def _diag_store_payload(payload, *, status=None, backup=True):
    """Inserta o actualiza un reporte manual sin mezclarlo con los de Sheets."""
    token = str(payload.get("token") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        token = uuid.uuid4().hex
    report_type = _diag_type(payload.get("report_type"))
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with _DIAG_RECORDS_LOCK:
        records = _diag_read_records()
        index = next((i for i, row in enumerate(records) if row.get("token") == token), None)
        previous = records[index] if index is not None else {}
        report_date = str((payload.get("datos") or {}).get("fecha") or "")
        year = int(report_date[:4]) if re.fullmatch(r"\d{4}.*", report_date) else date.today().year
        folio = previous.get("folio") or payload.get("folio") or _diag_next_folio(records, report_type, year)
        record = {
            **previous,
            **payload,
            "token": token,
            "folio": folio,
            "report_type": report_type,
            "status": status or previous.get("status") or "draft",
            "created_at": previous.get("created_at") or now,
            "updated_at": now,
        }
        record.setdefault("datos", {})
        record.setdefault("partes", [])
        record.setdefault("fotos", previous.get("fotos", []))
        if record["status"] == "completed":
            record["completed_at"] = previous.get("completed_at") or now
        if index is None:
            records.append(record)
        else:
            records[index] = record
        drive_ok = _diag_write_records(records, backup=backup)
    record["drive_backup"] = drive_ok
    return record


def _diag_record(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        return None
    with _DIAG_RECORDS_LOCK:
        return next((row for row in _diag_read_records() if row.get("token") == token), None)


def _diag_refrigeration_data(payload):
    data = payload.get("datos", {})
    parts = payload.get("partes", [])
    return {
        "ID_Reporte": payload.get("folio", ""), "Cliente": data.get("cliente", ""),
        "Direccion": data.get("direccion", ""), "Departamento": data.get("departamento", ""),
        "Responsable": data.get("responsable") or data.get("atencion", ""),
        "NombreEquipo": data.get("equipo", ""), "Ubicacion": data.get("ubicacion", ""),
        "Marca": data.get("marca", ""), "Modelo": data.get("modelo", ""),
        "NoSerie": data.get("no_serie", ""), "NoInventario": data.get("no_inventario", ""),
        "NoContrato": data.get("no_contrato", ""), "FechaInicio": data.get("fecha", ""),
        "FechaFin": data.get("fecha_fin", ""), "Vigencia": data.get("vigencia", ""),
        "MtoCorrectivo": data.get("trabajo", ""),
        "PartesUtilizadas": "\n".join(
            f"{part.get('cantidad')} — {part.get('descripcion')}" if part.get("cantidad") else part.get("descripcion", "")
            for part in parts if part.get("descripcion")
        ),
        "TolPresion": data.get("tol_presion") or "± 5 psi",
        "TolTemperatura": data.get("tol_temperatura") or "± 1 °C",
        "TolAmperaje": data.get("tol_amperaje") or "± 1 A",
        "PresionCto1": data.get("presion_cto1", ""), "PresionCto2": data.get("presion_cto2", ""),
        "TempCto1": data.get("temperatura_cto1", ""), "TempCto2": data.get("temperatura_cto2", ""),
        "Amperaje1": data.get("amperaje1", ""), "Amperaje2": data.get("amperaje2", ""),
        "ObsAmperaje": data.get("obs_amperaje", ""), "ObsElectrico": data.get("obs_electrico", ""),
        "OBsElectrónico": data.get("obs_electronico", ""), "ObsMecanico": data.get("obs_mecanico", ""),
        "Notas": data.get("notas") or data.get("observaciones", ""),
        "TecnicoResponsable": data.get("tecnico_responsable", ""),
    }


def _diag_render(payload, *, show_toolbar, embed_for_pdf):
    logo_web, logo_fs = _logo_paths()
    if _diag_type(payload.get("report_type")) == "refrigeracion":
        photos = [photo.get("fs_uri") if embed_for_pdf else photo.get("web_path")
                  for photo in payload.get("fotos", []) if isinstance(photo, dict)]
        return render_template(
            "reporte_formato.html", datos=_diag_refrigeration_data(payload), fotos=photos,
            embed_for_pdf=embed_for_pdf, logo_web=logo_web, logo_fs=logo_fs,
            show_toolbar=show_toolbar, manual_token=payload.get("token"),
        )
    return render_template(
        "reporte_diag_pdf.html", show_toolbar=show_toolbar, embed_for_pdf=embed_for_pdf,
        token=payload.get("token"), folio=payload.get("folio", ""),
        datos=payload.get("datos", {}), partes=payload.get("partes", []),
        total_partes_fmt=_mxn(payload.get("total_partes", 0)), fotos=payload.get("fotos", []),
        logo_web=logo_web, logo_fs=logo_fs,
    )

def _diag_paths():
    """Rutas base para subidas y PDFs locales del manual."""
    base_static = Path(current_app.root_path) / "static"
    up = base_static / "diag_uploads"
    pdfs = base_static / "diag_pdfs"
    tmp = Path(os.getenv("DIAG_TMP_DIR", os.path.join(os.getenv("TMP", os.getenv("TEMP", "/tmp")), "reportes_diag")))
    up.mkdir(parents=True, exist_ok=True)
    pdfs.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return up, pdfs, tmp

def _mxn(n):
    try:
        return f"${n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ",")
    except Exception:
        return "$0.00"

def _diag_pick(rec, *keys):
    """Devuelve el primer campo no vacio, tolerando variantes de encabezados."""
    for key in keys:
        value = rec.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""

def _diag_clientes_catalogo():
    """Usa el mismo catalogo unificado de Clientes para ambos reportes manuales."""
    clientes = {}

    def agregar_catalogo(raw):
        if not isinstance(raw, dict):
            return
        for nombre, info in raw.items():
            info = info if isinstance(info, dict) else {}
            nombre = str(nombre or "").strip()
            if not nombre:
                continue
            atenciones = info.get("atencion") or info.get("atenciones") or []
            if isinstance(atenciones, str):
                atenciones = [atenciones]
            atenciones = [str(x).strip() for x in atenciones if str(x).strip()]
            for contacto in info.get("contactos") or []:
                if not isinstance(contacto, dict):
                    continue
                contacto_nombre = _diag_pick(contacto, "nombre", "atencion", "atención", "contacto")
                if contacto_nombre and contacto_nombre not in atenciones:
                    atenciones.append(contacto_nombre)
            clientes[nombre.casefold()] = {
                "nombre": nombre,
                "direccion": _diag_pick(info, "direccion", "dirección", "domicilio"),
                "atenciones": atenciones,
            }

    # La memoria sincronizada con clientes.json de Drive es la fuente vigente
    # para cotizaciones, facturacion y ahora tambien para ambos reportes.
    provider = current_app.config.get("HSC_CLIENTES_PROVIDER")
    if callable(provider):
        try:
            agregar_catalogo(provider())
        except Exception:
            pass

    # Respaldo local de arranque si la memoria aun no fue sincronizada.
    if not clientes:
        for path in (
            Path(current_app.root_path) / "clientes.json",
            Path(current_app.root_path) / "data" / "clientes.json",
        ):
            try:
                agregar_catalogo(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if clientes:
                break

    # La hoja antigua de AppSheet queda solo como ultimo respaldo; no se mezcla
    # con la lista principal para evitar dos catalogos diferentes.
    if not clientes:
        if not _clientes_cache.get("by_id"):
            _load_clientes_cache()
        for rec in _clientes_cache.get("by_id", {}).values():
            if not isinstance(rec, dict):
                continue
            nombre = _diag_pick(rec, "NombreCliente", "Nombre", "Cliente", "RazonSocial", "Razón Social")
            if not nombre:
                continue
            atencion = _diag_pick(
                rec, "Atencion", "Atención", "Solicitante", "Responsable",
                "NombreContacto", "Contacto"
            )
            clientes[nombre.casefold()] = {
                "nombre": nombre,
                "direccion": _diag_pick(rec, "Direccion", "Dirección", "Domicilio"),
                "atenciones": [atencion] if atencion else [],
            }

    return sorted(clientes.values(), key=lambda c: c["nombre"].casefold())

def _diag_payload_temporal(token):
    """Carga un reporte durable o, por compatibilidad, una vista previa temporal."""
    token = (token or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    stored = _diag_record(token)
    if stored:
        return stored
    _, _, tmp_dir = _diag_paths()
    meta_path = tmp_dir / f"{token}.json"
    if not meta_path.exists():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None

@reportes_bp.get("/reportes/diag/nuevo")
def diag_nuevo():
    # Formulario del “Reporte de trabajo”
    hoy = date.today().isoformat()
    token = (request.args.get("token") or "").strip()
    payload = _diag_payload_temporal(token) if token else None
    report_type = _diag_type((payload or {}).get("report_type") or request.args.get("tipo"))
    if token and not payload:
        flash("La vista previa anterior ya vencio. Inicia nuevamente el reporte.", "warning")
    return render_template(
        "reporte_diag_form.html",
        fecha_hoy=hoy,
        edit_token=token if payload else "",
        report_type=report_type,
        report_folio=(payload or {}).get("folio", "Nuevo borrador"),
        form_data=(payload or {}).get("datos", {}),
        form_partes=(payload or {}).get("partes", []),
        form_fotos=(payload or {}).get("fotos", []),
        clientes_catalogo=_diag_clientes_catalogo(),
    )


@reportes_bp.post("/reportes/diag/borrador")
def diag_guardar_borrador():
    body = request.get_json(silent=True) or {}
    data = _diag_clean_data(body.get("datos") or {})
    parts = _diag_clean_parts(body.get("partes") or [])
    previous = _diag_payload_temporal(body.get("token"))
    payload = {
        "token": body.get("token"), "report_type": _diag_type(body.get("report_type")),
        "datos": data, "partes": parts, "total_partes": 0,
        "fotos": (previous or {}).get("fotos", []),
    }
    record = _diag_store_payload(payload, status="draft")
    return jsonify({
        "ok": True, "token": record["token"], "folio": record["folio"],
        "updated_at": record["updated_at"], "drive_backup": record.get("drive_backup", False),
    })


@reportes_bp.get("/reportes/diag/prev/<token>")
def diag_prev_guardado(token):
    payload = _diag_payload_temporal(token)
    if not payload:
        flash("No encontré ese reporte o borrador.", "warning")
        return redirect(url_for("reportes.reportes_inicio"))
    return _diag_render(payload, show_toolbar=True, embed_for_pdf=False)


@reportes_bp.post("/reportes/diag/<token>/eliminar")
def diag_eliminar(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        abort(404)
    with _DIAG_RECORDS_LOCK:
        records = _diag_read_records()
        remaining = [row for row in records if row.get("token") != token]
        if len(remaining) == len(records):
            abort(404)
        _diag_write_records(remaining)
    flash("Borrador eliminado.", "success")
    return redirect(url_for("reportes.reportes_inicio"))

@reportes_bp.post("/reportes/diag/prev")
def diag_prev():
    up_dir, pdf_dir, tmp_dir = _diag_paths()
    edit_token = (request.form.get("edit_token") or "").strip()
    previous_payload = _diag_payload_temporal(edit_token)
    token = edit_token if previous_payload else uuid.uuid4().hex
    report_type = _diag_type(request.form.get("report_type"))

    # ----- Datos del formulario (sin id_reporte) -----
    datos = _diag_clean_data(request.form)

    # ----- Material -----
    partes = []
    descs = request.form.getlist("part_desc[]")
    cants = request.form.getlist("part_cant[]")
    precios = request.form.getlist("part_precio[]")  # vienen "0" desde el form
    total_partes = 0.0
    for d, c, p in zip(descs, cants, precios):
        d = (d or "").strip()
        try: c = float((c or "0").replace(",", "."))
        except Exception: c = 0.0
        try: p = float((p or "0").replace(",", "."))
        except Exception: p = 0.0
        importe = c * p
        total_partes += importe
        if d:
            partes.append({
                "descripcion": d,
                "cantidad": c if c else "",
                "precio": p if p else "",
                "precio_fmt": _mxn(p) if p else "",
                "importe_fmt": _mxn(importe) if importe else "",
            })

    # ----- Fotos (hasta 6) -----
    fotos_meta = list((previous_payload or {}).get("fotos", []))
    if request.form.get("reemplazar_fotos") == "1":
        fotos_meta = []
    files = request.files.getlist("fotos")
    sess_dir = (up_dir / token)
    sess_dir.mkdir(parents=True, exist_ok=True)
    available = max(0, 6 - len(fotos_meta))
    for i, f in enumerate(files[:available], start=len(fotos_meta)):
        if not f or not getattr(f, "filename", ""): continue
        fname = secure_filename(f.filename)
        stem = Path(fname).stem[:40] or f"foto{i+1}"
        safe_name = f"{i+1:02d}_{stem}.jpg"
        dst = sess_dir / safe_name
        try:
            raw = f.stream.read(15 * 1024 * 1024 + 1)
            if len(raw) > 15 * 1024 * 1024:
                raise ValueError("la foto supera 15 MB")
            optimized, _ = _optimize_photo_bytes(raw)
            dst.write_bytes(optimized)
        except Exception as exc:
            flash(f"No se pudo usar la foto {i + 1}: {exc}", "warning")
            continue
        fotos_meta.append({
            "filename": safe_name,
            "web_path": url_for('static', filename=f"diag_uploads/{token}/{safe_name}"),
            "fs_uri": dst.resolve().as_uri()
        })
    if len([f for f in files if f and getattr(f, "filename", "")]) > available:
        flash("El reporte admite un maximo de 6 fotos; se conservaron las primeras seis.", "warning")

    # ----- Persistir JSON temporal -----
    payload = {
        "token": token,
        "report_type": report_type,
        "datos": datos,
        "partes": partes,
        "total_partes": total_partes,
        "fotos": fotos_meta,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    payload = _diag_store_payload(payload, status="draft")
    (tmp_dir / f"{token}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # Render de vista previa
    return _diag_render(payload, show_toolbar=True, embed_for_pdf=False)

@reportes_bp.route("/reportes/editar/<id_reporte>", methods=["GET", "POST"], endpoint="reportes_editar")
def reportes_editar(id_reporte):
    data = get_reporte_con_overrides(id_reporte)
    if not data:
        flash("ID_Reporte no encontrado en la hoja.")
        return redirect(url_for("reportes.reportes_inicio"))

    if request.method == "POST":
        fields = [
            "Cliente", "Direccion", "Departamento", "Ubicacion", "Responsable",
            "Modelo", "NoSerie", "NoInventario", "NoContrato", "Vigencia",
            "MtoCorrectivo", "PartesUtilizadas",
            "ObsElectrico", "OBsElectronico", "ObsMecanico",
            "Notas", "Recomendaciones", "Comentarios",
            "TolPresion", "TolTemperatura", "TolAmperaje",
            "PresionCto1", "PresionCto2", "TempCto1", "TempCto2",
            "Amperaje1", "Amperaje2", "ObsAmperaje",
        ]
        newov = _overrides.get(id_reporte, {}).copy()
        for f in fields:
            val = (request.form.get(f) or "").strip()
            if val:
                newov[f] = val
        _overrides[id_reporte] = newov
        _save_overrides()
        flash("Cambios guardados.")
        return redirect(url_for("reportes.reportes_prev", id_reporte=id_reporte))

    data.setdefault("TolPresion", "± 5 psi")
    data.setdefault("TolTemperatura", "± 1 °C")
    data.setdefault("TolAmperaje", "± 1 A")
    return render_template("reporte_editar.html", datos=data, id_reporte=id_reporte)


@reportes_bp.get("/reportes/diag/pdf/<token>")
def diag_pdf(token):
    up_dir, pdf_dir, tmp_dir = _diag_paths()
    payload = _diag_payload_temporal(token)
    if not payload:
        flash("No encontré los datos temporales del reporte. Vuelve a generar la vista previa.", "error")
        return redirect(url_for("reportes.diag_nuevo"))
    datos = payload.get("datos", {})
    html = _diag_render(payload, show_toolbar=False, embed_for_pdf=True)
    folio = payload.get("folio") or token[:8].upper()
    label = "Reporte Refrigeracion" if _diag_type(payload.get("report_type")) == "refrigeracion" else "Reporte de Trabajo"
    pdf_name = f"{label} - {folio}.pdf"

    force_download = (request.args.get("dl") == "1")
    try:
        pdf_bytes = render_pdf_bytes(html, base_url=current_app.root_path, wait_timeout=5)
    except PdfRendererBusy:
        flash("Hay otro PDF procesándose. Inténtalo nuevamente en unos segundos.", "warning")
        return redirect(url_for("reportes.diag_prev_guardado", token=token))
    if force_download:
        return send_file(io.BytesIO(pdf_bytes),
                         mimetype="application/pdf",
                         as_attachment=True,
                         download_name=pdf_name)

    try:
        if not REPORTES_ROOT_ID:
            raise RuntimeError("No está configurada la carpeta de reportes en Drive")
        client_name = _sanitize_name(datos.get("cliente") or "Sin Cliente")
        client_folder = _ensure_folder(REPORTES_ROOT_ID, client_name)
        report_folder = _ensure_folder(client_folder, folio)
        pdf_id = _upsert_pdf(report_folder, pdf_name, pdf_bytes)

        for index, photo in enumerate(payload.get("fotos", []), start=1):
            if not isinstance(photo, dict):
                continue
            local_path = up_dir / token / str(photo.get("filename") or "")
            if local_path.is_file():
                photo["drive_id"] = _upsert_bytes(
                    report_folder, f"Evidencia {index:02d}.jpg", local_path.read_bytes(), "image/jpeg"
                )

        safe_payload = {key: value for key, value in payload.items() if key != "drive_backup"}
        _upsert_bytes(
            report_folder, f"Datos - {folio}.json",
            json.dumps(safe_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
        )
        payload["pdf_url"] = _file_web_link(pdf_id)
        payload["folder_url"] = _folder_web_link(report_folder)
        payload = _diag_store_payload(payload, status="completed")
        try:
            _log_pdf_historial(
                client_name, folio, payload.get("pdf_url"), payload.get("folder_url"),
                tipo="reporte_refrigeracion" if payload.get("report_type") == "refrigeracion" else "reporte_trabajo",
            )
        except Exception:
            pass
        flash(f"✅ {folio} finalizado y guardado en Drive.", "success")
        return redirect(url_for("reportes.reportes_inicio"))
    except Exception as exc:
        flash(f"No se pudo guardar en Drive: {type(exc).__name__}: {exc}. Se descargó una copia para no perderla.", "error")
        return send_file(
            io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
            download_name=pdf_name,
        )



