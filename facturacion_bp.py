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
import smtplib
import mimetypes
import requests
from werkzeug.utils import secure_filename

from cfdi_drive import backup_cfdi, backup_payments_index, load_payments_index
from smtp_mailer import authorized_to_send, send_cfdi_email, smtp_config, trusted_device_token

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
_FISCAL_CATALOG_CACHE = {}
_CFDI_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

_FISCAL_REGIMES = [
    ("601", "General de Ley Personas Morales", False, True),
    ("603", "Personas Morales con Fines no Lucrativos", False, True),
    ("605", "Sueldos y Salarios e Ingresos Asimilados a Salarios", True, False),
    ("606", "Arrendamiento", True, False),
    ("607", "Régimen de Enajenación o Adquisición de Bienes", True, False),
    ("608", "Demás ingresos", True, False),
    ("610", "Residentes en el Extranjero sin Establecimiento Permanente en México", True, True),
    ("611", "Ingresos por Dividendos (socios y accionistas)", True, False),
    ("612", "Personas Físicas con Actividades Empresariales y Profesionales", True, False),
    ("614", "Ingresos por intereses", True, False),
    ("615", "Régimen de los ingresos por obtención de premios", True, False),
    ("616", "Sin obligaciones fiscales", True, True),
    ("620", "Sociedades Cooperativas de Producción que optan por diferir sus ingresos", False, True),
    ("621", "Incorporación Fiscal", True, False),
    ("622", "Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras", True, True),
    ("623", "Opcional para Grupos de Sociedades", False, True),
    ("624", "Coordinados", False, True),
    ("625", "Actividades Empresariales con ingresos a través de Plataformas Tecnológicas", True, False),
    ("626", "Régimen Simplificado de Confianza", True, True),
]
_PAYMENT_FORMS = [
    ("01", "Efectivo"), ("02", "Cheque nominativo"),
    ("03", "Transferencia electrónica de fondos"), ("04", "Tarjeta de crédito"),
    ("05", "Monedero electrónico"), ("06", "Dinero electrónico"),
    ("08", "Vales de despensa"), ("12", "Dación en pago"),
    ("13", "Pago por subrogación"), ("14", "Pago por consignación"),
    ("15", "Condonación"), ("17", "Compensación"), ("23", "Novación"),
    ("24", "Confusión"), ("25", "Remisión de deuda"),
    ("26", "Prescripción o caducidad"), ("27", "A satisfacción del acreedor"),
    ("28", "Tarjeta de débito"), ("29", "Tarjeta de servicios"),
    ("30", "Aplicación de anticipos"), ("31", "Intermediario pagos"),
    ("99", "Por definir"),
]
_PAYMENT_METHODS = [("PUE", "Pago en una sola exhibición"), ("PPD", "Pago en parcialidades o diferido")]
_CFDI_USES = [
    ("G01", "Adquisición de mercancías", True, True),
    ("G02", "Devoluciones, descuentos o bonificaciones", True, True),
    ("G03", "Gastos en general", True, True),
    ("I01", "Construcciones", True, True),
    ("I02", "Mobiliario y equipo de oficina por inversiones", True, True),
    ("I03", "Equipo de transporte", True, True),
    ("I04", "Equipo de cómputo y accesorios", True, True),
    ("I05", "Dados, troqueles, moldes, matrices y herramental", True, True),
    ("I06", "Comunicaciones telefónicas", True, True),
    ("I07", "Comunicaciones satelitales", True, True),
    ("I08", "Otra maquinaria y equipo", True, True),
    ("D01", "Honorarios médicos, dentales y gastos hospitalarios", True, False),
    ("D02", "Gastos médicos por incapacidad o discapacidad", True, False),
    ("D03", "Gastos funerales", True, False),
    ("D04", "Donativos", True, False),
    ("D05", "Intereses reales pagados por créditos hipotecarios", True, False),
    ("D06", "Aportaciones voluntarias al SAR", True, False),
    ("D07", "Primas por seguros de gastos médicos", True, False),
    ("D08", "Gastos de transportación escolar obligatoria", True, False),
    ("D09", "Depósitos en cuentas para el ahorro y pensiones", True, False),
    ("D10", "Pagos por servicios educativos (colegiaturas)", True, False),
    ("S01", "Sin efectos fiscales", True, True),
]

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
INDEX = DATA_DIR / "pagos_index.json"

