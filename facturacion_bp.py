# facturacion_bp.py — versión completa
from flask import Blueprint, request, jsonify, Response, current_app
from datetime import datetime, timedelta
from calendar import monthrange
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from base64 import b64decode
from threading import Lock
from uuid import uuid4
from xml.etree import ElementTree as ET
import hashlib
import hmac
import os
import json
import re
import smtplib
import mimetypes
import requests
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename

from cfdi_drive import (
    backup_cfdi, backup_json_file, backup_payments_index,
    download_invoice_support_documents, list_invoice_support_documents,
    load_json_file, load_payments_index, move_pending_documents,
)
from smtp_mailer import authorized_to_send, send_cfdi_email, smtp_config, trusted_device_token
from email_tracking import delivery_status, read_email_deliveries, record_email_delivery
from factura_pdf_hsc import build_invoice_pdf_bytes, parse_cfdi
import notification_center as notices

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
_PAYMENT_SYNC_LOCK = Lock()
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
INVOICE_TEMPLATES = DATA_DIR / "plantillas_factura.json"
INVOICE_TEMPLATES_DRIVE_FILE = "HSC-plantillas-factura.json"
_TEMPLATE_LOCK = Lock()
INVOICE_SCHEDULES = DATA_DIR / "facturas_programadas.json"
INVOICE_SCHEDULES_DRIVE_FILE = "HSC-facturas-programadas.json"
PUSH_SUBSCRIPTIONS = DATA_DIR / "notificaciones_dispositivos.json"
PUSH_SUBSCRIPTIONS_DRIVE_FILE = "HSC-notificaciones-dispositivos.json"
_SCHEDULE_LOCK = Lock()
_SCHEDULE_RUN_LOCK = Lock()
_PUSH_LOCK = Lock()
STAMP_REQUESTS = DATA_DIR / "solicitudes_timbrado.json"
STAMP_REQUESTS_DRIVE_FILE = "HSC-solicitudes-timbrado.json"

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
            notices.publish("Respaldo de pagos pendiente", "Los saldos se guardaron localmente, pero Drive no confirmó su respaldo.",
                            category="respaldos", key=f"payments-backup-{datetime.now().date()}", level="warning", url="/facturacion")
        except RuntimeError:
            pass


def _read_invoice_templates(refresh=False):
    with _TEMPLATE_LOCK:
        if not refresh and INVOICE_TEMPLATES.exists():
            try:
                value = json.loads(INVOICE_TEMPLATES.read_text("utf-8"))
                if isinstance(value, list):
                    return value
            except Exception:
                pass
        try:
            value = load_json_file(INVOICE_TEMPLATES_DRIVE_FILE, default=[])
            value = value if isinstance(value, list) else []
            INVOICE_TEMPLATES.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return value
        except Exception:
            return []


def _write_invoice_templates(items):
    with _TEMPLATE_LOCK:
        INVOICE_TEMPLATES.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            backup_json_file(INVOICE_TEMPLATES_DRIVE_FILE, items)
            return True
        except Exception as exc:
            current_app.logger.warning("No se pudieron respaldar las plantillas en Drive: %s", exc)
            return False


def _read_json_collection(path, drive_name, lock):
    with lock:
        if path.exists():
            try:
                value = json.loads(path.read_text("utf-8"))
                if isinstance(value, list):
                    return value
            except Exception:
                pass
        try:
            value = load_json_file(drive_name, default=[])
            value = value if isinstance(value, list) else []
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return value
        except Exception:
            return []


def _write_json_collection(path, drive_name, items, lock):
    with lock:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            backup_json_file(drive_name, items)
            return True
        except Exception as exc:
            current_app.logger.warning("No se pudo respaldar %s en Drive: %s", drive_name, exc)
            return False


def _read_invoice_schedules():
    return _read_json_collection(INVOICE_SCHEDULES, INVOICE_SCHEDULES_DRIVE_FILE, _SCHEDULE_LOCK)


def _write_invoice_schedules(items):
    return _write_json_collection(INVOICE_SCHEDULES, INVOICE_SCHEDULES_DRIVE_FILE, items, _SCHEDULE_LOCK)


def _read_push_subscriptions():
    return _read_json_collection(PUSH_SUBSCRIPTIONS, PUSH_SUBSCRIPTIONS_DRIVE_FILE, _PUSH_LOCK)


def _write_push_subscriptions(items):
    return _write_json_collection(PUSH_SUBSCRIPTIONS, PUSH_SUBSCRIPTIONS_DRIVE_FILE, items, _PUSH_LOCK)


def _local_now():
    return datetime.now(ZoneInfo("America/Mexico_City"))


def _next_monthly_run(day, at_time, *, after=None):
    now = after or _local_now()
    hour, minute = (int(value) for value in at_time.split(":", 1))
    year, month = now.year, now.month
    for _ in range(14):
        actual_day = min(int(day), monthrange(year, month)[1])
        candidate = datetime(year, month, actual_day, hour, minute, tzinfo=now.tzinfo)
        if candidate > now:
            return candidate
        month += 1
        if month == 13:
            month, year = 1, year + 1
    raise ValueError("No se pudo calcular la siguiente ejecución.")


def _template_invoice_payload(template, schedule, run_key):
    receiver = template.get("receiver") or {}
    conditions = template.get("conditions") or {}
    retentions = template.get("retentions") or {}
    return {
        "receptor": {
            "rfc": str(receiver.get("tax_id") or "").strip().upper(),
            "nombre": str(receiver.get("name") or "").strip(),
            "cp": str(receiver.get("zip") or "").strip(),
            "regimen_fiscal": int(receiver.get("tax_system") or 0),
            "uso_cfdi": str(receiver.get("cfdi_use") or "G03"),
            "email": str(receiver.get("email") or "").strip(),
        },
        "forma_pago": str(conditions.get("payment_form") or "03"),
        "metodo_pago": str(conditions.get("payment_method") or "PUE"),
        "serie": str(conditions.get("serie") or "HSC").strip(),
        "numero_orden_compra": str(conditions.get("purchase_order") or "").strip(),
        "alias_factura": str(conditions.get("invoice_alias") or "").strip(),
        "condiciones": str(conditions.get("notes") or "").strip(),
        "source_quote_id": "",
        "quote_folio": "",
        "cliente_carpeta": str(template.get("client_name") or receiver.get("name") or "").strip(),
        "request_id": f"schedule-{schedule['id']}-{run_key}",
        "confirmed": True,
        "retenciones": {
            "aplicar": bool(float(retentions.get("isr") or 0) or float(retentions.get("iva") or 0)),
            "isr": float(retentions.get("isr") or 0),
            "iva": float(retentions.get("iva") or 0),
        },
        "items": [{
            "codigo": "",
            "descripcion": str(item.get("description") or "Concepto"),
            "clave_prod_serv": str(item.get("product_key") or "85121600"),
            "clave_unidad": str(item.get("unit_key") or "E48"),
            "precio_unitario": float(item.get("unit_price") or 0),
            "valor_unitario": float(item.get("unit_price") or 0),
            "cantidad": float(item.get("quantity") or 1),
            "tasa_iva": float(item.get("tax_rate") or 0),
            "descuento": float(item.get("discount") or 0),
        } for item in (template.get("items") or [])],
    }


def _push_configured():
    return bool(os.getenv("VAPID_PUBLIC_KEY", "").strip() and os.getenv("VAPID_PRIVATE_KEY", "").strip())


def _send_push_notifications(title, body, url="/facturacion?tab=plantillas", tag="hsc-facturas"):
    notices.publish(title, body, category="facturas", key=tag, url=url)
    subscriptions = _read_push_subscriptions()
    if not subscriptions or not _push_configured():
        return {"sent": 0, "configured": _push_configured(), "devices": len(subscriptions)}
    try:
        from pywebpush import webpush
    except ImportError:
        current_app.logger.error("Falta instalar pywebpush para enviar notificaciones.")
        return {"sent": 0, "configured": False, "devices": len(subscriptions)}
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    kept, sent = [], 0
    for item in subscriptions:
        try:
            webpush(
                subscription_info=item.get("subscription") or {},
                data=payload,
                vapid_private_key=os.getenv("VAPID_PRIVATE_KEY", "").strip(),
                vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:hectors@hscrefrigeracion.com")},
                ttl=86400,
            )
            kept.append(item)
            sent += 1
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in {404, 410}:
                kept.append(item)
                current_app.logger.warning("No se pudo enviar una notificación push: %s", exc)
    if len(kept) != len(subscriptions):
        _write_push_subscriptions(kept)
    return {"sent": sent, "configured": True, "devices": len(subscriptions)}


