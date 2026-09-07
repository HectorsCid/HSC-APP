# facturacion_bp.py — versión completa
from flask import Blueprint, request, jsonify, Response, current_app
from datetime import datetime
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from base64 import b64decode
from threading import Lock
import os
import json
import re
import requests

# ----------------------------------------------------------------------
# Blueprint
# ----------------------------------------------------------------------
facturacion_bp = Blueprint("facturacion", __name__, url_prefix="/api")

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
FACTURAPI_BASE = "https://www.facturapi.io/v2"
FACTURAMA_SANDBOX_BASE = "https://apisandbox.facturama.mx"
FACTURAMA_PRODUCTION_BASE = "https://api.facturama.mx"

_STAMP_LOCK = Lock()
_STAMP_RESULTS = {}
_FM_PROFILE_CACHE = None
_FM_BRANCH_CACHE = None

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


def _facturama_config():
    """Configuración de Facturama. Sandbox es el valor seguro por defecto."""
    user = os.getenv("FACTURAMA_USER", "").strip()
    password = os.getenv("FACTURAMA_PASSWORD", "").strip()
    sandbox = os.getenv("FACTURAMA_SANDBOX", "true").strip().lower() not in {"0", "false", "no"}
    return {
        "user": user,
        "password": password,
        "sandbox": sandbox,
        "base": FACTURAMA_SANDBOX_BASE if sandbox else FACTURAMA_PRODUCTION_BASE,
        "configured": bool(user and password),
    }


def _provider():
    """Facturama tiene prioridad; Facturapi queda como respaldo temporal."""
    return "facturama" if _facturama_config()["configured"] else "facturapi"


