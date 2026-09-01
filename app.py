# app.py
from flask import Flask, render_template, request, redirect, url_for, make_response, flash, send_file, abort, jsonify, current_app

app = Flask(__name__)
@app.get("/ping_root")
def ping_root():
    return "pong", 200



from markupsafe import escape
from datetime import date, datetime
import json
import os
import platform
import threading
import time
import unicodedata
from urllib.parse import quote_plus
from pathlib import Path
import io
import math
import uuid

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
from werkzeug.utils import safe_join
from reportes_bp import reportes_bp, start_auto_report_monitor

from facturacion_bp import facturacion_bp
app.register_blueprint(facturacion_bp)
print(">>> Blueprint facturacion registrado")
print(app.url_map)

from pagos_bp import pagos_bp
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

# Protegen las escrituras tipo read/modify/write. En Render hay dos hilos web y
# una sincronización de arranque en segundo plano; ninguno debe pisar al otro.
_CLIENTES_DATA_LOCK = threading.RLock()
_COTIZACIONES_DATA_LOCK = threading.RLock()
_BORRADORES_DATA_LOCK = threading.RLock()
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
    except Exception as e:
        print("⚠️ No se pudo subir cotizaciones.json a Drive:", e)


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

clientes_predefinidos = cargar_clientes()

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
_QUOTE_MUTATION_ENDPOINTS = {"generar_pdf"}
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

# ================================= Rutas =======================================
@app.route('/')
def inicio():
    subtotal = sum(p['total'] for p in partidas)
    iva = subtotal * 0.16

    usar_retenciones = bool(datos_cliente.get("usar_retenciones"))
    if usar_retenciones:
        isr_retenido = subtotal * 0.0125
        iva_retenido = iva * (2/3)
    else:
        isr_retenido = 0
        iva_retenido = 0

    total = subtotal + iva - isr_retenido - iva_retenido

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
        total=total,
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
    datos_cliente["usar_retenciones"] = ("usar_retenciones" in request.form)

@app.route('/agregar', methods=['POST'])
def agregar():
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

@app.route('/nuevo_cliente', methods=['GET', 'POST'])
def nuevo_cliente():
    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        atencion = [a.strip() for a in (request.form.get('atencion') or '').split(',') if a.strip()]
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

        if not nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('nuevo_cliente'))

        with _CLIENTES_DATA_LOCK:
            clientes_predefinidos[nombre] = {
                "atencion": atencion,
                "direccion": direccion,
                "tiempo": tiempo,
                "anticipo": anticipo,
                "vigencia": vigencia
            }

            # Solo guarda si vienen
            if rfc:            clientes_predefinidos[nombre]["rfc"] = rfc
            if razon_social:   clientes_predefinidos[nombre]["razon_social"] = razon_social
            if cp:             clientes_predefinidos[nombre]["cp"] = cp
            if regimen_fiscal: clientes_predefinidos[nombre]["regimen_fiscal"] = regimen_fiscal
            if uso_cfdi:       clientes_predefinidos[nombre]["uso_cfdi"] = uso_cfdi

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
        base = Path(current_app.root_path) / "data"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "cotizaciones.json"

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
    if datos_cliente.get("usar_retenciones"):
        total_borrador = subtotal + iva - (subtotal * 0.0125) - (iva * (2 / 3))
    else:
        total_borrador = subtotal + iva
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

@app.route('/generar_pdf')
def generar_pdf():
    import shutil
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

    usar_retenciones = bool(datos.get("usar_retenciones"))
    if usar_retenciones:
        isr_retenido = subtotal * 0.0125
        iva_retenido = iva * (2/3)
    else:
        isr_retenido = 0.0
        iva_retenido = 0.0

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
                "clave_unidad": "E48"
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
            "total": round(float(subtotal + iva), 2),
            "view_url": archivo_url,           # ← AÑADIDO
            "receptor": rec,
            "conceptos": conceptos
        })
        print(f"🗂️ Cotización registrada para listado: {cot} ({cliente})")
    except Exception as e:
        print("⚠️ No se pudo registrar la cotización en data/cotizaciones.json:", e)

    # Si este folio venía de un borrador, el PDF ya lo convirtió en cotización terminada.
    try:
        _eliminar_borrador_por_id(cot)
    except Exception as e:
        print("⚠️ No se pudo retirar el borrador ya finalizado:", e)

    mensaje = f"Cotización {cot} - {cliente}\nArchivo: {archivo_url}"
    wa_url = f"https://wa.me/?text={quote_plus(mensaje)}"
    mailto_url = f"mailto:?subject={quote_plus(f'Cotización {cot} - {cliente}')}&body={quote_plus(mensaje)}"

    return f"""PDF generado y guardado en:<br>{ruta_pdf}<br><br>
📄 <a href='{archivo_url}' target='_blank'>Abrir PDF en Drive</a><br>
📂 <a href='{carpeta_url}' target='_blank'>Abrir carpeta en Drive</a><br><br>
📱 <a href='{wa_url}' target='_blank'>Compartir por WhatsApp</a> &nbsp;|&nbsp;
✉️ <a href='{mailto_url}'>Enviar por Email</a><br><br>
<a href='/'>← Volver a esta cotización</a> &nbsp;|&nbsp;
<a href='{url_for("nueva_cotizacion")}'>＋ Generar nueva cotización</a>"""

