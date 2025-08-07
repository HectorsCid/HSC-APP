from flask import Flask, render_template, request, redirect, url_for, make_response, flash
from markupsafe import escape
import json
import os
import pdfkit
import tempfile
from datetime import date  # asegúrate de tener esta importación al inicio
import subprocess  # asegúrate de tener esta línea arriba

app = Flask(__name__)
app.secret_key = 'superclave'  # Necesario para flash()
CARPETA_COTIZACIONES = "cotizaciones"
config = pdfkit.configuration(wkhtmltopdf=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')

# Ruta absoluta al archivo footer.html
FOOTER_PATH = os.path.abspath("footer.html")

options = {
    'footer-html': FOOTER_PATH,
    'margin-bottom': '20mm',
    'encoding': "UTF-8",
    'disable-smart-shrinking': '',
    'javascript-delay': '200',
    'enable-local-file-access': ''  # ESTA LÍNEA ES IMPORTANTE
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
@app.route('/generar_pdf')
def generar_pdf():
    global datos_cliente, partidas

    # Calcular totales
    subtotal = sum(p['total'] for p in partidas)
    iva = subtotal * 0.16
    total = subtotal + iva

    # Renderizar HTML
    rendered_html = render_template(
        "plantilla_pdf.html",
        datos=datos_cliente,
        partidas=partidas,
        subtotal=subtotal,
        iva=iva,
        total=total,
        preview=False
    )

    # Ruta de guardado
    nombre_cliente = datos_cliente.get('cliente', 'cliente')
    folio = datos_cliente.get('cotizacion', 'cotizacion')
    carpeta_cliente = os.path.join(CARPETA_COTIZACIONES, nombre_cliente)
    os.makedirs(carpeta_cliente, exist_ok=True)
    nombre_archivo = f"{nombre_cliente} - {folio}.pdf"
    ruta_completa = os.path.join(carpeta_cliente, nombre_archivo)

    # Generar PDF
    print("Ruta del footer HTML:", FOOTER_PATH)

    pdfkit.from_string(rendered_html, ruta_completa, configuration=config, options=options)

    # Abrir carpeta (opcional)
    try:
        subprocess.Popen(f'explorer "{carpeta_cliente}"')
    except Exception as e:
        print("No se pudo abrir la carpeta:", e)

    return f"PDF generado y guardado en:<br>{ruta_completa}<br><a href='/'>← Volver</a>"



   


 




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

@app.route('/vista_previa')
def vista_previa():
    return render_template("plantilla_pdf.html",
                           datos=datos_cliente,
                           partidas=partidas,
                           subtotal=sum(p['total'] for p in partidas),
                           iva=sum(p['total'] for p in partidas) * 0.16,
                           total=sum(p['total'] for p in partidas) * 1.16,
                           preview=True)

# ================================= Run App =====================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