def _stamp_fingerprint(payload):
    """Identifica el contenido fiscal, excluyendo únicamente la llave del intento."""
    clean = {key: value for key, value in payload.items() if key != "request_id"}
    raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_stamp_registry():
    """Carga el seguro antirrepetición local y, después de un despliegue, desde Drive."""
    if current_app.testing:
        return _STAMP_RESULTS
    if _STAMP_RESULTS:
        return _STAMP_RESULTS
    value = None
    if STAMP_REQUESTS.exists():
        try:
            value = json.loads(STAMP_REQUESTS.read_text("utf-8"))
        except Exception:
            value = None
    if not isinstance(value, dict):
        try:
            value = load_json_file(STAMP_REQUESTS_DRIVE_FILE, default={})
        except Exception as exc:
            current_app.logger.warning("No se pudo recuperar el seguro de timbrado: %s", exc)
            value = {}
    if isinstance(value, dict):
        _STAMP_RESULTS.update(value)
    return _STAMP_RESULTS


def _persist_stamp_registry(require_drive=False):
    """Persiste como máximo los 500 intentos más recientes."""
    ordered = sorted(
        _STAMP_RESULTS.items(),
        key=lambda item: str((item[1] or {}).get("updated_at") or ""),
        reverse=True,
    )[:500]
    snapshot = dict(ordered)
    if current_app.testing:
        return True
    STAMP_REQUESTS.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        backup_json_file(STAMP_REQUESTS_DRIVE_FILE, snapshot)
        return True
    except Exception as exc:
        current_app.logger.warning("No se pudo respaldar el seguro de timbrado: %s", exc)
        if require_drive:
            raise RuntimeError(
                "No fue posible activar el seguro contra facturas duplicadas. No se timbró nada."
            ) from exc
        return False

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


def _payment_index_key(index, invoice_uuid):
    """Conserva una sola clave aunque el UUID cambie entre mayúsculas y minúsculas."""
    wanted = str(invoice_uuid or "").strip()
    folded = wanted.casefold()
    return next((key for key in (index or {}) if str(key).strip().casefold() == folded), wanted)


def _payment_record(index, invoice_uuid):
    """Busca el UUID sin depender de si Facturama lo devolvió en mayúsculas."""
    if not isinstance(index, dict):
        return None
    wanted = str(invoice_uuid or "").strip().casefold()
    if not wanted:
        return None
    return index.get(_payment_index_key(index, invoice_uuid))


def _xml_local_name(tag):
    return str(tag or "").rsplit("}", 1)[-1]


def _parse_payment_complement_xml(xml_bytes, *, rep_id="", status="active", fallback_date=""):
    """Extrae cada documento relacionado de un REP 1.0/2.0 del SAT."""
    root = ET.fromstring(xml_bytes)
    if str(root.attrib.get("TipoDeComprobante") or "").upper() != "P":
        return []
    stamp = next((node for node in root.iter() if _xml_local_name(node.tag) == "TimbreFiscalDigital"), None)
    rep_uuid = str((stamp.attrib if stamp is not None else {}).get("UUID") or "").strip()
    cfdi_date = str(root.attrib.get("Fecha") or fallback_date or "").strip()
    entries = []
    for payment in (node for node in root.iter() if _xml_local_name(node.tag) == "Pago"):
        payment_date = str(payment.attrib.get("FechaPago") or cfdi_date).strip()
        payment_form = str(payment.attrib.get("FormaDePagoP") or "").strip()
        for related in (node for node in payment.iter() if _xml_local_name(node.tag) == "DoctoRelacionado"):
            invoice_uuid = str(related.attrib.get("IdDocumento") or "").strip()
            if not _valid_cfdi_uuid(invoice_uuid):
                continue
            try:
                amount = float(_money(related.attrib.get("ImpPagado")))
                previous = float(_money(related.attrib.get("ImpSaldoAnt")))
                remaining = float(_money(related.attrib.get("ImpSaldoInsoluto")))
                partiality = max(1, int(related.attrib.get("NumParcialidad") or 1))
            except (TypeError, ValueError):
                continue
            entries.append({
                "invoice_uuid": invoice_uuid,
                "rep_id": str(rep_id or ""),
                "rep_uuid": rep_uuid,
                "status": status,
                "amount": amount,
                "previous_balance": previous,
                "remaining_balance": remaining,
                "partiality_number": partiality,
                "date": payment_date,
                "payment_form": payment_form,
                "imported": True,
            })
    return entries


def _sync_facturama_payment_rows(rows):
    """Incorpora REP importados en Facturama al índice que alimenta saldos y balance."""
    payment_rows = []
    for item in rows or []:
        normalized = _facturama_invoice_row(item)
        if normalized.get("type") == "P" and normalized.get("id"):
            payment_rows.append((item, normalized))
    if not payment_rows:
        return {"found": 0, "imported": 0, "removed": 0, "errors": 0}

    with _PAYMENT_SYNC_LOCK:
        index = _read_index()
        changed = False
        imported = removed = errors = 0
        for _raw, row in payment_rows:
            rep_id = str(row.get("id") or "").strip()
            rep_uuid = str(row.get("uuid") or "").strip()
            status_text = str(row.get("status") or "").strip().lower()
            canceled = any(word in status_text for word in ("cancel", "canceled", "cancelado"))

            if canceled:
                for invoice_uuid, record in list(index.items()):
                    prior = _payment_entries(record)
                    kept = [entry for entry in prior if not (
                        str(entry.get("rep_id") or "") == rep_id
                        or (rep_uuid and str(entry.get("rep_uuid") or "").casefold() == rep_uuid.casefold())
                    )]
                    if len(kept) == len(prior):
                        continue
                    removed += len(prior) - len(kept)
                    changed = True
                    if kept:
                        index[invoice_uuid] = {
                            "status": "active", "payments": kept,
                            "paid_amount": round(sum(float(entry.get("amount") or 0) for entry in kept), 2),
                            "remaining_balance": kept[-1].get("remaining_balance"),
                        }
                    else:
                        index.pop(invoice_uuid, None)
                continue

            already_known = any(
                str(entry.get("rep_id") or "") == rep_id
                or (rep_uuid and str(entry.get("rep_uuid") or "").casefold() == rep_uuid.casefold())
                for record in index.values() for entry in _payment_entries(record)
            )
            if already_known:
                continue
            try:
                xml_bytes = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{rep_id}"))
                relations = _parse_payment_complement_xml(
                    xml_bytes, rep_id=rep_id, status="active", fallback_date=row.get("date") or ""
                )
            except Exception as exc:
                errors += 1
                current_app.logger.warning("No se pudo sincronizar el REP %s: %s", rep_id, exc)
                continue
            for relation in relations:
                invoice_uuid = relation.pop("invoice_uuid")
                matching_key = _payment_index_key(index, invoice_uuid)
                prior = list(_payment_entries(index.get(matching_key)))
                duplicate = any(
                    str(entry.get("rep_id") or "") == rep_id
                    and int(entry.get("partiality_number") or 1) == relation["partiality_number"]
                    for entry in prior
                )
                if duplicate:
                    continue
                prior.append(relation)
                prior.sort(key=lambda entry: (int(entry.get("partiality_number") or 1), str(entry.get("date") or "")))
                index[matching_key] = {
                    "status": "active", "payments": prior,
                    "paid_amount": round(sum(float(entry.get("amount") or 0) for entry in prior), 2),
                    "remaining_balance": prior[-1].get("remaining_balance"),
                }
                imported += 1
                changed = True
        if changed:
            _write_index(index)
        return {"found": len(payment_rows), "imported": imported, "removed": removed, "errors": errors}

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
    """Proveedor único: nunca cambiar silenciosamente de PAC por una mala configuración."""
    return "facturama"


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