def _fm_request(method, path, *, json_body=None, params=None, timeout=60):
    cfg = _facturama_config()
    if not cfg["configured"]:
        raise RuntimeError("Faltan FACTURAMA_USER y FACTURAMA_PASSWORD")
    response = requests.request(
        method,
        f"{cfg['base']}{path}",
        auth=(cfg["user"], cfg["password"]),
        json=json_body,
        params=params,
        timeout=timeout,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return response.text


def _facturama_issuer_locations():
    """Obtiene los códigos postales configurados en el perfil fiscal."""
    global _FM_PROFILE_CACHE, _FM_BRANCH_CACHE
    expedition = os.getenv("FACTURAMA_EXPEDITION_ZIP", "").strip()
    tax_zip = os.getenv("FACTURAMA_TAX_ZIP", "").strip()
    if expedition:
        tax_zip = tax_zip or expedition
        return expedition, tax_zip
    if _FM_PROFILE_CACHE is None:
        _FM_PROFILE_CACHE = _fm_request("GET", "/TaxEntity", timeout=25)
    if _FM_BRANCH_CACHE is None:
        branches = _fm_request("GET", "/BranchOffice", timeout=25)
        _FM_BRANCH_CACHE = branches if isinstance(branches, list) else []
    profile = _FM_PROFILE_CACHE if isinstance(_FM_PROFILE_CACHE, dict) else {}
    if not str(_pick(profile, "FiscalRegime") or "").strip():
        raise ValueError("Falta guardar el régimen fiscal del emisor en el perfil de Facturama")
    tax_address = _pick(profile, "TaxAddress") or {}
    branches = _FM_BRANCH_CACHE or []
    default_branch = next((branch for branch in branches if _pick(branch, "IsDefault")), None)
    selected_branch = default_branch or (branches[0] if branches else {})
    branch_address = _pick(selected_branch, "Address") or {}
    expedition = str(_pick(branch_address, "ZipCode") or "").strip()
    tax_zip = tax_zip or str(_pick(tax_address, "ZipCode") or expedition).strip()
    return expedition, tax_zip


def _http_error_detail(exc):
    response = getattr(exc, "response", None)
    if response is None:
        return {"message": str(exc)}
    try:
        return response.json()
    except Exception:
        return {"message": (response.text or str(exc))[:1200]}


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Importe inválido: {value}")


def _number(value, default=0):
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Número inválido: {value}")


def _facturama_unit_name(unit_code):
    return {
        "E48": "Unidad de servicio",
        "H87": "Pieza",
        "MTR": "Metro",
        "KGM": "Kilogramo",
        "LTR": "Litro",
    }.get(unit_code, "Unidad")


def _validate_receiver(receptor):
    allowed_regimes = {
        "601", "603", "605", "606", "607", "608", "610", "611", "612",
        "614", "615", "616", "620", "621", "622", "623", "624", "625", "626",
    }
    rfc = str(receptor.get("rfc", "")).strip().upper()
    name = str(receptor.get("nombre", "")).strip()
    zip_code = str(receptor.get("cp", "")).strip()
    regime = str(receptor.get("regimen_fiscal", "")).strip()
    if not re.fullmatch(r"[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}", rfc):
        raise ValueError("RFC del receptor inválido")
    if not name:
        raise ValueError("Falta la razón social del receptor")
    if not re.fullmatch(r"\d{5}", zip_code):
        raise ValueError("El código postal del receptor debe tener 5 dígitos")
    if regime not in allowed_regimes:
        raise ValueError("Régimen fiscal del receptor fuera del catálogo permitido")
    return rfc, name, zip_code, regime


def _build_facturama_cfdi(payload):
    receptor = payload.get("receptor") or {}
    rfc, name, zip_code, regime = _validate_receiver(receptor)
    profile_expedition, profile_tax_zip = _facturama_issuer_locations()
    expedition_zip = str(payload.get("expedition_place") or profile_expedition).strip()
    if not re.fullmatch(r"\d{5}", expedition_zip):
        raise ValueError("Falta FACTURAMA_EXPEDITION_ZIP con el código postal de expedición")

    items = []
    for index, raw in enumerate(payload.get("items") or [], start=1):
        description = str(raw.get("descripcion", "")).strip() or f"Concepto {index}"
        quantity = _number(raw.get("cantidad"), 1)
        unit_price = _money(raw.get("precio_unitario", raw.get("valor_unitario", 0)))
        tax_rate = _number(raw.get("tasa_iva"), 0)
        if quantity <= 0 or unit_price < 0 or tax_rate < 0:
            raise ValueError(f"Valores inválidos en el concepto {index}")
        subtotal = _money(quantity * unit_price)
        tax_total = _money(subtotal * tax_rate)
        unit_code = str(raw.get("clave_unidad") or "E48").strip().upper()
        item = {
            "ProductCode": str(raw.get("clave_prod_serv") or "85121600").strip(),
            "IdentificationNumber": str(index),
            "Description": description,
            "Unit": _facturama_unit_name(unit_code),
            "UnitCode": unit_code,
            "UnitPrice": float(unit_price),
            "Quantity": float(quantity),
            "Subtotal": float(subtotal),
            "Discount": 0.0,
            "TaxObject": "02" if tax_rate > 0 else "01",
            "Total": float(_money(subtotal + tax_total)),
        }
        if tax_rate > 0:
            item["Taxes"] = [{
                "Total": float(tax_total),
                "Name": "IVA",
                "Base": float(subtotal),
                "Rate": float(tax_rate),
                "IsRetention": False,
                "IsFederalTax": True,
            }]
        items.append(item)
    if not items:
        raise ValueError("Agrega al menos un concepto")

    payment_method = str(payload.get("metodo_pago") or "PUE").upper()
    payment_form = str(payload.get("forma_pago") or "03").zfill(2)
    if payment_method == "PPD":
        payment_form = "99"
    cfdi = {
        "Date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "Currency": str(payload.get("moneda") or "MXN").upper(),
        "ExpeditionPlace": expedition_zip,
        "TaxZipCode": profile_tax_zip,
        "CfdiType": "I",
        "PaymentForm": payment_form,
        "PaymentMethod": payment_method,
        "Receiver": {
            "Rfc": rfc,
            "Name": name,
            "CfdiUse": str(receptor.get("uso_cfdi") or "G03").upper(),
            "FiscalRegime": regime,
            "TaxZipCode": zip_code,
        },
        "Items": items,
    }
    if payload.get("serie"):
        cfdi["Serie"] = str(payload["serie"]).strip()
    if payload.get("folio"):
        cfdi["Folio"] = str(payload["folio"]).strip()
    if payload.get("source_quote_id"):
        cfdi["OrderNumber"] = str(payload["source_quote_id"]).strip()
    if payload.get("condiciones"):
        cfdi["PaymentConditions"] = str(payload["condiciones"]).strip()
    return cfdi


def _pick(data, *keys):
    if not isinstance(data, dict):
        return None
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _decode_facturama_file(data):
    candidate = data
    if isinstance(data, dict):
        candidate = _pick(data, "Content", "Data", "File", "Base64")
    if isinstance(candidate, dict):
        candidate = _pick(candidate, "Content", "Data", "Base64")
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("Facturama no devolvió el contenido del archivo")
    candidate = candidate.strip()
    if candidate.startswith("data:") and "," in candidate:
        candidate = candidate.split(",", 1)[1]
    try:
        return b64decode(candidate, validate=True)
    except Exception as exc:
        raise ValueError("Facturama devolvió un archivo inválido") from exc

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
    cfg = _facturama_config()
    return {
        "ok": True,
        "svc": "facturacion",
        "provider": _provider(),
        "environment": "sandbox" if cfg["sandbox"] else "production",
    }, 200


@facturacion_bp.get("/facturama/status")
def api_facturama_status():
    cfg = _facturama_config()
    if not cfg["configured"]:
        return jsonify({
            "ok": False,
            "provider": "facturama",
            "environment": "sandbox" if cfg["sandbox"] else "production",
            "error": "Facturama no está configurado",
        }), 503
    try:
        # El perfil fiscal valida autenticación y que el emisor esté preparado.
        profile = _fm_request("GET", "/TaxEntity", timeout=25)
        branches = _fm_request("GET", "/BranchOffice", timeout=25)
        official_test_issuer = str(_pick(profile, "Rfc") or "").strip().upper() == "EKU9003173C9"
        branch_rows = branches if isinstance(branches, list) else []
        expedition_zip = any(
            bool(_pick(_pick(branch, "Address") or {}, "ZipCode")) for branch in branch_rows
        )
        has_rfc = bool(_pick(profile, "Rfc"))
        has_fiscal_regime = bool(str(_pick(profile, "FiscalRegime") or "").strip())
        has_expedition_zip = bool(expedition_zip)
        has_csd = bool(_pick(profile, "Csd"))
        return jsonify({
            "ok": True,
            "provider": "facturama",
            "environment": "sandbox" if cfg["sandbox"] else "production",
            "account_connected": True,
            "issuer_ready": has_rfc and has_fiscal_regime and has_expedition_zip and has_csd,
            "checks": {
                "fiscal_profile": has_rfc,
                "fiscal_regime": has_fiscal_regime,
                "expedition_zip": has_expedition_zip,
                "test_certificate": has_csd,
                "official_test_issuer": official_test_issuer,
            },
        }), 200
    except requests.HTTPError as exc:
        return jsonify({
            "ok": False,
            "provider": "facturama",
            "environment": "sandbox" if cfg["sandbox"] else "production",
            "error": _http_error_detail(exc),
        }), getattr(exc.response, "status_code", 502) or 502
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": str(exc), "stage": "connection"}), 502

# ----------------------------------------------------------------------
# Timbrado (flujo principal)
# ----------------------------------------------------------------------
@facturacion_bp.post("/facturar")
def facturar():
    try:
        payload = request.get_json(force=True) or {}
        provider = _provider()

        if provider == "facturama":
            cfg = _facturama_config()
            try:
                cfdi = _build_facturama_cfdi(payload)
            except (KeyError, TypeError, ValueError) as exc:
                return jsonify({"ok": False, "stage": "validation", "error": str(exc)}), 400
            except requests.HTTPError as exc:
                return jsonify({
                    "ok": False,
                    "provider": "facturama",
                    "stage": "issuer_profile",
                    "error": _http_error_detail(exc),
                }), 400
            except requests.RequestException as exc:
                return jsonify({
                    "ok": False,
                    "provider": "facturama",
                    "stage": "connection",
                    "error": "No se pudo consultar el perfil fiscal de Facturama",
                    "detail": str(exc),
                }), 502

            request_id = str(payload.get("request_id") or "").strip()
            if not request_id:
                return jsonify({
                    "ok": False,
                    "stage": "validation",
                    "error": "Falta el identificador seguro de la solicitud; recarga la página",
                }), 400

            # Serializa el timbrado y reutiliza la respuesta de un doble clic/reintento.
            with _STAMP_LOCK:
                if request_id in _STAMP_RESULTS:
                    cached = dict(_STAMP_RESULTS[request_id])
                    cached["duplicate_prevented"] = True
                    return jsonify(cached), 200
                try:
                    invoice = _fm_request("POST", "/3/cfdis", json_body=cfdi, timeout=75)
                except requests.HTTPError as exc:
                    status_code = getattr(exc.response, "status_code", 400) or 400
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "environment": "sandbox" if cfg["sandbox"] else "production",
                        "stage": "stamp",
                        "error": _http_error_detail(exc),
                    }), 400 if status_code < 500 else 502
                except requests.RequestException as exc:
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "connection",
                        "error": "Facturama no respondió a tiempo. Puedes reintentar sin duplicar.",
                        "detail": str(exc),
                    }), 502

                inv_id = _pick(invoice, "Id")
                uuid = _pick(invoice, "Uuid", "Complement", "FolioFiscal")
                if isinstance(uuid, dict):
                    uuid = _pick(uuid, "TaxStamp", "Uuid")
                if not inv_id:
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "response",
                        "error": "Facturama respondió sin identificador de CFDI",
                    }), 502
                result = {
                    "ok": True,
                    "provider": "facturama",
                    "environment": "sandbox" if cfg["sandbox"] else "production",
                    "invoice_id": str(inv_id),
                    "uuid": uuid or "Timbrado de prueba",
                    "status": _pick(invoice, "Status") or "active",
                    "total": _pick(invoice, "Total"),
                    "pdf_url": f"/api/invoices/{inv_id}/pdf",
                    "xml_url": f"/api/invoices/{inv_id}/xml",
                    "complementos_disponibles": False,
                }
                _STAMP_RESULTS[request_id] = dict(result)
                return jsonify(result), 200

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
            "provider": "facturapi",
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
    if _provider() == "facturama":
        try:
            raw = _fm_request("GET", "/api/cfdi", params={"type": "issued", "status": "all", "page": 0})
        except requests.HTTPError as exc:
            return jsonify({"ok": False, "provider": "facturama", "error": _http_error_detail(exc)}), 400
        rows = raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])
        out = []
        for inv in rows:
            receiver = _pick(inv, "Receiver", "Customer") or {}
            out.append({
                "id": _pick(inv, "Id"),
                "uuid": _pick(inv, "Uuid"),
                "date": _pick(inv, "Date"),
                "total": _pick(inv, "Total"),
                "status": _pick(inv, "Status"),
                "payment_method": _pick(inv, "PaymentMethod"),
                "type": _pick(inv, "CfdiType", "Type") or "I",
                "customer_name": _pick(receiver, "Name", "LegalName"),
                "customer_tax_id": _pick(receiver, "Rfc", "TaxId"),
                "paid": False,
            })
        return jsonify({"ok": True, "provider": "facturama", "data": out}), 200
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
    if _provider() == "facturama":
        try:
            content = _decode_facturama_file(_fm_request("GET", f"/Cfdi/pdf/issued/{inv_id}"))
        except (requests.HTTPError, ValueError) as exc:
            detail = _http_error_detail(exc) if isinstance(exc, requests.HTTPError) else {"message": str(exc)}
            return jsonify({"ok": False, "error": detail}), 400
        return Response(
            content, mimetype="application/pdf",
            headers={"Content-Disposition": f"inline; filename=Factura-{inv_id}.pdf"},
        )
    try:
        content = _fa_get_binary(f"/invoices/{inv_id}/pdf", "application/pdf")
    except requests.HTTPError as e:
        return getattr(e.response, "text", str(e)), 400
    return Response(
        content, mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Factura-{inv_id}.pdf"}
    )


