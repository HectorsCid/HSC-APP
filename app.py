# app.py
from flask import Flask, render_template, request, redirect, url_for, make_response, flash, send_file, abort, jsonify

from markupsafe import escape
from datetime import date, datetime
from weasyprint import HTML
import json
import os
import platform
from urllib.parse import quote_plus
from pathlib import Path
import io

# Google / OAuth
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload
from auth_google import get_drive_service, get_sheets_service, get_drive_service_user

# NEW: para detectar RefreshError con claridad
from google.auth.exceptions import RefreshError

# Otros
from werkzeug.utils import safe_join
from reportes_bp import reportes_bp

# --- Google Drive scopes y constantes ---
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

ID_COT = '1oCf8Mt2nLynS6d2ryCngNyQ7rtf5jfiz'   # Carpeta "01. Cotizaciones" en Drive
CLIENTES_FILENAME = 'clientes.json'           # Archivo para persistir clientes en Drive

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

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "superclave")
app.register_blueprint(reportes_bp)

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
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, CLIENTES_FILENAME, ID_COT)
        if not fid:
            print("ℹ️ clientes.json no encontrado en Drive; usando vacío.")
            return {}
        request = service.files().get_media(fileId=fid)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        content = fh.read().decode('utf-8')
        data = json.loads(content)
        with open('clientes.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("✅ clientes.json cargado desde Drive.")
        return data
    except Exception as e:
        print("⚠️ No se pudo descargar clientes.json de Drive:", e)
        return {}

def subir_clientes_a_drive(clientes_dict):
    try:
        service = _drive_service_cfg()
        fid = _drive_buscar_archivo(service, CLIENTES_FILENAME, ID_COT)
        payload = json.dumps(clientes_dict, ensure_ascii=False, indent=2).encode('utf-8')
        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype='application/json', resumable=False)
        if fid:
            updated = service.files().update(fileId=fid, media_body=media, fields='id').execute()
            print("♻️ clientes.json actualizado en Drive:", updated.get('id'))
        else:
            meta = {'name': CLIENTES_FILENAME, 'parents': [ID_COT]}
            created = service.files().create(body=meta, media_body=media, fields='id').execute()
            print("📤 clientes.json creado en Drive:", created.get('id'))
    except Exception as e:
        print("⚠️ No se pudo subir clientes.json a Drive:", e)