def _facturama_received_row(inv):
    """Normaliza CFDI recibido mostrando al proveedor, no al receptor HSC."""
    row = _facturama_invoice_row(inv)
    issuer = _pick(inv, "Issuer", "Emisor") or {}
    row.update({
        "supplier_name": _pick(issuer, "Name", "LegalName", "TaxName")
            or _pick(inv, "IssuerName", "TaxName") or "Proveedor sin nombre",
        "supplier_tax_id": _pick(issuer, "Rfc", "TaxId")
            or _pick(inv, "IssuerRfc", "Rfc") or "",
        "subtotal": _pick(inv, "Subtotal") or 0,
        "discount": _pick(inv, "Discount") or 0,
        "currency": _pick(inv, "Currency") or "MXN",
    })
    return row


def _received_summary(rows):
    """Importes documentales separados por moneda, no deducibilidad ni IVA pagado."""
    currencies = {}
    for row in rows:
        if "cancel" in str(row.get("status") or "").lower() or row.get("type") not in {"I", "E"}:
            continue
        currency = str(row.get("currency") or "MXN").upper()
        group = currencies.setdefault(currency, {"invoices": 0, "credit_notes": 0, "subtotal": 0.0, "total": 0.0})
        sign = -1 if row["type"] == "E" else 1
        group["credit_notes" if sign < 0 else "invoices"] += 1
        group["subtotal"] += sign * (float(row.get("subtotal") or 0) - float(row.get("discount") or 0))
        group["total"] += sign * float(row.get("total") or 0)
    for group in currencies.values():
        group["subtotal"] = round(group["subtotal"], 2)
        group["total"] = round(group["total"], 2)
    return currencies


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
    # En producción la serie HSC administra su propio consecutivo en Facturama.
    # El navegador nunca envía un folio fiscal: así una cotización no puede
    # alterar ni duplicar accidentalmente la numeración oficial.
    series = str(payload.get("serie") or ("HSC" if not cfg["sandbox"] else "")).strip()
    if series:
        cfdi["Serie"] = series
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


def _invoice_pdf_metadata(inv_id):
    """Recupera los datos comerciales que no forman parte del XML del SAT."""
    wanted = str(inv_id or "")
    for entry in _load_stamp_registry().values():
        if not isinstance(entry, dict):
            continue
        result = entry.get("result") if isinstance(entry.get("result"), dict) else entry
        if str(result.get("invoice_id") or "") == wanted:
            return {
                "internal_folio": str(result.get("internal_folio") or "").strip(),
                "order_number": str(result.get("order_number") or "").strip(),
                "client_name": str(result.get("client_name") or "").strip(),
                "source_quote_id": str(result.get("source_quote_id") or "").strip(),
                "quote_folio": str(result.get("quote_folio") or "").strip(),
                "invoice_alias": str(result.get("invoice_alias") or "").strip(),
                "drive_folder_name": str(result.get("drive_folder_name") or "").strip(),
            }
    return {"internal_folio": "", "order_number": "", "client_name": "", "source_quote_id": "", "quote_folio": "", "invoice_alias": "", "drive_folder_name": ""}


def _hsc_invoice_pdf(xml_bytes, inv_id, *, internal_folio="", order_number="", quote_folio=""):
    metadata = _invoice_pdf_metadata(inv_id)
    return build_invoice_pdf_bytes(
        xml_bytes,
        internal_folio=internal_folio or metadata["internal_folio"],
        order_number=order_number or metadata["order_number"],
        quote_folio=quote_folio or metadata["quote_folio"],
    )


def _facturama_printable_pdf(xml_bytes, inv_id, *, internal_folio="", order_number="", quote_folio=""):
    """Personaliza facturas de ingreso y conserva el formato oficial de otros CFDI."""
    if parse_cfdi(xml_bytes).get("cfdi_type") == "I":
        return _hsc_invoice_pdf(
            xml_bytes, inv_id,
            internal_folio=internal_folio,
            order_number=order_number,
            quote_folio=quote_folio,
        )
    return _decode_facturama_file(_fm_request("GET", f"/Cfdi/pdf/issued/{inv_id}"))


def _backup_facturama_cfdi(
    inv_id, uuid, client_name, internal_folio, document_type="Factura", order_number="", source_quote_id="", invoice_alias="", quote_folio=""
):
    """Descarga PDF/XML y los respalda sin comprometer un timbrado exitoso."""
    try:
        xml = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
        pdf = _facturama_printable_pdf(
            xml, inv_id, internal_folio=internal_folio, order_number=order_number,
            quote_folio=quote_folio,
        )
        result = backup_cfdi(
            client_name, internal_folio, uuid, pdf, xml,
            document_type=document_type, folder_alias=invoice_alias
        )
        moved = []
        if source_quote_id and result.get("folder_id"):
            moved = move_pending_documents(client_name, source_quote_id, result["folder_id"])
        result["support_documents_moved"] = len(moved)
        if result.get("ok"):
            notices.publish("Respaldo confirmado", f"{document_type} {internal_folio or uuid} guardado en Drive.",
                            category="respaldos", key=f"backup-ok-{uuid}", url="/facturacion")
        return result
    except Exception as exc:
        current_app.logger.warning("CFDI timbrado, pero falló su respaldo en Drive: %s", exc)
        notices.publish("Respaldo pendiente", f"El CFDI {uuid} está timbrado, pero su respaldo en Drive no se confirmó. No vuelvas a timbrarlo.",
                        category="respaldos", key=f"backup-error-{uuid}", level="warning", url="/facturacion")
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


