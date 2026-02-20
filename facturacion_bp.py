# facturacion_bp.py — versión completa
from flask import Blueprint, request, jsonify, Response, current_app
from datetime import datetime
from pathlib import Path
import os
import json
import requests

# ----------------------------------------------------------------------
# Blueprint
# ----------------------------------------------------------------------
facturacion_bp = Blueprint("facturacion", __name__, url_prefix="/api")

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
FACTURAPI_BASE = "https://www.facturapi.io/v2"

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
INDEX = DATA_DIR / "pagos_index.json"

# ----------------------------------------------------------------------
# Índice local de REP
# ----------------------------------------------------------------------
def _read_index():
    if INDEX.exists():
        try:
            return json.loads(INDEX.read_text("utf-8"))
        except Exception:
            return {}
    return {}

def _write_index(d):
    INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def _remove_rep_by_id(rep_id: str):
    """Elimina del índice el REP cuyo id coincide, para re-habilitar complemento."""
    idx = _read_index()
    changed = False
    for k, v in list(idx.items()):
        if v.get("rep_id") == rep_id:
            idx.pop(k)
            changed = True
    if changed:
        _write_index(idx)

# ----------------------------------------------------------------------
# HTTP helpers (leen la API key en cada llamada)
# ----------------------------------------------------------------------
def _auth():
    api_key = os.getenv("FACTURAPI_API_KEY", "").strip()
    return {"Authorization": f"Bearer {api_key}"}