# ======================= Funciones para clientes (con persistencia en Drive) ======================
def cargar_clientes():
    if IS_RENDER:
        data = descargar_clientes_de_drive()
        return data or {}

    if os.path.exists("clientes.json"):
        try:
            with open("clientes.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception as e:
            print("⚠️ clientes.json local ilegible:", e)

    data = descargar_clientes_de_drive()
    return data or {}

def guardar_clientes(clientes):
    try:
        with open("clientes.json", "w", encoding="utf-8") as f:
            json.dump(clientes, f, indent=2, ensure_ascii=False)
        print("💾 clientes.json guardado localmente.")
    except Exception as e:
        print("⚠️ No se pudo guardar clientes.json local:", e)

    subir_clientes_a_drive(clientes)

clientes_predefinidos = cargar_clientes()

def _sync_clientes_from_drive_into_memory():
    data = descargar_clientes_de_drive()
    if data is not None:
        try:
            clientes_predefinidos.clear()
            clientes_predefinidos.update(data)
            print("🔄 clientes_predefinidos sincronizado desde Drive (startup).")
        except Exception as e:
            print("⚠️ No se pudo actualizar clientes_predefinidos:", e)

# ---------- Reemplazo de before_first_request (Flask 3.x) ----------
__did_sync_once = False

@app.before_request
def _bootstrap_sync_clientes():
    global __did_sync_once, clientes_predefinidos
    # Si no se ha sincronizado exitosamente o la memoria está vacía → intenta cargar
    need_sync = (not __did_sync_once) or (not clientes_predefinidos)
    if need_sync and (IS_RENDER or AUTO_SYNC_FROM_DRIVE):
        try:
            _sync_clientes_from_drive_into_memory()  # esto deja clientes_predefinidos poblado si todo va bien
            if clientes_predefinidos:   # ✅ solo marcamos done si hay datos
                __did_sync_once = True
                print(f"🔄 clientes_predefinidos cargados: {len(clientes_predefinidos)}")
            else:
                print("⚠️ Sync intentada pero sin datos; se volverá a intentar en el siguiente request.")
        except Exception as e:
            print("❌ Error sincronizando clientes:", e)
            # No marcamos __did_sync_once; reintentará en el próximo request

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

# =========================== Variables de trabajo ==============================
partidas = []
datos_cliente = {}

# ================================= Rutas =======================================
@app.route('/')
def inicio():
    if not datos_cliente.get('cotizacion'):
        datos_cliente['cotizacion'] = obtener_siguiente_folio()

    subtotal = sum(p['total'] for p in partidas)
    iva = subtotal * 0.16
    total = subtotal + iva
    return render_template('inicio.html',
                           partidas=partidas,
                           datos=datos_cliente,
                           clientes=clientes_predefinidos,
                           subtotal=subtotal,
                           iva=iva,
                           total=total,
                           today=date.today().isoformat())

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
    datos_cliente['cliente'] = request.form.get('cliente')
    datos_cliente['atencion'] = request.form.getlist('atencion')
    datos_cliente['direccion'] = request.form.get('direccion', '')
    datos_cliente['fecha'] = request.form.get('fecha', '')
    datos_cliente['anticipo'] = request.form.get('anticipo', '')
    datos_cliente['tiempo'] = request.form.get('tiempo', '')
    datos_cliente['vigencia'] = request.form.get('vigencia', '')
    datos_cliente['cotizacion'] = request.form.get('cotizacion', '')
    datos_cliente['comentarios'] = request.form.get('comentarios', '')
    return redirect(url_for('inicio'))

@app.route('/agregar', methods=['POST'])
def agregar():
    descripcion = request.form['descripcion']
    try:
        cantidad = int(request.form['cantidad'])
        precio = float(request.form['precio'])
    except ValueError:
        flash("❌ Error: Ingresa valores numéricos válidos en cantidad y precio.")
        return redirect(url_for('inicio'))

    total = cantidad * precio
    partidas.append({
        'descripcion': descripcion,
        'cantidad': cantidad,
        'precio': precio,
        'total': total
    })
    return redirect(url_for('inicio'))

@app.route('/editar/<int:indice>', methods=['GET', 'POST'])
def editar(indice):
    if request.method == 'POST':
        partidas[indice]['descripcion'] = request.form['descripcion']
        partidas[indice]['cantidad'] = int(request.form['cantidad'])
        partidas[indice]['precio'] = float(request.form['precio'])
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
    return redirect(url_for('inicio'))

@app.route('/nuevo_cliente', methods=['GET', 'POST'])
def nuevo_cliente():
    if request.method == 'POST':
        nombre = (request.form['nombre'] or '').strip()
        atencion = [a.strip() for a in request.form.get('atencion', '').split(',') if a.strip()]
        direccion = request.form.get('direccion', '')
        tiempo = request.form.get('tiempo', '')
        anticipo = request.form.get('anticipo', '')
        vigencia = request.form.get('vigencia', '')
        if not nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('nuevo_cliente'))
        clientes_predefinidos[nombre] = {
            "atencion": atencion,
            "direccion": direccion,
            "tiempo": tiempo,
            "anticipo": anticipo,
            "vigencia": vigencia
        }
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