@facturacion_bp.get("/invoices/<inv_id>/xml")
def api_invoice_xml(inv_id):
    if _provider() == "facturama":
        try:
            content = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
        except (requests.HTTPError, ValueError) as exc:
            detail = _http_error_detail(exc) if isinstance(exc, requests.HTTPError) else {"message": str(exc)}
            return jsonify({"ok": False, "error": detail}), 400
    else:
        try:
            content = _fa_get_binary(f"/invoices/{inv_id}/xml", "application/xml")
        except requests.HTTPError as exc:
            return getattr(exc.response, "text", str(exc)), 400
    return Response(
        content, mimetype="application/xml",
        headers={"Content-Disposition": f"inline; filename=Factura-{inv_id}.xml"},
    )

@facturacion_bp.post("/invoices/<inv_id>/cancel")
def api_invoice_cancel(inv_id):
    b = request.get_json(force=True) if request.data else {}
    motive = (b.get("motive") or b.get("reason") or "02").strip()
    params = {"motive": motive}
    sub = (b.get("substitution_folio") or "").strip()
    if motive == "01" and sub:
        params["substitution_folio"] = sub
    if _provider() == "facturama":
        fm_params = {"type": "issued", "motive": motive}
        if motive == "01" and sub:
            fm_params["uuidReplacement"] = sub
        try:
            result = _fm_request("DELETE", f"/api/cfdi/{inv_id}", params=fm_params)
            return jsonify({"ok": True, "provider": "facturama", "result": result}), 200
        except requests.HTTPError as exc:
            return jsonify({
                "ok": False,
                "provider": "facturama",
                "stage": "cancel",
                "status": getattr(exc.response, "status_code", None),
                "error": _http_error_detail(exc),
            }), 400
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

