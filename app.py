from flask import Flask, render_template, request, redirect, url_for, make_response, flash
from markupsafe import escape
from datetime import date
from weasyprint import HTML
import json
import os
import subprocess
import platform
from pathlib import Path

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


@app.route('/generar_pdf')
def generar_pdf():
    from weasyprint import HTML
    from pathlib import Path

    datos = cargar_datos()
    partidas = cargar_partidas()
    subtotal, iva, total = calcular_totales(partidas)

    cliente_folder = os.path.join('cotizaciones', datos['cliente'])
    os.makedirs(cliente_folder, exist_ok=True)

    nombre_archivo = f"{datos['cliente']} - {datos['cotizacion']}.pdf"
    ruta_completa = os.path.abspath(os.path.join(cliente_folder, nombre_archivo))

    img_path = Path("img/LOGO.png").resolve().as_uri()

    html = render_template('plantilla_pdf.html',
                           datos=datos,
                           partidas=partidas,
                           subtotal=subtotal,
                           iva=iva,
                           total=total,
                           img_path=img_path)

    HTML(string=html).write_pdf(ruta_completa)

    try:
        os.startfile(os.path.dirname(ruta_completa))
    except Exception as e:
        print("No se pudo abrir la carpeta:", e)

    return f"""PDF generado y guardado en:<br>{ruta_completa}<br>
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