# ----------------------------------------------------------------------
# Índice local de REP
# ----------------------------------------------------------------------
def _read_index():
    if INDEX.exists():
        try:
            local = json.loads(INDEX.read_text("utf-8"))
            if isinstance(local, dict) and local:
                return local
        except Exception:
            pass
    try:
        remote = load_payments_index()
        if remote:
            INDEX.write_text(json.dumps(remote, ensure_ascii=False, indent=2), encoding="utf-8")
            return remote
    except Exception:
        pass
    return {}

def _write_index(d):
    INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        backup_payments_index(d)
    except Exception as exc:
        try:
            current_app.logger.warning("No se pudo respaldar el índice de pagos en Drive: %s", exc)
        except RuntimeError:
            pass

def _payment_entries(record):
    """Devuelve pagos activos y conserva compatibilidad con el índice anterior."""
    if not isinstance(record, dict):
        return []
    if isinstance(record.get("payments"), list):
        return [p for p in record["payments"] if isinstance(p, dict) and p.get("status", "active") == "active"]
    if record.get("rep_id") and record.get("status", "active") == "active":
        return [record]
    return []

def _payment_summary(record, invoice_total=0):
    payments = _payment_entries(record)
    paid = sum((float(p.get("amount") or 0) for p in payments), 0.0)
    total = float(invoice_total or 0)
    remaining = max(0.0, round(total - paid, 2))
    if payments and payments[-1].get("remaining_balance") is not None:
        remaining = max(0.0, round(float(payments[-1].get("remaining_balance") or 0), 2))
    return {
        "payments": payments,
        "payment_count": len(payments),
        "paid_amount": round(paid, 2),
        "remaining_balance": remaining,
        "paid": bool(payments) and remaining <= 0,
    }

def _rep_cancellation_state(rep_id):
    """Ubica un REP y confirma que sea la última parcialidad de su factura."""
    wanted = str(rep_id or "")
    for invoice_uuid, record in _read_index().items():
        entries = _payment_entries(record)
        for position, payment in enumerate(entries):
            if str(payment.get("rep_id") or "") == wanted:
                return {
                    "tracked": True,
                    "invoice_uuid": invoice_uuid,
                    "is_latest": position == len(entries) - 1,
                    "partiality_number": payment.get("partiality_number") or position + 1,
                    "later_count": len(entries) - position - 1,
                }
    return {"tracked": False, "is_latest": True, "later_count": 0}

def _remove_rep_by_id(rep_id: str):
    """Elimina del índice el REP cuyo id coincide, para re-habilitar complemento."""
    idx = _read_index()
    changed = False
    for k, v in list(idx.items()):
        if not isinstance(v, dict):
            continue
        if isinstance(v.get("payments"), list):
            kept = [p for p in v["payments"] if str(p.get("rep_id")) != str(rep_id)]
            if len(kept) != len(v["payments"]):
                changed = True
                if kept:
                    v["payments"] = kept
                    v["remaining_balance"] = kept[-1].get("remaining_balance", v.get("remaining_balance"))
                else:
                    idx.pop(k)
        elif str(v.get("rep_id")) == str(rep_id):
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


def _valid_cfdi_uuid(value):
    """Acepta solamente un folio fiscal UUID confirmado, nunca textos sustitutos."""
    return bool(_CFDI_UUID_RE.fullmatch(str(value or "").strip()))