@facturacion_bp.get("/facturama/subscription")
def api_facturama_subscription():
    """Expone únicamente el saldo y la vigencia del paquete API."""
    cfg = _facturama_config()
    if not cfg["configured"]:
        return jsonify({
            "ok": False,
            "environment": "sandbox" if cfg["sandbox"] else "production",
            "error": "Facturama no está configurado",
        }), 503
    try:
        subscription = _fm_request("GET", "/SuscriptionPlan", timeout=25)
        raw_folios = _pick(subscription, "CurrentFolios")
        try:
            current_folios = max(0, int(float(raw_folios)))
        except (TypeError, ValueError):
            current_folios = None
        return jsonify({
            "ok": True,
            "environment": "sandbox" if cfg["sandbox"] else "production",
            "plan": str(_pick(subscription, "Plan") or "").strip(),
            "current_folios": current_folios,
            "creation_date": str(_pick(subscription, "CreationDate") or "").strip(),
            "expiration_date": str(_pick(subscription, "ExpirationDate") or "").strip(),
        }), 200
    except requests.HTTPError as exc:
        return jsonify({
            "ok": False,
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

        # El endpoint también exige la confirmación explícita de la interfaz.
        # Así un doble clic, un script viejo o una llamada incompleta no timbra.
        if payload.get("confirmed") is not True:
            return jsonify({
                "ok": False,
                "stage": "confirmation",
                "error": "Confirma los datos de la factura antes de timbrar.",
            }), 400

        if provider == "facturama":
            cfg = _facturama_config()
            if not cfg["configured"]:
                return jsonify({
                    "ok": False,
                    "provider": "facturama",
                    "stage": "configuration",
                    "error": "Facturama no está configurado. No se intentó timbrar con otro proveedor.",
                }), 503
            invoice_alias = str(payload.get("alias_factura") or "").strip()
            if len(invoice_alias) > 100:
                return jsonify({"ok": False, "stage": "validation", "error": "El alias admite hasta 100 caracteres"}), 400
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

            # Serializa el timbrado y conserva el candado incluso tras reiniciar Render.
            with _STAMP_LOCK:
                registry = _load_stamp_registry()
                fingerprint = _stamp_fingerprint(payload)
                existing = registry.get(request_id)
                if existing:
                    if existing.get("fingerprint") and existing.get("fingerprint") != fingerprint:
                        return jsonify({
                            "ok": False,
                            "stage": "duplicate_check",
                            "error": "El formulario cambió después de iniciar este timbrado. Recarga y verifica el panel antes de continuar.",
                        }), 409
                    if existing.get("state") == "completed" and isinstance(existing.get("result"), dict):
                        cached = dict(existing["result"])
                        cached["duplicate_prevented"] = True
                        return jsonify(cached), 200
                    # Compatibilidad con respuestas guardadas antes del registro persistente.
                    if existing.get("ok") and existing.get("invoice_id"):
                        cached = dict(existing)
                        cached["duplicate_prevented"] = True
                        return jsonify(cached), 200
                    return jsonify({
                        "ok": False,
                        "stage": "duplicate_check",
                        "uncertain": existing.get("state") == "uncertain",
                        "error": (
                            "Este timbrado quedó pendiente de confirmación. Revisa primero el panel de facturas; "
                            "por seguridad no se enviará nuevamente."
                        ),
                    }), 409

                registry[request_id] = {
                    "state": "processing",
                    "fingerprint": fingerprint,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "receiver_rfc": str((payload.get("receptor") or {}).get("rfc") or ""),
                    "folio": str(payload.get("folio") or ""),
                }
                try:
                    _persist_stamp_registry(require_drive=not cfg["sandbox"])
                except (OSError, RuntimeError) as exc:
                    registry.pop(request_id, None)
                    return jsonify({"ok": False, "stage": "duplicate_lock", "error": str(exc)}), 503
                try:
                    invoice = _fm_request("POST", "/3/cfdis", json_body=cfdi, timeout=75)
                except requests.HTTPError as exc:
                    registry.pop(request_id, None)
                    _persist_stamp_registry(require_drive=False)
                    status_code = getattr(exc.response, "status_code", 400) or 400
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "environment": "sandbox" if cfg["sandbox"] else "production",
                        "stage": "stamp",
                        "error": _http_error_detail(exc),
                    }), 400 if status_code < 500 else 502
                except requests.RequestException as exc:
                    registry[request_id].update({
                        "state": "uncertain",
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    _persist_stamp_registry(require_drive=False)
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "connection",
                        "uncertain": True,
                        "error": (
                            "Facturama no confirmó la respuesta. Revisa el panel antes de hacer otra factura; "
                            "este intento quedó bloqueado para evitar duplicados."
                        ),
                        "detail": str(exc),
                    }), 502

                inv_id = _pick(invoice, "Id")
                uuid = _pick(invoice, "Uuid", "FolioFiscal")
                if not uuid:
                    complement = _pick(invoice, "Complement") or {}
                    tax_stamp = _pick(complement, "TaxStamp") or {}
                    uuid = _pick(tax_stamp, "Uuid")
                if not inv_id:
                    registry.pop(request_id, None)
                    _persist_stamp_registry(require_drive=False)
                    return jsonify({
                        "ok": False,
                        "provider": "facturama",
                        "stage": "response",
                        "error": "Facturama respondió sin identificador de CFDI",
                    }), 502
                if not _valid_cfdi_uuid(uuid):
                    registry.pop(request_id, None)
                    _persist_stamp_registry(require_drive=False)
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
                order_number = str(payload.get("numero_orden_compra") or "").strip()
                quote_folio = str(payload.get("quote_folio") or "").strip()
                # El folio interno siempre es el consecutivo que confirma Facturama.
                # La cotización queda guardada como referencia comercial separada.
                internal_folio = str(_pick(invoice, "Folio") or inv_id).strip()
                drive_backup = _backup_facturama_cfdi(
                    str(inv_id), str(uuid).strip(),
                    str(payload.get("cliente_carpeta")
                        or (payload.get("receptor") or {}).get("nombre") or "SIN_CLIENTE"),
                    internal_folio,
                    "Factura",
                    order_number,
                    str(payload.get("source_quote_id") or "").strip(),
                    invoice_alias,
                    quote_folio=quote_folio,
                )
                result["drive_backup"] = drive_backup
                result["internal_folio"] = internal_folio
                result["order_number"] = order_number
                result["client_name"] = str(payload.get("cliente_carpeta") or (payload.get("receptor") or {}).get("nombre") or "SIN_CLIENTE")
                result["source_quote_id"] = str(payload.get("source_quote_id") or "").strip()
                result["quote_folio"] = quote_folio
                result["invoice_alias"] = invoice_alias
                result["drive_folder_name"] = str(drive_backup.get("folder_name") or internal_folio)
                if not drive_backup.get("ok"):
                    result["warning"] = (
                        "La factura se timbró correctamente, pero Drive no confirmó el respaldo. "
                        "Puedes descargarla desde el panel de facturación."
                    )
                registry[request_id] = {
                    "state": "completed",
                    "fingerprint": fingerprint,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "result": dict(result),
                }
                _persist_stamp_registry(require_drive=False)
                return jsonify(result), 200

        # Código heredado de Facturapi conservado temporalmente para referencia,
        # pero ya no es seleccionable automáticamente.
        if provider != "facturapi":
            return jsonify({"ok": False, "stage": "configuration", "error": "Proveedor de timbrado no permitido."}), 503
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
    """Facturas del mes solicitado, incluyendo XML históricos importados."""
    if _provider() == "facturama":
        selected_month = str(request.args.get("month") or "").strip()
        if selected_month and not re.fullmatch(r"\d{4}-\d{2}", selected_month):
            return jsonify({"ok": False, "error": "El mes solicitado no es válido."}), 400
        try:
            if selected_month:
                month_start = datetime.strptime(selected_month + "-01", "%Y-%m-%d")
                next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
                rows = _facturama_period_rows("issued", month_start, next_month - timedelta(days=1))
            else:
                raw = _fm_request("GET", "/api/cfdi", params={"type": "issued", "status": "all", "page": 0})
                rows = raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])
        except requests.HTTPError as exc:
            return jsonify({"ok": False, "provider": "facturama", "error": _http_error_detail(exc)}), 400
        payment_sync = _sync_facturama_payment_rows(rows)
        out = []
        paid_idx = _read_index()
        deliveries = read_email_deliveries()
        for inv in rows:
            normalized = _facturama_invoice_row(inv)
            summary = _payment_summary(_payment_record(paid_idx, normalized["uuid"]), normalized["total"])
            normalized.update({
                "paid": normalized["type"] == "I" and summary["paid"],
                "payment_count": summary["payment_count"] if normalized["type"] == "I" else 0,
                "paid_amount": summary["paid_amount"] if normalized["type"] == "I" else 0,
                "remaining_balance": summary["remaining_balance"] if normalized["type"] == "I" else 0,
                "email_delivery": delivery_status("factura", normalized["id"], deliveries),
            })
            # Los intentos rechazados pueden tener Id, pero no folio fiscal.
            if normalized["id"] and _valid_cfdi_uuid(normalized["uuid"]):
                out.append(normalized)
        return jsonify({"ok": True, "provider": "facturama", "data": out, "payment_sync": payment_sync}), 200
    try:
        rs = _fa_get("/invoices", params={"limit": 100})
    except requests.HTTPError as e:
        return jsonify({"ok": False, "error": getattr(e.response, "text", str(e))}), 400

    paid_idx = _read_index()  # claves = UUID de facturas origen con REP activo
    deliveries = read_email_deliveries()
    out = []
    for inv in rs.get("data", []):
        cust = inv.get("customer") or {}
        uuid = inv.get("uuid")
        summary = _payment_summary(_payment_record(paid_idx, uuid), inv.get("total"))
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
            "email_delivery": delivery_status("factura", inv.get("id"), deliveries),
        })
    return jsonify({"ok": True, "data": out}), 200


