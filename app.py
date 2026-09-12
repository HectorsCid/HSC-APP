# app.py
from flask import Flask, render_template, request, redirect, url_for, make_response, flash, send_file, send_from_directory, abort, jsonify, current_app, session

app = Flask(__name__)
@app.get("/ping_root")
def ping_root():
    return "pong", 200



from markupsafe import escape
from datetime import date, datetime, timedelta
import hmac
import json
import os
import platform
import re
import threading
import time
import unicodedata
from pathlib import Path
import io
import hashlib
import math
import uuid
import csv
import mimetypes
import smtplib
from difflib import SequenceMatcher

# Google / OAuth
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload
from auth_google import (
    get_drive_service,
    get_sheets_service,
    get_drive_service_user,
    reset_thread_google_services,
)
from pdf_runtime import PdfRendererBusy, render_pdf_file

# NEW: para detectar RefreshError con claridad
from google.auth.exceptions import RefreshError

# Otros
from werkzeug.utils import safe_join, secure_filename
from smtp_mailer import authorized_to_send, parse_recipients, send_quote_email, smtp_config, trusted_device_token
from cfdi_drive import delete_pending_document, list_pending_documents, save_pending_document
from email_tracking import delivery_status, read_email_deliveries, record_email_delivery
from reportes_bp import (
    reportes_bp,
    serve_drive_image_ref_fast,
    start_auto_report_monitor,
    find_client_reports_folder_url,
)
from operaciones_matrix import read_operaciones_matrix
from operaciones_store import OperationsStore

from facturacion_bp import facturacion_bp
app.register_blueprint(facturacion_bp)
print(">>> Blueprint facturacion registrado")
print(app.url_map)

from pagos_facturama_bp import pagos_bp
app.register_blueprint(pagos_bp)

# imports facturas

from pathlib import Path

from flask import current_app, render_template

import json, sys
from flask import current_app, render_template

# Fallback a variable global si existe
try:
    # importa sin romper si no existe
    from facturacion_bp import clientes_predefinidos  # ya dice "cargados: 14" en logs
except Exception:
    clientes_predefinidos = []

# --- Google Drive scopes y constantes ---
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

ID_COT = '1oCf8Mt2nLynS6d2ryCngNyQ7rtf5jfiz'   # Carpeta "01. Cotizaciones" en Drive
CLIENTES_FILENAME = 'clientes.json'           # Archivo para persistir clientes en Drive
COTIZACIONES_FILENAME = 'cotizaciones.json'    # Archivo para persistir historial en Drive
BORRADORES_FILENAME = 'borradores_cotizaciones.json'
ARTICULOS_FILENAME = 'articulos_servicios.json'

# Protegen las escrituras tipo read/modify/write. En Render hay dos hilos web y
# una sincronización de arranque en segundo plano; ninguno debe pisar al otro.
_CLIENTES_DATA_LOCK = threading.RLock()
_COTIZACIONES_DATA_LOCK = threading.RLock()
_BORRADORES_DATA_LOCK = threading.RLock()
_ARTICULOS_DATA_LOCK = threading.RLock()
_FOLIO_ASSIGN_LOCK = threading.Lock()

# --- Google Sheets datos ---
SHEET_ID = "15xLRRfR_Leidnd34Cpr3ERbpJ7AaMelMxMa-9B0d6kQ"
SHEET_TAB = "Reportes"
SHEET_ID_REPORTE_RANGE = f"{SHEET_TAB}!A2:A"

# ===== Persistencia de folios en Google Sheets =====
FOLIO_RANGE = "Control_Procesamiento!B3"  # aquí vive el ultimo_folio

def _sheets_values_get(range_):
    sh = get_sheets_service()
    return sh.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=range_
    ).execute()

def _sheets_values_update(range_, value):
    sh = get_sheets_service()
    body = {"values": [[value]]}
    return sh.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=range_,
        valueInputOption="RAW",
        body=body
    ).execute()

def _get_ultimo_folio_sheets():
    """Lee B3 (ultimo_folio) de Control_Procesamiento. Devuelve int o None."""
    try:
        res = _sheets_values_get(FOLIO_RANGE)
        vals = res.get("values", [[]])
        if vals and vals[0]:
            return int(str(vals[0][0]).strip())
    except Exception as e:
        print("⚠️ Sheets: no se pudo leer ultimo_folio:", e)
    return None

def _set_ultimo_folio_sheets(nuevo_valor):
    """Escribe B3 con el folio indicado. Devuelve True/False."""
    try:
        _sheets_values_update(FOLIO_RANGE, int(nuevo_valor))
        return True
    except Exception as e:
        print("⚠️ Sheets: no se pudo escribir ultimo_folio:", e)
        return False

# ===== Historial de PDFs en Google Sheets =====
HIST_TAB = "HistorialPDF"
HIST_RANGE_READ = f"{HIST_TAB}!A2:E"  # lectura (sin encabezado)

def _sheets_values_append(range_, rows):
    """Append de filas al final de la hoja."""
    sh = get_sheets_service()
    body = {"values": rows}
    return sh.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=range_,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

def _sheets_values_get_all(range_):
    sh = get_sheets_service()
    return sh.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=range_
    ).execute()

def log_pdf_event(cliente, folio, archivo_url, carpeta_url, tipo="cotizacion"):
    """Registra fila en HistorialPDF con tipo."""
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [[ts, cliente or "", str(folio or ""), archivo_url or "", carpeta_url or "", tipo or ""]]
        _sheets_values_append(f"{HIST_TAB}!A:F", rows)
        print(f"📝 HistorialPDF: agregado {cliente} folio {folio} tipo={tipo}")
    except Exception as e:
        print("⚠️ No se pudo escribir en HistorialPDF:", e)


# Detección de entorno y auto-sync
IS_RENDER = bool(os.environ.get('RENDER') or
                 os.environ.get('RENDER_SERVICE_ID') or
                 os.environ.get('RENDER_EXTERNAL_HOSTNAME'))
AUTO_SYNC_FROM_DRIVE = True  # si no quieres en local, pon False

# La app local usa SQLite para desarrollar sin costo. Render sólo activa la
# base al recibir OPERACIONES_DATABASE_URL, evitando confiar en su disco efímero.
OPERACIONES_STORE = OperationsStore.from_environment(
    is_render=IS_RENDER, project_root=Path(__file__).resolve().parent
)

app.static_folder = "static"
app.template_folder = "templates"

app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("SERVICE_ACCOUNT_B64")
    or os.environ.get("SERVICE_ACCOUNT_JSON")
    or os.environ.get("TOKEN_JSON_B64")
    or os.environ.get("GOOGLE_TOKEN_B64")
    or "solo-desarrollo-local"
)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=400),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_RENDER,
    SESSION_REFRESH_EACH_REQUEST=True,
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
)


def _app_access_password():
    """Clave general; reutiliza temporalmente la clave de correo si no hay una exclusiva."""
    return os.environ.get("HSC_APP_PASSWORD", "") or os.environ.get("SMTP_SEND_KEY", "")


def _safe_return_path(value):
    target = str(value or "").strip()
    return target if target.startswith("/") and not target.startswith("//") else "/inicio-app"


@app.route("/acceso", methods=["GET", "POST"])
def acceso():
    password = _app_access_password()
    next_path = _safe_return_path(request.values.get("next"))
    error = ""
    if request.method == "POST":
        supplied = str(request.form.get("password") or "")
        if password and hmac.compare_digest(supplied, password):
            session.clear()
            session.permanent = True
            session["hsc_authenticated"] = True
            # La contraseña general actual pertenece al propietario. Los accesos
            # de técnico/cliente se crearán después mediante invitaciones.
            session["hsc_role"] = "admin"
            session["hsc_user_id"] = "owner"
            return redirect(next_path)
        error = "La contraseña no es correcta. Intenta nuevamente."
    return render_template("acceso.html", error=error, next_path=next_path, configured=bool(password))


@app.get("/cerrar-sesion")
def cerrar_sesion():
    session.clear()
    return redirect(url_for("acceso"))


@app.before_request
def _require_app_login():
    endpoint = request.endpoint or ""
    if endpoint in {
        "acceso", "healthz", "health", "health_check", "ping_root",
        "pwa_manifest", "pwa_technician_manifest", "pwa_partner_manifest", "pwa_service_worker",
    }:
        return None
    if endpoint == "static" and (
        request.path.startswith("/static/img/")
        or request.path.endswith((".css", ".js", ".ico", ".svg", ".png", ".jpg", ".jpeg", ".webp"))
    ):
        return None
    if request.path == "/api/invoice-schedules/run":
        expected = os.environ.get("HSC_SCHEDULER_KEY", "").strip()
        supplied = request.headers.get("X-HSC-Scheduler-Key", "")
        if len(expected) >= 20 and hmac.compare_digest(expected, supplied):
            return None
    password = _app_access_password()
    if current_app.testing and not password:
        return None
    if not password and not IS_RENDER:
        return None
    if session.get("hsc_authenticated") is True:
        return None
    if not password:
        message = "Falta configurar HSC_APP_PASSWORD en Render."
        return (jsonify({"ok": False, "error": message}), 503) if request.path.startswith("/api/") else (message, 503)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Tu sesión está cerrada. Vuelve a ingresar."}), 401
    return redirect(url_for("acceso", next=_safe_return_path(request.full_path.rstrip("?"))))