def _facturama_invoice_row(inv):
    """Normaliza tanto el resultado plano de búsqueda como el detalle de Facturama."""
    receiver = _pick(inv, "Receiver", "Customer") or {}
    uuid = _pick(inv, "Uuid", "FolioFiscal")
    if not uuid:
        complement = _pick(inv, "Complement") or {}
        uuid = _pick(_pick(complement, "TaxStamp") or {}, "Uuid")
    active = _pick(inv, "IsActive")
    status = _pick(inv, "Status") or ("active" if active is not False else "canceled")
    cfdi_type = str(_pick(inv, "CfdiType", "Type") or "I").strip().lower()
    cfdi_type = {
        "ingreso": "I", "pago": "P", "egreso": "E",
        "traslado": "T", "nomina": "N", "nómina": "N",
    }.get(cfdi_type, cfdi_type.upper()[:1])
    payment_method = str(_pick(inv, "PaymentMethod") or "").strip().upper()[:3]
    return {
        "id": _pick(inv, "Id"),
        "uuid": str(uuid or "").strip(),
        "folio": str(_pick(inv, "Folio") or "").strip(),
        "date": _pick(inv, "Date"),
        "total": _pick(inv, "Total"),
        "status": status,
        "payment_method": payment_method,
        "type": cfdi_type or "I",
        "customer_name": _pick(receiver, "Name", "LegalName", "TaxName") or _pick(inv, "TaxName"),
        "customer_tax_id": _pick(receiver, "Rfc", "TaxId") or _pick(inv, "Rfc"),
        "customer_email": _pick(receiver, "Email") or _pick(inv, "Email", "ReceiverEmail"),
        "paid": False,
    }


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
    profile_regime = str(_pick(profile, "FiscalRegime") or "").strip()
    profile_rfc = str(_pick(profile, "Rfc") or "").strip().upper()
    if not profile_regime:
        raise ValueError("Falta guardar el régimen fiscal del emisor en el perfil de Facturama")
    if _facturama_config()["sandbox"] and profile_rfc == "EKU9003173C9" and profile_regime != "601":
        raise ValueError("El RFC oficial de pruebas EKU9003173C9 requiere régimen fiscal 601")
    csd = _pick(profile, "Csd") or {}
    if not (_pick(csd, "Certificate") and _pick(csd, "PrivateKey")):
        raise ValueError("Falta cargar el certificado .cer y la llave .key de pruebas en Facturama")
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


def _facturama_receiver_validation(receptor):
    """Valida contra Facturama/SAT los cuatro datos obligatorios del receptor."""
    rfc, name, zip_code, regime = _validate_receiver(receptor)
    result = _fm_request("POST", "/customers/validate", json_body={
        "Rfc": rfc,
        "Name": name,
        "ZipCode": zip_code,
        "FiscalRegime": regime,
    }, timeout=30)
    checks = (
        ("ExistRfc", "rfc", "El RFC no fue localizado o no está habilitado para recibir CFDI."),
        ("MatchName", "nombre", "La razón social no coincide con la registrada ante el SAT."),
        ("MatchZipCode", "cp", "El código postal no coincide con el domicilio fiscal registrado ante el SAT."),
        ("MatchFiscalRegime", "regimen_fiscal", "El régimen fiscal no coincide con el registrado ante el SAT."),
    )
    field_errors = {
        field: message
        for key, field, message in checks
        if _pick(result, key) is not True
    }
    return result, field_errors


