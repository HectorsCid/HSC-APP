from flask import Flask, render_template, request, redirect, url_for, make_response, flash, send_file, redirect
from markupsafe import escape
from datetime import date
from weasyprint import HTML
import json
import os
import subprocess
import platform
from urllib.parse import quote_plus
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from werkzeug.utils import safe_join
from flask import abort


# --- Google Drive scopes y constantes ---
SCOPES = ['https://www.googleapis.com/auth/drive.file']
ID_COT = '1oCf8Mt2nLynS6d2ryCngNyQ7rtf5jfiz'   # Carpeta "01. Cotizaciones" en Drive
CLIENTES_FILENAME = 'clientes.json'           # Archivo para persistir clientes en Drive

# Detección de entorno y auto-sync
IS_RENDER = bool(os.environ.get('RENDER') or
                 os.environ.get('RENDER_SERVICE_ID') or
                 os.environ.get('RENDER_EXTERNAL_HOSTNAME'))
# Si NO quieres sincronizar automáticamente en local, cambia a False
AUTO_SYNC_FROM_DRIVE = True

# Cargas/descargas de bytes para Drive
import io
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

app = Flask(__name__)
app.secret_key = 'superclave'  # Necesario para flash()
CARPETA_COTIZACIONES = "cotizaciones"

@app.template_filter('currency')
def currency_filter(value):
    try:
        return "${:,.2f}".format(float(value))
    except Exception:
        return "${:,.2f}".format(0)

# (opciones guardadas por si las ocupas después)
options = {
    'margin-bottom': '20mm',
    'encoding': "UTF-8",
    'disable-smart-shrinking': '',
    'javascript-delay': '200',
    'enable-local-file-access': ''
}

# ===================================== Helpers Drive (clientes.json) =====================================

def _drive_service_cfg():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("token.json inválido o sin refresh_token.")
    return build('drive', 'v3', credentials=creds)

def _drive_buscar_archivo(service, nombre, parent_id):
    """Devuelve file_id (str) del archivo 'nombre' dentro de parent_id, o None si no existe."""
    res = service.files().list(
        q=f"name='{nombre}' and '{parent_id}' in parents and trashed=false",
        spaces='drive',
        fields='files(id,name)',
        pageSize=10
    ).execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None