@app.route('/generar_pdf')
def generar_pdf():
    import shutil
    # Congelar datos a disco
    guardar_datos(datos_cliente)
    guardar_partidas(partidas)

    datos = dict(datos_cliente)
    partidas_actuales = list(partidas)

    def calcular_totales_mem(partidas_lst):
        subtotal = sum((p.get('cantidad', 0) or 0) * (p.get('precio', 0.0) or 0.0) for p in partidas_lst)
        iva = subtotal * 0.16
        total = subtotal + iva
        return subtotal, iva, total

    subtotal, iva, total = calcular_totales_mem(partidas_actuales)

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
        img_path=img_path
    )
    HTML(string=html).write_pdf(ruta_pdf)

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

    mensaje = f"Cotización {cot} - {cliente}\nArchivo: {archivo_url}"
    wa_url = f"https://wa.me/?text={quote_plus(mensaje)}"
    mailto_url = f"mailto:?subject={quote_plus(f'Cotización {cot} - {cliente}')}&body={quote_plus(mensaje)}"

    return f"""PDF generado y guardado en:<br>{ruta_pdf}<br><br>
📄 <a href='{archivo_url}' target='_blank'>Abrir PDF en Drive</a><br>
📂 <a href='{carpeta_url}' target='_blank'>Abrir carpeta en Drive</a><br><br>
📱 <a href='{wa_url}' target='_blank'>Compartir por WhatsApp</a> &nbsp;|&nbsp;
✉️ <a href='{mailto_url}'>Enviar por Email</a><br><br>
<a href='/'>← Volver</a>"""

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
        direccion = request.form.get('direccion', '').strip()
        tiempo = request.form.get('tiempo', '').strip()
        anticipo = request.form.get('anticipo', '').strip()
        vigencia = request.form.get('vigencia', '').strip()

        if not nuevo_nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('editar_cliente'))

        existe_conflicto = (nuevo_nombre != nombre_actual) and (nuevo_nombre in clientes_predefinidos)
        if existe_conflicto:
            flash(f"Ya existe un cliente llamado '{nuevo_nombre}'. Elige otro nombre.")
            return redirect(url_for('editar_cliente'))

        payload = {
            "atencion": atencion,
            "direccion": direccion,
            "tiempo": tiempo,
            "anticipo": anticipo,
            "vigencia": vigencia
        }

        if nuevo_nombre == nombre_actual:
            clientes_predefinidos[nombre_actual] = payload
        else:
            clientes_predefinidos[nuevo_nombre] = payload
            if nombre_actual in clientes_predefinidos:
                del clientes_predefinidos[nombre_actual]
            datos_cliente['cliente'] = nuevo_nombre

        guardar_clientes(clientes_predefinidos)
        flash("Cliente actualizado correctamente.")
        return redirect(url_for('inicio'))

    return render_template('editar_cliente.html',
                           cliente=nombre_actual,
                           datos=datos)

@app.route('/borrar_cliente', methods=['GET', 'POST'])
def borrar_cliente():
    if not datos_cliente.get('cliente'):
        return "Primero selecciona un cliente para borrar.", 400

    cliente = datos_cliente['cliente']

    if request.method == 'POST':
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
@app.route('/vista_previa')
def vista_previa():
    guardar_datos(datos_cliente)
    guardar_partidas(partidas)
    return render_template(
        "plantilla_pdf.html",
        datos=datos_cliente,
        partidas=partidas,
        subtotal=sum(p['total'] for p in partidas),
        iva=sum(p['total'] for p in partidas) * 0.16,
        total=sum(p['total'] for p in partidas) * 1.16,
        img_path=url_for('static', filename='img/logo2.png'),
        preview=True
    )

# =============================== Explorador de cotizaciones ================================
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
        usr = get_drive_service_user()
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
        svc = get_drive_service()
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
    return jsonify(_health_snapshot())

# --- Healthcheck para la UI de /inicio-app ---
@app.get("/health")
def health():
    out = {
        "user_ok": False,
        "user_email": None,
        "sa_ok": False,
        "sa_email": None,
        "drive_ok": False,
        "sheets_ok": False,
        "needs_reconnect": False,
    }

    # Usuario (token.json via TOKEN_JSON_B64 en Render)
    try:
        usr = get_drive_service_user()
        who_u = usr.about().get(fields="user(displayName,emailAddress)").execute().get("user", {})
        out["user_ok"] = True
        out["user_email"] = who_u.get("emailAddress")
    except RefreshError:
        out["needs_reconnect"] = True
    except Exception:
        pass

    # Service account
    try:
        svc = get_drive_service()
        who_s = svc.about().get(fields="user(emailAddress)").execute().get("user", {})
        out["sa_ok"] = True
        out["sa_email"] = who_s.get("emailAddress")
    except Exception:
        pass

    # Drive acceso a la carpeta madre
    try:
        # Usa la cuenta de servicio para listar la carpeta madre
        svc = get_drive_service()
        svc.files().get(fileId=ID_COT, fields="id").execute()
        out["drive_ok"] = True
    except Exception:
        pass

    # Sheets (leer encabezado)
    try:
        sh = get_sheets_service()
        rng = f"{SHEET_TAB}!A1:A1"
        _ = sh.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=rng).execute()
        out["sheets_ok"] = True
    except Exception:
        pass

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
    # Pasamos el snapshot para pintar semáforos en SSR
    health = _health_snapshot()
    return render_template('inicio_app.html', IS_RENDER=IS_RENDER, health=health)

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