def _build_facturama_cfdi(payload):
    cfg = _facturama_config()
    receptor = payload.get("receptor") or {}
    rfc, name, zip_code, regime = _validate_receiver(receptor)
    profile_expedition, profile_tax_zip = _facturama_issuer_locations()
    expedition_zip = str(payload.get("expedition_place") or profile_expedition).strip()
    if not re.fullmatch(r"\d{5}", expedition_zip):
        raise ValueError("Falta FACTURAMA_EXPEDITION_ZIP con el código postal de expedición")

    items = []
    retention_cfg = payload.get("retenciones") or {}
    apply_retentions = bool(retention_cfg.get("aplicar"))
    isr_rate = _number(retention_cfg.get("isr"), Decimal("0.0125")) if apply_retentions else Decimal("0")
    iva_ret_rate = _number(retention_cfg.get("iva"), Decimal("0.1066666667")) if apply_retentions else Decimal("0")
    allowed_isr = {Decimal("0"), Decimal("0.0125"), Decimal("0.10")}
    allowed_iva_ret = {
        Decimal("0"), Decimal("0.03"), Decimal("0.04"), Decimal("0.053333"),
        Decimal("0.06"), Decimal("0.106667"), Decimal("0.1066666667"), Decimal("0.16"),
    }
    if isr_rate not in allowed_isr or iva_ret_rate not in allowed_iva_ret:
        raise ValueError("Selecciona tasas de retención permitidas")
    for index, raw in enumerate(payload.get("items") or [], start=1):
        description = str(raw.get("descripcion", "")).strip() or f"Concepto {index}"
        quantity = _number(raw.get("cantidad"), 1)
        unit_price = _money(raw.get("precio_unitario", raw.get("valor_unitario", 0)))
        tax_rate = _number(raw.get("tasa_iva"), 0)
        if quantity <= 0 or unit_price < 0 or tax_rate < 0:
            raise ValueError(f"Valores inválidos en el concepto {index}")
        subtotal = _money(quantity * unit_price)
        discount = _money(raw.get("descuento", 0))
        if discount < 0 or discount > subtotal:
            raise ValueError(f"Descuento inválido en el concepto {index}")
        tax_base = _money(subtotal - discount)
        tax_total = _money(tax_base * tax_rate)
        isr_total = _money(tax_base * isr_rate)
        item_iva_ret_rate = iva_ret_rate
        iva_ret_total = _money(tax_base * item_iva_ret_rate)
        unit_code = str(raw.get("clave_unidad") or "E48").strip().upper()
        item = {
            "ProductCode": str(raw.get("clave_prod_serv") or "85121600").strip(),
            "IdentificationNumber": str(raw.get("codigo") or index)[:50],
            "Description": description,
            "Unit": _facturama_unit_name(unit_code),
            "UnitCode": unit_code,
            "UnitPrice": float(unit_price),
            "Quantity": float(quantity),
            "Subtotal": float(subtotal),
            "Discount": float(discount),
            "TaxObject": "02" if tax_rate > 0 or apply_retentions else "01",
            "Total": float(_money(tax_base + tax_total - isr_total - iva_ret_total)),
        }
        taxes = []
        if tax_rate > 0:
            taxes.append({
                "Total": float(tax_total),
                "Name": "IVA",
                "Base": float(tax_base),
                "Rate": float(tax_rate),
                "IsRetention": False,
                "IsFederalTax": True,
            })
        if apply_retentions and iva_ret_total > 0:
            taxes.append({
                "Total": float(iva_ret_total), "Name": "IVA RET", "Base": float(tax_base),
                "Rate": float(item_iva_ret_rate), "IsRetention": True, "IsFederalTax": True,
            })
        if apply_retentions and isr_total > 0:
            taxes.append({
                "Total": float(isr_total), "Name": "ISR", "Base": float(tax_base),
                "Rate": float(isr_rate), "IsRetention": True, "IsFederalTax": True,
            })
        if taxes:
            item["Taxes"] = taxes
        items.append(item)
    if not items:
        raise ValueError("Agrega al menos un concepto")

    payment_method = str(payload.get("metodo_pago") or "PUE").upper()
    payment_form = str(payload.get("forma_pago") or "03").zfill(2)
    if payment_method == "PPD":
        payment_form = "99"
    cfdi_use = str(receptor.get("uso_cfdi") or "G03").upper()
    allowed_uses = {row[0] for row in _CFDI_USES}
    if cfdi_use not in allowed_uses:
        raise ValueError("Uso de CFDI fuera del catálogo vigente para facturas")
    if cfdi_use.startswith("D") and len(rfc) != 13:
        raise ValueError("Los usos de CFDI para deducciones personales solo aplican a personas físicas")
    cfdi = {
        "Currency": str(payload.get("moneda") or "MXN").upper(),
        "ExpeditionPlace": expedition_zip,
        "TaxZipCode": profile_tax_zip,
        "CfdiType": "I",
        "PaymentForm": payment_form,
        "PaymentMethod": payment_method,
        "Receiver": {
            "Rfc": rfc,
            "Name": name,
            "CfdiUse": cfdi_use,
            "FiscalRegime": regime,
            "TaxZipCode": zip_code,
        },
        "Items": items,
    }
    requested_date = str(payload.get("fecha_emision") or "").strip()
    if requested_date:
        cfdi["Date"] = requested_date
    elif not cfg["sandbox"]:
        cfdi["Date"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if payload.get("serie"):
        cfdi["Serie"] = str(payload["serie"]).strip()
    if payload.get("folio"):
        cfdi["Folio"] = str(payload["folio"]).strip()
    order_number = str(payload.get("numero_orden_compra") or "").strip()
    if len(order_number) > 100:
        raise ValueError("El número de orden de compra admite hasta 100 caracteres")
    if order_number:
        cfdi["OrderNumber"] = order_number
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


def _catalog_entries(raw, allowed_values):
    rows = raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])
    result = []
    seen = set()
    for row in rows:
        value = str(_pick(row, "Value", "Code", "Id") or "").strip().upper()
        name = str(_pick(row, "Name", "Description") or "").strip()
        if value in allowed_values and value not in seen:
            result.append({"value": value, "name": name or value})
            seen.add(value)
    return result


