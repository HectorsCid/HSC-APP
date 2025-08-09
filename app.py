from flask import Flask, render_template, request, redirect, url_for, make_response, flash
from markupsafe import escape
from datetime import date
from weasyprint import HTML
import json
import os
import subprocess
import platform
from pathlib import Path
from flask import send_file
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
SCOPES = ['https://www.googleapis.com/auth/drive.file']




app = Flask(__name__)
app.secret_key = 'superclave'



app.secret_key = 'superclave'  # Necesario para flash()
CARPETA_COTIZACIONES = "cotizaciones"


# Ruta absoluta al archivo footer.html
from pathlib import Path  # Asegúrate de tener esta importación
import os



options = {
   
    'margin-bottom': '20mm',
    'encoding': "UTF-8",
    'disable-smart-shrinking': '',
    'javascript-delay': '200',
    'enable-local-file-access': ''  # NECESARIO para acceder al footer local
}




# ======================= Funciones para clientes ======================

def cargar_clientes():
    if os.path.exists("clientes.json"):
        with open("clientes.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def guardar_clientes(clientes):
    with open("clientes.json", "w", encoding="utf-8") as f:
        json.dump(clientes, f, indent=2, ensure_ascii=False)

clientes_predefinidos = cargar_clientes()

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
        nombre = request.form['nombre']
        atencion = [a.strip() for a in request.form['atencion'].split(',')]
        direccion = request.form['direccion']
        tiempo = request.form['tiempo']
        anticipo = request.form['anticipo']
        vigencia = request.form['vigencia']
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
def abrir_explorador_carpeta_cliente(cliente_nombre):
    # Carpeta local sincronizada con Drive (ajusta si cambia tu letra/unidad)
    base_local = r"G:\Mi unidad\appsheet\HSC\1. Refrigeración y Manto. industrial\01. Clientes\01. Cotizaciones"
    ruta_local = os.path.join(base_local, cliente_nombre)
    try:
        if platform.system() == 'Windows' and os.path.isdir(ruta_local):
            os.startfile(ruta_local)  # Abre Explorador en la carpeta
            print("🗂️ Explorador abierto:", ruta_local)
        else:
            print("⚠️ No se pudo abrir Explorador. Ruta no existe o no es Windows:", ruta_local)
    except Exception as e:
        print("⚠️ Error al abrir Explorador:", e)


@app.route('/generar_pdf')
def generar_pdf():
    from weasyprint import HTML
    from pathlib import Path
    import shutil

    # --- Datos y totales ---
    datos = cargar_datos()
    partidas = cargar_partidas()
    subtotal, iva, total = calcular_totales(partidas)

    cliente = (datos.get('cliente') or 'SIN_CLIENTE').strip()
    cot = (datos.get('cotizacion') or 'S/F').strip()

    # --- Generar PDF en carpeta original ---
    cliente_folder = os.path.join('cotizaciones', cliente)
    os.makedirs(cliente_folder, exist_ok=True)

    nombre_archivo = f"{cliente} - {cot}.pdf"
    ruta_pdf = os.path.abspath(os.path.join(cliente_folder, nombre_archivo))

    img_path = Path("img/LOGO.png").resolve().as_uri()
    html = render_template(
        'plantilla_pdf.html',
        datos=datos,
        partidas=partidas,
        subtotal=subtotal,
        iva=iva,
        total=total,
        img_path=img_path
    )
    HTML(string=html).write_pdf(ruta_pdf)

    # --- Funciones internas (DENTRO de generar_pdf) ---
    def guardar_respaldo_local(ruta_pdf_local, cliente_nombre, nombre_arch):
        ruta_respaldo_dir = os.path.join('static', 'cotizaciones', cliente_nombre)
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
        res = service.files().list(q=query, spaces='drive', fields='files(id)', pageSize=1).execute()
        items = res.get('files', [])
        if items:
            return items[0]['id']
        meta = {'name': nombre, 'mimeType': 'application/vnd.google-apps.folder'}
        if id_padre:
            meta['parents'] = [id_padre]
        carpeta = service.files().create(body=meta, fields='id').execute()
        return carpeta['id']

    def subir_a_drive_archivo(ruta_local, cliente_nombre, nombre_arch):
        service = _drive_service()

        # 📌 Carpeta base fija: "01. Cotizaciones"
        id_cot = '1oCf8Mt2nLynS6d2ryCngNyQ7rtf5jfiz'

        # Crear/obtener subcarpeta del cliente dentro de "01. Cotizaciones"
        id_cliente = _obtener_o_crear_carpeta(service, cliente_nombre, id_cot)

        # URL de la carpeta del cliente
        carpeta_url = f"https://drive.google.com/drive/folders/{id_cliente}"
        print("📂 Carpeta en Drive:", carpeta_url)

        # Subir PDF
        meta = {'name': nombre_arch, 'parents': [id_cliente]}
        media = MediaFileUpload(ruta_local, mimetype='application/pdf')
        service.files().create(body=meta, media_body=media, fields='id, webViewLink').execute()
        print("📤 Subido a Drive OK")




        return carpeta_url



    # --- Ejecutar copias y subida (FUERA de la función) ---
    guardar_respaldo_local(ruta_pdf, cliente, nombre_archivo)
    carpeta_url = subir_a_drive_archivo(ruta_pdf, cliente, nombre_archivo)
    abrir_explorador_carpeta_cliente(cliente)


    return f"""PDF generado y guardado en:<br>{ruta_pdf}<br>
📂 <a href='{carpeta_url}' target='_blank'>Abrir carpeta en Drive</a><br>
<a href='/'>← Volver</a>"""





   


 




@app.route('/editar_cliente', methods=['GET', 'POST'])
def editar_cliente():
    if not datos_cliente.get('cliente'):
        return "Primero selecciona un cliente para editar.", 400

    cliente_actual = datos_cliente['cliente']
    datos = clientes_predefinidos.get(cliente_actual, {})

    if request.method == 'POST':
        clientes_predefinidos[cliente_actual] = {
            "atencion": [a.strip() for a in request.form['atencion'].split(',')],
            "direccion": request.form['direccion'],
            "tiempo": request.form['tiempo'],
            "anticipo": request.form['anticipo'],
            "vigencia": request.form['vigencia']
        }
        guardar_clientes(clientes_predefinidos)
        return redirect(url_for('inicio'))

    return render_template('editar_cliente.html',
                           cliente=cliente_actual,
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
##explorador de cotizaciones 
@app.route('/repositorio')
def repositorio():
    base_dir = os.path.join(os.getcwd(), "static", "cotizaciones")

    estructura = {}

    for cliente in os.listdir(base_dir):
        cliente_path = os.path.join(base_dir, cliente)
        if os.path.isdir(cliente_path):
            pdfs = [archivo for archivo in os.listdir(cliente_path) if archivo.endswith('.pdf')]
            estructura[cliente] = pdfs

    return render_template("repositorio.html", estructura=estructura)



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