def descargar_clientes_de_drive():
    """Descarga clientes.json desde Drive (si existe) y devuelve dict; también guarda una copia local."""
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
            status, done = downloader.next_chunk()
        fh.seek(0)
        content = fh.read().decode('utf-8')
        data = json.loads(content)
        # Caché local (útil en dev y para lectura rápida)
        with open('clientes.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("✅ clientes.json cargado desde Drive.")
        return data
    except Exception as e:
        print("⚠️ No se pudo descargar clientes.json de Drive:", e)
        return {}

def subir_clientes_a_drive(clientes_dict):
    """Sube/actualiza clientes.json a Drive en la carpeta ID_COT."""
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
    """
    En Render: siempre bajar desde Drive (contenedor efímero).
    En local: usar archivo si existe; si no, intentar desde Drive.
    """
    if IS_RENDER:
        data = descargar_clientes_de_drive()
        return data or {}

    # Local: primero archivo, luego Drive
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
    """Guarda local (caché) y sincroniza a Drive para persistir en Render."""
    try:
        with open("clientes.json", "w", encoding="utf-8") as f:
            json.dump(clientes, f, indent=2, ensure_ascii=False)
        print("💾 clientes.json guardado localmente.")
    except Exception as e:
        print("⚠️ No se pudo guardar clientes.json local:", e)

    # Sincronizar a Drive
    subir_clientes_a_drive(clientes)

clientes_predefinidos = cargar_clientes()

def _sync_clientes_from_drive_into_memory():
    """Descarga clientes.json desde Drive, escribe caché local y refresca el dict en memoria."""
    data = descargar_clientes_de_drive()  # esta ya escribe clientes.json local como caché
    if data is not None:
        try:
            clientes_predefinidos.clear()
            clientes_predefinidos.update(data)
            print("🔄 clientes_predefinidos sincronizado desde Drive (startup).")
        except Exception as e:
            print("⚠️ No se pudo actualizar clientes_predefinidos:", e)

# ---------- Reemplazo de before_first_request (Flask 3.x) ----------
# Corre solo una vez por proceso en el primer request
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
    # Carpeta base del Google Drive sincronizado en tu PC
    base = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
    cliente_seguro = (cliente_nombre or "SIN_CLIENTE").replace("/", "-").replace("\\", "-").strip()
    destino_dir = os.path.join(base, cliente_seguro)
    try:
        os.makedirs(destino_dir, exist_ok=True)  # si no existe, la crea (Drive Desktop la sincroniza)
        os.startfile(destino_dir)                # abre Explorador en esa carpeta
        print("📂 Abierto Drive local:", destino_dir)
    except Exception as e:
        print("⚠️ No se pudo abrir Drive local:", e)

@app.route('/generar_pdf')
def generar_pdf():
    import shutil

    # --- 1) Congelar en disco lo que hay en memoria (para /repositorio) ---
    guardar_datos(datos_cliente)
    guardar_partidas(partidas)

    # --- 2) Usar SIEMPRE la memoria vigente ---
    datos = dict(datos_cliente)  # copia defensiva
    partidas_actuales = list(partidas)

    # --- Totales ---
    def calcular_totales_mem(partidas_lst):
        subtotal = sum((p.get('cantidad', 0) or 0) * (p.get('precio', 0.0) or 0.0) for p in partidas_lst)
        iva = subtotal * 0.16
        total = subtotal + iva
        return subtotal, iva, total

    subtotal, iva, total = calcular_totales_mem(partidas_actuales)

    # Cliente/folio seguros
    cliente = (datos.get('cliente') or 'SIN_CLIENTE').strip()
    cot = (str(datos.get('cotizacion')) or 'S/F').strip()

    # --- 3) Generar PDF en carpeta original ---
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

    # --- Helpers internos de generar_pdf ---
    def guardar_respaldo_local(ruta_pdf_local, cliente_nombre, nombre_arch):
        ruta_respaldo_dir = os.path.join('static', 'cotizaciones', cliente_nombre.replace("/", "-").replace("\\", "-"))
        os.makedirs(ruta_respaldo_dir, exist_ok=True)
        ruta_final = os.path.join(ruta_respaldo_dir, nombre_arch)
        shutil.copy2(ruta_pdf_local, ruta_final)
        print("💾 Copiado a respaldo local:", ruta_final)

    def _drive_service():
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise RuntimeError("token.json inválido o sin refresh_token. Regénéralo en local.")
        return build('drive', 'v3', credentials=creds)

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
                os.startfile(pdf_local)          # abrir el PDF si ya bajó por sync
                print("📂 Abierto PDF local:", pdf_local)
            elif os.path.isdir(dir_local):
                os.startfile(dir_local)          # abrir carpeta solo si YA existe
                print("📂 Abierta carpeta local existente:", dir_local)
            else:
                print("ℹ️ Carpeta/archivo local aún no existen (pendiente de sync).")
        except Exception as e:
            print("⚠️ No se pudo abrir recurso local:", e)

    def subir_a_drive_archivo(ruta_pdf, cliente_nombre, nombre_archivo):
        print(f"🚀 Iniciando subida a Drive: {nombre_archivo} para cliente '{cliente_nombre}'")
        service = _drive_service()

        # Carpeta base fija: "01. Cotizaciones"
        id_cot = ID_COT

        # Reusar carpeta del cliente ignorando mayúsculas/espacios
        canon = (cliente_nombre or "").strip().lower()
        print(f"🔍 Buscando carpeta del cliente en Drive (canon: '{canon}')")
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
        else:
            print(f"📁 Carpeta cliente encontrada: {id_cliente}")

        carpeta_url = f"https://drive.google.com/drive/folders/{id_cliente}"

        # --- Upsert del PDF ---
        print(f"🔍 Buscando si el archivo '{nombre_archivo}' ya existe en carpeta del cliente...")
        existing = service.files().list(
            q=f"name='{nombre_archivo}' and '{id_cliente}' in parents and trashed=false",
            spaces='drive',
            fields='files(id,name)',
            pageSize=100
        ).execute().get('files', [])

        media = MediaFileUpload(ruta_pdf, mimetype='application/pdf')

        if existing:
            print(f"♻️ Archivo encontrado, actualizando: {existing[0]['id']}")
            file_id = existing[0]['id']
            updated = service.files().update(
                fileId=file_id,
                media_body=media,
                fields='id, webViewLink, webContentLink'
            ).execute()
            for dup in existing[1:]:
                try:
                    service.files().delete(fileId=dup['id']).execute()
                    print(f"🗑️ Duplicado eliminado: {dup['id']}")
                except Exception as e:
                    print("⚠️ No se pudo borrar duplicado:", e)
            archivo_url = updated.get('webViewLink') or carpeta_url
            print("✅ Archivo actualizado en Drive:", archivo_url)
        else:
            print(f"📤 Subiendo nuevo archivo a Drive: {nombre_archivo}")
            meta = {'name': nombre_archivo, 'parents': [id_cliente]}
            created = service.files().create(
                body=meta,
                media_body=media,
                fields='id, webViewLink, webContentLink'
            ).execute()
            archivo_url = created.get('webViewLink') or carpeta_url
            print("✅ Archivo subido a Drive:", archivo_url)

        return carpeta_url, archivo_url

    # --- Ejecutar copias y subida ---
    guardar_respaldo_local(ruta_pdf, cliente, nombre_archivo)  # para /repositorio
    carpeta_url, archivo_url = subir_a_drive_archivo(ruta_pdf, cliente, nombre_archivo)

    # Intento de abrir local SOLO si ya existe (no forzar creación)
    abrir_drive_local_win(cliente, nombre_archivo)

    # Enlaces de compartir
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
    # Debe existir un cliente seleccionado en memoria
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
        # Campos del formulario
        nuevo_nombre = (request.form.get('nombre') or "").strip()
        atencion = [a.strip() for a in (request.form.get('atencion') or "").split(',') if a.strip()]
        direccion = request.form.get('direccion', '').strip()
        tiempo = request.form.get('tiempo', '').strip()
        anticipo = request.form.get('anticipo', '').strip()
        vigencia = request.form.get('vigencia', '').strip()

        # Validaciones básicas
        if not nuevo_nombre:
            flash("El nombre del cliente no puede estar vacío.")
            return redirect(url_for('editar_cliente'))

        # Si cambió el nombre y el nuevo ya existe, prevenimos colisión
        existe_conflicto = (nuevo_nombre != nombre_actual) and (nuevo_nombre in clientes_predefinidos)
        if existe_conflicto:
            flash(f"Ya existe un cliente llamado '{nuevo_nombre}'. Elige otro nombre.")
            return redirect(url_for('editar_cliente'))

        # Armar el payload a guardar
        payload = {
            "atencion": atencion,
            "direccion": direccion,
            "tiempo": tiempo,
            "anticipo": anticipo,
            "vigencia": vigencia
        }

        # Si el nombre NO cambia: solo actualiza los datos
        if nuevo_nombre == nombre_actual:
            clientes_predefinidos[nombre_actual] = payload
        else:
            # Renombrado: crea la entrada nueva y borra la vieja
            clientes_predefinidos[nuevo_nombre] = payload
            if nombre_actual in clientes_predefinidos:
                del clientes_predefinidos[nombre_actual]
            # Actualiza el cliente vigente en memoria
            datos_cliente['cliente'] = nuevo_nombre

        guardar_clientes(clientes_predefinidos)
        flash("Cliente actualizado correctamente.")
        return redirect(url_for('inicio'))

    # GET -> mostrar formulario con datos actuales
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
    # Ruta local donde Drive Desktop sincroniza tus cotizaciones
    BASE_LOCAL_DRIVE = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"

    # Si estamos en Render o no existe la ruta local, listamos desde Drive
    use_drive = IS_RENDER or (not os.path.isdir(BASE_LOCAL_DRIVE))

    if use_drive:
        try:
            service = _drive_service_cfg()
            estructura = {}  # { cliente: [ {name, link}, ... ] }

            # 1) listar carpetas (clientes) bajo ID_COT
            resp = service.files().list(
                q=f"'{ID_COT}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='files(id,name)',
                pageSize=1000
            ).execute()

            for folder in resp.get('files', []):
                cliente = folder['name']
                fid = folder['id']
                # 2) listar PDFs dentro de la carpeta del cliente
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

    # --- Modo local: listar desde la carpeta sincronizada G:\... ---
    estructura = {}  # { cliente: [ nombres_pdf ], ... }
    try:
        for cliente in sorted(os.listdir(BASE_LOCAL_DRIVE)):
            c_path = os.path.join(BASE_LOCAL_DRIVE, cliente)
            if os.path.isdir(c_path):
                pdfs = [a for a in os.listdir(c_path) if a.lower().endswith('.pdf')]
                estructura[cliente] = sorted(pdfs)
    except Exception as e:
        print("⚠️ Error listando en local /repositorio:", e)
        estructura = {}

    # En local, servimos los PDFs con una ruta dedicada (/repo/local/...)
    return render_template("repositorio.html", estructura=estructura, from_drive=False)

@app.route('/repo/local/<cliente>/<path:filename>')
def repo_local_file(cliente, filename):
    BASE_LOCAL_DRIVE = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
    cliente_seguro = (cliente or "").replace("/", "-").replace("\\", "-").strip()
    base_cliente = os.path.join(BASE_LOCAL_DRIVE, cliente_seguro)

    # Solo PDFs por seguridad
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
    # Helpers locales (para no tocar tu generar_pdf)
    def _drive_service():
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise RuntimeError("token.json inválido o sin refresh_token.")
        return build('drive', 'v3', credentials=creds)

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

    # 📌 Carpeta base fija "01. Cotizaciones"
    id_cot = ID_COT

    # Buscar carpeta del cliente sin duplicar por mayúsculas/espacios
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
