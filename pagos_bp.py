# pagos_bp.py
from flask import Blueprint, request, jsonify
import os, requests, json
from pathlib import Path

pagos_bp = Blueprint("pagos", __name__, url_prefix="/api/pagos")
FACTURAPI = "https://www.facturapi.io/v2"
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
INDEX = DATA_DIR / "pagos_index.json"

def _auth():
    return {"Authorization": f"Bearer {os.getenv('FACTURAPI_API_KEY','')}"}

def _get(path, params=None):
    r = requests.get(FACTURAPI+path, headers=_auth(), params=params, timeout=30)
    r.raise_for_status(); return r.json()

def _post(path, json_):
    r = requests.post(FACTURAPI+path, headers={**_auth(),"Content-Type":"application/json"}, json=json_, timeout=60)
    r.raise_for_status(); return r.json()

def _get_bin(path, accept):
    h = _auth(); h["Accept"]=accept
    r = requests.get(FACTURAPI+path, headers=h, timeout=60)
    r.raise_for_status(); return r.content

def _read_index():
    if INDEX.exists():
        try: return json.loads(INDEX.read_text("utf-8"))
        except Exception: return {}
    return {}

def _write_index(d): INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

@pagos_bp.get("/resolve")
def resolve_uuid():
    u = (request.args.get("uuid") or "").strip()
    if not u: return jsonify({"ok":False,"error":"uuid requerido"}), 400
    try:
        rs = _get("/invoices", params={"q": u})
        data = (rs.get("data") or [])
        if not data: return jsonify({"ok":False,"error":"UUID no encontrado"}), 404
        return jsonify({"ok":True,"id": data[0]["id"]}), 200
    except requests.HTTPError as e:
        return jsonify({"ok":False,"error": e.response.text}), 400

@pagos_bp.get("/info/<invoice_id>")
def info(invoice_id):
    try:
        inv = _get(f"/invoices/{invoice_id}")
        subset = {
            "id": inv.get("id"),
            "uuid": inv.get("uuid"),
            "date": inv.get("date"),
            "status": inv.get("status"),
            "payment_method": inv.get("payment_method"),
            "total": inv.get("total"),
            "customer": {
                "legal_name": (inv.get("customer") or {}).get("legal_name"),
                "tax_id": (inv.get("customer") or {}).get("tax_id"),
            }
        }
        return jsonify(subset), 200
    except requests.HTTPError as e:
        return jsonify({"ok":False,"error": e.response.text}), 400

@pagos_bp.post("/crear")
def crear_pago():
    """Complemento de pago con control de duplicados."""
    b = request.get_json(force=True)
    inv_id = (b.get("invoice_id") or "").strip()
    payment_form = (b.get("payment_form") or "03").strip()
    amount = b.get("amount")
    pay_date = (b.get("date") or "").strip()
    reference = (b.get("reference") or "").strip()

    if not inv_id:
        return jsonify({"ok":False,"error":"invoice_id requerido"}), 400

    # 1) Traer factura
    try:
        inv = _get(f"/invoices/{inv_id}")
    except requests.HTTPError as e:
        return jsonify({"ok":False,"stage":"fetch_invoice","error":e.response.text}), 400

    if inv.get("payment_method") != "PPD":
        return jsonify({"ok":False,"stage":"precheck","error":"La factura origen no es PPD"}), 412

    uuid = inv.get("uuid"); total = float(inv.get("total") or 0)
    cust = inv.get("customer") or {}
    zip_code = ((cust.get("address") or {}).get("zip") or "")
    customer_obj = {
        "legal_name": cust.get("legal_name"),
        "tax_id": cust.get("tax_id"),
        "tax_system": str(cust.get("tax_system") or ""),
        "address": {"zip": str(zip_code)}
    }

    # 2) Bloqueo de duplicados por índice local
    idx = _read_index()
    entry = idx.get(uuid)
    if entry and entry.get("status") == "active":
        return jsonify({"ok":False,"stage":"duplicate_check","error":"La factura ya tiene complemento de pago activo"}), 409

    # 3) Importe
    amt = float(amount or total)
    base = round(amt / 1.16, 2)

    pago_item = {
        "payment_form": payment_form,
        "related_documents": [{
            "uuid": uuid,
            "amount": amt,
            "installment": 1,
            "last_balance": total,
            "taxes": [{"base": base, "type":"IVA", "rate":0.16}]
        }]
    }
    if pay_date: pago_item["date"] = pay_date
    if reference: pago_item["operation_number"] = reference

    data = {
        "type": "P",
        "customer": customer_obj,
        "complements": [{ "type": "pago", "data": [pago_item] }]
    }

    # 4) Timbrar REP
    try:
        pago = _post("/invoices", data)
    except requests.HTTPError as e:
        return jsonify({"ok":False,"stage":"stamp_payment","error":e.response.text}), 400

    rep_id = pago.get("id"); rep_uuid = pago.get("uuid")

    # 5) Guardar índice
    idx[uuid] = {"rep_id": rep_id, "rep_uuid": rep_uuid, "status":"active"}
    _write_index(idx)

    # 6) Descargar
    try:
        xml = _get_bin(f"/invoices/{rep_id}/xml", "application/xml")
        pdf = _get_bin(f"/invoices/{rep_id}/pdf", "application/pdf")
        base_dir = Path("static/pagos_sandbox")/ (cust.get("tax_id") or "SIN_RFC")
        base_dir.mkdir(parents=True, exist_ok=True)
        x = base_dir / f"REP-{rep_uuid}.xml"; p = base_dir / f"REP-{rep_uuid}.pdf"
        x.write_bytes(xml); p.write_bytes(pdf)
    except requests.HTTPError as e:
        return jsonify({"ok":True,"uuid":rep_uuid,"warn":"download_failed","error":e.response.text}), 206

    return jsonify({"ok":True,"uuid":rep_uuid,"id":rep_id,"xml_path":str(x),"pdf_path":str(p)}), 200