def _fallback_fiscal_catalogs(rfc=""):
    rfc = str(rfc or "").strip().upper()
    is_natural = len(rfc) == 13
    is_moral = len(rfc) == 12

    def eligible(natural, moral):
        return not (is_natural or is_moral) or (is_natural and natural) or (is_moral and moral)

    return {
        "regimes": [
            {"value": value, "name": name}
            for value, name, natural, moral in _FISCAL_REGIMES
            if eligible(natural, moral)
        ],
        "cfdi_uses": [
            {"value": value, "name": name}
            for value, name, natural, moral in _CFDI_USES
            if eligible(natural, moral)
        ],
        "payment_forms": [{"value": value, "name": name} for value, name in _PAYMENT_FORMS],
        "payment_methods": [{"value": value, "name": name} for value, name in _PAYMENT_METHODS],
    }


def _facturama_fiscal_catalogs(rfc=""):
    rfc = str(rfc or "").strip().upper()
    cache_key = (_facturama_config()["sandbox"], rfc)
    if cache_key in _FISCAL_CATALOG_CACHE:
        return _FISCAL_CATALOG_CACHE[cache_key]

    fallback = _fallback_fiscal_catalogs(rfc)
    requests_to_make = {
        "regimes": ("/Catalogs/FiscalRegimens", {"rfc": rfc} if rfc else None, {row[0] for row in _FISCAL_REGIMES}),
        "cfdi_uses": ("/Catalogs/CfdiUses", {"keyword": rfc} if rfc else None, {row[0] for row in _CFDI_USES}),
        "payment_forms": ("/Catalogs/PaymentForms", None, {row[0] for row in _PAYMENT_FORMS}),
        "payment_methods": ("/Catalogs/PaymentMethods", None, {row[0] for row in _PAYMENT_METHODS}),
    }
    catalogs = {}
    remote_count = 0
    for key, (path, params, allowed) in requests_to_make.items():
        try:
            entries = _catalog_entries(_fm_request("GET", path, params=params, timeout=25), allowed)
        except requests.RequestException:
            entries = []
        catalogs[key] = entries or fallback[key]
        if entries:
            remote_count += 1
    catalogs["source"] = "facturama" if remote_count == len(requests_to_make) else "respaldo_local"
    _FISCAL_CATALOG_CACHE[cache_key] = catalogs
    return catalogs


@facturacion_bp.get("/catalogos/fiscales")
def api_fiscal_catalogs():
    rfc = str(request.args.get("rfc") or "").strip().upper()
    if rfc and not re.fullmatch(r"[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}", rfc):
        return jsonify({"ok": False, "error": "RFC inválido para filtrar los catálogos"}), 400
    if _provider() != "facturama":
        return jsonify({"ok": True, "source": "respaldo_local", **_fallback_fiscal_catalogs(rfc)}), 200
    return jsonify({"ok": True, **_facturama_fiscal_catalogs(rfc)}), 200


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