@facturacion_bp.get("/facturacion/clientes")
def api_billing_clients():
    """Relación por receptor de facturas, cobros y complementos registrados."""
    if _provider() != "facturama":
        return jsonify({"ok": False, "error": "El panel por cliente requiere Facturama."}), 503
    try:
        raw = _fm_request("GET", "/api/cfdi", params={"type": "issued", "status": "all", "page": 0})
    except requests.HTTPError as exc:
        return jsonify({"ok": False, "error": _http_error_detail(exc)}), 400
    source = raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])
    payment_sync = _sync_facturama_payment_rows(source)
    payments = _read_index()
    deliveries = read_email_deliveries()
    groups = {}
    for item in source:
        invoice = _facturama_invoice_row(item)
        if invoice["type"] != "I" or not invoice["id"] or not _valid_cfdi_uuid(invoice["uuid"]):
            continue
        rfc = str(invoice.get("customer_tax_id") or "").strip().upper()
        name = str(invoice.get("customer_name") or "Cliente sin nombre").strip()
        key = rfc or re.sub(r"\W+", "", name.casefold())
        group = groups.setdefault(key, {
            "key": key, "name": name, "rfc": rfc, "invoice_count": 0,
            "invoiced_total": 0.0, "collected_total": 0.0, "pending_total": 0.0,
            "pending_complements": 0, "invoices": [], "complements": [],
        })
        status_text = str(invoice.get("status") or "").lower()
        active = not any(word in status_text for word in ("cancel", "canceled", "cancelado"))
        summary = _payment_summary(_payment_record(payments, invoice["uuid"]), invoice.get("total"))
        total = round(float(invoice.get("total") or 0), 2)
        pending = summary["remaining_balance"] if invoice.get("payment_method") == "PPD" and active else 0.0
        collected = (total if invoice.get("payment_method") == "PUE" else summary["paid_amount"]) if active else 0.0
        group["invoice_count"] += 1
        group["invoiced_total"] = round(group["invoiced_total"] + (total if active else 0), 2)
        group["collected_total"] = round(group["collected_total"] + collected, 2)
        group["pending_total"] = round(group["pending_total"] + pending, 2)
        if active and invoice.get("payment_method") == "PPD" and pending > 0.009:
            group["pending_complements"] += 1
        group["invoices"].append({
            **invoice, "active": active, "paid_amount": summary["paid_amount"],
            "remaining_balance": pending, "payment_count": summary["payment_count"],
            "email_delivery": delivery_status("factura", invoice["id"], deliveries),
        })
        for payment in summary["payments"]:
            rep_id = str(payment.get("rep_id") or "")
            group["complements"].append({
                "id": rep_id, "uuid": str(payment.get("rep_uuid") or ""),
                "invoice_id": invoice["id"], "invoice_uuid": invoice["uuid"],
                "invoice_folio": invoice.get("folio") or "", "date": payment.get("date") or "",
                "amount": float(payment.get("amount") or 0),
                "partiality_number": payment.get("partiality_number") or 1,
                "email_delivery": delivery_status("factura", rep_id, deliveries),
            })
    result = list(groups.values())
    for group in result:
        group["invoices"].sort(key=lambda row: str(row.get("date") or ""), reverse=True)
        group["complements"].sort(key=lambda row: str(row.get("date") or ""), reverse=True)
    result.sort(key=lambda row: (row["pending_complements"] == 0, -row["pending_total"], row["name"].casefold()))
    return jsonify({"ok": True, "data": result, "payment_sync": payment_sync}), 200


def _facturama_period_rows(document_type, start_date, end_date):
    """Recorre páginas sin depender del tamaño de página que use Facturama."""
    rows = []
    seen = set()
    params = {
        "type": document_type,
        "status": "all",
        "dateStart": start_date.strftime("%d/%m/%Y"),
        "dateEnd": end_date.strftime("%d/%m/%Y"),
    }
    for page in range(20):
        raw = _fm_request("GET", "/api/cfdi", params={**params, "page": page}, timeout=45)
        batch = raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])
        if not batch:
            break
        added = 0
        for item in batch:
            identity = str(_pick(item, "Id") or _pick(item, "Uuid", "FolioFiscal") or "").strip()
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            rows.append(item)
            added += 1
        if added == 0:
            break
    return rows


@facturacion_bp.get("/facturacion/balance")
def api_billing_balance():
    """Resumen de ingresos, cobranza y CFDI recibidos para control operativo."""
    if _provider() != "facturama":
        return jsonify({"ok": False, "error": "El balance requiere Facturama."}), 503
    period = str(request.args.get("period") or datetime.now().strftime("%Y-%m")).strip()
    scope = str(request.args.get("scope") or "month").strip().lower()
    if not re.fullmatch(r"\d{4}-\d{2}", period) or scope not in {"month", "year"}:
        return jsonify({"ok": False, "error": "El periodo solicitado no es válido."}), 400
    selected = datetime.strptime(period + "-01", "%Y-%m-%d")
    if scope == "year":
        start_date = selected.replace(month=1, day=1)
        end_date = selected.replace(month=12, day=31)
        date_prefix = selected.strftime("%Y-")
    else:
        start_date = selected
        next_month = (selected.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month - timedelta(days=1)
        date_prefix = selected.strftime("%Y-%m")
    client_filter = str(request.args.get("client") or "").strip().upper()

    try:
        issued_source = _facturama_period_rows("issued", start_date, end_date)
        received_source = _facturama_period_rows("received", start_date, end_date)
    except requests.HTTPError as exc:
        return jsonify({"ok": False, "error": _http_error_detail(exc)}), 400

    payment_sync = _sync_facturama_payment_rows(issued_source)
    payments = _read_index()
    issued_total = collected_total = pending_total = received_total = 0.0
    invoice_count = expense_count = pending_complements = 0
    client_rows = {}
    client_options = {}
    pending_rows = []
    series = {f"{selected.year}-{month:02d}": {"issued": 0.0, "collected": 0.0, "received": 0.0} for month in range(1, 13)}

    for item in issued_source:
        invoice = _facturama_invoice_row(item)
        if not str(invoice.get("date") or "").startswith(date_prefix) or not _valid_cfdi_uuid(invoice.get("uuid")):
            continue
        status_text = str(invoice.get("status") or "").lower()
        if any(word in status_text for word in ("cancel", "canceled", "cancelado")):
            continue
        rfc = str(invoice.get("customer_tax_id") or "").strip().upper()
        name = str(invoice.get("customer_name") or "Cliente sin nombre").strip()
        client_key = rfc or re.sub(r"\W+", "", name.casefold())
        client_options[client_key] = {"key": client_key, "name": name, "rfc": rfc}
        if client_filter and client_key.upper() != client_filter:
            continue
        total = round(float(invoice.get("total") or 0), 2)
        month_key = str(invoice.get("date") or "")[:7]
        client = client_rows.setdefault(client_key, {"key": client_key, "name": name, "rfc": rfc, "total": 0.0, "collected": 0.0, "pending": 0.0, "count": 0})
        if invoice.get("type") == "E":
            issued_total -= total
            client["total"] -= total
            if month_key in series:
                series[month_key]["issued"] -= total
            continue
        if invoice.get("type") != "I":
            continue
        summary = _payment_summary(_payment_record(payments, invoice.get("uuid")), total)
        collected = total if invoice.get("payment_method") == "PUE" else summary["paid_amount"]
        pending = summary["remaining_balance"] if invoice.get("payment_method") == "PPD" else 0.0
        issued_total += total
        collected_total += collected
        pending_total += pending
        invoice_count += 1
        client["total"] += total
        client["collected"] += collected
        client["pending"] += pending
        client["count"] += 1
        if month_key in series:
            series[month_key]["issued"] += total
            series[month_key]["collected"] += collected
        if invoice.get("payment_method") == "PPD" and pending > 0.009:
            pending_complements += 1
            pending_rows.append({
                "id": invoice.get("id"), "uuid": invoice.get("uuid"), "folio": invoice.get("folio"),
                "date": invoice.get("date"), "client_name": name, "client_tax_id": rfc,
                "total": total, "paid": summary["paid_amount"], "pending": pending,
            })

    foreign_received_count = 0
    for item in received_source:
        invoice = _facturama_received_row(item)
        if not str(invoice.get("date") or "").startswith(date_prefix) or not _valid_cfdi_uuid(invoice.get("uuid")):
            continue
        status_text = str(invoice.get("status") or "").lower()
        if any(word in status_text for word in ("cancel", "canceled", "cancelado")):
            continue
        if invoice.get("type") not in {"I", "E"}:
            continue
        if str(invoice.get("currency") or "MXN").upper() != "MXN":
            foreign_received_count += 1
            continue
        amount = float(invoice.get("total") or 0) * (-1 if invoice.get("type") == "E" else 1)
        received_total += amount
        expense_count += 1 if invoice.get("type") == "I" else 0
        month_key = str(invoice.get("date") or "")[:7]
        if month_key in series:
            series[month_key]["received"] += amount

    clients = list(client_rows.values())
    for row in clients:
        for field in ("total", "collected", "pending"):
            row[field] = round(row[field], 2)
    clients.sort(key=lambda row: row["total"], reverse=True)
    pending_rows.sort(key=lambda row: (-row["pending"], str(row.get("date") or "")))
    series_rows = [{"month": key, **{name: round(value, 2) for name, value in values.items()}} for key, values in sorted(series.items())]
    if scope == "month":
        series_rows = [row for row in series_rows if row["month"] == selected.strftime("%Y-%m")]
    return jsonify({"ok": True, "data": {
        "scope": scope, "period": period, "client_filtered": bool(client_filter), "invoice_count": invoice_count, "expense_count": expense_count,
        "issued_total": round(issued_total, 2), "collected_total": round(collected_total, 2),
        "pending_total": round(pending_total, 2), "received_total": round(received_total, 2),
        "estimated_result": round(issued_total - received_total, 2),
        "pending_complements": pending_complements, "clients": clients,
        "client_options": sorted(client_options.values(), key=lambda row: row["name"].casefold()),
        "top_clients": clients[:8], "pending_invoices": pending_rows, "series": series_rows,
        "payment_sync": payment_sync,
        "foreign_received_count": foreign_received_count,
        "received_coverage": "Sólo CFDI disponibles en Facturama; no acredita descarga completa del SAT ni deducibilidad.",
    }}), 200


@facturacion_bp.get("/facturas/received")
def api_list_received_invoices():
    """CFDI recibidos por HSC disponibles en la cuenta Web de Facturama."""
    if _provider() != "facturama":
        return jsonify({"ok": False, "error": "Las facturas recibidas requieren la conexión con Facturama."}), 503
    try:
        params = {"type": "received", "status": "all"}
        selected_month = str(request.args.get("month") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}", selected_month):
            month_start = datetime.strptime(selected_month + "-01", "%Y-%m-%d")
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end = next_month - timedelta(days=1)
            params.update({
                "dateStart": month_start.strftime("%d/%m/%Y"),
                "dateEnd": month_end.strftime("%d/%m/%Y"),
            })
        rows = []
        seen = set()
        truncated = False
        for page in range(20):
            page_params = {**params, "page": page}
            raw = _fm_request("GET", "/api/cfdi", params=page_params, timeout=45)
            batch = raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])
            if not batch:
                break
            fresh = []
            for item in batch:
                identity = str(_pick(item, "Uuid", "FolioFiscal") or _pick(item, "Id") or "")
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                fresh.append(item)
            if not fresh:
                break
            rows.extend(fresh)
        else:
            truncated = True
        data = []
        for invoice in rows:
            normalized = _facturama_received_row(invoice)
            if normalized["id"] and _valid_cfdi_uuid(normalized["uuid"]):
                data.append(normalized)
        return jsonify({"ok": True, "provider": "facturama", "data": data,
                        "totals_by_currency": _received_summary(data), "truncated": truncated,
                        "coverage": "Sólo documentos disponibles en Facturama. No confirma que estén todos los gastos ni su deducibilidad."}), 200
    except requests.HTTPError as exc:
        return jsonify({"ok": False, "error": _http_error_detail(exc)}), 400


