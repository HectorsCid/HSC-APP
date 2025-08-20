from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort, jsonify, current_app
import os, io, re, time, json, random
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

# Credenciales OAuth de usuario (NO service account)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError
from weasyprint import HTML
from werkzeug.utils import secure_filename
from auth_google import get_drive_service_user, get_sheets_service, get_drive_service



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

# ----------------------------------------------------------------------
# Construcción de clientes Google con token.json (como antes)
# ----------------------------------------------------------------------
_sheets_svc = None
_drive_svc  = None

def _sheets_service():
    global _sheets_svc
    if _sheets_svc is None:
        _sheets_svc = get_sheets_service()
    return _sheets_svc

def _drive_service():
    global _drive_svc
    if _drive_svc is None:
        _drive_svc = get_drive_service()
    return _drive_svc

# ----------------------------------------------------------------------
# Helpers de reintento (429/500/503)
# ----------------------------------------------------------------------
def _retry(callable_fn, *, retries=4, base_delay=0.5):
    last = None
    for attempt in range(retries + 1):
        if attempt:
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            time.sleep(delay)
        try:
            return callable_fn()
        except HttpError as e:
            if getattr(e, "resp", None) and e.resp.status in (429, 500, 503):
                last = e
                continue
            raise
    if last:
        raise last

# ----------------------------------------------------------------------
# Sheets utils
# ----------------------------------------------------------------------
def _values_get(svc, rng):
    return _retry(lambda: svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=rng
    ).execute())

def _values_batch_get(svc, ranges):
    return _retry(lambda: svc.spreadsheets().values().batchGet(
        spreadsheetId=SHEET_ID, ranges=ranges
    ).execute())

def _spreadsheet_meta(svc, fields=None):
    req = svc.spreadsheets().get(spreadsheetId=SHEET_ID)
    if fields:
        req = svc.spreadsheets().get(spreadsheetId=SHEET_ID, fields=fields)
    return _retry(lambda: req.execute())

# Encabezados y columnas (memo con TTL)
_HEADERS_CACHE = {"ts": 0, "ttl": 300, "val": []}

def _get_headers():
    now = time.time()
    if _HEADERS_CACHE["val"] and (now - _HEADERS_CACHE["ts"] < _HEADERS_CACHE["ttl"]):
        return _HEADERS_CACHE["val"]
    svc = _sheets_service()
    res = _values_get(svc, f"{SHEET_TAB}!A1:ZZ1")
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
    svc = _sheets_service()

    # 1) Leer columna real de ID_Reporte
    _, _, id_range = _resolve_id_col()
    col_vals = _values_get(svc, id_range).get("values", [])
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
    svc = _sheets_service()
    _, _, id_range = _resolve_id_col()
    col_vals = _values_get(svc, id_range).get("values", [])
    values = [(r[0].strip() if r and str(r[0]).strip() else "") for r in col_vals]
    last_row_idx = None
    for i, val in enumerate(values, start=2):
        if val == id_reporte:
            last_row_idx = i
    if not last_row_idx:
        return None

    row = _values_get(svc, f"{SHEET_TAB}!A{last_row_idx}:ZZ{last_row_idx}").get("values", [[]])[0]
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
            svc = _sheets_service()
            meta = _spreadsheet_meta(svc, fields="sheets(properties(sheetId,title))")
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
        svc = _sheets_service()
        hdr = _values_get(svc, f"{tab}!A1:ZZ1").get("values", [[]])[0]
        rows = _values_get(svc, f"{tab}!A2:ZZ").get("values", [])
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
        svc = _sheets_service()
        _, _, id_range = _resolve_id_col()
        col_vals = _values_get(svc, id_range).get("values", [])
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
def _drive_service_for_imgs():
    return get_drive_service_user()

_DRIVE_PATTERNS = [
    r'drive\.google\.com\/file\/d\/([a-zA-Z0-9_-]+)',
    r'drive\.google\.com\/open\?id=([a-zA-Z0-9_-]+)',
    r'drive\.google\.com\/uc\?id=([a-zA-Z0-9_-]+)',
    r'[?&]id=([a-zA-Z0-9_-]+)',
]

def _extract_drive_id(url: str):
    for pat in _DRIVE_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def _resolve_shortcut(drive, file_id: str):
    meta = _retry(lambda: drive.files().get(fileId=file_id, fields="mimeType,shortcutDetails/targetId").execute())
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