def _backup_facturama_cfdi(inv_id, uuid, client_name, internal_folio, document_type="Factura"):
    """Descarga PDF/XML y los respalda sin comprometer un timbrado exitoso."""
    try:
        pdf = _decode_facturama_file(_fm_request("GET", f"/Cfdi/pdf/issued/{inv_id}"))
        xml = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
        return backup_cfdi(
            client_name, internal_folio, uuid, pdf, xml, document_type=document_type
        )
    except Exception as exc:
        current_app.logger.warning("CFDI timbrado, pero falló su respaldo en Drive: %s", exc)
        return {"ok": False, "error": str(exc)}


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
        fiscal_regime_code = str(_pick(profile, "FiscalRegime") or "").strip()
        has_fiscal_regime = bool(fiscal_regime_code)
        has_tax_name = bool(str(_pick(profile, "TaxName") or "").strip())
        tax_address = _pick(profile, "TaxAddress") or {}
        has_tax_address_zip = bool(str(_pick(tax_address, "ZipCode") or "").strip())
        regime_matches_issuer = not official_test_issuer or fiscal_regime_code == "601"
        has_expedition_zip = bool(expedition_zip)
        csd = _pick(profile, "Csd") or {}
        has_certificate = bool(str(_pick(csd, "Certificate") or "").strip())
        has_private_key = bool(str(_pick(csd, "PrivateKey") or "").strip())
        has_csd = has_certificate and has_private_key
        return jsonify({
            "ok": True,
            "provider": "facturama",
            "environment": "sandbox" if cfg["sandbox"] else "production",
            "account_connected": True,
            "issuer_ready": (
                has_rfc and has_fiscal_regime and regime_matches_issuer
                and has_expedition_zip and has_csd
            ),
            "checks": {
                "fiscal_profile": has_rfc,
                "fiscal_regime": has_fiscal_regime,
                "regime_matches_issuer": regime_matches_issuer,
                "expedition_zip": has_expedition_zip,
                "test_certificate": has_csd,
                "certificate_file": has_certificate,
                "private_key_file": has_private_key,
                "official_test_issuer": official_test_issuer,
                "tax_name": has_tax_name,
                "tax_address_zip": has_tax_address_zip,
            },
            "fiscal_regime_code": fiscal_regime_code,
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

            # El padrón sandbox no representa el padrón real del SAT y puede
            # rechazar RFC reales. En pruebas se conserva la validación local;
            # en producción se exige además la coincidencia fiscal remota.
            if not cfg["sandbox"]:
                try:
                    _, field_errors = _facturama_receiver_validation(payload.get("receptor") or {})
                except requests.HTTPError as exc:
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "receiver_validation",
                        "error": "No se pudieron validar los datos fiscales del receptor en Facturama.",
                        "detail": _http_error_detail(exc),
                    }), 502
                except requests.RequestException as exc:
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "receiver_validation",
                        "error": "Facturama no respondió durante la validación del receptor. Intenta nuevamente.",
                        "detail": str(exc),
                    }), 502
                if field_errors:
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "receiver_validation",
                        "error": next(iter(field_errors.values())),
                        "field_errors": field_errors,
                    }), 400

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
                uuid = _pick(invoice, "Uuid", "FolioFiscal")
                if not uuid:
                    complement = _pick(invoice, "Complement") or {}
                    tax_stamp = _pick(complement, "TaxStamp") or {}
                    uuid = _pick(tax_stamp, "Uuid")
                if not inv_id:
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "response",
                        "error": "Facturama respondió sin identificador de CFDI",
                    }), 502
                if not _valid_cfdi_uuid(uuid):
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "environment": "sandbox" if cfg["sandbox"] else "production",
                        "stage": "stamp_confirmation",
                        "error": "Facturama no confirmó el timbrado con un folio fiscal (UUID). La factura no se agregará al panel.",
                    }), 502
                result = {
                    "ok": True,
                    "provider": "facturama",
                    "environment": "sandbox" if cfg["sandbox"] else "production",
                    "invoice_id": str(inv_id),
                    "uuid": str(uuid).strip(),
                    "status": _pick(invoice, "Status") or "active",
                    "total": _pick(invoice, "Total"),
                    "receiver_validation": "local_sandbox" if cfg["sandbox"] else "sat_confirmed",
                    "pdf_url": f"/api/invoices/{inv_id}/pdf",
                    "xml_url": f"/api/invoices/{inv_id}/xml",
                    "complementos_disponibles": False,
                }
                internal_folio = str(
                    payload.get("folio") or _pick(invoice, "Folio")
                    or payload.get("source_quote_id") or inv_id
                ).strip()
                drive_backup = _backup_facturama_cfdi(
                    str(inv_id), str(uuid).strip(),
                    str(payload.get("cliente_carpeta")
                        or (payload.get("receptor") or {}).get("nombre") or "SIN_CLIENTE"),
                    internal_folio,
                    "Factura",
                )
                result["drive_backup"] = drive_backup
                result["internal_folio"] = internal_folio
                if not drive_backup.get("ok"):
                    result["warning"] = (
                        "La factura se timbró correctamente, pero Drive no confirmó el respaldo. "
                        "Puedes descargarla desde el panel de facturación."
                    )
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
        paid_idx = _read_index()
        for inv in rows:
            normalized = _facturama_invoice_row(inv)
            summary = _payment_summary(paid_idx.get(normalized["uuid"]), normalized["total"])
            normalized.update({
                "paid": normalized["type"] == "I" and summary["paid"],
                "payment_count": summary["payment_count"] if normalized["type"] == "I" else 0,
                "paid_amount": summary["paid_amount"] if normalized["type"] == "I" else 0,
                "remaining_balance": summary["remaining_balance"] if normalized["type"] == "I" else 0,
            })
            # Los intentos rechazados pueden tener Id, pero no folio fiscal.
            if normalized["id"] and _valid_cfdi_uuid(normalized["uuid"]):
                out.append(normalized)
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
        summary = _payment_summary(paid_idx.get(uuid), inv.get("total"))
        paid = summary["paid"] if inv.get("type") == "I" else False
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
            "payment_count": summary["payment_count"] if inv.get("type") == "I" else 0,
            "paid_amount": summary["paid_amount"] if inv.get("type") == "I" else 0,
            "remaining_balance": summary["remaining_balance"] if inv.get("type") == "I" else 0,
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