@facturacion_bp.get("/invoice-templates")
def api_invoice_templates():
    return jsonify({
        "ok": True,
        "data": _read_invoice_templates(refresh=request.args.get("actualizar") == "1"),
    }), 200


@facturacion_bp.post("/invoice-templates")
def api_save_invoice_template():
    body = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()
    if len(name) < 2 or len(name) > 80:
        return jsonify({"ok": False, "error": "Escribe un nombre de plantilla de 2 a 80 caracteres."}), 400
    items = body.get("items") if isinstance(body.get("items"), list) else []
    if not items:
        return jsonify({"ok": False, "error": "Agrega al menos un concepto a la plantilla."}), 400
    if len(items) > 60:
        return jsonify({"ok": False, "error": "Una plantilla admite hasta 60 conceptos."}), 400
    template_id = str(body.get("id") or uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    template = {
        "id": template_id,
        "name": name,
        "client_name": str(body.get("client_name") or "").strip(),
        "receiver": body.get("receiver") if isinstance(body.get("receiver"), dict) else {},
        "conditions": body.get("conditions") if isinstance(body.get("conditions"), dict) else {},
        "retentions": body.get("retentions") if isinstance(body.get("retentions"), dict) else {},
        "items": items,
        "updated_at": now,
    }
    templates = _read_invoice_templates()
    previous = next((row for row in templates if str(row.get("id")) == template_id), None)
    template["created_at"] = (previous or {}).get("created_at") or now
    templates = [template if str(row.get("id")) == template_id else row for row in templates]
    if previous is None:
        templates.append(template)
    templates.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    drive_ok = _write_invoice_templates(templates)
    return jsonify({
        "ok": True, "template": template, "drive_backup": drive_ok,
        "warning": "La plantilla se guardó, pero Drive no confirmó el respaldo." if not drive_ok else "",
    }), 200


@facturacion_bp.delete("/invoice-templates/<template_id>")
def api_delete_invoice_template(template_id):
    templates = _read_invoice_templates()
    kept = [row for row in templates if str(row.get("id")) != str(template_id)]
    if len(kept) == len(templates):
        return jsonify({"ok": False, "error": "La plantilla ya no existe."}), 404
    drive_ok = _write_invoice_templates(kept)
    return jsonify({"ok": True, "drive_backup": drive_ok}), 200


def _validated_invoice_schedule(body, previous=None):
    templates = _read_invoice_templates()
    template_id = str(body.get("template_id") or (previous or {}).get("template_id") or "").strip()
    template = next((item for item in templates if str(item.get("id")) == template_id), None)
    if not template:
        raise ValueError("Selecciona una plantilla disponible.")
    name = re.sub(r"\s+", " ", str(body.get("name") or (previous or {}).get("name") or template.get("name") or "")).strip()
    if len(name) < 2 or len(name) > 80:
        raise ValueError("Escribe un nombre de 2 a 80 caracteres.")
    try:
        day = int(body.get("day", (previous or {}).get("day", 1)))
    except (TypeError, ValueError):
        day = 0
    if day < 1 or day > 31:
        raise ValueError("El día del mes debe estar entre 1 y 31.")
    at_time = str(body.get("time") or (previous or {}).get("time") or "09:00").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at_time):
        raise ValueError("Escribe una hora válida.")
    mode = str(body.get("mode") or (previous or {}).get("mode") or "confirm").strip()
    if mode not in {"reminder", "confirm", "auto_stamp", "auto_stamp_email"}:
        raise ValueError("Selecciona una modalidad válida.")
    if mode in {"auto_stamp", "auto_stamp_email"} and (not previous or previous.get("mode") != mode) and body.get("automatic_acknowledged") is not True:
        raise ValueError("Confirma expresamente que autorizas el timbrado automático.")
    receiver = template.get("receiver") or {}
    if mode in {"auto_stamp", "auto_stamp_email"}:
        required = [receiver.get("tax_id"), receiver.get("name"), receiver.get("zip"), receiver.get("tax_system"), template.get("items")]
        if not all(required):
            raise ValueError("Completa los datos fiscales y conceptos de la plantilla antes de automatizar el timbrado.")
    recipient = str(body.get("recipient", (previous or {}).get("recipient", receiver.get("email") or ""))).strip()
    cc = str(body.get("cc", (previous or {}).get("cc", ""))).strip()
    if mode == "auto_stamp_email" and not recipient:
        raise ValueError("Escribe el correo al que se enviará la factura automática.")
    now = _local_now()
    return {
        "id": str((previous or {}).get("id") or uuid4()),
        "name": name,
        "template_id": template_id,
        "frequency": "monthly",
        "day": day,
        "time": at_time,
        "mode": mode,
        "recipient": recipient,
        "cc": cc,
        "subject": str(body.get("subject", (previous or {}).get("subject", "Factura HSC {folio}"))).strip()[:200],
        "message": str(body.get("message", (previous or {}).get("message", (
            "Buen día, estimado cliente. Envío la factura solicitada.\n\n"
            "De antemano muchas gracias.\n\nQuedo a sus órdenes.\n\n"
            "Ing. Héctor Silva Cid\n\nCel: 5527605496"
        )))).strip(),
        "active": bool(body.get("active", (previous or {}).get("active", True))),
        "next_run": _next_monthly_run(day, at_time, after=now - timedelta(minutes=1)).isoformat(),
        "created_at": (previous or {}).get("created_at") or now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "last_run_key": (previous or {}).get("last_run_key", ""),
        "last_status": (previous or {}).get("last_status", "pending"),
        "last_error": (previous or {}).get("last_error", ""),
        "last_invoice_id": (previous or {}).get("last_invoice_id", ""),
    }