def _fa_get(path, params=None):
    r = requests.get(f"{FACTURAPI_BASE}{path}", headers=_auth(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _fa_post(path, json=None, params=None):
    r = requests.post(f"{FACTURAPI_BASE}{path}", headers=_auth(), json=json, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def _fa_delete(path, params=None):
    r = requests.delete(f"{FACTURAPI_BASE}{path}", headers=_auth(), params=params, timeout=60)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"ok": True, "status_code": r.status_code}

def _fa_get_binary(path, accept):
    headers = _auth()
    headers["Accept"] = accept
    r = requests.get(f"{FACTURAPI_BASE}{path}", headers=headers, timeout=60)
    r.raise_for_status()
    return r.content

# ----------------------------------------------------------------------
# Armado de datos
# ----------------------------------------------------------------------
def _build_items(items):
    """Convierte items del front al formato de Facturapi."""
    conceptos = []
    for it in items:
        desc = str(it.get("descripcion", "")).strip() or "Concepto"
        cps  = str(it.get("clave_prod_serv", "")).strip() or "85121600"
        cu   = str(it.get("clave_unidad", "")).strip() or "E48"
        # compatibilidad: precio_unitario | valor_unitario
        pu   = float(it.get("precio_unitario", it.get("valor_unitario", 0.0)))
        qty  = float(it.get("cantidad", 1))
        tasa = float(it.get("tasa_iva", 0.0))

        prod = {
            "description": desc,
            "product_key": cps,
            "unit_key": cu,
            "price": pu,
        }
        if tasa > 0:
            prod["taxes"] = [{"type": "IVA", "rate": tasa, "withholding": False}]

        conceptos.append({"product": prod, "quantity": qty})
    return conceptos

def _customer_inline(receptor):
    """Crea objeto customer inline. tax_system como string de catálogo SAT."""
    ALLOWED = {
        "601","603","605","606","607","608","610","611","612",
        "614","615","616","620","621","622","623","624","625","626"
    }

    rfc     = str(receptor.get("rfc","")).strip().upper()
    nombre  = str(receptor.get("nombre","")).strip()
    cp      = str(receptor.get("cp","")).strip()

    try:
        regimen = str(int(str(receptor.get("regimen_fiscal","")).strip()))
    except ValueError:
        regimen = ""

    if len(rfc) not in (12,13):
        raise ValueError("RFC inválido en longitud")
    if regimen not in ALLOWED:
        raise ValueError("regimen_fiscal fuera de catálogo SAT")

    obj = {
        "legal_name": nombre,
        "tax_id": rfc,
        "tax_system": regimen,      # string, p.ej. "626"
        "address": {"zip": cp},
    }
    if receptor.get("email"):
        obj["email"] = receptor["email"]
    if receptor.get("telefono"):
        obj["phone"] = receptor["telefono"]
    return obj

# ----------------------------------------------------------------------
# Salud
# ----------------------------------------------------------------------
@facturacion_bp.get("/ping")
def api_ping():
    return {"ok": True, "svc": "facturacion"}, 200

# ----------------------------------------------------------------------
# Timbrado (flujo principal)
# ----------------------------------------------------------------------
@facturacion_bp.post("/facturar")
def facturar():
    try:
        payload = request.get_json(force=True) or {}
        current_app.logger.info("DBG payload.receptor: %s", payload.get("receptor"))

        # 0) API key presente
        if not os.getenv("FACTURAPI_API_KEY", "").strip():
            return jsonify({"ok": False, "stage": "precheck", "error": "Falta FACTURAPI_API_KEY"}), 500

        # 1) Conceptos
        try:
            conceptos = _build_items(payload["items"])
        except Exception as e:
            return jsonify({"ok": False, "stage": "build_items", "error": str(e)}), 400

        # 2) Cliente inline
        try:
            customer_obj = _customer_inline(payload["receptor"])
        except Exception as e:
            return jsonify({"ok": False, "stage": "build_customer", "error": str(e)}), 400

        # 3) CFDI (Ingreso)
        data_cfdi = {
            "type": "I",
            "customer": customer_obj,
            "payment_form": payload.get("forma_pago"),
            "payment_method": payload.get("metodo_pago", "PUE"),
            "currency": payload.get("moneda", "MXN"),
            "series": payload.get("serie", "HSC"),
            "conditions": payload.get("condiciones"),
            "items": conceptos,
            "use": payload["receptor"].get("uso_cfdi", "G03"),
            "external_id": f"HSC-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        }
        # Regla: PPD => forma 99
        if data_cfdi.get("payment_method") == "PPD":
            data_cfdi["payment_form"] = "99"

        if payload.get("folio"):
            data_cfdi["folio_number"] = payload["folio"]

        # 4) Timbrar
        try:
            current_app.logger.info("DBG data_cfdi: %s", json.dumps(data_cfdi, ensure_ascii=False))
            invoice = _fa_post("/invoices", json=data_cfdi)
            uuid   = invoice.get("uuid")
            total  = invoice.get("total")
            status = invoice.get("status")
            inv_id = invoice.get("id")
        except requests.HTTPError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = {"raw": getattr(e.response, "text", str(e))}
            return jsonify({"ok": False, "stage": "stamp", "error": detail}), 400

        # 5) Descarga XML y PDF
        try:
            xml_bytes = _fa_get_binary(f"/invoices/{inv_id}/xml", "application/xml")
            pdf_bytes = _fa_get_binary(f"/invoices/{inv_id}/pdf", "application/pdf")
        except requests.HTTPError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = {"raw": getattr(e.response, "text", str(e))}
            # 206: timbrado ok pero falló descarga
            return jsonify({
                "ok": True, "uuid": uuid, "status": status, "total": total,
                "warn": "download_failed", "error": detail, "invoice_id": inv_id
            }), 206

        # 6) Guardado local
        base = Path("static") / "facturas_sandbox"
        carpeta = base / (payload["receptor"].get("rfc") or "SIN_RFC")
        carpeta.mkdir(parents=True, exist_ok=True)
        xml_path = carpeta / f"Factura-{uuid}.xml"
        pdf_path = carpeta / f"Factura-{uuid}.pdf"
        xml_path.write_bytes(xml_bytes)
        pdf_path.write_bytes(pdf_bytes)

        # 7) Respuesta final
        return jsonify({
            "ok": True,
            "uuid": uuid,
            "invoice_id": inv_id,
            "status": status,
            "total": total,
            "xml_path": str(xml_path).replace("\\", "/"),
            "pdf_path": str(pdf_path).replace("\\", "/")
        }), 200

    except Exception as e:
        current_app.logger.exception("Error en /api/facturar")
        return jsonify({
            "ok": False,
            "error": {"message": str(e)},
            "stage": "facturar"
        }), 500

# ----------------------------------------------------------------------
# Wrapper seguro (opcional). Delegado al flujo principal.
# ----------------------------------------------------------------------
@facturacion_bp.route("/facturar_safe", methods=["POST", "OPTIONS"])
def facturar_safe():
    try:
        resp = facturar()
        if resp is None:
            raise RuntimeError("La función facturar() no retornó respuesta")
        return resp
    except Exception as e:
        current_app.logger.exception("Error en /api/facturar_safe")
        return jsonify({"ok": False, "error": {"message": str(e)}, "stage": "facturar_safe"}), 500

# ----------------------------------------------------------------------
# Listado
# ----------------------------------------------------------------------
@facturacion_bp.get("/facturas/list")
def api_list_facturas():
    """Últimas 100 facturas, con bandera paid según índice local de REP."""
    try:
        rs = _fa_get("/invoices", params={"limit": 100})
    except requests.HTTPError as e:
        return jsonify({"ok": False, "error": getattr(e.response, "text", str(e))}), 400

    paid_idx = _read_index()  # claves = UUID de facturas origen con REP activo
    out = []
    for inv in rs.get("data", []):
        cust = inv.get("customer") or {}
        uuid = inv.get("uuid")
        paid = bool(paid_idx.get(uuid)) if inv.get("type") == "I" else False
        out.append({
            "id": inv.get("id"),
            "uuid": uuid,
            "date": inv.get("date"),
            "total": inv.get("total"),
            "status": inv.get("status"),
            "payment_method": inv.get("payment_method"),
            "type": inv.get("type"),
            "customer_name": cust.get("legal_name"),
            "customer_tax_id": cust.get("tax_id"),
            "paid": paid,
        })
    return jsonify({"ok": True, "data": out}), 200

# ----------------------------------------------------------------------
# PDF / XML / Cancelación
# ----------------------------------------------------------------------
@facturacion_bp.get("/invoices/<inv_id>/pdf")
def api_invoice_pdf(inv_id):
    try:
        content = _fa_get_binary(f"/invoices/{inv_id}/pdf", "application/pdf")
    except requests.HTTPError as e:
        return getattr(e.response, "text", str(e)), 400
    return Response(
        content, mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Factura-{inv_id}.pdf"}
    )

@facturacion_bp.post("/invoices/<inv_id>/cancel")
def api_invoice_cancel(inv_id):
    b = request.get_json(force=True) if request.data else {}
    motive = (b.get("motive") or b.get("reason") or "02").strip()
    params = {"motive": motive}
    sub = (b.get("substitution_folio") or "").strip()
    if motive == "01" and sub:
        params["substitution_folio"] = sub
    try:
        inv = _fa_get(f"/invoices/{inv_id}")
        res = _fa_delete(f"/invoices/{inv_id}", params=params)
        if inv.get("type") == "P":
            _remove_rep_by_id(inv_id)
        return jsonify({"ok": True, "result": res}), 200
    except requests.HTTPError as e:
        return jsonify({
            "ok": False, "stage": "cancel",
            "status": getattr(e.response, "status_code", None),
            "body": getattr(e.response, "text", "")
        }), 400