@facturacion_bp.post("/invoices/<inv_id>/email")
def api_invoice_email(inv_id):
    body = (request.get_json(silent=True) or {}) if request.is_json else request.form.to_dict()
    recipient = str(body.get("email") or "").strip()
    folio = str(body.get("folio") or body.get("uuid") or inv_id).strip()
    subject = str(body.get("subject") or f"Factura HSC {folio}")
    comments = str(body.get("message") or (
        "Buen día, estimado cliente. Envío la factura solicitada.\n\n"
        "De antemano muchas gracias.\n\n"
        "Quedo a sus órdenes.\n\n"
        "Ing. Héctor Silva Cid\n\n"
        "Cel: 5527605496"
    ))
    if not smtp_config()["configured"]:
        return jsonify({"ok": False, "error": "Falta configurar el correo de salida de HSC en Render."}), 503
    trusted_cookie = request.cookies.get("hsc_mail_trusted", "")
    supplied_key = body.get("send_key")
    if not authorized_to_send(supplied_key, trusted_cookie):
        return jsonify({"ok": False, "error": "La clave de envío no es correcta."}), 403

    extras = []
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".xls", ".xlsx", ".csv"}
    files = [upload for upload in request.files.getlist("attachments") if upload and upload.filename]
    if len(files) > 8:
        return jsonify({"ok": False, "error": "Puedes adjuntar como máximo 8 archivos adicionales."}), 400
    total_size = 0
    for upload in files:
        filename = secure_filename(upload.filename)
        suffix = Path(filename).suffix.lower()
        if not filename or suffix not in allowed:
            return jsonify({"ok": False, "error": f"El archivo {upload.filename} no tiene un formato permitido."}), 400
        content = upload.read()
        total_size += len(content)
        if total_size > 15 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Los archivos adicionales superan el límite total de 15 MB."}), 400
        extras.append({
            "data": content,
            "filename": filename,
            "content_type": upload.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        })
    try:
        if _provider() == "facturama":
            pdf = _decode_facturama_file(_fm_request("GET", f"/Cfdi/pdf/issued/{inv_id}"))
            xml = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
        else:
            pdf = _fa_get_binary(f"/invoices/{inv_id}/pdf", "application/pdf")
            xml = _fa_get_binary(f"/invoices/{inv_id}/xml", "application/xml")
        result = send_cfdi_email(
            recipient=recipient,
            subject=subject,
            body=comments,
            pdf_bytes=pdf,
            xml_bytes=xml,
            folio=folio,
            extra_attachments=extras,
        )
        copy_warning = result.get("sent_copy_saved") is False
        response = jsonify({
            "ok": True,
            "message": (
                f"Factura enviada a {result.get('recipient') or recipient}."
                + (" CarrierZone no confirmó la copia en Enviados." if copy_warning else "")
            ),
            "sent_copy_saved": not copy_warning,
            "result": result,
        })
        response.set_cookie(
            "hsc_mail_trusted", trusted_device_token(), max_age=315360000,
            secure=True, httponly=True, samesite="Strict", path="/api",
        )
        return response, 200
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except smtplib.SMTPAuthenticationError:
        current_app.logger.exception("CarrierZone rechazó la autenticación SMTP")
        return jsonify({"ok": False, "error": "CarrierZone rechazó el usuario o la contraseña del correo."}), 502
    except (smtplib.SMTPException, OSError, requests.HTTPError) as exc:
        current_app.logger.exception("No se pudo enviar el CFDI por correo")
        return jsonify({"ok": False, "error": "No se pudo enviar el correo. La factura permanece disponible para reintentar."}), 502
    except Exception:
        current_app.logger.exception("Error inesperado al preparar el CFDI para correo")
        return jsonify({"ok": False, "error": "No se pudieron preparar los archivos para enviarlos. Intenta nuevamente."}), 500