@facturacion_bp.get("/invoice-schedules")
def api_invoice_schedules():
    templates = {str(row.get("id")): row for row in _read_invoice_templates()}
    rows = []
    for row in _read_invoice_schedules():
        item = dict(row)
        template = templates.get(str(item.get("template_id"))) or {}
        item["template_name"] = template.get("name") or "Plantilla eliminada"
        item["client_name"] = template.get("client_name") or (template.get("receiver") or {}).get("name") or ""
        rows.append(item)
    rows.sort(key=lambda item: (not bool(item.get("active")), str(item.get("next_run") or "")))
    return jsonify({"ok": True, "data": rows, "push_configured": _push_configured()}), 200


@facturacion_bp.post("/invoice-schedules")
def api_save_invoice_schedule():
    body = request.get_json(silent=True) or {}
    schedules = _read_invoice_schedules()
    schedule_id = str(body.get("id") or "").strip()
    previous = next((row for row in schedules if str(row.get("id")) == schedule_id), None) if schedule_id else None
    try:
        schedule = _validated_invoice_schedule(body, previous)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    schedules = [schedule if str(row.get("id")) == schedule["id"] else row for row in schedules]
    if previous is None:
        schedules.append(schedule)
    drive_ok = _write_invoice_schedules(schedules)
    return jsonify({"ok": True, "schedule": schedule, "drive_backup": drive_ok}), 200


@facturacion_bp.delete("/invoice-schedules/<schedule_id>")
def api_delete_invoice_schedule(schedule_id):
    schedules = _read_invoice_schedules()
    kept = [row for row in schedules if str(row.get("id")) != str(schedule_id)]
    if len(kept) == len(schedules):
        return jsonify({"ok": False, "error": "La programación ya no existe."}), 404
    return jsonify({"ok": True, "drive_backup": _write_invoice_schedules(kept)}), 200


@facturacion_bp.post("/invoice-schedules/<schedule_id>/complete")
def api_complete_invoice_schedule(schedule_id):
    body = request.get_json(silent=True) or {}
    schedules = _read_invoice_schedules()
    schedule = next((row for row in schedules if str(row.get("id")) == str(schedule_id)), None)
    if not schedule:
        return jsonify({"ok": False, "error": "La programación ya no existe."}), 404
    schedule["last_status"] = "completed"
    schedule["last_error"] = ""
    schedule["last_invoice_id"] = str(body.get("invoice_id") or "")
    schedule["last_uuid"] = str(body.get("uuid") or "")
    schedule["confirmed_at"] = _local_now().isoformat(timespec="seconds")
    return jsonify({"ok": True, "drive_backup": _write_invoice_schedules(schedules)}), 200


@facturacion_bp.post("/push/subscribe")
def api_push_subscribe():
    body = request.get_json(silent=True) or {}
    subscription = body.get("subscription") if isinstance(body.get("subscription"), dict) else body
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"ok": False, "error": "La suscripción del dispositivo no es válida."}), 400
    rows = _read_push_subscriptions()
    item = {"endpoint": endpoint, "subscription": subscription, "updated_at": _local_now().isoformat(timespec="seconds")}
    rows = [item if row.get("endpoint") == endpoint else row for row in rows]
    if not any(row.get("endpoint") == endpoint for row in rows):
        rows.append(item)
    return jsonify({"ok": True, "drive_backup": _write_push_subscriptions(rows)}), 200


@facturacion_bp.get("/push/config")
def api_push_config():
    return jsonify({"ok": True, "enabled": _push_configured(), "public_key": os.getenv("VAPID_PUBLIC_KEY", "").strip()}), 200


@facturacion_bp.get("/notifications")
def api_notifications():
    try:
        data = notices.snapshot()
        schedules = _read_invoice_schedules()
        data["schedules"] = [{key: row.get(key) for key in (
            "id", "name", "active", "next_run", "last_run_at", "last_status", "last_error"
        )} for row in schedules]
        return jsonify({"ok": True, **data})
    except Exception:
        current_app.logger.exception("No se pudo consultar el centro de avisos")
        return jsonify({"ok": False, "error": "No se pudieron cargar los avisos. Intenta actualizar."}), 503


@facturacion_bp.post("/notifications/<item_id>/read")
def api_notification_read(item_id):
    try:
        found, backed_up = notices.mark_read(item_id)
        return jsonify({"ok": found, "drive_backup": backed_up}), 200 if found else 404
    except Exception:
        return jsonify({"ok": False, "error": "No se pudo guardar el cambio."}), 503


def _stamp_scheduled_invoice(schedule, template, run_key):
    payload = _template_invoice_payload(template, schedule, run_key)
    with current_app.test_request_context("/api/facturar", method="POST", json=payload):
        response = current_app.make_response(facturar())
    data = response.get_json(silent=True) or {}
    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "Facturama no confirmó el timbrado."))
    # Si el correo falla después, conservar la identidad del CFDI ya timbrado.
    schedule["last_invoice_id"] = str(data.get("invoice_id") or "")
    schedule["last_uuid"] = str(data.get("uuid") or "")
    if schedule.get("mode") == "auto_stamp_email":
        inv_id = str(data.get("invoice_id") or "")
        folio = str(data.get("internal_folio") or data.get("uuid") or inv_id)
        xml = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
        pdf = _facturama_printable_pdf(xml, inv_id)
        subject = str(schedule.get("subject") or "Factura HSC {folio}").replace("{folio}", folio).replace("{cliente}", str(template.get("client_name") or ""))
        delivery = delivery_status("factura", inv_id)
        if not delivery.get("sent"):
            mail_result = send_cfdi_email(
                recipient=schedule.get("recipient") or (template.get("receiver") or {}).get("email") or "",
                cc=schedule.get("cc") or "", subject=subject, body=schedule.get("message") or "",
                pdf_bytes=pdf, xml_bytes=xml, folio=folio,
            )
            record_email_delivery("factura", inv_id, recipients=mail_result.get("recipients") or [], cc=mail_result.get("cc") or [], folio=folio)
        data["email_sent"] = True
    return data