@app.after_request
def _security_headers(response):
    """Protecciones comunes sin interferir con los formularios existentes."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if IS_RENDER:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith(("/factur", "/api/", "/pagos", "/clientes", "/acceso")):
        response.headers.setdefault("Cache-Control", "no-store, private")
    if request.path == "/service-worker.js":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/manifest.webmanifest")
def pwa_manifest():
    response = send_from_directory(app.static_folder, "manifest.webmanifest")
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def _operations_manifest(app_kind):
    is_partner = app_kind == "partner"
    slug = "hsc-partner" if is_partner else "hsc-tecnico"
    name = "HSC Partner" if is_partner else "HSC Técnico"
    description = (
        "Consulta de equipos, reportes y fallas para clientes HSC."
        if is_partner
        else "Agenda, clientes, equipos y reportes para técnicos HSC."
    )
    response = jsonify({
        "id": f"/{slug}",
        "name": name,
        "short_name": name,
        "description": description,
        "lang": "es-MX",
        "start_url": f"/{slug}?origen=app",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "orientation": "any",
        "background_color": "#07101f",
        "theme_color": "#0f2f5f",
        "categories": ["business", "productivity", "utilities"],
        "icons": [
            {"src": "/static/img/hsc-app-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/img/hsc-app-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    })
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.get("/manifest-hsc-tecnico.webmanifest")
def pwa_technician_manifest():
    return _operations_manifest("technician")


@app.get("/manifest-hsc-partner.webmanifest")
def pwa_partner_manifest():
    return _operations_manifest("partner")


@app.get("/service-worker.js")
def pwa_service_worker():
    return send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")

app.register_blueprint(reportes_bp)
start_auto_report_monitor(app)

@app.template_filter('currency')
def currency_filter(value):
    try:
        return "${:,.2f}".format(float(value))
    except Exception:
        return "${:,.2f}".format(0)

# ===================================== Helpers Drive (clientes.json) =====================================
def _drive_service():
    # Ahora usa cuenta de servicio (sin token.json)
    return get_drive_service()

def _drive_service_cfg():
    return _drive_service()

def _drive_buscar_archivo(service, nombre, parent_id):
    res = service.files().list(
        q=f"name='{nombre}' and '{parent_id}' in parents and trashed=false",
        spaces='drive',
        fields='files(id,name)',
        pageSize=10
    ).execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None

def descargar_clientes_de_drive():
    """Descarga clientes; devuelve None si Drive falla o el archivo no es válido."""
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, CLIENTES_FILENAME, ID_COT)
        if not fid:
            print("clientes.json no encontrado en Drive; se conservan los datos actuales.")
            return None
        request = service.files().get_media(fileId=fid)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        content = fh.read().decode('utf-8')
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("clientes.json debe contener un objeto JSON")
        with open('clientes.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"clientes.json cargado desde Drive: {len(data)} clientes.")
        return data
    except Exception as e:
        print("No se pudo descargar clientes.json de Drive:", e)
        return None

def subir_clientes_a_drive(clientes_dict):
    try:
        if not isinstance(clientes_dict, dict) or not clientes_dict:
            raise ValueError("Se cancelo la subida para evitar reemplazar Drive con una lista vacia")
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, CLIENTES_FILENAME, ID_COT)
        payload = json.dumps(clientes_dict, ensure_ascii=False, indent=2).encode('utf-8')
        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype='application/json', resumable=False)
        if fid:
            updated = service.files().update(fileId=fid, media_body=media, fields='id').execute()
            print("clientes.json actualizado en Drive:", updated.get('id'))
        else:
            meta = {'name': CLIENTES_FILENAME, 'parents': [ID_COT]}
            created = service.files().create(body=meta, media_body=media, fields='id').execute()
            print("clientes.json creado en Drive:", created.get('id'))
    except Exception as e:
        print("No se pudo subir clientes.json a Drive:", e)


def descargar_cotizaciones_de_drive():
    """Descarga cotizaciones.json desde la misma carpeta (ID_COT). Si no existe, crea uno vacío [] en Drive."""
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, COTIZACIONES_FILENAME, ID_COT)
        if not fid:
            # Crear archivo vacío en Drive
            payload = b"[]"
            media = MediaIoBaseUpload(io.BytesIO(payload), mimetype='application/json', resumable=False)
            meta = {'name': COTIZACIONES_FILENAME, 'parents': [ID_COT]}
            created = service.files().create(body=meta, media_body=media, fields='id').execute()
            fid = created.get('id')
            print("📤 cotizaciones.json creado en Drive:", fid)

        request = service.files().get_media(fileId=fid)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        content = fh.read().decode('utf-8') if fh.getbuffer().nbytes else "[]"
        data = json.loads(content) if content.strip() else []
        if not isinstance(data, list):
            # Si por algún motivo llegó dict, intenta normalizar
            if isinstance(data, dict):
                if isinstance(data.get("items"), list):
                    data = data["items"]
                elif isinstance(data.get("data"), list):
                    data = data["data"]
                else:
                    data = []
            else:
                data = []

        # Guardar localmente en /data/cotizaciones.json (igual que registrar_cotizacion)
        base = Path(current_app.root_path) / "data"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "cotizaciones.json"
        tmp_path = path.with_suffix(".sync.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

        print("✅ cotizaciones.json cargado desde Drive.")
        return data
    except Exception as e:
        print("⚠️ No se pudo descargar cotizaciones.json de Drive:", e)
        return None

def subir_cotizaciones_a_drive(items_list):
    """Sube (upsert) cotizaciones.json a Drive en la carpeta ID_COT."""
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, COTIZACIONES_FILENAME, ID_COT)

        payload = json.dumps(items_list if isinstance(items_list, list) else [], ensure_ascii=False, indent=2).encode('utf-8')
        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype='application/json', resumable=False)

        if fid:
            updated = service.files().update(fileId=fid, media_body=media, fields='id').execute()
            print("♻️ cotizaciones.json actualizado en Drive:", updated.get('id'))
        else:
            meta = {'name': COTIZACIONES_FILENAME, 'parents': [ID_COT]}
            created = service.files().create(body=meta, media_body=media, fields='id').execute()
            print("📤 cotizaciones.json creado en Drive:", created.get('id'))
        return True
    except Exception as e:
        print("⚠️ No se pudo subir cotizaciones.json a Drive:", e)
        return False


def _ruta_cotizaciones():
    base = Path(current_app.root_path) / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / COTIZACIONES_FILENAME


def _ruta_borradores():
    base = Path(current_app.root_path) / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / BORRADORES_FILENAME


def _leer_borradores_locales():
    path = _ruta_borradores()
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else []
        return data if isinstance(data, list) else []
    except Exception as e:
        print("⚠️ No se pudieron leer los borradores locales:", e)
        return []


def _escribir_borradores_locales(items):
    path = _ruta_borradores()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def descargar_borradores_de_drive():
    """Sincroniza el respaldo de borradores sin mezclarlo con cotizaciones terminadas."""
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, BORRADORES_FILENAME, ID_COT)
        if not fid:
            _escribir_borradores_locales([])
            print("ℹ️ Todavía no existe un respaldo de borradores en Drive.")
            return []

        request_drive = service.files().get_media(fileId=fid)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_drive)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        content = fh.getvalue().decode("utf-8") if fh.getbuffer().nbytes else "[]"
        data = json.loads(content) if content.strip() else []
        if not isinstance(data, list):
            data = []
        _escribir_borradores_locales(data)
        print(f"✅ Borradores cargados desde Drive: {len(data)}.")
        return data
    except Exception as e:
        print("⚠️ No se pudieron descargar los borradores de Drive:", e)
        return None


def subir_borradores_a_drive(items):
    """Actualiza el respaldo; devuelve False para poder avisar si Google falla."""
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, BORRADORES_FILENAME, ID_COT)
        payload = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json", resumable=False)
        if fid:
            service.files().update(fileId=fid, media_body=media, fields="id").execute()
        else:
            meta = {"name": BORRADORES_FILENAME, "parents": [ID_COT]}
            service.files().create(body=meta, media_body=media, fields="id").execute()
        return True
    except Exception as e:
        print("⚠️ No se pudieron respaldar los borradores en Drive:", e)
        return False


# ================= Catálogo de artículos y servicios =================
def _ruta_articulos():
    base = Path(current_app.root_path) / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / ARTICULOS_FILENAME


def _leer_articulos_locales():
    path = _ruta_articulos()
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else []
        return data if isinstance(data, list) else []
    except Exception as exc:
        print("⚠️ No se pudo leer el catálogo local:", exc)
        return []


def _escribir_articulos_locales(items):
    path = _ruta_articulos()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _servicios_drive_catalogo():
    """Prefiere OAuth de usuario porque una cuenta de servicio no tiene cuota propia."""
    servicios = []
    try:
        servicios.append(get_drive_service_user())
    except Exception:
        pass
    try:
        servicio_sa = _drive_service_cfg()
        if servicio_sa not in servicios:
            servicios.append(servicio_sa)
    except Exception:
        pass
    return servicios


def descargar_articulos_de_drive():
    for service in _servicios_drive_catalogo():
        try:
            fid = _drive_buscar_archivo(service, ARTICULOS_FILENAME, ID_COT)
            if not fid:
                continue
            request_drive = service.files().get_media(fileId=fid)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request_drive)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = json.loads(fh.getvalue().decode("utf-8") or "[]")
            if not isinstance(data, list):
                raise ValueError("El catálogo debe ser una lista")
            _escribir_articulos_locales(data)
            return data
        except Exception as exc:
            print("⚠️ No se pudo leer artículos con una credencial de Drive:", exc)
    return None


def subir_articulos_a_drive(items):
    payload = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
    ultimo_error = None
    for service in _servicios_drive_catalogo():
        try:
            fid = _drive_buscar_archivo(service, ARTICULOS_FILENAME, ID_COT)
            media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json", resumable=False)
            if fid:
                service.files().update(fileId=fid, media_body=media, fields="id").execute()
            else:
                service.files().create(
                    body={"name": ARTICULOS_FILENAME, "parents": [ID_COT]},
                    media_body=media,
                    fields="id",
                ).execute()
            return True
        except Exception as exc:
            ultimo_error = exc
    print("⚠️ No se pudo respaldar el catálogo de artículos en Drive:", ultimo_error)
    return False


# ======================= Funciones para clientes (con persistencia en Drive) ======================
def cargar_clientes():
    """Carga solo el respaldo local; la red se sincroniza fuera de la petición."""
    if os.path.exists("clientes.json"):
        try:
            with open("clientes.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception as e:
            print("clientes.json local ilegible:", e)
    return {}

def guardar_clientes(clientes):
    with _CLIENTES_DATA_LOCK:
        snapshot = dict(clientes)
        try:
            with open("clientes.json", "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            print("clientes.json guardado localmente.")
        except Exception as e:
            print("No se pudo guardar clientes.json local:", e)

        subir_clientes_a_drive(snapshot)


def _normalizar_nombre_importacion(value):
    """Clave comparable sin acentos, puntuación ni diferencias de mayúsculas."""
    text = unicodedata.normalize("NFKD", str(value or "").strip().upper())
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", text)


def _nombre_base_importacion(value):
    """Retira terminaciones societarias para comparar el nombre comercial."""
    text = unicodedata.normalize("NFKD", str(value or "").strip().upper())
    text = text.encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[A-Z0-9]+", text)
    ignored = {"DE", "DEL", "LA", "EL", "LOS", "LAS", "MEXICO", "MEXICANA",
               "SA", "S", "CV", "RL", "SAPI", "SC", "AC"}
    return "".join(token for token in tokens if token not in ignored)


def _leer_clientes_konta(upload):
    """Lee la exportación de Konta en XLSX o CSV y devuelve nombre legal/RFC."""
    filename = str(getattr(upload, "filename", "") or "").strip()
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xlsx", ".csv"}:
        raise ValueError("Sube el archivo de Konta en formato Excel (.xlsx) o CSV (.csv).")

    rows = []
    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("El servidor todavía no tiene habilitada la lectura de Excel.") from exc
        workbook = load_workbook(upload.stream, read_only=True, data_only=True)
        sheet = workbook.active
        values = sheet.iter_rows(values_only=True)
        headers = [str(v or "").strip() for v in next(values, [])]
        source_rows = values
    else:
        raw = upload.stream.read()
        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        headers = [str(v or "").strip() for v in next(reader, [])]
        source_rows = reader

    normalized_headers = [_normalizar_nombre_importacion(v) for v in headers]
    name_aliases = {"NOMBRELEGAL", "RAZONSOCIAL", "NOMBRE", "CLIENTE"}
    rfc_aliases = {"RFC", "TAXID"}
    try:
        name_index = next(i for i, value in enumerate(normalized_headers) if value in name_aliases)
        rfc_index = next(i for i, value in enumerate(normalized_headers) if value in rfc_aliases)
    except StopIteration as exc:
        raise ValueError("No encontré las columnas Nombre legal y RFC en el archivo.") from exc

    for row in source_rows:
        values = list(row)
        name = str(values[name_index] or "").strip() if name_index < len(values) else ""
        rfc = str(values[rfc_index] or "").strip().upper() if rfc_index < len(values) else ""
        if name and rfc:
            rows.append({"nombre_legal": name, "rfc": rfc})
        if len(rows) > 5000:
            raise ValueError("El archivo supera el límite de 5,000 clientes.")

    if not rows:
        raise ValueError("El archivo no contiene clientes con nombre legal y RFC.")

    # Konta puede exportar varias fichas con el mismo RFC. Conservamos una sola
    # y preferimos el nombre legal más completo.
    by_rfc = {}
    for row in rows:
        current = by_rfc.get(row["rfc"])
        appearances = int((current or {}).get("apariciones", 0)) + 1
        if current is None or len(row["nombre_legal"]) > len(current["nombre_legal"]):
            by_rfc[row["rfc"]] = {**row, "apariciones": appearances}
        else:
            current["apariciones"] = appearances
    return sorted(by_rfc.values(), key=lambda row: row["nombre_legal"])


def _plan_importacion_clientes(rows, existentes):
    """Sugiere un destino; la pantalla de revisión conserva la decisión final."""
    names = list(existentes.keys())
    by_rfc = {
        str(data.get("rfc") or "").strip().upper(): name
        for name, data in existentes.items()
        if isinstance(data, dict) and str(data.get("rfc") or "").strip()
    }
    by_name = {_normalizar_nombre_importacion(name): name for name in names}
    planned = []
    for row in rows:
        legal_name = row["nombre_legal"]
        rfc = row["rfc"]
        target = by_rfc.get(rfc) or by_name.get(_normalizar_nombre_importacion(legal_name))
        match_type = "rfc" if by_rfc.get(rfc) else ("nombre" if target else "nuevo")
        confidence = 1.0 if target else 0.0

        if not target and names:
            source_key = _normalizar_nombre_importacion(legal_name)
            source_base = _nombre_base_importacion(legal_name)
            ranked = sorted(
                ((max(
                    SequenceMatcher(None, source_key, _normalizar_nombre_importacion(name)).ratio(),
                    SequenceMatcher(None, source_base, _nombre_base_importacion(name)).ratio(),
                ), name)
                 for name in names),
                reverse=True,
            )
            best_score, best_name = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0
            if best_score >= 0.88 and best_score - second_score >= 0.08:
                target = best_name
                match_type = "sugerido"
                confidence = best_score

        planned.append({**row, "destino": target or "__new__", "tipo": match_type,
                        "confianza": round(confidence * 100)})
    return planned

clientes_predefinidos = cargar_clientes()
app.config["HSC_CLIENTES_PROVIDER"] = lambda: clientes_predefinidos


def _resolver_cliente_catalogo(nombre="", rfc=""):
    """Devuelve (clave, ficha) aunque llegue el alias, razón social o una variación menor."""
    name = str(nombre or "").strip()
    rfc_key = str(rfc or "").strip().upper()
    name_key = _normalizar_nombre_importacion(name)
    name_base = _nombre_base_importacion(name)
    with _CLIENTES_DATA_LOCK:
        catalogo = dict(clientes_predefinidos or {})

    if rfc_key:
        for alias, data in catalogo.items():
            if isinstance(data, dict) and str(data.get("rfc") or "").strip().upper() == rfc_key:
                return alias, data

    exact_matches = []
    candidates = []
    for alias, data in catalogo.items():
        if not isinstance(data, dict):
            continue
        names = [alias, data.get("razon_social"), data.get("razon"), data.get("legal_name")]
        saved_aliases = data.get("aliases") or []
        names.extend(saved_aliases if isinstance(saved_aliases, list) else [saved_aliases])
        normalized = {_normalizar_nombre_importacion(value) for value in names if str(value or "").strip()}
        bases = {_nombre_base_importacion(value) for value in names if str(value or "").strip()}
        if name_key and (name_key in normalized or name_base in bases):
            exact_matches.append((alias, data))
            continue
        if name_key:
            score = max((max(
                SequenceMatcher(None, name_key, candidate).ratio(),
                SequenceMatcher(None, name_base, _nombre_base_importacion(candidate)).ratio(),
            ) for candidate in normalized), default=0)
            candidates.append((score, alias, data))

    if len(exact_matches) == 1:
        return exact_matches[0]
    candidates.sort(key=lambda item: item[0], reverse=True)
    if candidates and candidates[0][0] >= 0.90 and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.06):
        return candidates[0][1], candidates[0][2]
    return None, None

def _sync_clientes_from_drive_into_memory():
    global clientes_predefinidos
    with _CLIENTES_DATA_LOCK:
        data = descargar_clientes_de_drive()
        if isinstance(data, dict) and data:
            try:
                # Reemplazo atómico: una petición nunca itera un dict a medio actualizar.
                clientes_predefinidos = dict(data)
                print(f"Clientes sincronizados desde Drive: {len(clientes_predefinidos)}.")
                return True
            except Exception as e:
                print("No se pudo actualizar clientes_predefinidos:", e)
        elif data == {}:
            print("Drive devolvio una lista vacia; se conservan los clientes actuales.")
    return False

# ---------- Sincronización inicial sin bloquear solicitudes ----------
__did_sync_once = False
__did_sync_cotizaciones_once = False
__did_sync_borradores_once = False
__bootstrap_sync_lock = threading.Lock()
__bootstrap_sync_last_attempt = 0.0
BOOTSTRAP_SYNC_RETRY_SECONDS = 60

def _sync_cotizaciones_from_drive_into_local():
    """Descarga/crea cotizaciones.json en Drive y lo deja en data/cotizaciones.json."""
    with _COTIZACIONES_DATA_LOCK:
        return descargar_cotizaciones_de_drive()

def _sync_borradores_from_drive_into_local():
    with _BORRADORES_DATA_LOCK:
        return descargar_borradores_de_drive()

def _bootstrap_sync_worker(app_obj):
    global __did_sync_once, __did_sync_cotizaciones_once, __did_sync_borradores_once
    try:
        with app_obj.app_context():
            if not __did_sync_once and (IS_RENDER or AUTO_SYNC_FROM_DRIVE):
                sync_ok = _sync_clientes_from_drive_into_memory()
                if sync_ok:
                    __did_sync_once = True
                    print(f"Clientes disponibles: {len(clientes_predefinidos)} (Drive).")
            if IS_RENDER and not __did_sync_cotizaciones_once:
                data = _sync_cotizaciones_from_drive_into_local()
                if data is not None:
                    __did_sync_cotizaciones_once = True
            if IS_RENDER and not __did_sync_borradores_once:
                data = _sync_borradores_from_drive_into_local()
                if data is not None:
                    __did_sync_borradores_once = True
    except Exception as exc:
        app_obj.logger.exception("Falló la sincronización inicial con Drive: %s", exc)
    finally:
        reset_thread_google_services()
        __bootstrap_sync_lock.release()


@app.before_request
def _schedule_bootstrap_sync():
    """Programa la carga de Drive y permite que la página responda de inmediato."""
    global __bootstrap_sync_last_attempt
    endpoint = request.endpoint or ""
    if (
        endpoint in {"healthz", "health", "health_check", "static"}
        or endpoint.startswith("reportes.")
        or endpoint.endswith("reportes_auto_status")
    ):
        return
    clients_done = __did_sync_once or not (IS_RENDER or AUTO_SYNC_FROM_DRIVE)
    quotes_done = __did_sync_cotizaciones_once or not IS_RENDER
    drafts_done = __did_sync_borradores_once or not IS_RENDER
    if clients_done and quotes_done and drafts_done:
        return
    now = time.monotonic()
    if now - __bootstrap_sync_last_attempt < BOOTSTRAP_SYNC_RETRY_SECONDS:
        return
    if not __bootstrap_sync_lock.acquire(blocking=False):
        return
    __bootstrap_sync_last_attempt = now
    try:
        threading.Thread(
            target=_bootstrap_sync_worker,
            args=(current_app._get_current_object(),),
            name="google-bootstrap-sync",
            daemon=True,
        ).start()
    except Exception:
        __bootstrap_sync_lock.release()
        raise


_CLIENT_MUTATION_ENDPOINTS = {"nuevo_cliente", "editar_cliente", "borrar_cliente"}
_QUOTE_MUTATION_ENDPOINTS = {"generar_pdf", "eliminar_cotizacion"}
_DRAFT_MUTATION_ENDPOINTS = {
    "guardar_borrador", "eliminar_borrador", "guardar_costos_internos", "generar_pdf"
}


@app.before_request
def _guard_bootstrap_writes():
    """Nunca sube a Drive un respaldo parcial mientras termina el arranque."""
    if not IS_RENDER:
        return None
    endpoint = request.endpoint or ""
    clients_required = endpoint in _CLIENT_MUTATION_ENDPOINTS
    quotes_required = endpoint in _QUOTE_MUTATION_ENDPOINTS
    drafts_required = endpoint in _DRAFT_MUTATION_ENDPOINTS
    if not clients_required and not quotes_required and not drafts_required:
        return None
    if (not clients_required or __did_sync_once) and (
        not quotes_required or (__did_sync_once and __did_sync_cotizaciones_once)
    ) and (not drafts_required or __did_sync_borradores_once):
        return None

    response = make_response(
        "Los datos todavía se están sincronizando con Google Drive. "
        "Espera unos segundos y vuelve a intentarlo; no se guardó ningún cambio.",
        503,
    )
    response.headers["Retry-After"] = "10"
    return response

# -------------------------------------------------------------------

# ======================= Función para folios automáticos =======================
def obtener_siguiente_folio():
    """
    Nuevo flujo:
    1) Intentar leer y actualizar folio en Google Sheets (Control_Procesamiento!B3).
    2) Si Sheets falla, usar folios.json local como respaldo (comportamiento actual).
    3) Espejar el valor final en folios.json (best-effort) para consulta local.
    """
    # --- 1) Intento con Sheets (oficial) ---
    ultimo_sheets = _get_ultimo_folio_sheets()
    if isinstance(ultimo_sheets, int):
        siguiente = ultimo_sheets + 1
        if _set_ultimo_folio_sheets(siguiente):
            # Espejo local (best effort)
            try:
                with open("folios.json", "w", encoding="utf-8") as f:
                    json.dump({"ultimo_folio": siguiente}, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print("⚠️ No se pudo espejar folio en folios.json:", e)
            return siguiente
        else:
            print("⚠️ No se pudo escribir en Sheets, se usará respaldo local.")

    # --- 2) Respaldo local: folios.json (comportamiento previo) ---
    ruta_folios = "folios.json"
    try:
        if not os.path.exists(ruta_folios):
            with open(ruta_folios, "w", encoding="utf-8") as f:
                json.dump({"ultimo_folio": 0}, f)

        with open(ruta_folios, "r", encoding="utf-8") as f:
            datos = json.load(f)
        # Tolerancia a archivo raro/corrupto
        if not isinstance(datos, dict) or "ultimo_folio" not in datos:
            datos = {"ultimo_folio": 0}

        datos["ultimo_folio"] = int(datos.get("ultimo_folio", 0)) + 1

        with open(ruta_folios, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)

        # --- 3) Espejo a Sheets (best effort) ---
        _set_ultimo_folio_sheets(datos["ultimo_folio"])

        return datos["ultimo_folio"]

    except Exception as e:
        print("❌ Error con folios.json:", e)
        # Último salvavidas para no romper el flujo:
        return int(datetime.now().strftime("%y%m%d%H%M%S"))


def _asegurar_folio_actual():
    """Asigna un folio solo al persistir o finalizar una cotización."""
    with _FOLIO_ASSIGN_LOCK:
        folio = str(datos_cliente.get("cotizacion") or "").strip()
        if not folio:
            folio = str(obtener_siguiente_folio())
            datos_cliente["cotizacion"] = folio
        return folio

# =========================== Variables de trabajo ==============================
partidas = []
datos_cliente = {}
costos_internos = {
    "id": None,
    "items": [],
    "gastos_extra": 0.0,
    "ganancia_modo": "porcentaje",
    "ganancia_valor": 0.0,
    "redondeo": 1.0,
    "descripcion_publica": "Suministro de materiales y servicios",
    "desgloses": [],
}

def _reiniciar_costos_internos():
    costos_internos.clear()
    costos_internos.update({
        "id": None,
        "items": [],
        "gastos_extra": 0.0,
        "ganancia_modo": "porcentaje",
        "ganancia_valor": 0.0,
        "redondeo": 1.0,
        "descripcion_publica": "Suministro de materiales y servicios",
        "desgloses": [],
    })


def _nuevo_desglose_costos(desglose_id=None):
    return {
        "id": desglose_id or uuid.uuid4().hex,
        "items": [],
        "gastos_extra": 0.0,
        "ganancia_modo": "porcentaje",
        "ganancia_valor": 0.0,
        "redondeo": 1.0,
        "descripcion_publica": "Suministro de materiales y servicios",
    }


def _campos_desglose(costos):
    base = _nuevo_desglose_costos(costos.get("id"))
    for clave in (
        "items", "gastos_extra", "ganancia_modo", "ganancia_valor",
        "redondeo", "descripcion_publica",
    ):
        if clave in costos:
            base[clave] = json.loads(json.dumps(costos[clave], ensure_ascii=False))
    return base


def _normalizar_desgloses_costos():
    """Migra el cálculo único anterior sin romper borradores existentes."""
    desgloses = costos_internos.get("desgloses")
    if not isinstance(desgloses, list):
        desgloses = []
    desgloses = [d for d in desgloses if isinstance(d, dict)]
    costos_internos["desgloses"] = desgloses

    actual_id = str(costos_internos.get("id") or "").strip()
    tiene_datos = bool(costos_internos.get("items")) or any(
        _numero_seguro(costos_internos.get(clave)) > 0
        for clave in ("gastos_extra", "ganancia_valor")
    )
    linea_legacy = next((p for p in partidas if p.get("origen_costos_internos")), None)
    if not actual_id and (tiene_datos or linea_legacy):
        actual_id = uuid.uuid4().hex
        costos_internos["id"] = actual_id
        if linea_legacy is not None:
            linea_legacy["costos_internos_id"] = actual_id

    if actual_id and not any(str(d.get("id")) == actual_id for d in desgloses):
        desgloses.append(_campos_desglose(costos_internos))


def _guardar_desglose_activo():
    _normalizar_desgloses_costos()
    desglose_id = str(costos_internos.get("id") or "").strip()
    if not desglose_id:
        desglose_id = uuid.uuid4().hex
        costos_internos["id"] = desglose_id
    copia = _campos_desglose(costos_internos)
    for index, existente in enumerate(costos_internos["desgloses"]):
        if str(existente.get("id")) == desglose_id:
            costos_internos["desgloses"][index] = copia
            break
    else:
        costos_internos["desgloses"].append(copia)
    return desglose_id


def _activar_desglose(desglose_id):
    _normalizar_desgloses_costos()
    desglose = next((
        d for d in costos_internos["desgloses"]
        if str(d.get("id")) == str(desglose_id)
    ), None)
    if not desglose:
        return False
    desgloses = costos_internos["desgloses"]
    costos_internos.clear()
    costos_internos.update(_campos_desglose(desglose))
    costos_internos["desgloses"] = desgloses
    return True


def _activar_nuevo_desglose():
    _normalizar_desgloses_costos()
    desgloses = costos_internos["desgloses"]
    costos_internos.clear()
    costos_internos.update(_nuevo_desglose_costos())
    costos_internos["desgloses"] = desgloses

def _numero_seguro(valor, default=0.0):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return float(default)

def _totales_costos_internos(costos=None):
    costos = costos if isinstance(costos, dict) else costos_internos
    costo_directo = 0.0
    for item in costos.get("items", []):
        cantidad = _numero_seguro(item.get("cantidad"))
        unitario = _numero_seguro(item.get("costo_unitario"))
        merma = max(0.0, _numero_seguro(item.get("merma")))
        total_linea = cantidad * unitario * (1 + merma / 100)
        item["total"] = round(total_linea, 2)
        costo_directo += total_linea

    gastos_extra = max(0.0, _numero_seguro(costos.get("gastos_extra")))
    costo_total = costo_directo + gastos_extra
    valor_ganancia = max(0.0, _numero_seguro(costos.get("ganancia_valor")))
    if costos.get("ganancia_modo") == "monto":
        ganancia = valor_ganancia
    else:
        ganancia = costo_total * valor_ganancia / 100
    sugerido = costo_total + ganancia
    redondeo = max(1.0, _numero_seguro(costos.get("redondeo"), 1))
    precio_final = math.ceil(sugerido / redondeo) * redondeo if sugerido else 0.0
    return {
        "costo_directo": round(costo_directo, 2),
        "gastos_extra": round(gastos_extra, 2),
        "costo_total": round(costo_total, 2),
        "ganancia": round(ganancia, 2),
        "precio_sugerido": round(sugerido, 2),
        "precio_final": round(precio_final, 2),
        "precio_con_iva": round(precio_final * 1.16, 2),
    }


def _transferir_desglose_a_partidas(desglose):
    desglose_id = str(desglose.get("id") or "").strip()
    precio = _totales_costos_internos(desglose)["precio_final"]
    if not desglose_id or precio <= 0:
        return False
    descripcion = (
        desglose.get("descripcion_publica") or "Suministro de materiales y servicios"
    ).strip()
    linea = next((
        p for p in partidas
        if str(p.get("costos_internos_id") or "") == desglose_id
    ), None)
    nuevos_datos = {
        "descripcion": descripcion,
        "cantidad": 1,
        "precio": precio,
        "total": precio,
        "precio_pendiente": False,
        "origen_costos_internos": True,
        "costos_internos_id": desglose_id,
    }
    if linea is None:
        partidas.append(nuevos_datos)
    else:
        linea.update(nuevos_datos)
    return True

def _clave_orden_cliente(nombre):
    texto = unicodedata.normalize("NFKD", str(nombre or ""))
    return "".join(ch for ch in texto if not unicodedata.combining(ch)).casefold()


def _cotizacion_tiene_cambios_sin_guardar():
    tiene_contenido = bool(
        partidas
        or datos_cliente.get("cliente")
        or datos_cliente.get("nombre_borrador")
        or datos_cliente.get("comentarios")
        or datos_cliente.get("cotizacion")
    )
    if not tiene_contenido:
        return False
    folio = str(datos_cliente.get("cotizacion") or "").strip()
    if not folio:
        return True
    with _BORRADORES_DATA_LOCK:
        guardados = _leer_borradores_locales()
    borrador = next((
        item for item in guardados
        if str(item.get("id") or item.get("folio") or "").strip() == folio
    ), None)
    if not borrador:
        return True
    return (
        (borrador.get("datos") or {}) != dict(datos_cliente)
        or (borrador.get("partidas") or []) != [dict(item) for item in partidas]
        or (borrador.get("costos_internos") or {}) != costos_internos
    )


def _tasas_retencion(datos):
    """Normaliza preferencias nuevas y conserva cotizaciones antiguas."""
    datos = datos if isinstance(datos, dict) else {}
    legacy = bool(datos.get("usar_retenciones"))
    tiene_nuevas = "retencion_isr_tasa" in datos or "retencion_iva_tasa" in datos
    try:
        isr = float(datos.get("retencion_isr_tasa") or 0)
        iva_ret = float(datos.get("retencion_iva_tasa") or 0)
    except (TypeError, ValueError):
        isr, iva_ret = 0.0, 0.0
    if legacy and not tiene_nuevas:
        return 0.0125, 0.1066666667
    tasas_isr = {0.0, 0.0125, 0.10}
    tasas_iva = {0.0, 0.03, 0.04, 0.053333, 0.06, 0.106667, 0.16}
    return (isr if isr in tasas_isr else 0.0, iva_ret if iva_ret in tasas_iva else 0.0)

# ================================= Rutas =======================================
@app.route('/')
def inicio():
    subtotal = sum(p['total'] for p in partidas)
    iva = subtotal * 0.16

    tasa_isr, tasa_iva_ret = _tasas_retencion(datos_cliente)
    usar_retenciones = bool(tasa_isr or tasa_iva_ret)
    isr_retenido = subtotal * tasa_isr
    iva_retenido = subtotal * tasa_iva_ret

    total = subtotal + iva - isr_retenido - iva_retenido
    cambios_sin_guardar = _cotizacion_tiene_cambios_sin_guardar()

    return render_template(
        'inicio.html',
        partidas=partidas,
        datos=datos_cliente,
        clientes=clientes_predefinidos,
        clientes_orden=list(clientes_predefinidos.keys()),
        clientes_alfabeticos=sorted(clientes_predefinidos.keys(), key=_clave_orden_cliente),
        subtotal=subtotal,
        iva=iva,
        isr_retenido=isr_retenido,
        iva_retenido=iva_retenido,
        tasa_isr=tasa_isr,
        tasa_iva_ret=tasa_iva_ret,
        total=total,
        cambios_sin_guardar=cambios_sin_guardar,
        today=date.today().isoformat()
    )

@app.route('/debug/clientes')
def debug_clientes():
    try:
        svc = _drive_service_cfg()
        q = f"name='clientes.json' and '{ID_COT}' in parents and trashed=false"
        res = svc.files().list(
            q=q, spaces='drive', fields='files(id,name,mimeType,parents,owners/emailAddress)', pageSize=10
        ).execute()
        files = res.get('files', [])
        if not files:
            return "❌ No encontré clientes.json DIRECTO dentro de 01. Cotizaciones", 404
        f = files[0]
        return f"✅ Encontrado: {f['name']} ({f['id']}) · mime={f.get('mimeType')} · owner={f.get('owners',[{}])[0].get('emailAddress','?')}"
    except Exception as e:
        return f"❌ Error buscando clientes.json: {e}", 500

@app.route('/clientes/status')
def clientes_status():
    try:
        n = len(clientes_predefinidos)
        sample = list(clientes_predefinidos.keys())[:5]
        return f"✅ En memoria: {n} clientes. Ejemplos: {sample}"
    except Exception as e:
        return f"❌ Error: {e}", 500

@app.route('/clientes/refresh-cache')
def clientes_refresh_cache():
    try:
        _sync_clientes_from_drive_into_memory()
        return f"🔄 Recargados. Ahora hay {len(clientes_predefinidos)} clientes."
    except Exception as e:
        return f"❌ No se pudo recargar: {e}", 500

@app.route('/guardar_datos', methods=['POST'])
def guardar_datos():
    _actualizar_datos_cliente_desde_form()
    return redirect(url_for('inicio'))

def _actualizar_datos_cliente_desde_form():
    """Copia el formulario activo sin generar PDF ni alterar el historial."""
    datos_cliente['cliente'] = request.form.get('cliente')
    datos_cliente['atencion'] = request.form.getlist('atencion')
    datos_cliente['direccion'] = request.form.get('direccion', '')
    datos_cliente['fecha'] = request.form.get('fecha', '')
    datos_cliente['anticipo'] = request.form.get('anticipo', '')
    datos_cliente['tiempo'] = request.form.get('tiempo', '')
    datos_cliente['vigencia'] = request.form.get('vigencia', '')
    datos_cliente['cotizacion'] = request.form.get('cotizacion', '')
    datos_cliente['nombre_borrador'] = (request.form.get('nombre_borrador') or '').strip()
    datos_cliente['comentarios'] = request.form.get('comentarios', '')
    datos_cliente["retencion_isr_tasa"] = request.form.get("retencion_isr_tasa", "0")
    datos_cliente["retencion_iva_tasa"] = request.form.get("retencion_iva_tasa", "0")
    tasa_isr, tasa_iva_ret = _tasas_retencion(datos_cliente)
    datos_cliente["usar_retenciones"] = bool(tasa_isr or tasa_iva_ret)

@app.route('/agregar', methods=['POST'])
def agregar():
    if request.form.get("preservar_datos_cotizacion") == "1":
        _actualizar_datos_cliente_desde_form()
    descripcion = (request.form.get('descripcion') or '').strip()
    if not descripcion:
        flash("❌ Escribe la descripción de la partida.")
        return redirect(url_for('inicio'))
    try:
        cantidad = int(request.form['cantidad'])
        precio_texto = (request.form.get('precio') or '').strip()
        precio_pendiente = not precio_texto
        precio = float(precio_texto) if precio_texto else 0.0
    except (TypeError, ValueError):
        flash("❌ Error: Ingresa una cantidad y un precio válidos, o deja el precio vacío si está pendiente.")
        return redirect(url_for('inicio'))

    total = cantidad * precio
    partidas.append({
        'descripcion': descripcion,
        'cantidad': cantidad,
        'precio': precio,
        'total': total,
        'precio_pendiente': precio_pendiente,
    })
    return redirect(url_for('inicio'))

@app.route('/editar/<int:indice>', methods=['GET', 'POST'])
def editar(indice):
    if request.method == 'POST':
        try:
            cantidad = int(request.form['cantidad'])
            precio_texto = (request.form.get('precio') or '').strip()
            precio = float(precio_texto) if precio_texto else 0.0
        except (TypeError, ValueError):
            flash("❌ Ingresa una cantidad válida; el precio puede quedar vacío mientras sea borrador.")
            return redirect(url_for('editar', indice=indice))
        partidas[indice]['descripcion'] = (request.form.get('descripcion') or '').strip()
        partidas[indice]['cantidad'] = cantidad
        partidas[indice]['precio'] = precio
        partidas[indice]['precio_pendiente'] = not precio_texto
        partidas[indice]['total'] = partidas[indice]['cantidad'] * partidas[indice]['precio']
        return redirect(url_for('inicio'))
    else:
        return render_template('editar.html', indice=indice, partida=partidas[indice])

@app.route('/eliminar/<int:indice>')
def eliminar(indice):
    if 0 <= indice < len(partidas):
        partidas.pop(indice)
    return redirect(url_for('inicio'))

@app.route('/limpiar')
def limpiar():
    partidas.clear()
    datos_cliente.clear()
    _reiniciar_costos_internos()
    return redirect(url_for('inicio'))

@app.route('/nueva-cotizacion')
def nueva_cotizacion():
    """Inicia una cotización limpia únicamente cuando el usuario lo solicita."""
    partidas.clear()
    datos_cliente.clear()
    _reiniciar_costos_internos()
    return redirect(url_for('inicio'))

def _normalizar_contactos_cliente(datos):
    """Devuelve contactos individuales y conserva compatibilidad con correos antiguos."""
    datos = datos if isinstance(datos, dict) else {}
    contactos = []
    vistos = set()
    for item in datos.get("contactos") or []:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("nombre") or item.get("atencion") or "").strip()
        correo = str(item.get("correo") or item.get("email") or "").strip()
        tipo = str(item.get("tipo") or "ambos").strip().lower()
        if tipo not in {"compras", "cuentas_pagar", "ambos", "otro"}:
            tipo = "ambos"
        try:
            parsed = parse_recipients(correo)
        except ValueError:
            parsed = []
        if not nombre or len(parsed) > 1 or (parsed and parsed[0].casefold() in vistos):
            continue
        correo_normalizado = parsed[0] if parsed else ""
        if correo_normalizado:
            vistos.add(correo_normalizado.casefold())
        contactos.append({"nombre": nombre, "correo": correo_normalizado, "tipo": tipo})
    if contactos:
        return contactos

    atencion_raw = datos.get("atencion") or []
    if isinstance(atencion_raw, str):
        atencion_raw = [value for value in atencion_raw.split(",")]
    nombres = [str(value).strip() for value in atencion_raw if str(value).strip()]
    legacy = []
    for campo, tipo, etiqueta in (
        ("correo_compras", "compras", "Compras"),
        ("correo_cuentas_pagar", "cuentas_pagar", "Cuentas por pagar"),
        ("correo_facturacion", "cuentas_pagar", "Facturación"),
        ("email", "ambos", "Contacto"),
    ):
        try:
            emails = parse_recipients(datos.get(campo) or "")
        except ValueError:
            emails = []
        for correo in emails:
            if correo.casefold() in vistos:
                continue
            vistos.add(correo.casefold())
            legacy.append((correo, tipo, etiqueta))
    conteo_etiquetas = {}
    for index, (correo, tipo, etiqueta) in enumerate(legacy):
        conteo_etiquetas[etiqueta] = conteo_etiquetas.get(etiqueta, 0) + 1
        nombre = nombres[index] if index < len(nombres) else f"{etiqueta} {conteo_etiquetas[etiqueta]}"
        contactos.append({"nombre": nombre, "correo": correo, "tipo": tipo})
    for nombre in nombres[len(legacy):]:
        contactos.append({"nombre": nombre, "correo": "", "tipo": "ambos"})
    return contactos


def _contactos_desde_form(form):
    nombres = form.getlist("contacto_nombre")
    correos = form.getlist("contacto_correo")
    tipos = form.getlist("contacto_tipo")
    contactos = []
    vistos = set()
    for index in range(max(len(nombres), len(correos), len(tipos))):
        nombre = str(nombres[index] if index < len(nombres) else "").strip()
        correo = str(correos[index] if index < len(correos) else "").strip()
        tipo = str(tipos[index] if index < len(tipos) else "ambos").strip().lower()
        if not nombre and not correo:
            continue
        if not nombre:
            raise ValueError("Cada correo debe tener un nombre de contacto en Atención.")
        parsed = parse_recipients(correo) if correo else []
        if correo and len(parsed) != 1:
            raise ValueError(f"Guarda un solo correo para {nombre}.")
        correo = parsed[0] if parsed else ""
        if correo and correo.casefold() in vistos:
            raise ValueError(f"El correo {correo} está repetido.")
        if tipo not in {"compras", "cuentas_pagar", "ambos", "otro"}:
            tipo = "ambos"
        if correo:
            vistos.add(correo.casefold())
        contactos.append({"nombre": nombre, "correo": correo, "tipo": tipo})
    return contactos


def _correos_por_tipo(contactos, *tipos):
    return ", ".join(
        item["correo"] for item in contactos
        if item.get("correo") and item.get("tipo") in tipos
    )


@app.route('/nuevo_cliente', methods=['GET', 'POST'])
def nuevo_cliente():
    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        try:
            contactos = _contactos_desde_form(request.form)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for('nuevo_cliente'))
        atencion = [item["nombre"] for item in contactos]
        direccion = request.form.get('direccion', '')
        tiempo = request.form.get('tiempo', '')
        anticipo = request.form.get('anticipo', '')
        vigencia = request.form.get('vigencia', '')

        # Datos fiscales opcionales
        rfc            = (request.form.get('rfc') or '').strip().upper()
        razon_social   = (request.form.get('razon_social') or request.form.get('razon') or '').strip()
        cp             = (request.form.get('cp') or '').strip()
        regimen_fiscal = (request.form.get('regimen_fiscal') or '').strip()  # ej. 601, 612, 621, 626
        uso_cfdi       = (request.form.get('uso_cfdi') or '').strip()        # ej. G03, G01, P01
        correo_compras = _correos_por_tipo(contactos, "compras", "ambos")
        correo_cuentas_pagar = _correos_por_tipo(contactos, "cuentas_pagar", "ambos")
        correos_frecuentes = (request.form.get('correos_frecuentes') or '').strip()
        retencion_isr_tasa = (request.form.get('retencion_isr_tasa') or '0').strip()
        retencion_iva_tasa = (request.form.get('retencion_iva_tasa') or '0').strip()

        if not nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('nuevo_cliente'))

        with _CLIENTES_DATA_LOCK:
            clientes_predefinidos[nombre] = {
                "tiene_poliza": request.form.get('tiene_poliza') == '1',
                "atencion": atencion,
                "contactos": contactos,
                "direccion": direccion,
                "tiempo": tiempo,
                "anticipo": anticipo,
                "vigencia": vigencia,
                "retencion_isr_tasa": retencion_isr_tasa,
                "retencion_iva_tasa": retencion_iva_tasa,
            }

            # Solo guarda si vienen
            if rfc:            clientes_predefinidos[nombre]["rfc"] = rfc
            if razon_social:   clientes_predefinidos[nombre]["razon_social"] = razon_social
            if cp:             clientes_predefinidos[nombre]["cp"] = cp
            if regimen_fiscal: clientes_predefinidos[nombre]["regimen_fiscal"] = regimen_fiscal
            if uso_cfdi:       clientes_predefinidos[nombre]["uso_cfdi"] = uso_cfdi
            if correo_compras: clientes_predefinidos[nombre]["correo_compras"] = correo_compras
            if correo_cuentas_pagar:
                clientes_predefinidos[nombre]["correo_cuentas_pagar"] = correo_cuentas_pagar
                clientes_predefinidos[nombre]["correo_facturacion"] = correo_cuentas_pagar
            if correos_frecuentes: clientes_predefinidos[nombre]["correos_frecuentes"] = correos_frecuentes

            guardar_clientes(clientes_predefinidos)
        return redirect(url_for('inicio'))

    return render_template('agregar_cliente.html')


def cargar_datos():
    try:
        with open('datos.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def cargar_partidas():
    try:
        with open('partidas.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def calcular_totales(partidas):
    subtotal = sum(p['cantidad'] * p['precio'] for p in partidas)
    iva = subtotal * 0.16
    total = subtotal + iva
    return subtotal, iva, total

def abrir_drive_local(cliente_nombre):
    base = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
    cliente_seguro = (cliente_nombre or "SIN_CLIENTE").replace("/", "-").replace("\\", "-").strip()
    destino_dir = os.path.join(base, cliente_seguro)
    try:
        os.makedirs(destino_dir, exist_ok=True)
        os.startfile(destino_dir)
        print("📂 Abierto Drive local:", destino_dir)
    except Exception as e:
        print("⚠️ No se pudo abrir Drive local:", e)

# ---------- NUEVO: registrar cotizaciones para inicio_cotizacion ----------
def registrar_cotizacion(cot):
    """
    Guarda/actualiza una cotización en data/cotizaciones.json para que
    /cotizaciones la liste. Upsert por id/folio.
    """
    with _COTIZACIONES_DATA_LOCK:
        path = _ruta_cotizaciones()

        try:
            arr = json.loads(path.read_text("utf-8")) if path.exists() else []
            if not isinstance(arr, list):
                arr = []
        except Exception:
            arr = []

        cid = str(cot.get("id") or cot.get("folio") or "").strip()
        if cid:
            for i, q in enumerate(arr):
                qid = str(q.get("id") or q.get("folio") or "").strip()
                if qid and qid == cid:
                    arr[i] = {**q, **cot}
                    break
            else:
                arr.insert(0, cot)
        else:
            arr.insert(0, cot)

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(arr, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

        # Persistencia en Drive (Render) para no perder historial
        if IS_RENDER:
            subir_cotizaciones_a_drive(arr)


def _eliminar_cotizacion_por_id(qid):
    """Quita una cotización del registro; nunca borra su PDF de Drive."""
    with _COTIZACIONES_DATA_LOCK:
        path = _ruta_cotizaciones()
        try:
            items = json.loads(path.read_text("utf-8")) if path.exists() else []
            if not isinstance(items, list):
                items = []
        except Exception:
            items = []

        objetivo = str(qid or "").strip()
        restantes = [
            item for item in items
            if str(item.get("id") or item.get("folio") or "").strip() != objetivo
        ]
        if len(restantes) == len(items):
            return False, True

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(restantes, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

        if IS_RENDER and not subir_cotizaciones_a_drive(restantes):
            # Evita mostrar una eliminación que reaparecería al reiniciar Render.
            restore_tmp = path.with_suffix(".restore.tmp")
            restore_tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            restore_tmp.replace(path)
            return True, False
        return True, True


def _guardar_o_actualizar_borrador(borrador):
    with _BORRADORES_DATA_LOCK:
        items = _leer_borradores_locales()
        draft_id = str(borrador.get("id") or borrador.get("folio") or "").strip()
        for index, item in enumerate(items):
            item_id = str(item.get("id") or item.get("folio") or "").strip()
            if draft_id and item_id == draft_id:
                items[index] = borrador
                break
        else:
            items.insert(0, borrador)
        _escribir_borradores_locales(items)
        return (not IS_RENDER) or subir_borradores_a_drive(items)


def _eliminar_borrador_por_id(draft_id):
    with _BORRADORES_DATA_LOCK:
        items = _leer_borradores_locales()
        draft_id = str(draft_id or "").strip()
        restantes = [
            item for item in items
            if str(item.get("id") or item.get("folio") or "").strip() != draft_id
        ]
        if len(restantes) == len(items):
            return False, True
        _escribir_borradores_locales(restantes)
        drive_ok = (not IS_RENDER) or subir_borradores_a_drive(restantes)
        return True, drive_ok


def _construir_borrador_actual():
    folio = _asegurar_folio_actual()
    subtotal = sum(float(item.get("total") or 0) for item in partidas)
    iva = subtotal * 0.16
    tasa_isr, tasa_iva_ret = _tasas_retencion(datos_cliente)
    total_borrador = subtotal + iva - (subtotal * tasa_isr) - (subtotal * tasa_iva_ret)
    return {
        "id": folio,
        "folio": folio,
        "estado": "borrador",
        "cliente": (datos_cliente.get("cliente") or "").strip(),
        "nombre_borrador": (datos_cliente.get("nombre_borrador") or "").strip(),
        "fecha": datos_cliente.get("fecha") or "",
        "actualizado": datetime.now().isoformat(timespec="seconds"),
        "datos": dict(datos_cliente),
        "partidas": [dict(item) for item in partidas],
        "costos_internos": json.loads(json.dumps(costos_internos, ensure_ascii=False)),
        "total": round(total_borrador, 2),
    }


@app.post('/borradores/guardar')
def guardar_borrador():
    _actualizar_datos_cliente_desde_form()
    borrador = _construir_borrador_actual()
    folio = borrador["folio"]
    drive_ok = _guardar_o_actualizar_borrador(borrador)
    if drive_ok:
        flash(f"Borrador {folio} guardado correctamente.")
    else:
        flash(
            f"Borrador {folio} guardado temporalmente, pero Google Drive no respondió. "
            "Vuelve a guardarlo antes de cerrar para confirmar el respaldo."
        )
    return redirect(url_for("inicio"))


@app.get('/api/borradores/list')
def api_borradores_list():
    with _BORRADORES_DATA_LOCK:
        items = _leer_borradores_locales()
    items.sort(key=lambda item: str(item.get("actualizado") or ""), reverse=True)
    return jsonify(items), 200


@app.get('/borradores/<draft_id>/continuar')
def continuar_borrador(draft_id):
    with _BORRADORES_DATA_LOCK:
        items = _leer_borradores_locales()
    borrador = next((
        item for item in items
        if str(item.get("id") or item.get("folio") or "") == str(draft_id)
    ), None)
    if not borrador:
        flash("No se encontró el borrador solicitado.")
        return redirect(url_for("ui_inicio_cotizacion"))

    datos = borrador.get("datos") if isinstance(borrador.get("datos"), dict) else {}
    lineas = borrador.get("partidas") if isinstance(borrador.get("partidas"), list) else []
    datos_cliente.clear()
    datos_cliente.update(datos)
    datos_cliente["cotizacion"] = str(borrador.get("folio") or draft_id)
    partidas.clear()
    partidas.extend(dict(item) for item in lineas if isinstance(item, dict))
    costos_guardados = borrador.get("costos_internos")
    _reiniciar_costos_internos()
    if isinstance(costos_guardados, dict):
        costos_internos.update(costos_guardados)
    _normalizar_desgloses_costos()
    flash(f"Borrador {draft_id} cargado. Puedes continuar editándolo.")
    return redirect(url_for("inicio"))


@app.post('/borradores/<draft_id>/eliminar')
def eliminar_borrador(draft_id):
    eliminado, drive_ok = _eliminar_borrador_por_id(draft_id)
    if not eliminado:
        flash("El borrador ya no existe.")
    elif drive_ok:
        flash(f"Borrador {draft_id} eliminado.")
    else:
        flash("El borrador se eliminó localmente, pero Google Drive no respondió.")
    return redirect(url_for("ui_inicio_cotizacion"))


@app.post('/costos-internos/abrir')
def abrir_costos_internos():
    _actualizar_datos_cliente_desde_form()
    return redirect(url_for("ver_costos_internos"))


@app.get('/costos-internos')
def ver_costos_internos():
    if request.args.get("nuevo") == "1":
        _activar_nuevo_desglose()
    elif request.args.get("desglose"):
        if not _activar_desglose(request.args.get("desglose")):
            flash("No se encontró ese cálculo interno.")
    else:
        _normalizar_desgloses_costos()

    categorias = [
        ("material", "Material"),
        ("mano_obra", "Mano de obra"),
        ("flete", "Flete o transporte"),
        ("viaticos", "Viáticos"),
        ("renta", "Renta de herramienta/equipo"),
        ("subcontrato", "Subcontratación"),
        ("otro", "Otro"),
    ]
    unidades = [
        "Pieza", "Metro", "m²", "m³", "Kilogramo", "Gramo", "Litro",
        "Mililitro", "Hora", "Jornada", "Día", "Servicio", "Lote",
        "Viaje", "Caja", "Paquete", "Rollo",
    ]
    desgloses = []
    for desglose in costos_internos.get("desgloses", []):
        desglose_id = str(desglose.get("id") or "")
        en_cotizacion = any(
            str(p.get("costos_internos_id") or "") == desglose_id
            for p in partidas
        )
        desgloses.append({
            "id": desglose_id,
            "descripcion": desglose.get("descripcion_publica") or "Cálculo sin descripción",
            "en_cotizacion": en_cotizacion,
        })
    actual_id = str(costos_internos.get("id") or "")
    if actual_id and not any(d["id"] == actual_id for d in desgloses):
        desgloses.append({
            "id": actual_id,
            "descripcion": "Nueva partida (sin guardar)",
            "en_cotizacion": False,
        })
    return render_template(
        "costos_internos.html",
        costos=costos_internos,
        totales=_totales_costos_internos(),
        categorias=categorias,
        unidades=unidades,
        folio=datos_cliente.get("cotizacion") or "",
        desgloses=desgloses,
    )


@app.post('/costos-internos/guardar')
def guardar_costos_internos():
    categorias_validas = {
        "material", "mano_obra", "flete", "viaticos", "renta", "subcontrato", "otro"
    }
    categorias = request.form.getlist("categoria")
    nombres = request.form.getlist("nombre")
    cantidades = request.form.getlist("cantidad")
    unidades = request.form.getlist("unidad")
    costos = request.form.getlist("costo_unitario")
    mermas = request.form.getlist("merma")
    notas = request.form.getlist("nota")

    items = []
    total_filas = max(
        len(categorias), len(nombres), len(cantidades), len(unidades),
        len(costos), len(mermas), len(notas), 0
    )
    for index in range(total_filas):
        nombre = (nombres[index] if index < len(nombres) else "").strip()
        if not nombre:
            continue
        categoria = categorias[index] if index < len(categorias) else "material"
        if categoria not in categorias_validas:
            categoria = "otro"
        cantidad = max(0.0, _numero_seguro(cantidades[index] if index < len(cantidades) else 0))
        costo_unitario = max(0.0, _numero_seguro(costos[index] if index < len(costos) else 0))
        merma = max(0.0, _numero_seguro(mermas[index] if index < len(mermas) else 0))
        items.append({
            "categoria": categoria,
            "nombre": nombre,
            "cantidad": cantidad,
            "unidad": (unidades[index] if index < len(unidades) else "Pieza").strip() or "Pieza",
            "costo_unitario": costo_unitario,
            "merma": merma,
            "nota": (notas[index] if index < len(notas) else "").strip(),
        })

    desglose_solicitado = str(request.form.get("desglose_id") or "").strip()
    if desglose_solicitado and desglose_solicitado != str(costos_internos.get("id") or ""):
        _activar_desglose(desglose_solicitado)

    costos_internos["items"] = items
    costos_internos["gastos_extra"] = max(0.0, _numero_seguro(request.form.get("gastos_extra")))
    modo = request.form.get("ganancia_modo")
    costos_internos["ganancia_modo"] = modo if modo in {"porcentaje", "monto"} else "porcentaje"
    costos_internos["ganancia_valor"] = max(0.0, _numero_seguro(request.form.get("ganancia_valor")))
    redondeo = _numero_seguro(request.form.get("redondeo"), 1)
    costos_internos["redondeo"] = redondeo if redondeo in {1.0, 10.0, 50.0, 100.0} else 1.0
    costos_internos["descripcion_publica"] = (
        request.form.get("descripcion_publica") or "Suministro de materiales y servicios"
    ).strip()
    desglose_id = _guardar_desglose_activo()

    accion = request.form.get("accion") or "guardar"
    totales = _totales_costos_internos()
    if accion == "transferir":
        if not _transferir_desglose_a_partidas(costos_internos):
            flash("Agrega costos antes de transferir un precio a la cotización.")
            return redirect(url_for("ver_costos_internos"))
        _guardar_desglose_activo()
    elif accion == "transferir_todos":
        procesadas = sum(
            1 for desglose in costos_internos.get("desgloses", [])
            if _transferir_desglose_a_partidas(desglose)
        )
        omitidas = len(costos_internos.get("desgloses", [])) - procesadas
        if not procesadas:
            flash("No hay cálculos con precio para enviar a la cotización.")
            return redirect(url_for("ver_costos_internos"))

    borrador = _construir_borrador_actual()
    drive_ok = _guardar_o_actualizar_borrador(borrador)
    if accion == "transferir":
        if drive_ok:
            flash("Precio interno transferido a la cotización. El desglose permanece privado.")
        else:
            flash("El precio se transfirió, pero Google Drive no confirmó el respaldo del desglose.")
        return redirect(url_for("inicio"))
    if accion == "transferir_todos":
        detalle_omitidas = f" Se omitieron {omitidas} cálculos vacíos." if omitidas else ""
        if drive_ok:
            flash(f"{procesadas} partidas enviadas o actualizadas en la cotización.{detalle_omitidas}")
        else:
            flash(
                f"{procesadas} partidas se enviaron, pero Google Drive no confirmó el respaldo."
                f"{detalle_omitidas}"
            )
        return redirect(url_for("inicio"))
    if drive_ok:
        flash(f"Cálculo interno guardado en el borrador {borrador['folio']}.")
    else:
        flash("El cálculo quedó local, pero Google Drive no respondió. Intenta guardarlo otra vez.")
    return redirect(url_for("ver_costos_internos"))

@app.route('/generar_pdf', methods=['GET', 'POST'])
def generar_pdf():
    import shutil
    if request.method == 'POST':
        _actualizar_datos_cliente_desde_form()
    pendientes = [p for p in partidas if p.get("precio_pendiente")]
    if pendientes:
        flash(
            "No se puede generar el PDF: hay partidas con precio pendiente. "
            "Complétalas o guarda la cotización como borrador."
        )
        return redirect(url_for("inicio"))
    _asegurar_folio_actual()
    # Congelar datos a disco
    guardar_datos(datos_cliente)
    guardar_partidas(partidas)

    datos = dict(datos_cliente)
    partidas_actuales = list(partidas)

    # Totales (mismo cálculo que en vista_previa)
    subtotal = sum((p.get('cantidad', 0) or 0) * (p.get('precio', 0.0) or 0.0) for p in partidas_actuales)
    iva = subtotal * 0.16

    tasa_isr, tasa_iva_ret = _tasas_retencion(datos)
    usar_retenciones = bool(tasa_isr or tasa_iva_ret)
    isr_retenido = subtotal * tasa_isr
    iva_retenido = subtotal * tasa_iva_ret

    total = subtotal + iva - isr_retenido - iva_retenido
    total_final = total

    cliente = (datos.get('cliente') or 'SIN_CLIENTE').strip()
    cot = (str(datos.get('cotizacion')) or 'S/F').strip()

    # Guardar PDF en carpeta local del proyecto (respaldo)
    cliente_folder = os.path.join('cotizaciones', cliente.replace("/", "-").replace("\\", "-"))
    os.makedirs(cliente_folder, exist_ok=True)
    nombre_archivo = f"{cliente} - {cot}.pdf"
    ruta_pdf = os.path.abspath(os.path.join(cliente_folder, nombre_archivo))

    img_path = Path("img/logo.png").resolve().as_uri()
    html = render_template(
        'plantilla_pdf.html',
        datos=datos,
        partidas=partidas_actuales,
        subtotal=subtotal,
        iva=iva,
        total=total,
        isr_retenido=isr_retenido,
        iva_retenido=iva_retenido,
        tasa_isr=tasa_isr,
        tasa_iva_ret=tasa_iva_ret,
        total_final=total_final,
        img_path=img_path
    )
    try:
        render_pdf_file(html, ruta_pdf, wait_timeout=5)
    except PdfRendererBusy:
        flash("Hay otro PDF procesándose. Inténtalo nuevamente en unos segundos.", "warning")
        return redirect(url_for("inicio"))

    def guardar_respaldo_local(ruta_pdf_local, cliente_nombre, nombre_arch):
        ruta_respaldo_dir = os.path.join('static', 'cotizaciones', cliente_nombre.replace("/", "-").replace("\\", "-"))
        os.makedirs(ruta_respaldo_dir, exist_ok=True)
        ruta_final = os.path.join(ruta_respaldo_dir, nombre_arch)
        shutil.copy2(ruta_pdf_local, ruta_final)
        print("💾 Copiado a respaldo local:", ruta_final)

    def _obtener_o_crear_carpeta(service, nombre, id_padre=None):
        query = f"name='{nombre}' and mimeType='application/vnd.google-apps.folder'"
        if id_padre:
            query += f" and '{id_padre}' in parents"
        res = service.files().list(q=query, spaces='drive', fields='files(id,name)', pageSize=1).execute()
        items = res.get('files', [])
        if items:
            return items[0]['id']
        meta = {'name': nombre, 'mimeType': 'application/vnd.google-apps.folder'}
        if id_padre:
            meta['parents'] = [id_padre]
        carpeta = service.files().create(body=meta, fields='id').execute()
        return carpeta['id']

    def abrir_drive_local_win(cliente_nombre, nombre_archivo):
        base = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
        cliente_seguro = (cliente_nombre or "SIN_CLIENTE").replace("/", "-").replace("\\", "-").strip()
        dir_local = os.path.join(base, cliente_seguro)
        pdf_local = os.path.join(dir_local, nombre_archivo)
        try:
            if os.path.exists(pdf_local):
                os.startfile(pdf_local)
                print("📂 Abierto PDF local:", pdf_local)
            elif os.path.isdir(dir_local):
                os.startfile(dir_local)
                print("📂 Abierta carpeta local existente:", dir_local)
            else:
                print("ℹ️ Carpeta/archivo local aún no existen (pendiente de sync).")
        except Exception as e:
            print("⚠️ No se pudo abrir recurso local:", e)

    def subir_a_drive_archivo(ruta_pdf, cliente_nombre, nombre_archivo):
        print(f"🚀 Subiendo a Drive: {nombre_archivo} para '{cliente_nombre}'")
        service = get_drive_service_user()

        id_cot = ID_COT
        canon = (cliente_nombre or "").strip().lower()
        res = service.files().list(
            q=f"'{id_cot}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields='files(id,name)',
            pageSize=1000
        ).execute()

        id_cliente = None
        for it in res.get('files', []):
            if it['name'].strip().lower() == canon:
                id_cliente = it['id']
                break

        if not id_cliente:
            print(f"📁 Carpeta cliente no encontrada, creando: {cliente_nombre}")
            id_cliente = _obtener_o_crear_carpeta(service, cliente_nombre, id_cot)

        carpeta_url = f"https://drive.google.com/drive/folders/{id_cliente}"

        existing = service.files().list(
            q=f"name='{nombre_archivo}' and '{id_cliente}' in parents and trashed=false",
            spaces='drive',
            fields='files(id,name)',
            pageSize=100
        ).execute().get('files', [])

        media = MediaFileUpload(ruta_pdf, mimetype='application/pdf')
        if existing:
            file_id = existing[0]['id']
            updated = service.files().update(
                fileId=file_id,
                media_body=media,
                fields='id, webViewLink, webContentLink'
            ).execute()
            archivo_url = updated.get('webViewLink') or carpeta_url
            for dup in existing[1:]:
                try:
                    service.files().delete(fileId=dup['id']).execute()
                except Exception:
                    pass
        else:
            created = service.files().create(
                body={'name': nombre_archivo, 'parents': [id_cliente]},
                media_body=media,
                fields='id, webViewLink, webContentLink'
            ).execute()
            archivo_url = created.get('webViewLink') or carpeta_url

        return carpeta_url, archivo_url

    guardar_respaldo_local(ruta_pdf, cliente, nombre_archivo)
    carpeta_url, archivo_url = subir_a_drive_archivo(ruta_pdf, cliente, nombre_archivo)
    abrir_drive_local_win(cliente, nombre_archivo)

    # Registrar en historial (para el panel de "Generados recientes")
    try:
        log_pdf_event(cliente, cot, archivo_url, carpeta_url)
    except Exception as _e:
        print("⚠️ No se pudo registrar en HistorialPDF:", _e)

    # ---------- NUEVO: registrar cotización para /cotizaciones ----------
    try:
        conceptos = []
        for p in partidas_actuales:
            conceptos.append({
                "descripcion": p.get("descripcion", "Concepto"),
                "cantidad": float(p.get("cantidad", 1) or 1),
                "precio_unitario": float(p.get("precio", 0) or 0),
                "tasa_iva": 0.16,
                "clave_prod_serv": "85121600",
                "clave_unidad": "E48",
                "descuento": float(p.get("descuento", 0) or 0)
            })

        rec = {}
        try:
            if isinstance(clientes_predefinidos, dict):
                cinfo = clientes_predefinidos.get(cliente, {})
                if isinstance(cinfo, dict):
                    rec = {
                        "rfc": (cinfo.get("rfc") or "").upper(),
                        "nombre": cinfo.get("razon_social") or cinfo.get("razon") or cinfo.get("nombre") or cliente,
                        "cp": cinfo.get("cp") or cinfo.get("codigo_postal") or "",
                        "regimen_fiscal": cinfo.get("regimen_fiscal") or "",
                        "uso_cfdi": cinfo.get("uso_cfdi") or ""
                    }
        except Exception:
            pass

        registrar_cotizacion({
            "id": str(cot),
            "folio": str(cot),
            "cliente": cliente,
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "total": round(float(total), 2),
            "view_url": archivo_url,           # ← AÑADIDO
            "receptor": rec,
            "conceptos": conceptos,
            "datos": datos,
        })
        print(f"🗂️ Cotización registrada para listado: {cot} ({cliente})")
    except Exception as e:
        print("⚠️ No se pudo registrar la cotización en data/cotizaciones.json:", e)

    # Si este folio venía de un borrador, el PDF ya lo convirtió en cotización terminada.
    try:
        _eliminar_borrador_por_id(cot)
    except Exception as e:
        print("⚠️ No se pudo retirar el borrador ya finalizado:", e)

    cliente_seguro = cliente.replace("/", "-").replace("\\", "-")
    pdf_descarga_url = url_for(
        "static",
        filename=f"cotizaciones/{cliente_seguro}/{nombre_archivo}",
    )
    return render_template(
        "cotizacion_generada.html",
        cliente=cliente,
        folio=cot,
        nombre_archivo=nombre_archivo,
        pdf_descarga_url=pdf_descarga_url,
        archivo_url=archivo_url,
        carpeta_url=carpeta_url,
    )

@app.route('/editar_cliente', methods=['GET', 'POST'])
def editar_cliente():
    nombre_actual = (
        request.values.get('cliente_original')
        or request.args.get('cliente')
        or datos_cliente.get('cliente')
        or ""
    ).strip()
    if not nombre_actual:
        flash("Primero selecciona un cliente en Inicio para poder editarlo.")
        return redirect(url_for('inicio'))
    canonical_name, _ = _resolver_cliente_catalogo(nombre_actual)
    if canonical_name:
        nombre_actual = canonical_name
    if nombre_actual not in clientes_predefinidos:
        flash("El cliente seleccionado ya no existe.")
        return redirect(url_for('inicio'))
    datos_cliente['cliente'] = nombre_actual
    datos = clientes_predefinidos.get(nombre_actual, {
        "atencion": [],
        "direccion": "",
        "tiempo": "",
        "anticipo": "",
        "vigencia": ""
    })

    if request.method == 'POST':
        nuevo_nombre = (request.form.get('nombre') or "").strip()
        try:
            contactos = _contactos_desde_form(request.form)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for('editar_cliente', cliente=nombre_actual))
        atencion = [item["nombre"] for item in contactos]
        direccion = (request.form.get('direccion') or '').strip()
        tiempo    = (request.form.get('tiempo') or '').strip()
        anticipo  = (request.form.get('anticipo') or '').strip()
        vigencia  = (request.form.get('vigencia') or '').strip()

        # Datos fiscales opcionales
        rfc            = (request.form.get('rfc') or '').strip().upper()
        razon_social   = (request.form.get('razon_social') or request.form.get('razon') or '').strip()
        cp             = (request.form.get('cp') or '').strip()
        regimen_fiscal = (request.form.get('regimen_fiscal') or '').strip()  # 601, 612, 621, 626
        uso_cfdi       = (request.form.get('uso_cfdi') or '').strip()        # G03, G01, P01
        correo_compras = _correos_por_tipo(contactos, "compras", "ambos")
        correo_cuentas_pagar = _correos_por_tipo(contactos, "cuentas_pagar", "ambos")
        correos_frecuentes = (request.form.get('correos_frecuentes') or '').strip()
        retencion_isr_tasa = (request.form.get('retencion_isr_tasa') or '0').strip()
        retencion_iva_tasa = (request.form.get('retencion_iva_tasa') or '0').strip()

        if not nuevo_nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('editar_cliente', cliente=nombre_actual))

        existe_conflicto = (nuevo_nombre != nombre_actual) and (nuevo_nombre in clientes_predefinidos)
        if existe_conflicto:
            flash(f"Ya existe un cliente llamado '{nuevo_nombre}'. Elige otro nombre.")
            return redirect(url_for('editar_cliente', cliente=nombre_actual))

        # Merge con lo existente para no perder campos previos
        merged = dict(datos)
        # Formularios antiguos no deben desactivar una póliza por omitir el campo.
        if request.form.get('poliza_present') == '1':
            merged['tiene_poliza'] = request.form.get('tiene_poliza') == '1'
        merged.update({
            "atencion": atencion,
            "contactos": contactos,
            "direccion": direccion,
            "tiempo": tiempo,
            "anticipo": anticipo,
            "vigencia": vigencia
        })

        def set_or_pop(obj, key, val):
            if val: obj[key] = val
            else:   obj.pop(key, None)

        # Aplica opcionales solo si vienen
        set_or_pop(merged, "rfc", rfc)
        set_or_pop(merged, "razon_social", razon_social)
        set_or_pop(merged, "cp", cp)
        set_or_pop(merged, "regimen_fiscal", regimen_fiscal)
        set_or_pop(merged, "uso_cfdi", uso_cfdi)
        set_or_pop(merged, "correo_compras", correo_compras)
        set_or_pop(merged, "correo_cuentas_pagar", correo_cuentas_pagar)
        set_or_pop(merged, "correo_facturacion", correo_cuentas_pagar)
        set_or_pop(merged, "correos_frecuentes", correos_frecuentes)
        merged["retencion_isr_tasa"] = retencion_isr_tasa
        merged["retencion_iva_tasa"] = retencion_iva_tasa

        # Guarda y renombra si cambió el nombre
        with _CLIENTES_DATA_LOCK:
            if nuevo_nombre == nombre_actual:
                clientes_predefinidos[nombre_actual] = merged
            else:
                clientes_predefinidos[nuevo_nombre] = merged
                if nombre_actual in clientes_predefinidos:
                    del clientes_predefinidos[nombre_actual]
                datos_cliente['cliente'] = nuevo_nombre

            guardar_clientes(clientes_predefinidos)
        flash("Cliente actualizado correctamente.")
        return redirect(url_for('inicio'))

    datos_vista = dict(datos)
    datos_vista["contactos"] = _normalizar_contactos_cliente(datos)
    return render_template('editar_cliente.html', cliente=nombre_actual, datos=datos_vista)


@app.route('/borrar_cliente', methods=['GET', 'POST'])
def borrar_cliente():
    cliente = (
        request.values.get('cliente')
        or datos_cliente.get('cliente')
        or ""
    ).strip()
    if not cliente:
        return "Primero selecciona un cliente para borrar.", 400

    if request.method == 'POST':
        with _CLIENTES_DATA_LOCK:
            if cliente in clientes_predefinidos:
                del clientes_predefinidos[cliente]
                guardar_clientes(clientes_predefinidos)
                datos_cliente.clear()
                return redirect(url_for('inicio'))
            else:
                return "Cliente no encontrado.", 404

    return render_template('borrar_cliente.html', cliente=cliente)

# ================================== FUNCIONES DE GUARDADO ==================================
def guardar_datos(datos):
    with open('datos.json', 'w', encoding='utf-8') as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)

def guardar_partidas(partidas):
    with open('partidas.json', 'w', encoding='utf-8') as f:
        json.dump(partidas, f, indent=2, ensure_ascii=False)

# ============================ VISTA PREVIA (HTML en navegador) =============================
# ============================ VISTA PREVIA (HTML en navegador) =============================
@app.route('/vista_previa', methods=['GET', 'POST'])
def vista_previa():
    if request.method == 'POST':
        _actualizar_datos_cliente_desde_form()
    datos = dict(datos_cliente)
    partidas_actuales = list(partidas)

    subtotal = sum((p.get('cantidad', 0) or 0) * (p.get('precio', 0.0) or 0.0) for p in partidas_actuales)
    iva = subtotal * 0.16

    tasa_isr, tasa_iva_ret = _tasas_retencion(datos)
    usar_retenciones = bool(tasa_isr or tasa_iva_ret)
    isr_retenido = subtotal * tasa_isr
    iva_retenido = subtotal * tasa_iva_ret

    total = subtotal + iva - isr_retenido - iva_retenido

    img_path = Path("img/logo.png").resolve().as_uri()

    return render_template(
        "plantilla_pdf.html",
        datos=datos,
        partidas=partidas_actuales,
        subtotal=subtotal,
        iva=iva,
        total=total,
        isr_retenido=isr_retenido,
        iva_retenido=iva_retenido,
        tasa_isr=tasa_isr,
        tasa_iva_ret=tasa_iva_ret,
        img_path=img_path,
        preview=True
    )

@app.route('/repositorio')
def repositorio():
    BASE_LOCAL_DRIVE = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
    use_drive = IS_RENDER or (not os.path.isdir(BASE_LOCAL_DRIVE))

    if use_drive:
        try:
            service = _drive_service_cfg()
            estructura = {}

            resp = service.files().list(
                q=f"'{ID_COT}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='files(id,name)',
                pageSize=1000
            ).execute()

            for folder in resp.get('files', []):
                cliente = folder['name']
                fid = folder['id']

                files = service.files().list(
                    q=f"'{fid}' in parents and mimeType='application/pdf' and trashed=false",
                    spaces='drive',
                    fields='files(id,name,webViewLink)',
                    pageSize=1000
                ).execute().get('files', [])

                estructura[cliente] = [
                    {"name": f["name"], "link": f.get("webViewLink")} for f in files
                ]

            return render_template("repositorio.html", estructura=estructura, from_drive=True)

        except Exception as e:
            print("⚠️ No se pudo listar desde Drive en /repositorio:", e)
            return render_template("repositorio.html", estructura={}, from_drive=True)

    # --- Local ---
    estructura = {}
    try:
        for cliente in sorted(os.listdir(BASE_LOCAL_DRIVE)):
            c_path = os.path.join(BASE_LOCAL_DRIVE, cliente)
            if os.path.isdir(c_path):
                pdfs = [a for a in os.listdir(c_path) if a.lower().endswith('.pdf')]
                estructura[cliente] = sorted(pdfs)
    except Exception as e:
        print("⚠️ Error listando en local /repositorio:", e)
        estructura = {}

    return render_template("repositorio.html", estructura=estructura, from_drive=False)

@app.route('/repo/local/<cliente>/<path:filename>')
def repo_local_file(cliente, filename):
    BASE_LOCAL_DRIVE = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
    cliente_seguro = (cliente or "").replace("/", "-").replace("\\", "-").strip()
    base_cliente = os.path.join(BASE_LOCAL_DRIVE, cliente_seguro)

    if not filename.lower().endswith(".pdf"):
        abort(403)

    full_path = safe_join(base_cliente, filename)
    if not full_path or not os.path.isfile(full_path):
        abort(404)

    try:
        return send_file(full_path, mimetype="application/pdf", as_attachment=False, download_name=filename)
    except Exception as e:
        print("⚠️ No se pudo enviar archivo local:", e)
        abort(500)

@app.route('/drive/<cliente>')
def abrir_drive_cliente(cliente):
    def _obtener_o_crear_carpeta(service, nombre, id_padre=None):
        query = f"name='{nombre}' and mimeType='application/vnd.google-apps.folder'"
        if id_padre:
            query += f" and '{id_padre}' in parents"
        res = service.files().list(q=query, spaces='drive', fields='files(id,name)', pageSize=1).execute()
        items = res.get('files', [])
        if items:
            return items[0]['id']
        meta = {'name': nombre, 'mimeType': 'application/vnd.google-apps.folder'}
        if id_padre:
            meta['parents'] = [id_padre]
        folder = service.files().create(body=meta, fields='id').execute()
        return folder['id']

    service = get_drive_service_user()
    id_cot = ID_COT
    canon = (cliente or "").strip().lower()
    res = service.files().list(
        q=f"'{id_cot}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces='drive',
        fields='files(id,name)',
        pageSize=1000
    ).execute()
    id_cliente = None
    for it in res.get('files', []):
        if it['name'].strip().lower() == canon:
            id_cliente = it['id']
            break
    if not id_cliente:
        id_cliente = _obtener_o_crear_carpeta(service, cliente, id_cot)

    url = f"https://drive.google.com/drive/folders/{id_cliente}"
    return redirect(url)

@app.route('/debug/drive')
def debug_drive():
    try:
        service = _drive_service_cfg()
        who = service.about().get(fields="user(emailAddress)").execute().get('user', {}).get('emailAddress')
        folder = service.files().get(fileId=ID_COT, fields="id,name").execute()
        resp = service.files().list(
            q=f"'{ID_COT}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields='files(id,name)',
            pageSize=5
        ).execute()
        hijos = resp.get('files', [])
        return f"""✅ Token de: {who}<br>
        📁 Carpeta madre: {folder.get('name')} ({folder.get('id')})<br>
        👀 Primeras subcarpetas vistas: {len(hijos)}<br>
        {', '.join([h['name'] for h in hijos])}
        """
    except Exception as e:
        return f"❌ Error Drive: {e}", 500

# ====================== NUEVO: snapshot de salud + botones ======================

def _health_snapshot():
    """
    Retorna un dict con el estado de:
    - usuario (OAuth de usuario)
    - service (cuenta de servicio)
    - drive (acceso a carpeta ID_COT con usuario)
    - sheets (lectura de encabezados con service account)
    """
    health = {
        "usuario": {"ok": False, "label": "Desconocido", "hint": "", "needs_reconnect": False},
        "service": {"ok": False, "label": "Desconocido", "hint": ""},
        "drive":   {"ok": False, "label": "Desconocido", "hint": ""},
        "sheets":  {"ok": False, "label": "Desconocido", "hint": ""},
    }

    # Usuario (token.json / TOKEN_JSON_B64)
    try:
        usr = get_drive_service_user(timeout=6)
        who_usr = usr.about().get(fields="user(displayName,emailAddress)").execute().get('user', {})
        who_s = f"{who_usr.get('displayName','')} <{who_usr.get('emailAddress','')}>"
        health["usuario"] = {"ok": True, "label": "OK", "hint": who_s, "needs_reconnect": False}
    except RefreshError as e:
        health["usuario"] = {"ok": False, "label": "Requiere reconectar", "hint": str(e), "needs_reconnect": True}
    except RuntimeError as e:
        health["usuario"] = {"ok": False, "label": "Falta token", "hint": str(e), "needs_reconnect": True}
    except Exception as e:
        health["usuario"] = {"ok": False, "label": "Error", "hint": f"{type(e).__name__}: {e}", "needs_reconnect": False}

    # Service account
    try:
        svc = get_drive_service(timeout=6)
        who_svc = svc.about().get(fields="user(emailAddress)").execute().get('user', {}).get('emailAddress', '')
        health["service"] = {"ok": True, "label": "OK", "hint": who_svc}
    except Exception as e:
        health["service"] = {"ok": False, "label": "Error", "hint": f"{type(e).__name__}: {e}"}

    # Drive (usuario) acceso a carpeta madre
    try:
        usr = get_drive_service_user()
        folder = usr.files().get(fileId=ID_COT, fields="id,name").execute()
        health["drive"] = {"ok": True, "label": "OK", "hint": folder.get("name", "Carpeta")}
    except Exception as e:
        health["drive"] = {"ok": False, "label": "Error", "hint": f"{type(e).__name__}: {e}"}

    # Sheets (service account) lectura de encabezados
    try:
        sh = get_sheets_service()
        res = sh.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z1").execute()
        hdr = res.get("values", [[]])[0]
        health["sheets"] = {"ok": True, "label": "OK", "hint": f"{SHEET_TAB} · {len(hdr)} columnas"}
    except Exception as e:
        health["sheets"] = {"ok": False, "label": "Error", "hint": f"{type(e).__name__}: {e}"}

    return health

@app.route('/health-check')
def health_check():
    """Probar conexiones sin modificar datos (para los semáforos)."""
    try:
        return jsonify(_health_snapshot())
    finally:
        reset_thread_google_services()

# --- Healthcheck para la UI de /inicio-app ---
_HEALTH_CACHE = {"ts": 0.0, "ttl": 60.0, "payload": None}
_HEALTH_CACHE_LOCK = threading.Lock()
_HEALTH_REFRESH_LOCK = threading.Lock()
_HEALTH_PROBE_DEADLINE_SECONDS = 15.0


def _empty_ui_health():
    return {
        "user_ok": False,
        "user_email": None,
        "sa_ok": False,
        "sa_email": None,
        "drive_ok": False,
        "sheets_ok": False,
        "needs_reconnect": False,
    }


def _compute_ui_health():
    """Hace el diagnóstico lento fuera de los hilos que atienden la web."""
    out = _empty_ui_health()
    result_lock = threading.Lock()

    def user_probe():
        try:
            usr = get_drive_service_user(timeout=6)
            who = usr.about().get(fields="user(displayName,emailAddress)").execute().get("user", {})
            with result_lock:
                out.update(user_ok=True, user_email=who.get("emailAddress"))
        except RefreshError:
            with result_lock:
                out["needs_reconnect"] = True

    def service_probe():
        svc = get_drive_service(timeout=6)
        who = svc.about().get(fields="user(emailAddress)").execute().get("user", {})
        with result_lock:
            out.update(sa_ok=True, sa_email=who.get("emailAddress"))

    def drive_probe():
        get_drive_service(timeout=6).files().get(fileId=ID_COT, fields="id").execute()
        with result_lock:
            out["drive_ok"] = True

    def sheets_probe():
        rng = f"{SHEET_TAB}!A1:A1"
        get_sheets_service(timeout=6).spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=rng
        ).execute()
        with result_lock:
            out["sheets_ok"] = True

    def run_probe(probe):
        try:
            probe()
        except Exception:
            pass
        finally:
            reset_thread_google_services()

    threads = [
        threading.Thread(target=run_probe, args=(probe,), daemon=True)
        for probe in (user_probe, service_probe, drive_probe, sheets_probe)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + _HEALTH_PROBE_DEADLINE_SECONDS
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    return out


def _health_refresh_worker(app_obj):
    try:
        with app_obj.app_context():
            payload = _compute_ui_health()
            with _HEALTH_CACHE_LOCK:
                _HEALTH_CACHE.update(ts=time.monotonic(), payload=dict(payload))
    except Exception as exc:
        app_obj.logger.exception("Falló la comprobación de salud de Google: %s", exc)
    finally:
        reset_thread_google_services()
        _HEALTH_REFRESH_LOCK.release()


def _launch_health_refresh(app_obj):
    """Single-flight: nunca ocupa ambos hilos web con el mismo diagnóstico."""
    if not _HEALTH_REFRESH_LOCK.acquire(blocking=False):
        return False
    try:
        threading.Thread(
            target=_health_refresh_worker,
            args=(app_obj,),
            name="google-health-refresh",
            daemon=True,
        ).start()
    except Exception:
        _HEALTH_REFRESH_LOCK.release()
        raise
    return True


@app.get("/health")
def health():
    now = time.monotonic()
    with _HEALTH_CACHE_LOCK:
        cached = dict(_HEALTH_CACHE["payload"]) if _HEALTH_CACHE["payload"] is not None else None
        fresh = cached is not None and now - _HEALTH_CACHE["ts"] < _HEALTH_CACHE["ttl"]
    if fresh:
        cached.update(stale=False, refreshing=False, checking=False)
        return jsonify(cached)

    _launch_health_refresh(current_app._get_current_object())
    refreshing = _HEALTH_REFRESH_LOCK.locked()
    out = cached if cached is not None else _empty_ui_health()
    out.update(stale=cached is not None, refreshing=refreshing, checking=cached is None)
    return jsonify(out)

# === OAuth local-only: renovar token y devolver Base64 listo para Render ===
@app.route('/oauth/renew-local')
def oauth_renew_local():
    # Bloquear en Render (esto es solo para correr en tu PC)
    if IS_RENDER:
        return "⛔ Esta acción solo está disponible en tu PC (no en Render).", 403
    try:
        from auth_google import SCOPES
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(
            prompt="consent",
            access_type="offline",
            include_granted_scopes="true",
            port=0
        )
        token_json_str = creds.to_json()
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(token_json_str)

        import base64
        b64 = base64.b64encode(token_json_str.encode("utf-8")).decode("ascii")
        html = f"""
        <h3>✅ Token renovado localmente</h3>
        <p>Copia este Base64 y pégalo en <b>Render → Environment Variables → TOKEN_JSON_B64</b> (una sola línea):</p>
        <textarea style="width:100%;height:260px" readonly>{b64}</textarea>
        <p>Luego haz: <i>Manual Deploy → Deploy latest commit</i> y valida en <code>/debug/identidades</code>.</p>
        """
        return html
    except Exception as e:
        return f"❌ Error renovando token local: {type(e).__name__}: {e}", 500

# ============================ MAIN (solo local) ============================
@app.route('/inicio-app')
def inicio_app():
    # Los semáforos consultan /health una sola vez desde el navegador.
    return render_template('inicio_app.html', IS_RENDER=IS_RENDER, health=None)


def _render_operations_app(app_kind=None):
    role = _operations_role()
    if app_kind not in {"technician", "partner"}:
        app_kind = "partner" if role == "client" else "auto"
    app_name = "HSC Partner" if app_kind == "partner" else "HSC Técnico"
    manifest_endpoint = "pwa_partner_manifest" if app_kind == "partner" else "pwa_technician_manifest"
    return render_template(
        'app_operativa_demo.html',
        operations_role=role or "technician",
        operations_is_owner=role == "admin",
        operations_app_kind=app_kind,
        operations_app_name=app_name,
        operations_manifest_url=url_for(manifest_endpoint),
    )


@app.route('/app-operativa-demo')
def app_operativa_demo():
    """Vista compatible de la aplicación operativa."""
    return _render_operations_app()


@app.route('/hsc-tecnico')
def hsc_tecnico():
    """Entrada instalable independiente para personal técnico."""
    return _render_operations_app("technician")


@app.route('/hsc-partner')
def hsc_partner():
    """Entrada instalable independiente para clientes y vista Partner del administrador."""
    return _render_operations_app("partner")


_OPERACIONES_MATRIX_CACHE = {"ts": 0.0, "payload": None, "source": None}
_OPERACIONES_MATRIX_CACHE_LOCK = threading.Lock()
_OPERACIONES_MATRIX_TTL = 180.0
_OPERACIONES_MEDIA_REFS = {}


def _prepare_operaciones_payload(payload):
    """Oculta las rutas de Drive y publica sólo URLs internas por ID."""
    media_refs = {}
    for collection, kind in (("clients", "client"), ("equipment", "equipment")):
        for item in payload.get(collection, []):
            photo_ref = str(item.pop("_photo_ref", "") or "").strip()
            if not photo_ref:
                continue
            record_id = str(item.get("id") or "").strip()
            if not record_id:
                continue
            media_refs[(kind, record_id)] = photo_ref
            item["photo_url"] = url_for(
                "api_operaciones_photo", kind=kind, record_id=record_id,
                v=hashlib.sha1(photo_ref.encode("utf-8")).hexdigest()[:10],
            )
    for collection in ("clients", "equipment", "reports"):
        for item in payload.get(collection, []):
            item.pop("_raw", None)
            item.pop("_evidence_refs", None)
    _OPERACIONES_MEDIA_REFS.update(media_refs)
    return payload


def _operations_role():
    role = str(session.get("hsc_role") or "").strip().lower()
    if role in {"admin", "technician", "client"}:
        return role
    # Mantiene como administrador las sesiones del propietario abiertas antes
    # de introducir roles; no concede acceso a una sesión no autenticada.
    return "admin" if session.get("hsc_authenticated") is True else ""


def _operations_forbidden(*allowed):
    role = _operations_role()
    if role in allowed:
        return None
    return jsonify({
        "ok": False, "code": "forbidden", "role": role or None,
        "error": "Tu cuenta no tiene permiso para realizar esta acción.",
    }), 403


def _scope_operaciones_payload(payload):
    """Un cliente sólo recibe los registros vinculados con su propia empresa."""
    if _operations_role() != "client":
        return payload
    client_id = str(session.get("hsc_client_id") or "").strip()
    clients = [item for item in payload.get("clients", []) if item.get("id") == client_id]
    equipment = [item for item in payload.get("equipment", []) if item.get("client_id") == client_id]
    equipment_ids = {item.get("id") for item in equipment}
    reports = [item for item in payload.get("reports", [])
               if item.get("client_id") == client_id or item.get("equipment_id") in equipment_ids]
    scoped = dict(payload)
    scoped.update(clients=clients, equipment=equipment, reports=reports)
    scoped["stats"] = {
        "clients": len(clients), "equipment": len(equipment), "reports": len(reports),
        "client_photos": sum(bool(item.get("has_photo")) for item in clients),
        "equipment_photos": sum(bool(item.get("has_photo")) for item in equipment),
        "evidence_photos": sum(int(item.get("photo_count") or 0) for item in reports),
        "orphan_equipment": 0, "orphan_reports": 0,
        "duplicate_clients": 0, "duplicate_equipment": 0, "duplicate_reports": 0,
    }
    return scoped


@app.get('/api/operaciones/clients/<path:client_id>/reports-folder')
def api_operaciones_client_reports_folder(client_id):
    """Abre la carpeta existente de PDF sin crear ni modificar datos en Drive."""
    forbidden = _operations_forbidden("admin", "technician", "client")
    if forbidden:
        return forbidden
    requested_id = str(client_id or "").strip()
    if _operations_role() == "client":
        assigned_id = str(session.get("hsc_client_id") or "").strip()
        if not assigned_id or requested_id != assigned_id:
            return jsonify({"ok": False, "error": "Este cliente no pertenece a tu cuenta."}), 403
    clients = OPERACIONES_STORE.snapshot().get("clients", []) if OPERACIONES_STORE.enabled else []
    if not clients:
        clients = (_OPERACIONES_MATRIX_CACHE.get("payload") or {}).get("clients", [])
    client = next((item for item in clients if str(item.get("id") or "").strip() == requested_id), None)
    if not client:
        return jsonify({"ok": False, "error": "No se encontró el cliente."}), 404
    try:
        folder_url = find_client_reports_folder_url(client.get("name") or requested_id)
    except Exception as exc:
        current_app.logger.warning("No se pudo localizar la carpeta de reportes de %s: %s", requested_id, exc)
        folder_url = None
    if not folder_url:
        return jsonify({"ok": False, "error": "Este cliente todavía no tiene una carpeta de reportes PDF."}), 404
    return redirect(folder_url)


@app.get('/api/operaciones/bootstrap')
def api_operaciones_bootstrap():
    """Lee primero la base operativa; Sheets sólo alimenta la carga inicial/refresco."""
    now = time.monotonic()
    refresh = request.args.get("refresh") == "1"
    with _OPERACIONES_MATRIX_CACHE_LOCK:
        cached = _OPERACIONES_MATRIX_CACHE["payload"]
        fresh = cached is not None and now - _OPERACIONES_MATRIX_CACHE["ts"] < _OPERACIONES_MATRIX_TTL
        if fresh and not refresh:
            visible = _scope_operaciones_payload(cached)
            return jsonify({
                "ok": True, "read_only": not OPERACIONES_STORE.enabled, "cached": True,
                "source": _OPERACIONES_MATRIX_CACHE.get("source") or "memory", **visible,
            })
        try:
            if OPERACIONES_STORE.enabled and OPERACIONES_STORE.has_data() and not refresh:
                payload = _prepare_operaciones_payload(OPERACIONES_STORE.snapshot())
                _OPERACIONES_MATRIX_CACHE.update(ts=now, payload=payload, source="database")
                visible = _scope_operaciones_payload(payload)
                return jsonify({
                    "ok": True, "read_only": False, "cached": False,
                    "source": "database", **visible,
                })
            payload = read_operaciones_matrix(
                get_sheets_service(timeout=15), SHEET_ID, include_media_refs=True,
                include_raw=OPERACIONES_STORE.enabled,
            )
            if OPERACIONES_STORE.enabled:
                payload = _complete_legacy_operations_relations(payload)
                OPERACIONES_STORE.import_matrix_snapshot(payload)
                payload = OPERACIONES_STORE.snapshot()
            payload = _prepare_operaciones_payload(payload)
            source = "database" if OPERACIONES_STORE.enabled else "sheets"
            _OPERACIONES_MATRIX_CACHE.update(ts=now, payload=payload, source=source)
            visible = _scope_operaciones_payload(payload)
            return jsonify({
                "ok": True, "read_only": not OPERACIONES_STORE.enabled, "cached": False,
                "source": source, **visible,
            })
        except Exception as exc:
            current_app.logger.exception("No se pudo leer la matriz de Operaciones: %s", exc)
            if cached is not None:
                visible = _scope_operaciones_payload(cached)
                return jsonify({
                    "ok": True, "read_only": True, "cached": True, "stale": True,
                    "source": _OPERACIONES_MATRIX_CACHE.get("source") or "memory",
                    "warning": "La matriz no respondió; se muestran los últimos datos disponibles.",
                    **visible,
                })
            return jsonify({
                "ok": False,
                "read_only": True,
                "error": "No se pudo leer la Hoja Matriz. Revisa la conexión de Google.",
            }), 502
        finally:
            reset_thread_google_services()


@app.get('/api/operaciones/storage-status')
def api_operaciones_storage_status():
    """Diagnóstico sin secretos para confirmar la transición a PostgreSQL."""
    denied = _operations_forbidden("admin")
    if denied:
        return denied
    try:
        return jsonify({"ok": True, **OPERACIONES_STORE.status()})
    except Exception as exc:
        current_app.logger.exception("No se pudo consultar la base operativa: %s", exc)
        return jsonify({"ok": False, "enabled": OPERACIONES_STORE.enabled,
                        "error": "La base operativa no está disponible."}), 503


def _operations_migration_checks(stats):
    # Las relaciones huérfanas heredadas se preservan con fichas provisionales.
    # Los IDs duplicados sí son ambiguos y por eso bloquean la migración.
    problem_keys = ("duplicate_clients", "duplicate_equipment", "duplicate_reports")
    problems = {key: int(stats.get(key) or 0) for key in problem_keys if int(stats.get(key) or 0)}
    return {"ready": not problems, "problems": problems}


def _complete_legacy_operations_relations(payload):
    """Conserva filas huérfanas sin adivinar ni perder su relación histórica."""
    clients = payload.setdefault("clients", [])
    equipment = payload.setdefault("equipment", [])
    reports = payload.setdefault("reports", [])
    client_ids = {str(item.get("id") or "").strip() for item in clients}
    equipment_ids = {str(item.get("id") or "").strip() for item in equipment}
    equipment_clients = {
        str(item.get("id") or "").strip(): str(item.get("client_id") or "").strip()
        for item in equipment if str(item.get("id") or "").strip()
    }
    placeholder_clients = []
    placeholder_equipment = []

    required_client_ids = {
        str(item.get("client_id") or "").strip()
        for item in [*equipment, *reports]
        if str(item.get("client_id") or "").strip()
    }
    for client_id in sorted(required_client_ids - client_ids):
        clients.append({
            "id": client_id,
            "name": f"Cliente pendiente de vincular ({client_id})",
            "address": "", "selected_round": "", "policy_active": False,
            "has_photo": False,
            "_raw": {"ID_Cliente": client_id, "Migracion": "Registro provisional"},
        })
        placeholder_clients.append(client_id)
        client_ids.add(client_id)

    unassigned_client_id = "LEGACY_UNASSIGNED"
    for report in reports:
        equipment_id = str(report.get("equipment_id") or "").strip()
        if equipment_id in equipment_clients and not str(report.get("client_id") or "").strip():
            report["client_id"] = equipment_clients[equipment_id]
        if not equipment_id or equipment_id in equipment_ids:
            continue
        client_id = str(report.get("client_id") or "").strip()
        if not client_id:
            client_id = unassigned_client_id
            report["client_id"] = client_id
        if client_id not in client_ids:
            clients.append({
                "id": client_id,
                "name": f"Cliente pendiente de vincular ({client_id})",
                "address": "", "selected_round": "", "policy_active": False,
                "has_photo": False,
                "_raw": {"ID_Cliente": client_id, "Migracion": "Registro provisional"},
            })
            placeholder_clients.append(client_id)
            client_ids.add(client_id)
        equipment.append({
            "id": equipment_id, "client_id": client_id,
            "name": f"Equipo pendiente de vincular ({equipment_id})",
            "brand": "", "model": "", "serial": "", "status": "Inactivo",
            "location": "", "department": "", "equipment_type": "", "notes": "",
            "has_photo": False,
            "_raw": {"ID_Equipo": equipment_id, "Migracion": "Registro provisional"},
        })
        placeholder_equipment.append(equipment_id)
        equipment_ids.add(equipment_id)

    stats = payload.setdefault("stats", {})
    stats.update(
        clients=len(clients), equipment=len(equipment), reports=len(reports),
        orphan_equipment=0, orphan_reports=0,
        legacy_placeholder_clients=len(placeholder_clients),
        legacy_placeholder_equipment=len(placeholder_equipment),
    )
    payload["legacy_placeholders"] = {
        "clients": placeholder_clients, "equipment": placeholder_equipment,
    }
    return payload


@app.get('/api/operaciones/migration-preview')
def api_operaciones_migration_preview():
    """Cuenta toda la matriz sin modificar la base."""
    denied = _operations_forbidden("admin")
    if denied:
        return denied
    try:
        payload = read_operaciones_matrix(
            get_sheets_service(timeout=20), SHEET_ID,
            include_media_refs=True, include_raw=True,
        )
        return jsonify({
            "ok": True, "source": "sheets", "stats": payload.get("stats") or {},
            **_operations_migration_checks(payload.get("stats") or {}),
        })
    except Exception as exc:
        current_app.logger.exception("No se pudo revisar la migración operativa: %s", exc)
        return jsonify({"ok": False, "error": "No se pudo revisar la Hoja Matriz."}), 502
    finally:
        reset_thread_google_services()


@app.post('/api/operaciones/migrate')
def api_operaciones_migrate():
    """Importación completa, transaccional y verificable de la Hoja Matriz."""
    denied = _operations_forbidden("admin")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({
            "ok": False, "code": "storage_not_configured",
            "error": "Conecta la base operativa antes de iniciar la migración.",
        }), 503
    try:
        source = read_operaciones_matrix(
            get_sheets_service(timeout=30), SHEET_ID,
            include_media_refs=True, include_raw=True,
        )
        checks = _operations_migration_checks(source.get("stats") or {})
        if not checks["ready"]:
            return jsonify({
                "ok": False, "code": "matrix_integrity",
                "error": "La matriz tiene IDs duplicados; no se modificó la base.",
                "stats": source.get("stats") or {}, **checks,
            }), 409
        source = _complete_legacy_operations_relations(source)
        imported = OPERACIONES_STORE.import_matrix_snapshot(source)
        stored = OPERACIONES_STORE.snapshot()
        source_stats = source.get("stats") or {}
        stored_stats = stored.get("stats") or {}
        verify_keys = ("clients", "equipment", "reports", "evidence_photos")
        verification = {
            key: {"source": int(source_stats.get(key) or 0), "database": int(stored_stats.get(key) or 0)}
            for key in verify_keys
        }
        complete = all(item["source"] == item["database"] for item in verification.values())
        if complete:
            _invalidate_operations_cache()
            current_app.logger.info(
                "Migracion operativa completa: clientes=%s equipos=%s reportes=%s evidencias=%s provisionales=%s",
                stored_stats.get("clients"), stored_stats.get("equipment"),
                stored_stats.get("reports"), stored_stats.get("evidence_photos"),
                source.get("legacy_placeholders"),
            )
        return jsonify({
            "ok": complete, "complete": complete, "imported": imported,
            "verification": verification,
            "legacy_placeholders": source.get("legacy_placeholders") or {},
            "error": None if complete else "Los conteos no coinciden; la migración requiere revisión.",
        }), 200 if complete else 409
    except Exception as exc:
        current_app.logger.exception("No se pudo migrar la matriz operativa: %s", exc)
        return jsonify({"ok": False, "error": "No se pudo completar la migración."}), 500
    finally:
        reset_thread_google_services()


def _invalidate_operations_cache():
    with _OPERACIONES_MATRIX_CACHE_LOCK:
        _OPERACIONES_MATRIX_CACHE.update(ts=0.0, payload=None, source=None)


@app.post('/api/operaciones/clients')
def api_operaciones_save_client():
    """Guarda en la base; la escritura a Sheets queda en la cola de salida."""
    denied = _operations_forbidden("admin")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({
            "ok": False, "code": "storage_not_configured",
            "error": "La base operativa todavía no está conectada en Render.",
        }), 503
    payload = request.get_json(silent=True) or {}
    try:
        client = OPERACIONES_STORE.save_client(payload)
        _invalidate_operations_cache()
        return jsonify({"ok": True, "client": client, "sync_status": "pending"})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception("No se pudo guardar el cliente operativo: %s", exc)
        return jsonify({"ok": False, "error": "No se pudo guardar el cliente."}), 500


@app.post('/api/operaciones/clients/order')
def api_operaciones_reorder_clients():
    denied = _operations_forbidden("admin")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({"ok": False, "error": "La base operativa todavía no está conectada."}), 503
    try:
        ordered = OPERACIONES_STORE.reorder_clients((request.get_json(silent=True) or {}).get("client_ids"))
        _invalidate_operations_cache()
        return jsonify({"ok": True, "client_ids": ordered})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception("No se pudo ordenar clientes operativos: %s", exc)
        return jsonify({"ok": False, "error": "No se pudo guardar el orden de clientes."}), 500


@app.post('/api/operaciones/equipment')
def api_operaciones_save_equipment():
    """Guarda un lote de equipos de forma transaccional."""
    denied = _operations_forbidden("admin")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({
            "ok": False, "code": "storage_not_configured",
            "error": "La base operativa todavía no está conectada en Render.",
        }), 503
    payload = request.get_json(silent=True) or {}
    try:
        equipment = OPERACIONES_STORE.save_equipment(payload.get("items"))
        _invalidate_operations_cache()
        return jsonify({"ok": True, "equipment": equipment, "sync_status": "pending"})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception("No se pudieron guardar los equipos operativos: %s", exc)
        return jsonify({"ok": False, "error": "No se pudieron guardar los equipos."}), 500


@app.get('/api/operaciones/reports/draft')
def api_operaciones_get_report_draft():
    denied = _operations_forbidden("admin", "technician")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({"ok": True, "configured": False, "draft": None})
    try:
        draft = OPERACIONES_STORE.get_report_draft(
            request.args.get("equipment_id"), request.args.get("round")
        )
        return jsonify({"ok": True, "configured": True, "draft": draft})
    except Exception as exc:
        current_app.logger.exception("No se pudo leer el borrador operativo: %s", exc)
        return jsonify({"ok": False, "error": "No se pudo recuperar el borrador."}), 500


@app.get('/api/operaciones/reports/<path:report_id>')
def api_operaciones_report_detail(report_id):
    denied = _operations_forbidden("admin", "technician", "client")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({"ok": False, "error": "La base operativa todavía no está conectada."}), 503
    try:
        report = OPERACIONES_STORE.get_report_detail(report_id)
        if not report:
            abort(404)
        if _operations_role() == "client" and report.get("client_id") != str(session.get("hsc_client_id") or "").strip():
            abort(404)
        for evidence in report.get("evidence", []):
            evidence["url"] = url_for(
                "api_operaciones_report_evidence", report_id=report_id,
                position=evidence["position"],
            )
            evidence.pop("storage_ref", None)
            evidence.pop("drive_ref", None)
        return jsonify({"ok": True, "report": report})
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            raise
        current_app.logger.exception("No se pudo consultar reporte operativo %s: %s", report_id, exc)
        return jsonify({"ok": False, "error": "No se pudo cargar el reporte."}), 500


@app.post('/api/operaciones/reports/draft')
def api_operaciones_save_report_draft():
    denied = _operations_forbidden("admin", "technician")
    if denied:
        return denied
    if not OPERACIONES_STORE.enabled:
        return jsonify({
            "ok": False, "code": "storage_not_configured",
            "error": "La base operativa todavía no está conectada en Render.",
        }), 503
    try:
        draft = OPERACIONES_STORE.save_report_draft(request.get_json(silent=True) or {})
        _invalidate_operations_cache()
        return jsonify({"ok": True, "draft": draft, "sync_status": "local_only"})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception("No se pudo guardar el borrador operativo: %s", exc)
        return jsonify({"ok": False, "error": "No se pudo guardar el borrador."}), 500


def _serve_operations_thumbnail(kind, record_id, photo_ref):
    cached = None
    if OPERACIONES_STORE.enabled:
        try:
            cached = OPERACIONES_STORE.get_cached_thumbnail(kind, record_id, photo_ref)
        except Exception as exc:
            current_app.logger.warning("No se pudo consultar miniatura %s/%s: %s", kind, record_id, exc)
    if cached:
        response = send_file(io.BytesIO(cached["content"]), mimetype=cached["mime_type"])
        response.headers["X-HSC-Thumbnail"] = "cache"
    else:
        original_response = make_response(serve_drive_image_ref_fast(photo_ref))
        original_response.direct_passthrough = False
        original_bytes = original_response.get_data()
        try:
            from PIL import Image, ImageOps
            with Image.open(io.BytesIO(original_bytes)) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((360, 360), Image.Resampling.LANCZOS)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=78, method=4)
                thumbnail = output.getvalue()
                width, height = image.size
            # No guardar la imagen transparente de respaldo cuando Drive falló.
            if width > 1 and height > 1 and OPERACIONES_STORE.enabled:
                try:
                    OPERACIONES_STORE.save_cached_thumbnail(
                        kind, record_id, photo_ref, thumbnail, "image/webp", width, height
                    )
                except Exception as exc:
                    current_app.logger.warning("No se pudo guardar miniatura %s/%s: %s", kind, record_id, exc)
            response = send_file(io.BytesIO(thumbnail), mimetype="image/webp")
            response.headers["X-HSC-Thumbnail"] = "generated"
        except Exception as exc:
            current_app.logger.warning("No se pudo optimizar foto %s/%s: %s", kind, record_id, exc)
            response = original_response
            response.headers["X-HSC-Thumbnail"] = "original"
    response.headers["Cache-Control"] = "private, max-age=2592000, immutable"
    return response


@app.get('/api/operaciones/photo/<kind>/<path:record_id>')
def api_operaciones_photo(kind, record_id):
    """Sirve una miniatura persistente sin exponer la referencia de Drive."""
    if kind not in {"client", "equipment"}:
        abort(404)
    if _operations_role() == "client":
        client_id = str(session.get("hsc_client_id") or "").strip()
        if kind == "client":
            allowed = record_id == client_id
        else:
            cached = _OPERACIONES_MATRIX_CACHE.get("payload") or {}
            allowed = any(
                item.get("id") == record_id and item.get("client_id") == client_id
                for item in cached.get("equipment", [])
            )
        if not allowed:
            abort(404)
    photo_ref = _OPERACIONES_MEDIA_REFS.get((kind, record_id))
    if not photo_ref and OPERACIONES_STORE.enabled:
        photo_ref = OPERACIONES_STORE.get_media_ref(kind, record_id)
    if not photo_ref:
        abort(404)
    return _serve_operations_thumbnail(kind, record_id, photo_ref)


@app.get('/api/operaciones/reports/<path:report_id>/evidence/<int:position>')
def api_operaciones_report_evidence(report_id, position):
    denied = _operations_forbidden("admin", "technician", "client")
    if denied:
        return denied
    evidence = OPERACIONES_STORE.get_report_evidence_ref(report_id, position) if OPERACIONES_STORE.enabled else None
    if not evidence or not evidence.get("photo_ref"):
        abort(404)
    if _operations_role() == "client" and evidence.get("client_id") != str(session.get("hsc_client_id") or "").strip():
        abort(404)
    return _serve_operations_thumbnail("evidence", f"{report_id}:{position}", evidence["photo_ref"])

# --- Healthcheck muy ligero para Render ---
@app.route("/healthz")
def healthz():
    return "ok", 200

# Estado folios (debug rápido)
@app.route("/folios/status")
def folios_status():
    val_sheets = _get_ultimo_folio_sheets()
    val_local = None
    try:
        with open("folios.json", "r", encoding="utf-8") as f:
            val_local = json.load(f).get("ultimo_folio")
    except Exception:
        pass

    return {
        "sheets_B3": val_sheets,
        "folios_json": val_local
    }

# API para el panel "Generados recientes"
@app.route("/api/ultimos-pdfs")
def api_ultimos_pdfs():
    """Devuelve los últimos N registros de HistorialPDF (más reciente primero), con filtro opcional por tipo."""
    try:
        limit = max(1, min(int(request.args.get("limit", 5)), 50))
    except:
        limit = 5
    tipo_req = (request.args.get("tipo") or "").strip().lower()

    try:
        resp = _sheets_values_get_all(f"{HIST_TAB}!A2:F")  # incluir columna F
        vals = resp.get("values", [])
        tail = vals[-limit*3:] if len(vals) > limit*3 else vals  # buffer extra por filtro
        items = []
        for row in tail[::-1]:
            ts, cliente, folio, archivo_url, carpeta_url, tipo = (row + ["", "", "", "", "", ""])[:6]
            tipo = (tipo or "").lower()
            # Filtrado
            if tipo_req:
                if not tipo and tipo_req != "cotizacion":
                    continue
                if tipo and tipo != tipo_req:
                    continue
            items.append({
                "timestamp": ts,
                "cliente": cliente,
                "folio": folio,
                "archivo_url": archivo_url,
                "carpeta_url": carpeta_url,
                "tipo": tipo or "cotizacion"
            })
            if len(items) >= limit:
                break
        return jsonify({"ok": True, "count": len(items), "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "items": []}), 500


def _pick_first(*vals):
    for v in vals:
        if isinstance(v, (list, dict)) and len(v) > 0:
            return v
    return {}

def _get_clientes_from_modules():
    import sys as _sys
    candidatos_mod = ("facturacion_bp", "facturacion")
    nombres = (
        "clientes_predefinidos",
        "CLIENTES_PREDEFINIDOS",
        "clientes_sync",
        "clientes_cache",
        "CLIENTES_CACHE",
    )
    for modname in candidatos_mod:
        mod = _sys.modules.get(modname)
        if not mod:
            continue
        for nombre in nombres:
            if hasattr(mod, nombre):
                data = getattr(mod, nombre)
                if isinstance(data, (list, dict)) and len(data) > 0:
                    print(f">> ui_factura_nueva: tomado de {modname}.{nombre} items="
                          f"{len(data) if isinstance(data, list) else len(data.keys())}")
                    return data
    return {}

def _get_clientes_from_config():
    cfg = current_app.config
    claves = (
        "clientes_predefinidos",
        "CLIENTES_PREDEFINIDOS",
        "clientes_sync",
        "clientes_cache",
        "CLIENTES_CACHE",
    )
    for k in claves:
        if k in cfg and isinstance(cfg[k], (list, dict)) and len(cfg[k]) > 0:
            print(f">> ui_factura_nueva: tomado de config[{k}] items="
                  f"{len(cfg[k]) if isinstance(cfg[k], list) else len(cfg[k].keys())}")
            return cfg[k]
    return {}

@app.get("/facturas/nueva")
def ui_factura_nueva():
    base = Path(current_app.root_path)

    # La memoria sincronizada con clientes.json de Drive es la fuente vigente.
    # Los archivos alternos quedan únicamente como respaldo de arranque.
    global clientes_predefinidos
    with _CLIENTES_DATA_LOCK:
        clientes = dict(clientes_predefinidos or {})
    for p in (
        base / "clientes.json",
        base / "data" / "clientes.json",
        base / "static" / "data" / "clientes.json",
    ) if not clientes else ():
        try:
            if p.exists() and p.stat().st_size > 2:
                txt = p.read_text("utf-8")
                tmp = json.loads(txt)
                if isinstance(tmp, (dict, list)) and len(tmp) > 0:
                    clientes = tmp
                    print(f">> ui_factura_nueva: leído {p}")
                    break
        except Exception as e:
            print(">> error leyendo", p, e)

    # Seguridad de tipo
    if not isinstance(clientes, (dict, list)):
        clientes = {}

    facturama_sandbox = os.environ.get("FACTURAMA_SANDBOX", "true").strip().lower() not in {"0", "false", "no"}
    return render_template(
        "factura_nueva.html",
        clientes=clientes,
        facturama_environment="sandbox" if facturama_sandbox else "production",
    )


def _normalizar_articulo(raw, codigo_forzado=""):
    codigo = str(codigo_forzado or raw.get("codigo") or "").strip().upper()
    nombre = str(raw.get("nombre") or "").strip()
    descripcion = str(raw.get("descripcion") or nombre).strip()
    categoria = str(raw.get("categoria") or "General").strip()
    clave_sat = str(raw.get("clave_sat") or "").strip()
    unidad_sat = str(raw.get("unidad_sat") or "E48").strip().upper()
    try:
        precio = round(float(raw.get("precio") or 0), 2)
        iva = float(raw.get("iva") if raw.get("iva") not in (None, "") else 0.16)
    except (TypeError, ValueError):
        raise ValueError("Precio o IVA inválido")
    if not codigo or len(codigo) > 50:
        raise ValueError("El código interno es obligatorio y admite hasta 50 caracteres")
    if len(nombre) < 2 or len(nombre) > 80:
        raise ValueError("El nombre debe tener entre 2 y 80 caracteres")
    if not re.fullmatch(r"\d{8}", clave_sat):
        raise ValueError("La clave SAT debe tener 8 dígitos")
    if not re.fullmatch(r"[A-Z0-9]{2,5}", unidad_sat):
        raise ValueError("La unidad SAT no tiene un formato válido")
    if precio < 0 or iva not in (0, 0.08, 0.16):
        raise ValueError("Usa un precio positivo e IVA de 0%, 8% o 16%")
    activo_raw = raw.get("activo", True)
    activo = activo_raw if isinstance(activo_raw, bool) else str(activo_raw).lower() not in ("0", "false", "no")
    return {
        "codigo": codigo,
        "nombre": nombre,
        "descripcion": descripcion,
        "categoria": categoria,
        "clave_sat": clave_sat,
        "unidad_sat": unidad_sat,
        "precio": precio,
        "iva": iva,
        "activo": activo,
        "actualizado": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/articulos-servicios")
def ui_articulos_servicios():
    return render_template("articulos_servicios.html")


@app.get("/api/articulos")
def api_articulos_list():
    with _ARTICULOS_DATA_LOCK:
        items = _leer_articulos_locales()
        if not items or request.args.get("actualizar") == "1":
            remotos = descargar_articulos_de_drive()
            if remotos is not None:
                items = remotos
    incluir_inactivos = request.args.get("todos") == "1"
    texto = (request.args.get("q") or "").strip().casefold()
    if not incluir_inactivos:
        items = [item for item in items if item.get("activo", True)]
    if texto:
        items = [item for item in items if texto in " ".join(str(item.get(k, "")) for k in (
            "codigo", "nombre", "descripcion", "categoria", "clave_sat"
        )).casefold()]
    items.sort(key=lambda item: (not item.get("activo", True), item.get("categoria", ""), item.get("nombre", "")))
    return jsonify({"ok": True, "items": items}), 200


@app.post("/api/articulos")
def api_articulos_guardar():
    try:
        articulo = _normalizar_articulo(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _ARTICULOS_DATA_LOCK:
        items = _leer_articulos_locales()
        if not items:
            items = descargar_articulos_de_drive() or []
        if any(str(item.get("codigo", "")).upper() == articulo["codigo"] for item in items):
            return jsonify({"ok": False, "error": "Ese código interno ya existe"}), 409
        items.append(articulo)
        _escribir_articulos_locales(items)
        respaldo = subir_articulos_a_drive(items)
    if IS_RENDER and not respaldo:
        with _ARTICULOS_DATA_LOCK:
            _escribir_articulos_locales(items[:-1])
        return jsonify({"ok": False, "error": "Drive no confirmó el respaldo. No se guardó el artículo para evitar una copia temporal engañosa"}), 503
    return jsonify({"ok": True, "item": articulo}), 201


@app.put("/api/articulos/<path:codigo>")
def api_articulos_actualizar(codigo):
    try:
        articulo = _normalizar_articulo(request.get_json(silent=True) or {}, codigo_forzado=codigo)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _ARTICULOS_DATA_LOCK:
        items = _leer_articulos_locales()
        if not items:
            items = descargar_articulos_de_drive() or []
        indice = next((i for i, item in enumerate(items) if str(item.get("codigo", "")).upper() == articulo["codigo"]), None)
        if indice is None:
            return jsonify({"ok": False, "error": "Artículo no encontrado"}), 404
        anterior = items[indice]
        items[indice] = articulo
        _escribir_articulos_locales(items)
        respaldo = subir_articulos_a_drive(items)
    if IS_RENDER and not respaldo:
        with _ARTICULOS_DATA_LOCK:
            items[indice] = anterior
            _escribir_articulos_locales(items)
        return jsonify({"ok": False, "error": "No se confirmó el respaldo en Drive"}), 503
    return jsonify({"ok": True, "item": articulo}), 200


@app.get("/pagos/nuevo")
def ui_pago_complemento():
    return render_template("pago_complemento.html")
@app.get("/facturacion")
def ui_facturacion_inicio():
    return render_template("facturas_inicio.html")
@app.post("/set_cliente")
def set_cliente():
    data = request.get_json(silent=True) or {}
    nombre = (data.get("cliente") or "").strip()
    if not nombre:
        return ("falta 'cliente'", 400)
    canonical, _ = _resolver_cliente_catalogo(nombre)
    datos_cliente["cliente"] = canonical or nombre
    return ("", 204)


def _contactos_cliente_seleccionado(nombre="", rfc=""):
    _, datos = _resolver_cliente_catalogo(nombre, rfc)
    if isinstance(datos, dict):
            legacy = str(datos.get("correo_facturacion") or datos.get("email") or "").strip()
            historial = datos.get("correos_historial") or []
            historial = [item if isinstance(item, dict) else {"email": str(item)} for item in historial]
            historial = sorted(
                [item for item in historial if str(item.get("email") or "").strip()],
                key=lambda item: (int(item.get("usos") or 0), str(item.get("ultimo_uso") or "")),
                reverse=True,
            )[:20]
            return {
                "compras": str(datos.get("correo_compras") or "").strip(),
                "cuentas_pagar": str(datos.get("correo_cuentas_pagar") or legacy).strip(),
                "frecuentes": str(datos.get("correos_frecuentes") or "").strip(),
                "contactos": _normalizar_contactos_cliente(datos),
                "historial": historial,
            }
    return {"compras": "", "cuentas_pagar": "", "frecuentes": "", "contactos": [], "historial": []}


@app.post("/api/clientes/contactos-seleccionado")
def api_contactos_cliente_seleccionado():
    if not authorized_to_send("", request.cookies.get("hsc_mail_trusted", "")):
        return jsonify(ok=False, error="Este navegador todavía no está autorizado para consultar los contactos."), 403
    payload = request.get_json(silent=True) or {}
    correos = _contactos_cliente_seleccionado(payload.get("nombre"), payload.get("rfc"))
    return jsonify(ok=True, correos=correos), 200


@app.post("/api/clientes/correos-historial")
def api_historial_correos_cliente():
    if not authorized_to_send("", request.cookies.get("hsc_mail_trusted", "")):
        return jsonify(ok=False, error="Este navegador todavía no está autorizado."), 403
    payload = request.get_json(silent=True) or {}
    nombre_key = str(payload.get("nombre") or "").strip().casefold()
    rfc_key = str(payload.get("rfc") or "").strip().upper()
    action = str(payload.get("action") or "record").strip().lower()
    try:
        emails = parse_recipients(payload.get("emails"))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    with _CLIENTES_DATA_LOCK:
        _, selected = _resolver_cliente_catalogo(payload.get("nombre"), payload.get("rfc"))
        if selected is None:
            return jsonify(ok=False, error="No se encontró el cliente para guardar su historial."), 404

        records = selected.get("correos_historial") or []
        by_email = {}
        for item in records:
            item = item if isinstance(item, dict) else {"email": str(item), "usos": 1}
            email = str(item.get("email") or "").strip()
            if email:
                by_email[email.casefold()] = {
                    "email": email,
                    "usos": max(1, int(item.get("usos") or 1)),
                    "ultimo_uso": str(item.get("ultimo_uso") or ""),
                }
        if action == "delete":
            for email in emails:
                by_email.pop(email.casefold(), None)
        else:
            now = datetime.now().isoformat(timespec="seconds")
            for email in emails:
                record = by_email.get(email.casefold(), {"email": email, "usos": 0, "ultimo_uso": ""})
                record["usos"] += 1
                record["ultimo_uso"] = now
                by_email[email.casefold()] = record
        selected["correos_historial"] = sorted(
            by_email.values(), key=lambda item: (item["usos"], item["ultimo_uso"]), reverse=True
        )[:20]
        guardar_clientes(clientes_predefinidos)
        historial = list(selected["correos_historial"])
    return jsonify(ok=True, historial=historial), 200

# ---------- NUEVO: listado para inicio_cotizacion ----------
@app.get("/api/cotizaciones/list")
def api_cotizaciones_list():
    path = _ruta_cotizaciones()
    items = []
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if isinstance(data.get("items"), list): items = data["items"]
                elif isinstance(data.get("data"), list): items = data["data"]
        except Exception:
            pass
    deliveries = read_email_deliveries()
    enriched = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or item.get("folio") or item.get("numero") or "").strip()
        enriched.append({**item, "email_delivery": delivery_status("cotizacion", qid, deliveries)})
    return jsonify(enriched), 200


def _buscar_cotizacion_registrada(qid):
    objetivo = str(qid or "").strip()
    path = _ruta_cotizaciones()
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else []
    except Exception:
        data = []
    if isinstance(data, dict):
        data = data.get("items") or data.get("data") or []
    for item in data if isinstance(data, list) else []:
        valores = (item.get("id"), item.get("folio"), item.get("numero"), item.get("uuid"))
        if objetivo in {str(valor or "").strip() for valor in valores}:
            return item
    return None


def _drive_file_id(url):
    value = str(url or "")
    for pattern in (r"/d/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)"):
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return ""


def _pdf_cotizacion_bytes(cotizacion):
    cliente_raw = str(cotizacion.get("cliente") or "SIN_CLIENTE").strip()
    canonical_name, _ = _resolver_cliente_catalogo(
        cliente_raw, (cotizacion.get("receptor") or {}).get("rfc")
    )
    cliente = canonical_name or cliente_raw
    folio = str(cotizacion.get("folio") or cotizacion.get("id") or "S-F").strip()
    cliente_seguro = cliente.replace("/", "-").replace("\\", "-")
    nombre = f"{cliente} - {folio}.pdf"
    for candidate in (
        Path(current_app.root_path) / "cotizaciones" / cliente_seguro / nombre,
        Path(current_app.root_path) / "static" / "cotizaciones" / cliente_seguro / nombre,
    ):
        if candidate.is_file():
            return candidate.read_bytes()

    file_id = _drive_file_id(cotizacion.get("view_url") or cotizacion.get("pdf_url"))
    if file_id:
        service = get_drive_service_user()
        request_drive = service.files().get_media(fileId=file_id)
        stream = io.BytesIO()
        downloader = MediaIoBaseDownload(stream, request_drive)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        if stream.getbuffer().nbytes:
            return stream.getvalue()
    raise FileNotFoundError("No se encontró el PDF de esta cotización en el respaldo local ni en Google Drive.")


def _correos_cotizacion(cotizacion):
    nombre = str(cotizacion.get("cliente") or "").strip()
    receptor = cotizacion.get("receptor") or {}
    correos = _contactos_cliente_seleccionado(nombre, receptor.get("rfc"))
    fallback = str(receptor.get("correo_facturacion") or receptor.get("email") or "").strip()
    if not correos["compras"]:
        correos["compras"] = fallback or correos["cuentas_pagar"]
    if not correos["cuentas_pagar"]:
        correos["cuentas_pagar"] = fallback
    return correos


def _atencion_cotizacion(cotizacion):
    datos = cotizacion.get("datos") or {}
    value = datos.get("atencion") or cotizacion.get("atencion") or []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _correo_individual_preferido(correos, atenciones=None, tipo="compras"):
    contactos = correos.get("contactos") or []
    buscados = {str(value).strip().casefold() for value in (atenciones or []) if str(value).strip()}
    elegibles = [
        item for item in contactos
        if item.get("correo") and item.get("tipo") in {tipo, "ambos"}
    ]
    coincidencia = next(
        (item for item in elegibles if str(item.get("nombre") or "").strip().casefold() in buscados),
        None,
    )
    if coincidencia:
        return coincidencia["correo"]
    if elegibles:
        return elegibles[0]["correo"]
    cualquier_contacto = next((item for item in contactos if item.get("correo")), None)
    if cualquier_contacto:
        return cualquier_contacto["correo"]
    try:
        legacy = parse_recipients(correos.get(tipo) or "")
    except ValueError:
        legacy = []
    return legacy[0] if legacy else ""


@app.route("/api/cotizaciones/<qid>/documentos", methods=["GET", "POST", "DELETE"])
def api_documentos_pendientes_cotizacion(qid):
    cotizacion = _buscar_cotizacion_registrada(qid)
    if not cotizacion:
        return jsonify(ok=False, error="No se encontró la cotización."), 404
    cliente = str(cotizacion.get("cliente") or "SIN_CLIENTE").strip()
    try:
        if request.method == "GET":
            items = list_pending_documents(cliente, qid)
            return jsonify(ok=True, documents=[{
                "id": item.get("id"), "name": item.get("name"),
                "size": int(item.get("size") or 0),
                "category": (item.get("appProperties") or {}).get("hscCategory") or "otro",
                "order_number": (item.get("appProperties") or {}).get("hscOrderNumber") or "",
            } for item in items]), 200
        if request.method == "DELETE":
            payload = request.get_json(silent=True) or {}
            delete_pending_document(cliente, qid, payload.get("file_id"))
            return jsonify(ok=True), 200

        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify(ok=False, error="Selecciona un archivo."), 400
        filename = secure_filename(upload.filename)
        allowed = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".xls", ".xlsx", ".csv"}
        if not filename or Path(filename).suffix.lower() not in allowed:
            return jsonify(ok=False, error="El formato del archivo no está permitido."), 400
        content = upload.read()
        if len(content) > 15 * 1024 * 1024:
            return jsonify(ok=False, error="El archivo supera el límite de 15 MB."), 400
        result = save_pending_document(
            cliente, qid, upload.filename, content,
            upload.mimetype or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream",
            category=request.form.get("category") or "otro",
            order_number=request.form.get("order_number") or "",
        )
        return jsonify(ok=True, document={"id": result.get("id"), "name": result.get("name")}), 201
    except FileNotFoundError as exc:
        return jsonify(ok=False, error=str(exc)), 404
    except Exception:
        current_app.logger.exception("No se pudo administrar el expediente de la cotización %s", qid)
        return jsonify(ok=False, error="Drive no pudo guardar el documento. Intenta nuevamente."), 502


@app.route("/api/cotizaciones/<qid>/email", methods=["GET", "POST"])
def api_enviar_cotizacion(qid):
    cotizacion = _buscar_cotizacion_registrada(qid)
    if not cotizacion:
        return jsonify(ok=False, error="No se encontró la cotización."), 404

    cfg = smtp_config()
    trusted = authorized_to_send("", request.cookies.get("hsc_mail_trusted", ""))
    if request.method == "GET":
        correos = _correos_cotizacion(cotizacion)
        atenciones = _atencion_cotizacion(cotizacion)
        return jsonify(
            ok=True,
            configured=bool(cfg.get("configured")),
            trusted=trusted,
            id=str(cotizacion.get("id") or cotizacion.get("folio") or qid),
            cliente=str(cotizacion.get("cliente") or ""),
            rfc=str((cotizacion.get("receptor") or {}).get("rfc") or ""),
            folio=str(cotizacion.get("folio") or cotizacion.get("id") or ""),
            email=_correo_individual_preferido(correos, atenciones, "compras"),
            atencion=atenciones[0] if atenciones else "",
            correos=correos,
        )

    if not cfg.get("configured"):
        return jsonify(ok=False, error="Falta configurar el correo de salida de HSC en Render."), 503
    send_key = request.form.get("send_key", "")
    if not authorized_to_send(send_key, request.cookies.get("hsc_mail_trusted", "")):
        return jsonify(ok=False, error="La clave de envío no es correcta."), 403

    extras = []
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".xls", ".xlsx", ".csv"}
    files = [upload for upload in request.files.getlist("attachments") if upload and upload.filename]
    if len(files) > 8:
        return jsonify(ok=False, error="Puedes adjuntar como máximo 8 archivos adicionales."), 400
    total_size = 0
    for upload in files:
        filename = secure_filename(upload.filename)
        suffix = Path(filename).suffix.lower()
        if not filename or suffix not in allowed:
            return jsonify(ok=False, error=f"El archivo {upload.filename} no tiene un formato permitido."), 400
        content = upload.read()
        total_size += len(content)
        if total_size > 15 * 1024 * 1024:
            return jsonify(ok=False, error="Los archivos adicionales superan el límite total de 15 MB."), 400
        extras.append({
            "data": content,
            "filename": filename,
            "content_type": upload.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        })

    try:
        folio = str(cotizacion.get("folio") or cotizacion.get("id") or "").strip()
        default_message = (
            "Buen día, estimado cliente. Envío la cotización solicitada.\n\n"
            "De antemano muchas gracias.\n\n"
            "Quedo a sus órdenes.\n\n"
            "Ing. Héctor Silva Cid\n\n"
            "Cel: 5527605496"
        )
        result = send_quote_email(
            recipient=request.form.get("email", ""),
            cc=request.form.get("cc", ""),
            subject=request.form.get("subject") or f"Cotización HSC {folio}",
            body=request.form.get("message") or default_message,
            pdf_bytes=_pdf_cotizacion_bytes(cotizacion),
            folio=folio,
            extra_attachments=extras,
        )
        delivery = record_email_delivery(
            "cotizacion", str(cotizacion.get("id") or folio),
            recipients=result.get("recipients") or [], cc=result.get("cc") or [],
            client_name=str(cotizacion.get("cliente") or ""), folio=folio,
        )
        copy_warning = result.get("sent_copy_saved") is False
        response = jsonify({
            "ok": True,
            "message": (
                f"Cotización enviada a {result.get('recipient')}."
                + (" CarrierZone no confirmó la copia en Enviados." if copy_warning else "")
            ),
            "sent_copy_saved": not copy_warning,
            "email_delivery": delivery,
            "result": result,
        })
        token = trusted_device_token()
        if token:
            response.set_cookie(
                "hsc_mail_trusted", token, max_age=315360000, secure=True,
                httponly=True, samesite="Strict", path="/api",
            )
        return response, 200
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except FileNotFoundError as exc:
        return jsonify(ok=False, error=str(exc)), 404
    except smtplib.SMTPAuthenticationError:
        return jsonify(ok=False, error="CarrierZone rechazó el usuario o la contraseña del correo."), 502
    except Exception:
        current_app.logger.exception("No se pudo enviar la cotización %s", qid)
        return jsonify(ok=False, error="No se pudo enviar el correo. La cotización permanece disponible para reintentar."), 502


def _combinar_receptor_cotizacion(cotizacion, cliente_actual):
    """La ficha fiscal vigente prevalece sobre una cotización histórica."""
    rec_raw = cotizacion.get("receptor") or {}
    current = cliente_actual if isinstance(cliente_actual, dict) else {}
    return {
        "rfc": str(current.get("rfc") or rec_raw.get("rfc") or cotizacion.get("rfc") or "").upper(),
        "nombre": (
            current.get("razon_social") or current.get("razon") or current.get("legal_name")
            or rec_raw.get("razon_social") or rec_raw.get("nombre")
            or cotizacion.get("cliente") or ""
        ),
        "cp": (
            current.get("cp") or current.get("codigo_postal") or current.get("zip")
            or rec_raw.get("cp") or rec_raw.get("codigo_postal") or rec_raw.get("zip") or ""
        ),
        "regimen_fiscal": (
            current.get("regimen_fiscal") or current.get("regimen")
            or rec_raw.get("regimen_fiscal") or rec_raw.get("regimen") or ""
        ),
        "uso_cfdi": current.get("uso_cfdi") or rec_raw.get("uso_cfdi") or "",
        "email": (
            current.get("correo_cuentas_pagar") or current.get("correo_facturacion") or current.get("email")
            or rec_raw.get("correo_facturacion") or rec_raw.get("email") or ""
        ),
    }

@app.get("/api/cotizaciones/<qid>")
def api_cotizacion_detalle(qid):
    """
    Devuelve una cotización normalizada para prefilling:
    { ok, id, cliente, fecha, folio, total, receptor{rfc,nombre,cp,regimen_fiscal,uso_cfdi}, items[] }
    Lee de data/cotizaciones.json (o data/quotes.json) y soporta varios formatos.
    """
    base = Path(current_app.root_path) / "data"
    candidatos = ["cotizaciones.json", "quotes.json"]
    items = []

    for nombre in candidatos:
        p = base / nombre
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                if isinstance(data, list):
                    items.extend(data)
                elif isinstance(data, dict):
                    if isinstance(data.get("items"), list):
                        items.extend(data["items"])
                    if isinstance(data.get("data"), list):
                        items.extend(data["data"])
            except Exception:
                pass

    if not items:
        return jsonify(ok=False, error={"message": "No hay cotizaciones en data/."}), 404

    def coincide(x):
        vals = [str(x.get(k, "")) for k in ("id", "folio", "numero", "uuid")]
        return str(qid) in vals

    match = next((x for x in items if coincide(x)), None)
    if not match:
        return jsonify(ok=False, error={"message": "Cotización no encontrada"}), 404

    client_name = str(match.get("cliente") or "").strip()
    canonical_name, resolved_client = _resolver_cliente_catalogo(
        client_name, (match.get("receptor") or {}).get("rfc")
    )
    current_client = dict(resolved_client or {})
    receptor = _combinar_receptor_cotizacion(match, current_client)

    detalle = match.get("conceptos") or match.get("items") or match.get("detalles") or match.get("partidas") or []
    items_norm = []
    for c in (detalle if isinstance(detalle, list) else []):
        try:
            cantidad = float(c.get("cantidad") or c.get("qty") or 1)
        except Exception:
            cantidad = 1.0
        try:
            precio = float(c.get("precio_unitario") or c.get("valor_unitario") or c.get("price") or 0)
        except Exception:
            precio = 0.0
        try:
            tasa = float(c.get("tasa_iva") or c.get("iva") or c.get("tax_rate") or 0)
        except Exception:
            tasa = 0.0

        items_norm.append({
            "descripcion": c.get("descripcion") or c.get("desc") or c.get("nombre") or "Concepto",
            "cantidad": cantidad,
            "precio_unitario": precio,
            "clave_prod_serv": c.get("clave_prod_serv") or c.get("clave") or c.get("cps") or "85121600",
            "clave_unidad": c.get("clave_unidad") or c.get("unidad") or "E48",
            "tasa_iva": tasa,
            "descuento": float(c.get("descuento") or c.get("discount") or 0),
        })

    total = match.get("total") or match.get("importe_total")
    if total is None:
        total = 0.0
        for c in items_norm:
            base_imp = max(0, c["cantidad"] * c["precio_unitario"] - c["descuento"])
            total += base_imp + base_imp * c["tasa_iva"]

    datos_cotizacion = match.get("datos") if isinstance(match.get("datos"), dict) else {}
    tasa_isr, tasa_iva_ret = _tasas_retencion(datos_cotizacion)
    usar_retenciones = bool(tasa_isr or tasa_iva_ret)

    out = {
        "ok": True,
        "id": match.get("id") or match.get("folio") or match.get("numero") or match.get("uuid") or str(qid),
        "cliente": canonical_name or match.get("cliente") or "",
        "fecha": match.get("fecha") or match.get("created_at") or "",
        "folio": match.get("folio") or match.get("numero") or "",
        "total": total,
        "receptor": receptor,
        "items": items_norm,
        "retenciones": {
            "aplicar": usar_retenciones,
            "isr": tasa_isr,
            "iva": tasa_iva_ret,
        },
    }
    return jsonify(out), 200

def _cargar_cotizacion_para_editar(qid, conservar_folio=False):
    path = _ruta_cotizaciones()
    try:
        historial = json.loads(path.read_text("utf-8")) if path.exists() else []
    except Exception:
        historial = []
    if not isinstance(historial, list):
        historial = historial.get("items", []) if isinstance(historial, dict) else []

    original = next((q for q in historial if str(q.get("id") or q.get("folio") or "") == str(qid)), None)
    if not original:
        return False

    conceptos = original.get("conceptos") or original.get("items") or original.get("partidas") or []
    nuevas_partidas = []
    for concepto in conceptos if isinstance(conceptos, list) else []:
        try:
            cantidad = float(concepto.get("cantidad") or concepto.get("qty") or 1)
            precio = float(concepto.get("precio_unitario") or concepto.get("precio") or concepto.get("price") or 0)
        except (TypeError, ValueError):
            continue
        nuevas_partidas.append({
            "descripcion": concepto.get("descripcion") or concepto.get("desc") or "Concepto",
            "cantidad": int(cantidad) if cantidad.is_integer() else cantidad,
            "precio": precio,
            "total": cantidad * precio,
        })

    partidas.clear()
    partidas.extend(nuevas_partidas)
    datos_cliente.clear()
    _reiniciar_costos_internos()
    datos_guardados = original.get("datos") if isinstance(original.get("datos"), dict) else {}
    datos_cliente.update(datos_guardados)
    datos_cliente.update({
        "cliente": original.get("cliente") or (original.get("receptor") or {}).get("nombre") or datos_guardados.get("cliente") or "",
        "fecha": datos_guardados.get("fecha") or date.today().isoformat(),
        "cotizacion": str(original.get("folio") or original.get("id") or qid) if conservar_folio else "",
        "nombre_borrador": "",
    })
    datos_cliente.setdefault("comentarios", "")
    datos_cliente.setdefault("usar_retenciones", False)
    return True


@app.get("/cotizaciones/<qid>/duplicar")
def duplicar_cotizacion(qid):
    """Carga una cotización histórica como una nueva, sin modificar el original."""
    if not _cargar_cotizacion_para_editar(qid, conservar_folio=False):
        flash("No se encontró la cotización que deseas duplicar.")
        return redirect(url_for("ui_inicio_cotizacion"))
    flash(
        f"Cotización {qid} duplicada. El folio nuevo se asignará al guardar el borrador "
        "o generar el PDF."
    )
    return redirect(url_for("inicio"))


@app.get("/cotizaciones/<qid>/editar")
def editar_cotizacion_mismo_folio(qid):
    """Carga una cotización para corregirla y reemplazarla conservando el folio."""
    if not _cargar_cotizacion_para_editar(qid, conservar_folio=True):
        flash("No se encontró la cotización que deseas corregir.")
        return redirect(url_for("ui_inicio_cotizacion"))
    flash(
        f"Editando la cotización {qid}. Al generar el PDF se reemplazará el registro "
        "y el archivo de este mismo folio."
    )
    return redirect(url_for("inicio"))


@app.post("/cotizaciones/<qid>/eliminar")
def eliminar_cotizacion(qid):
    encontrada, guardada = _eliminar_cotizacion_por_id(qid)
    if encontrada and guardada:
        flash(f"Cotización {qid} retirada del listado. Su PDF permanece en Google Drive.")
    elif encontrada:
        flash("Google Drive no confirmó el cambio; la cotización no se eliminó del listado.")
    else:
        flash("No se encontró la cotización que deseas eliminar.")
    return redirect(url_for("ui_inicio_cotizacion"))

@app.get("/cotizaciones")
@app.get("/inicio-cotizacion")
def ui_inicio_cotizacion():
    return render_template("inicio_cotizacion.html")
from flask import redirect, url_for

@app.get("/cotizador")
def ui_cotizador_alias():
    # Alias que apunta al mismo formulario que usas hoy
    return redirect(url_for('inicio'))
@app.get("/cotizaciones/<qid>")
def ui_cotizacion_detalle(qid):
    """Detalle legible de una cotización para ver datos sin PDF."""
    base = Path(current_app.root_path) / "data"
    path_opts = [base / "cotizaciones.json", base / "quotes.json"]
    items = []
    for p in path_opts:
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    if isinstance(data.get("items"), list): items = data["items"]
                    elif isinstance(data.get("data"), list): items = data["data"]
                break
            except Exception:
                pass

    def match_id(x):
        vals = [str(x.get(k, "")) for k in ("id","folio","numero","uuid")]
        return str(qid) in vals

    q = next((x for x in items if match_id(x)), None)
    if not q:
        return render_template("cotizacion_detalle.html", q=None), 404

    # Normaliza
    conceptos = q.get("conceptos") or q.get("items") or q.get("detalles") or q.get("partidas") or []
    def _tot():
        if q.get("total") is not None: return q["total"]
        tot = 0.0
        for c in conceptos if isinstance(conceptos, list) else []:
            cant = float(c.get("cantidad") or c.get("qty") or 1)
            pu   = float(c.get("precio_unitario") or c.get("valor_unitario") or c.get("price") or 0)
            iva  = float(c.get("tasa_iva") or c.get("iva") or 0)
            base = cant*pu
            tot += base + base*iva
        return round(tot, 2)

    out = {
        "id": q.get("id") or q.get("folio") or q.get("numero") or q.get("uuid") or str(qid),
        "cliente": q.get("cliente") or (q.get("receptor") or {}).get("nombre") or "",
        "fecha": q.get("fecha") or q.get("created_at") or "",
        "folio": q.get("folio") or q.get("numero") or "",
        "total": _tot(),
        "conceptos": conceptos if isinstance(conceptos, list) else [],
        "view_url": q.get("view_url") or q.get("pdf_url") or "",
    }
    return render_template("cotizacion_detalle.html", q=out), 200
# --- Vista de clientes (lista simple) ---
@app.get("/clientes")
def ui_clientes():
    # Tomar una fotografía estable: la sincronización de Drive puede reemplazar
    # o actualizar el catálogo mientras Jinja lo convierte a JSON.
    try:
        with _CLIENTES_DATA_LOCK:
            data = dict(clientes_predefinidos or {})
    except NameError:
        data = {}

    # En Render la página puede abrir antes de que termine la sincronización
    # inicial. Si todavía no hay datos, obtenerlos aquí evita una lista vacía.
    if not data and (IS_RENDER or AUTO_SYNC_FROM_DRIVE):
        try:
            _sync_clientes_from_drive_into_memory()
            with _CLIENTES_DATA_LOCK:
                data = dict(clientes_predefinidos or {})
        except Exception as exc:
            app.logger.warning("No se pudieron actualizar clientes al abrir el panel: %s", exc)

    # Fallback al archivo local si no hay nada en memoria
    if not data:
        base = Path(current_app.root_path) / "data"
        path = base / "clientes.json"
        if path.exists():
            try:
                data = json.loads(path.read_text("utf-8"))
            except Exception:
                data = {}

    return render_template("clientes_inicio.html", clientes=data)


@app.route("/clientes/importar", methods=["GET", "POST"])
def importar_clientes():
    with _CLIENTES_DATA_LOCK:
        existentes = dict(clientes_predefinidos or {})

    if request.method == "GET":
        return render_template("importar_clientes.html", clientes=existentes, preview=[])

    action = (request.form.get("action") or "preview").strip()
    if action == "preview":
        upload = request.files.get("archivo")
        if not upload or not upload.filename:
            flash("Selecciona el archivo Excel exportado desde Konta.")
            return redirect(url_for("importar_clientes"))
        if request.content_length and request.content_length > 6 * 1024 * 1024:
            flash("El archivo es demasiado grande. El límite es 5 MB.")
            return redirect(url_for("importar_clientes"))
        try:
            rows = _leer_clientes_konta(upload)
            preview = _plan_importacion_clientes(rows, existentes)
        except (ValueError, RuntimeError) as exc:
            flash(str(exc))
            return redirect(url_for("importar_clientes"))
        except Exception as exc:
            app.logger.warning("No se pudo leer la exportación de clientes: %s", exc)
            flash("No pude leer ese archivo. Vuelve a exportarlo desde Konta en formato Excel.")
            return redirect(url_for("importar_clientes"))
        return render_template("importar_clientes.html", clientes=existentes, preview=preview)

    try:
        rows = json.loads(request.form.get("rows_json") or "[]")
    except json.JSONDecodeError:
        rows = []
    if not isinstance(rows, list) or not rows or len(rows) > 5000:
        flash("La vista previa venció o no contiene clientes válidos.")
        return redirect(url_for("importar_clientes"))

    created = updated = skipped = 0
    with _CLIENTES_DATA_LOCK:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                skipped += 1
                continue
            legal_name = str(row.get("nombre_legal") or "").strip()
            rfc = str(row.get("rfc") or "").strip().upper()
            target = (request.form.get(f"destino_{index}") or "__skip__").strip()
            if not legal_name or not rfc or target == "__skip__":
                skipped += 1
                continue
            if target == "__new__":
                target = legal_name
                if target in clientes_predefinidos:
                    suffix = 2
                    while f"{legal_name} ({suffix})" in clientes_predefinidos:
                        suffix += 1
                    target = f"{legal_name} ({suffix})"
                clientes_predefinidos[target] = {
                    "atencion": [], "direccion": "", "tiempo": "",
                    "anticipo": "", "vigencia": "",
                    "retencion_isr_tasa": "0", "retencion_iva_tasa": "0",
                }
                created += 1
            elif target not in clientes_predefinidos:
                skipped += 1
                continue
            else:
                updated += 1

            client = clientes_predefinidos[target]
            client["rfc"] = rfc
            if not str(client.get("razon_social") or "").strip():
                client["razon_social"] = legal_name

        if created or updated:
            guardar_clientes(clientes_predefinidos)

    flash(f"Importación terminada: {created} nuevos, {updated} actualizados y {skipped} omitidos.")
    return redirect(url_for("ui_clientes"))


@app.post("/api/clientes/fiscales")
def guardar_datos_fiscales_cliente():
    payload = request.get_json(silent=True) or {}
    nombre = str(payload.get("cliente") or "").strip()
    if not nombre:
        return jsonify({"ok": False, "error": "Selecciona un cliente para guardar sus datos."}), 400

    with _CLIENTES_DATA_LOCK:
        canonical_name, client = _resolver_cliente_catalogo(nombre, payload.get("rfc"))
        if not canonical_name or client is None:
            return jsonify({"ok": False, "error": "El cliente ya no existe."}), 404
        correo_facturacion = str(payload.get("correo_facturacion") or "").strip()
        values = {
            "rfc": str(payload.get("rfc") or "").strip().upper(),
            "razon_social": str(payload.get("razon_social") or "").strip().upper(),
            "cp": str(payload.get("cp") or "").strip(),
            "regimen_fiscal": str(payload.get("regimen_fiscal") or "").strip(),
            "uso_cfdi": str(payload.get("uso_cfdi") or "").strip(),
            "correo_facturacion": correo_facturacion,
            "correo_cuentas_pagar": correo_facturacion,
            "retencion_isr_tasa": str(payload.get("retencion_isr_tasa") or "0").strip(),
            "retencion_iva_tasa": str(payload.get("retencion_iva_tasa") or "0").strip(),
        }
        for key, value in values.items():
            if value or key in {"retencion_isr_tasa", "retencion_iva_tasa"}:
                client[key] = value
        guardar_clientes(clientes_predefinidos)
    return jsonify({"ok": True, "cliente": canonical_name})






def _start_initial_drive_sync():
    """En Render carga datos al fondo sin retrasar el arranque web."""
    global __bootstrap_sync_last_attempt
    if not IS_RENDER or not __bootstrap_sync_lock.acquire(blocking=False):
        return
    __bootstrap_sync_last_attempt = time.monotonic()
    try:
        threading.Thread(
            target=_bootstrap_sync_worker,
            args=(app,),
            name="google-bootstrap-sync",
            daemon=True,
        ).start()
    except Exception:
        __bootstrap_sync_lock.release()
        raise


# La sincronización de clientes/cotizaciones se inicia de forma diferida desde
# sus propias páginas. No debe competir con el arranque web ni con reportes.


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