@app.route('/editar_cliente', methods=['GET', 'POST'])
def editar_cliente():
    if not datos_cliente.get('cliente'):
        flash("Primero selecciona un cliente en Inicio para poder editarlo.")
        return redirect(url_for('inicio'))

    nombre_actual = (datos_cliente.get('cliente') or "").strip()
    datos = clientes_predefinidos.get(nombre_actual, {
        "atencion": [],
        "direccion": "",
        "tiempo": "",
        "anticipo": "",
        "vigencia": ""
    })

    if request.method == 'POST':
        nuevo_nombre = (request.form.get('nombre') or "").strip()
        atencion = [a.strip() for a in (request.form.get('atencion') or "").split(',') if a.strip()]
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

        if not nuevo_nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('editar_cliente'))

        existe_conflicto = (nuevo_nombre != nombre_actual) and (nuevo_nombre in clientes_predefinidos)
        if existe_conflicto:
            flash(f"Ya existe un cliente llamado '{nuevo_nombre}'. Elige otro nombre.")
            return redirect(url_for('editar_cliente'))

        # Merge con lo existente para no perder campos previos
        merged = dict(datos)
        merged.update({
            "atencion": atencion,
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

    return render_template('editar_cliente.html', cliente=nombre_actual, datos=datos)


@app.route('/borrar_cliente', methods=['GET', 'POST'])
def borrar_cliente():
    if not datos_cliente.get('cliente'):
        return "Primero selecciona un cliente para borrar.", 400

    cliente = datos_cliente['cliente']

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
@app.route('/vista_previa')
def vista_previa():
    datos = dict(datos_cliente)
    partidas_actuales = list(partidas)

    subtotal = sum((p.get('cantidad', 0) or 0) * (p.get('precio', 0.0) or 0.0) for p in partidas_actuales)
    iva = subtotal * 0.16

    usar_retenciones = bool(datos.get("usar_retenciones"))
    if usar_retenciones:
        isr_retenido = subtotal * 0.0125
        iva_retenido = iva * (2/3)
    else:
        isr_retenido = 0.0
        iva_retenido = 0.0

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

    # 1) intenta leer archivo local
    clientes = {}
    for p in (
        base / "data" / "clientes.json",
        base / "static" / "data" / "clientes.json",
        Path.cwd() / "data" / "clientes.json",
    ):
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

    # 2) fallback: usar la variable global
    if not clientes:
        global clientes_predefinidos
        if clientes_predefinidos:
            clientes = clientes_predefinidos
            print(">> ui_factura_nueva: usando clientes_predefinidos global",
                  f"items={len(clientes)}")

    # 3) seguridad de tipo
    if not isinstance(clientes, (dict, list)):
        clientes = {}

    return render_template("factura_nueva.html", clientes=clientes)


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
    datos_cliente["cliente"] = nombre  # ya usas esta variable en editar_cliente
    return ("", 204)

# ---------- NUEVO: listado para inicio_cotizacion ----------
@app.get("/api/cotizaciones/list")
def api_cotizaciones_list():
    base = Path(current_app.root_path) / "data"
    path = base / "cotizaciones.json"
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
    return jsonify(items), 200

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

    rec_raw = match.get("receptor") or {}
    receptor = {
        "rfc": (rec_raw.get("rfc") or match.get("rfc") or "").upper(),
        "nombre": rec_raw.get("nombre") or rec_raw.get("razon_social") or match.get("cliente") or "",
        "cp": rec_raw.get("cp") or rec_raw.get("codigo_postal") or rec_raw.get("zip") or "",
        "regimen_fiscal": rec_raw.get("regimen_fiscal") or rec_raw.get("regimen") or "",
        "uso_cfdi": rec_raw.get("uso_cfdi") or ""
    }

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
            "tasa_iva": tasa
        })

    total = match.get("total") or match.get("importe_total")
    if total is None:
        total = 0.0
        for c in items_norm:
            base_imp = c["cantidad"] * c["precio_unitario"]
            total += base_imp + base_imp * c["tasa_iva"]

    out = {
        "ok": True,
        "id": match.get("id") or match.get("folio") or match.get("numero") or match.get("uuid") or str(qid),
        "cliente": match.get("cliente") or "",
        "fecha": match.get("fecha") or match.get("created_at") or "",
        "folio": match.get("folio") or match.get("numero") or "",
        "total": total,
        "receptor": receptor,
        "items": items_norm
    }
    return jsonify(out), 200

@app.get("/cotizaciones/<qid>/duplicar")
def duplicar_cotizacion(qid):
    """Carga una cotización histórica como una nueva, sin modificar el original."""
    path = Path(current_app.root_path) / "data" / "cotizaciones.json"
    try:
        historial = json.loads(path.read_text("utf-8")) if path.exists() else []
    except Exception:
        historial = []
    if not isinstance(historial, list):
        historial = historial.get("items", []) if isinstance(historial, dict) else []

    original = next((q for q in historial if str(q.get("id") or q.get("folio") or "") == str(qid)), None)
    if not original:
        flash("No se encontró la cotización que deseas duplicar.")
        return redirect(url_for("ui_inicio_cotizacion"))

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
    datos_cliente.update({
        "cliente": original.get("cliente") or (original.get("receptor") or {}).get("nombre") or "",
        "fecha": date.today().isoformat(),
        "cotizacion": "",
        "comentarios": "",
        "usar_retenciones": False,
    })
    flash(
        f"Cotización {qid} duplicada. El folio nuevo se asignará al guardar el borrador "
        "o generar el PDF."
    )
    return redirect(url_for("inicio"))

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
    # Preferir el diccionario en memoria sincronizado desde Drive
    try:
        data = clientes_predefinidos or {}
    except NameError:
        data = {}

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