@facturacion_bp.post("/invoice-schedules/run")
def api_run_invoice_schedules():
    expected = os.getenv("HSC_SCHEDULER_KEY", "").strip()
    supplied = request.headers.get("X-HSC-Scheduler-Key", "")
    if len(expected) < 20 or not hmac.compare_digest(expected, supplied):
        return jsonify({"ok": False, "error": "Ejecución no autorizada."}), 403
    if not _SCHEDULE_RUN_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Ya se están revisando las facturas programadas."}), 409
    try:
        now = _local_now()
        schedules = _read_invoice_schedules()
        templates = {str(row.get("id")): row for row in _read_invoice_templates()}
        processed = []
        for schedule in schedules:
            if not schedule.get("active"):
                continue
            try:
                due = datetime.fromisoformat(str(schedule.get("next_run") or ""))
                if due.tzinfo is None:
                    due = due.replace(tzinfo=now.tzinfo)
            except ValueError:
                due = _next_monthly_run(schedule.get("day", 1), schedule.get("time", "09:00"), after=now - timedelta(days=32))
            if due > now:
                continue
            run_key = f"{due.year:04d}-{due.month:02d}"
            if schedule.get("last_run_key") == run_key:
                schedule["next_run"] = _next_monthly_run(schedule.get("day", 1), schedule.get("time", "09:00"), after=due + timedelta(days=1)).isoformat()
                continue
            template = templates.get(str(schedule.get("template_id")))
            schedule["last_run_key"] = run_key
            schedule["last_run_at"] = now.isoformat(timespec="seconds")
            schedule["next_run"] = _next_monthly_run(schedule.get("day", 1), schedule.get("time", "09:00"), after=due + timedelta(days=1)).isoformat()
            schedule["last_error"] = ""
            if not template:
                schedule["last_status"] = "error"
                schedule["last_error"] = "La plantilla fue eliminada."
                _send_push_notifications("Factura programada requiere atención",
                                         f"{schedule.get('name')}: la plantilla ya no existe.",
                                         "/facturacion?tab=plantillas", f"invoice-schedule-error-{schedule.get('id')}-{run_key}")
            elif schedule.get("mode") in {"reminder", "confirm"}:
                schedule["last_status"] = "awaiting_confirmation"
                _send_push_notifications(
                    "Factura lista para revisar",
                    f"{schedule.get('name')}: abre HSC para confirmar el timbrado.",
                    f"/facturas/nueva?template_id={schedule.get('template_id')}&schedule_id={schedule.get('id')}",
                    f"invoice-schedule-{schedule.get('id')}-{run_key}",
                )
            else:
                try:
                    result = _stamp_scheduled_invoice(schedule, template, run_key)
                    schedule["last_status"] = "completed"
                    schedule["last_invoice_id"] = str(result.get("invoice_id") or "")
                    schedule["last_uuid"] = str(result.get("uuid") or "")
                    _send_push_notifications(
                        "Factura procesada correctamente",
                        f"{schedule.get('name')} fue timbrada" + (" y enviada por correo." if result.get("email_sent") else "."),
                        "/facturacion", f"invoice-schedule-{schedule.get('id')}-{run_key}",
                    )
                except Exception as exc:
                    schedule["last_status"] = "error"
                    schedule["last_error"] = str(exc)[:500]
                    _send_push_notifications(
                        "Factura programada requiere atención",
                        f"No se completó {schedule.get('name')}. Revisa si ya se timbró antes de reintentar.",
                        "/facturacion?tab=plantillas", f"invoice-schedule-error-{schedule.get('id')}-{run_key}",
                    )
            processed.append({"id": schedule.get("id"), "status": schedule.get("last_status"), "error": schedule.get("last_error", "")})
        drive_ok = _write_invoice_schedules(schedules) if processed else True
        failures = sum(row.get("status") == "error" for row in processed)
        notices.record_job("facturas_programadas", "error" if failures else "ok",
                           processed=len(processed), errors=failures, drive_backup=drive_ok)
        return jsonify({"ok": True, "processed": processed, "drive_backup": drive_ok, "checked_at": now.isoformat()}), 200
    except Exception:
        notices.record_job("facturas_programadas", "error", last_error="La revisión no pudo terminar. Consulta los registros de Render.")
        raise
    finally:
        _SCHEDULE_RUN_LOCK.release()

# ----------------------------------------------------------------------
# PDF / XML / Cancelación
# ----------------------------------------------------------------------
@facturacion_bp.get("/invoices/<inv_id>/pdf")
def api_invoice_pdf(inv_id):
    if _provider() == "facturama":
        try:
            xml = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
            metadata = _invoice_pdf_metadata(inv_id)
            content = _facturama_printable_pdf(xml, inv_id)
            filename_folio = " - ".join(filter(None, [metadata.get("invoice_alias"), metadata["internal_folio"]])) or inv_id
            filename_folio = secure_filename(filename_folio) or secure_filename(inv_id) or "factura"
        except (requests.HTTPError, ValueError, OSError) as exc:
            detail = _http_error_detail(exc) if isinstance(exc, requests.HTTPError) else {"message": str(exc)}
            return jsonify({"ok": False, "error": detail}), 400
        return Response(
            content, mimetype="application/pdf",
            headers={"Content-Disposition": f"inline; filename=Factura-HSC-{filename_folio}.pdf"},
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
            metadata = _invoice_pdf_metadata(inv_id)
            filename_folio = " - ".join(filter(None, [metadata.get("invoice_alias"), metadata["internal_folio"]])) or inv_id
            filename_folio = secure_filename(filename_folio) or secure_filename(inv_id) or "factura"
        except (requests.HTTPError, ValueError) as exc:
            detail = _http_error_detail(exc) if isinstance(exc, requests.HTTPError) else {"message": str(exc)}
            return jsonify({"ok": False, "error": detail}), 400
    else:
        try:
            content = _fa_get_binary(f"/invoices/{inv_id}/xml", "application/xml")
        except requests.HTTPError as exc:
            return getattr(exc.response, "text", str(exc)), 400
        filename_folio = secure_filename(inv_id) or "factura"
    return Response(
        content, mimetype="application/xml",
        headers={"Content-Disposition": f"inline; filename=Factura-HSC-{filename_folio}.xml"},
    )


@facturacion_bp.get("/invoices/<inv_id>/received/<file_format>")
def api_received_invoice_file(inv_id, file_format):
    if file_format not in {"pdf", "xml"}:
        return jsonify({"ok": False, "error": "Formato no permitido."}), 404
    if _provider() != "facturama":
        return jsonify({"ok": False, "error": "La descarga de recibidas requiere Facturama."}), 503
    try:
        content = _decode_facturama_file(
            _fm_request("GET", f"/Cfdi/{file_format}/received/{inv_id}")
        )
        mimetype = "application/pdf" if file_format == "pdf" else "application/xml"
        return Response(content, mimetype=mimetype, headers={
            "Content-Disposition": f"inline; filename=Recibida-{inv_id}.{file_format}",
        })
    except (requests.HTTPError, ValueError) as exc:
        detail = _http_error_detail(exc) if isinstance(exc, requests.HTTPError) else {"message": str(exc)}
        return jsonify({"ok": False, "error": detail}), 400


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
    stored_ids = request.form.getlist("stored_attachments") if not request.is_json else []
    if stored_ids:
        try:
            metadata = _invoice_pdf_metadata(inv_id)
            stored = download_invoice_support_documents(
                metadata.get("client_name"), metadata.get("drive_folder_name") or metadata.get("internal_folio"), stored_ids
            )
            total_size += sum(len(item.get("data") or b"") for item in stored)
            if total_size > 15 * 1024 * 1024:
                return jsonify({"ok": False, "error": "Los archivos adicionales superan el límite total de 15 MB."}), 400
            extras.extend(stored)
        except Exception:
            current_app.logger.exception("No se pudieron recuperar los documentos guardados de %s", inv_id)
            return jsonify({"ok": False, "error": "No se pudieron recuperar los documentos guardados en Drive."}), 502
    try:
        if _provider() == "facturama":
            xml = _decode_facturama_file(_fm_request("GET", f"/Cfdi/xml/issued/{inv_id}"))
            pdf = _facturama_printable_pdf(xml, inv_id)
        else:
            pdf = _fa_get_binary(f"/invoices/{inv_id}/pdf", "application/pdf")
            xml = _fa_get_binary(f"/invoices/{inv_id}/xml", "application/xml")
        result = send_cfdi_email(
            recipient=recipient,
            cc=body.get("cc", ""),
            subject=subject,
            body=comments,
            pdf_bytes=pdf,
            xml_bytes=xml,
            folio=folio,
            extra_attachments=extras,
        )
        delivery = record_email_delivery(
            "factura", inv_id, recipients=result.get("recipients") or [], cc=result.get("cc") or [],
            folio=folio,
        )
        copy_warning = result.get("sent_copy_saved") is False
        response = jsonify({
            "ok": True,
            "message": (
                f"Factura enviada a {result.get('recipient') or recipient}."
                + (" CarrierZone no confirmó la copia en Enviados." if copy_warning else "")
            ),
            "sent_copy_saved": not copy_warning,
            "email_delivery": delivery,
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


@facturacion_bp.get("/invoices/<inv_id>/documents")
def api_invoice_documents(inv_id):
    metadata = _invoice_pdf_metadata(inv_id)
    if not metadata.get("client_name") or not metadata.get("internal_folio"):
        return jsonify({"ok": True, "documents": []}), 200
    try:
        items = list_invoice_support_documents(
            metadata["client_name"], metadata.get("drive_folder_name") or metadata["internal_folio"]
        )
        return jsonify({"ok": True, "documents": [{
            "id": item.get("id"), "name": item.get("name"),
            "size": int(item.get("size") or 0),
            "category": (item.get("appProperties") or {}).get("hscCategory") or "otro",
            "order_number": (item.get("appProperties") or {}).get("hscOrderNumber") or "",
        } for item in items]}), 200
    except Exception:
        current_app.logger.exception("No se pudo consultar el expediente de %s", inv_id)
        return jsonify({"ok": False, "error": "Drive no pudo consultar los documentos de la factura."}), 502


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