def _resolve_path_to_id(drive, path_str: str):
    if not REPORTES_ROOT_ID:
        return None
    parts = [s for s in _normalize_relpath(path_str).split("/") if s]
    parent = REPORTES_ROOT_ID
    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)
        mime_filter = "" if is_last else " and mimeType='application/vnd.google-apps.folder'"
        safe = part.replace("'", "\\'")
        q = "name='{}' and '{}' in parents and trashed=false{}".format(safe, parent, mime_filter)
        res = _retry(lambda: drive.files().list(
            q=q, spaces='drive', fields='files(id,name,mimeType)', pageSize=50
        ).execute())
        files = res.get('files', [])
        if not files:
            return None
        parent = files[0]['id']
    return parent

@reportes_bp.route("/reportes/imgproxy", endpoint="reportes_imgproxy")
def reportes_imgproxy():
    """
    Devuelve bytes de imagen desde Drive.
    - Si falla cualquier cosa, devuelve un PNG transparente 1x1 (no 302).
    - Soporta IDs directos y ruta relativa bajo REPORTES_ROOT_ID.
    """
    # PNG transparente 1x1 (para fallback)
    TRANSPARENT_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01"
        b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    url = (request.args.get("url") or "").strip()
    if not url:
        return send_file(io.BytesIO(TRANSPARENT_PNG), mimetype="image/png")

    try:
        drive = _drive_service_for_imgs()

        # 1) ¿Es un ID de archivo de Drive en la URL?
        file_id = _extract_drive_id(url)

        # 2) ¿Es una ruta tipo ".../04. Reportes/<Cliente>/<ID>/archivo.jpg"?
        if not file_id:
            maybe = _resolve_path_to_id(drive, url)
            if maybe:
                file_id = maybe

        if not file_id:
            # No pudimos resolver nada -> PNG transparente
            return send_file(io.BytesIO(TRANSPARENT_PNG), mimetype="image/png")

        # Atajo (shortcuts) -> resolver target real
        file_id = _resolve_shortcut(drive, file_id)

        # Metadatos (para saber mimetype y nombre)
        meta = _retry(lambda: drive.files().get(
            fileId=file_id, fields="mimeType,name"
        ).execute())

        # Descargar bytes
        req = drive.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        # Pequeño límite de chunks para evitar loop infinito por red mala
        max_iters = 20
        iters = 0
        while not done and iters < max_iters:
            _, done = downloader.next_chunk()
            iters += 1
        buf.seek(0)

        # Si no descargó, devolver PNG transparente
        if buf.getbuffer().nbytes == 0:
            return send_file(io.BytesIO(TRANSPARENT_PNG), mimetype="image/png")

        mime = meta.get("mimeType", "image/jpeg") or "image/jpeg"
        # Cabeceras cacheables para que WeasyPrint no golpee varias veces
        resp = send_file(buf, mimetype=mime, as_attachment=False,
                         download_name=meta.get("name", "img"))
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    except Exception:
        # Falla silenciosa -> imagen transparente (evitar redirects/timeouts)
        return send_file(io.BytesIO(TRANSPARENT_PNG), mimetype="image/png")


# ----------------------------------------------------------------------
# Drive helpers (guardar PDF)
# ----------------------------------------------------------------------
def _drive_service_for_files():
    return get_drive_service_user()


def _sanitize_name(name: str) -> str:
    # Evitar caracteres problemáticos
    return re.sub(r'[\\/:*?"<>|]+', '-', (name or "")).strip() or "Sin nombre"

