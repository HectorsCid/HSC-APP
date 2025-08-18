# app.py
from flask import Flask, render_template, request, redirect, url_for, make_response, flash, send_file, abort
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
    token_path = 'token.json'
    creds = None

    # 1) Cargar token si existe
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # 2) Refrescar o flujo OAuth
    if not creds or not creds.valid:
        if creds and creds.expired and getattr(creds, 'refresh_token', None):
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0, prompt='consent')

        with open(token_path, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)

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
    global __did_sync_once
    if not __did_sync_once and (IS_RENDER or AUTO_SYNC_FROM_DRIVE):
        _sync_clientes_from_drive_into_memory()
        __did_sync_once = True
# -------------------------------------------------------------------

# ======================= Función para folios automáticos =======================
def obtener_siguiente_folio():
    ruta_folios = "folios.json"
    if not os.path.exists(ruta_folios):
        with open(ruta_folios, "w", encoding="utf-8") as f:
            json.dump({"ultimo_folio": 0}, f)

    with open(ruta_folios, "r", encoding="utf-8") as f:
        datos = json.load(f)

    datos["ultimo_folio"] += 1

    with open(ruta_folios, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2)

    return datos["ultimo_folio"]

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

    img_path = Path("img/LOGO.png").resolve().as_uri()
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
        service = _drive_service()

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

    service = _drive_service()
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

@app.route('/inicio-app')
def inicio_app():
    return render_template('inicio_app.html')

# ============================ MAIN (solo local) ============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