@facturacion_bp.get("/email/status")
def api_email_status():
    return jsonify({
        "ok": True,
        "configured": smtp_config()["configured"],
        "trusted": authorized_to_send("", request.cookies.get("hsc_mail_trusted", "")),
    }), 200

@facturacion_bp.post("/invoices/<inv_id>/cancel")
def api_invoice_cancel(inv_id):
    b = request.get_json(force=True) if request.data else {}
    motive = (b.get("motive") or b.get("reason") or "02").strip()
    params = {"motive": motive}
    sub = (b.get("substitution_folio") or "").strip()
    if motive == "01" and sub:
        params["substitution_folio"] = sub
    if _provider() == "facturama":
        rep_state = _rep_cancellation_state(inv_id)
        if rep_state["tracked"] and not rep_state["is_latest"]:
            return jsonify({
                "ok": False,
                "stage": "cancel_order",
                "error": (
                    f"No puedes cancelar la parcialidad {rep_state['partiality_number']} porque tiene "
                    f"{rep_state['later_count']} complemento(s) posterior(es). Cancela primero el más reciente."
                ),
            }), 409
        fm_params = {"type": "issued", "motive": motive}
        if motive == "01" and sub:
            fm_params["uuidReplacement"] = sub
        try:
            result = _fm_request("DELETE", f"/api/cfdi/{inv_id}", params=fm_params)
            if rep_state["tracked"]:
                _remove_rep_by_id(inv_id)
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
        rep_state = _rep_cancellation_state(inv_id) if inv.get("type") == "P" else {"is_latest": True}
        if not rep_state["is_latest"]:
            return jsonify({
                "ok": False, "stage": "cancel_order",
                "error": "Cancela primero el complemento de pago más reciente.",
            }), 409
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