def _ensure_folder(drive, parent_id: str, name: str) -> str:
    safe = _sanitize_name(name)
    safe_q = safe.replace("'", "\\'")
    q = (
        "name='{}' and '{}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    ).format(safe_q, parent_id)
    found = _retry(lambda: drive.files().list(q=q, spaces='drive', fields='files(id,name)', pageSize=1).execute()) \
        .get('files', [])
    if found:
        return found[0]['id']
    meta = {
        "name": safe,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    return _retry(lambda: drive.files().create(body=meta, fields="id").execute())["id"]

def _upsert_pdf(drive, parent_id: str, filename: str, pdf_bytes: bytes) -> str:
    safe_name = _sanitize_name(filename)
    safe_q = safe_name.replace("'", "\\'")
    q = "name='{}' and '{}' in parents and trashed=false".format(safe_q, parent_id)
    existing = _retry(lambda: drive.files().list(q=q, spaces='drive', fields='files(id,name)', pageSize=1).execute()) \
        .get('files', [])
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    if existing:
        file_id = existing[0]['id']
        _retry(lambda: drive.files().update(fileId=file_id, media_body=media).execute())
        return file_id
    meta = {"name": safe_name, "parents": [parent_id], "mimeType": "application/pdf"}
    return _retry(lambda: drive.files().create(body=meta, media_body=media, fields="id").execute())["id"]

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
    force = (request.args.get("refresh") == "1")
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
            ultimos_items = []

    return render_template("reportes_inicio.html", ultimos_items=ultimos_items)

@reportes_bp.route("/reportes/prev/<id_reporte>")
def reportes_prev(id_reporte):
    data = get_reporte_con_overrides(id_reporte)
    if not data:
        flash("ID_Reporte no encontrado en la hoja.")
        return redirect(url_for("reportes.reportes_inicio"))

    fotos = [f for f in (data.get(f"Foto{i}", "").strip() for i in range(1,7)) if f]

    # ⬇⬇⬇ NUEVO
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

    fotos = [f for f in (data.get(f"Foto{i}", "").strip() for i in range(1,7)) if f]

    # NUEVO: rutas del logo para PDF (filesystem) y bandera de embed
    logo_web, logo_fs = _logo_paths()
    html = render_template(
        "reporte_formato.html",
        datos=data,
        fotos=fotos,
        embed_for_pdf=True,
        logo_web=logo_web,
        logo_fs=logo_fs,
    )

    # CAMBIO: base_url usando filesystem para que WeasyPrint resuelva file://
    pdf_bytes = HTML(string=html, base_url=current_app.root_path).write_pdf()

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
        drive = _drive_service_for_files()
        if not REPORTES_ROOT_ID:
            raise RuntimeError("No está configurado REPORTES_ROOT_ID")

        # Cliente
        client_id = _ensure_folder(drive, REPORTES_ROOT_ID, cliente)

        # Ruta A: /04. Reportes/<Cliente>/<ID_Reporte>/
        id_folder = _ensure_folder(drive, client_id, id_reporte)
        _upsert_pdf(drive, id_folder, filename, pdf_bytes)

        # Ruta B: /04. Reportes/<Cliente>/Reportes[/Ronda N]/
        reportes_folder = _ensure_folder(drive, client_id, "Reportes")
        target_parent = reportes_folder
        if ronda_norm:
            target_parent = _ensure_folder(drive, reportes_folder, ronda_norm)
        _upsert_pdf(drive, target_parent, filename, pdf_bytes)

        # 4) Respaldo local
        base_static = Path(current_app.root_path) / "static" / "reportes_pdfs" / cliente
        base_static.mkdir(parents=True, exist_ok=True)
        (base_static / filename).write_bytes(pdf_bytes)

        # 5) Éxito → mensaje y regreso a la vista previa
        carpeta_b = f"Reportes/{ronda_norm}" if ronda_norm else "Reportes"
        msg = f"✅ PDF guardado en Drive:\n• {cliente}/{id_reporte}/{filename}\n• {cliente}/{carpeta_b}/{filename}"
        flash(msg)
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


# ----------------------------------------------------------------------
# Editar (GET/POST)
# ----------------------------------------------------------------------
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

# ----------------------------------------------------------------------
# Debug (incluye whoami de Drive)
# ----------------------------------------------------------------------
@reportes_bp.route("/reportes/debug")
def reportes_debug():
    try:
        svc = _sheets_service()
        cfg = f"SHEET_ID={SHEET_ID!r}, SHEET_TAB={SHEET_TAB!r}, RANGE={SHEET_ID_REPORTE_RANGE!r}"
        meta = _spreadsheet_meta(svc)
        title = meta.get("properties", {}).get("title")
        hdr = _values_get(svc, f"{SHEET_TAB}!A1:ZZ1").get("values", [])

        idx, letter, id_range = _resolve_id_col()
        sample_vals = _values_get(svc, id_range).get("values", [])
        sample_vals = [r[0] for r in sample_vals if r and r[0]]

        head5 = sample_vals[:5]
        tail5 = sample_vals[-5:]

        # whoami Drive
        try:
            drive = _drive_service()
            who = _retry(lambda: drive.about().get(fields="user(displayName,emailAddress)").execute())
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
# ====== BLOQUE NUEVO: Formato manual Servicio/Diagnóstico (diag) =====
# ======================================================================

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

@reportes_bp.get("/reportes/diag/nuevo")
def diag_nuevo():
    # Formulario del “Reporte de trabajo”
    hoy = date.today().isoformat()
    return render_template("reporte_diag_form.html", fecha_hoy=hoy)

@reportes_bp.post("/reportes/diag/prev")
def diag_prev():
    up_dir, pdf_dir, tmp_dir = _diag_paths()
    token = uuid4().hex

    # ----- Datos del formulario (sin id_reporte) -----
    datos = {
        "cliente": (request.form.get("cliente") or "").strip(),
        "departamento": (request.form.get("departamento") or "").strip(),
        "atencion": (request.form.get("atencion") or "").strip(),  # solicitante
        "fecha": (request.form.get("fecha") or date.today().isoformat()),
        "fecha_fin": (request.form.get("fecha_fin") or "").strip(),
        "direccion": (request.form.get("direccion") or "").strip(),  # opcional
        # Campos nuevos
        "trabajo_solicitado": (request.form.get("trabajo_solicitado") or "").strip(),
        "tipo_mantenimiento": (request.form.get("tipo_mantenimiento") or "").strip(),
        "descripcion_falla": (request.form.get("descripcion_falla") or "").strip(),
        # Campos ya usados
        "trabajo": (request.form.get("trabajo") or "").strip(),
        "observaciones": (request.form.get("observaciones") or "").strip(),
        "notas": (request.form.get("notas") or "").strip(),  # Garantía
    }

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
    fotos_meta = []
    files = request.files.getlist("fotos")
    sess_dir = (up_dir / token)
    sess_dir.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files[:6]):
        if not f or not getattr(f, "filename", ""): continue
        fname = secure_filename(f.filename)
        stem = Path(fname).stem[:40] or f"foto{i+1}"
        ext = Path(fname).suffix.lower() or ".jpg"
        safe_name = f"{i+1:02d}_{stem}{ext}"
        dst = sess_dir / safe_name
        f.save(dst)
        fotos_meta.append({
            "filename": safe_name,
            "web_path": url_for('static', filename=f"diag_uploads/{token}/{safe_name}"),
            "fs_uri": dst.resolve().as_uri()
        })

    # ----- Persistir JSON temporal -----
    payload = {
        "token": token,
        "datos": datos,
        "partes": partes,
        "total_partes": total_partes,
        "fotos": fotos_meta,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    (tmp_dir / f"{token}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # Render de vista previa
    logo_web, logo_fs = _logo_paths()
    return render_template(
        "reporte_diag_pdf.html",
        show_toolbar=True,
        embed_for_pdf=False,
        token=token,
        datos=datos,
        partes=partes,
        total_partes_fmt=_mxn(total_partes),
        fotos=fotos_meta,
        logo_web=logo_web,
        logo_fs=logo_fs,
    )


@reportes_bp.get("/reportes/diag/pdf/<token>")
def diag_pdf(token):
    up_dir, pdf_dir, tmp_dir = _diag_paths()
    meta_path = tmp_dir / f"{token}.json"
    if not meta_path.exists():
        flash("No encontré los datos temporales del reporte. Vuelve a generar la vista previa.", "error")
        return redirect(url_for("reportes.diag_nuevo"))

    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    datos = payload.get("datos", {})
    partes = payload.get("partes", [])
    total_partes = float(payload.get("total_partes", 0.0))
    fotos = payload.get("fotos", [])

    logo_web, logo_fs = _logo_paths()
    html = render_template(
        "reporte_diag_pdf.html",
        show_toolbar=False,
        embed_for_pdf=True,   # usa rutas filesystem para imágenes y logo
        token=token,
        datos=datos,
        partes=partes,
        total_partes_fmt=_mxn(total_partes),
        fotos=fotos,
        logo_web=logo_web,
        logo_fs=logo_fs,
    )
    

    # Nombre de archivo: Cliente + Fecha (sin id)
    base_cliente = re.sub(r"[^A-Za-z0-9_-]+", "_", (datos.get("cliente") or "Reporte"))
    base_fecha = re.sub(r"[^0-9\-]", "", (datos.get("fecha") or ""))
    if not base_fecha:
        base_fecha = datetime.now().strftime("%Y-%m-%d")
    pdf_name = f"ReporteTrabajo-{base_cliente}-{base_fecha}.pdf"
    pdf_path = (pdf_dir / pdf_name).resolve()

    # ¿Descargar (dialogo) o guardar local y avisar?
    force_download = (request.args.get("dl") == "1")
    if force_download:
        # Genera a memoria y descarga
        from weasyprint import HTML
        pdf_bytes = HTML(string=html, base_url=current_app.root_path).write_pdf()
        return send_file(io.BytesIO(pdf_bytes),
                         mimetype="application/pdf",
                         as_attachment=True,
                         download_name=pdf_name)

    # Guardar en /static/diag_pdfs y regresar al form
    from weasyprint import HTML
    HTML(string=html, base_url=current_app.root_path).write_pdf(str(pdf_path))
    flash(f"PDF generado: {pdf_path}", "success")
    return redirect(url_for("reportes.diag_nuevo"))


